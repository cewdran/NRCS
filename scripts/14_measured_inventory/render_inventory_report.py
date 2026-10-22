#!/usr/bin/env python3
"""Render the source-preserving measured-outcome inventory only on allocated Puma.

Values and dispositions are copied as strings from pinned engine outputs. This
stage performs no eligibility calculation, measurement conversion, aggregation,
fit, prediction, scoring, statistical test, or scientific plot generation.
"""
from hpc_guard import require_hpc
execution = require_hpc()

from datetime import datetime, timezone
from html import escape
from pathlib import Path
import csv
import importlib.metadata
import importlib.util
import json
import sys

from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from common import require, save, sha, table, verify_staged_sources

PACKAGE = 'NRCS_measured_outcomes_HPC_v1'
REPORT_STATUS = 'MEASURED_OUTCOME_INVENTORY_REPORT_COMPLETED_REVIEW_PENDING'
ENGINE_STATUS = 'HPC_MEASURED_OUTCOME_INVENTORY_COMPLETED_REVIEW_PENDING'
INK, MUTED = '#172738', '#536578'
PAGE_WIDTH, PAGE_HEIGHT = 792, 612
LEFT, RIGHT, BODY_TOP, BODY_BOTTOM = 36, 756, 500, 40
BODY_WIDTH = RIGHT - LEFT
REPORT_RELATIVE = 'results/NRCS_measured_outcomes_inventory_review.pdf'
LAYOUT_RELATIVE = 'audit/report_layout_receipt.json'


def safe_file(base, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and '..' not in relative.parts,
            'Unsafe relative path: ' + str(relative))
    base = base.resolve(strict=True)
    path = (base / relative).resolve(strict=True)
    require(path.is_file() and path.is_relative_to(base), 'File escapes verified root: ' + str(relative))
    return path


def load_json(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    require(isinstance(value, dict), 'Expected an object receipt: ' + str(path))
    return value


def read_csv(path, required=()):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        require(fields and len(fields) == len(set(fields)), 'Missing or duplicate CSV columns: ' + str(path))
        require(set(required) <= set(fields), 'Required CSV columns missing: ' + str(path))
        rows = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in rows),
            'Malformed CSV row: ' + str(path))
    return fields, rows


def nonnegative_integer(text, context):
    require(isinstance(text, str) and text.isdigit(), 'Noncanonical nonnegative integer: ' + context)
    return int(text)


def same_execution(record, context):
    require(isinstance(record, dict), 'Missing execution record: ' + context)
    require(all(str(record.get(k)) == str(execution[k]) for k in ('job_id', 'cluster', 'node')),
            'Different current allocation in ' + context)


def validate_environment(run, config):
    path = safe_file(run, 'audit/software/environment.json')
    value = load_json(path)
    same_execution(value.get('execution'), 'report environment')
    require(value.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED', 'Pinned runtime not verified.')
    require(value.get('versions') == config['runtime_expected_versions'] and
            value.get('requirements_sha256') == config['runtime_requirements_sha256'] and
            value.get('runtime_package') == config['runtime_package'], 'Runtime pin mismatch.')
    require(Path(value['executable']).resolve(strict=True) == Path(sys.executable).resolve(strict=True),
            'Report interpreter differs from verified environment.')
    require(value.get('python') == sys.version == config['runtime_expected_python'] and
            value.get('software_installed') is False and
            value.get('runtime_ready_sha256') == config['runtime_ready_sha256'] ==
            sha(safe_file(run, 'audit/software/original_runtime_READY.json')),
            'Runtime was installed/changed or its READY snapshot differs.')
    for name, expected in value['versions'].items():
        require(importlib.metadata.version(name) == expected, 'Runtime version changed: ' + name)
    return value


def register_fonts():
    spec = importlib.util.find_spec('matplotlib')
    roots = [Path('/usr/share/fonts/truetype/dejavu')]
    if spec and spec.origin:
        roots.insert(0, Path(spec.origin).parent / 'mpl-data' / 'fonts' / 'ttf')
    directory = next((root for root in roots if (root / 'DejaVuSans.ttf').is_file() and
                      (root / 'DejaVuSans-Bold.ttf').is_file()), None)
    require(directory is not None, 'An embedded DejaVu font pair is required for the report.')
    paths = [directory / 'DejaVuSans.ttf', directory / 'DejaVuSans-Bold.ttf']
    pdfmetrics.registerFont(TTFont('ReportSans', str(paths[0])))
    pdfmetrics.registerFont(TTFont('ReportBold', str(paths[1])))
    pdfmetrics.registerFontFamily('ReportSans', normal='ReportSans', bold='ReportBold',
                                italic='ReportSans', boldItalic='ReportBold')
    return [{'path': str(p), 'sha256': sha(p)} for p in paths]


def paragraph(text, width, size=8.0, bold=False, color=INK):
    require(width > 12, 'Paragraph column is narrower than its padding.')
    style = ParagraphStyle('inventory', fontName='ReportBold' if bold else 'ReportSans',
                           fontSize=size, leading=size * 1.30, textColor=HexColor(color),
                           splitLongWords=True)
    # Text is escaped exactly; only source newlines become visual line breaks.
    text = escape(str(text)).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')
    value = Paragraph(text, style)
    _, height = value.wrap(width, PAGE_HEIGHT)
    return value, height


def header_parts(widths, labels):
    require(len(widths) == len(labels) and abs(sum(widths) - BODY_WIDTH) < 0.001,
            'Report columns differ from the 720-point body.')
    cells = [paragraph(label, width - 12, size=7.8, bold=True) for label, width in zip(labels, widths)]
    return cells, max(26, max(h for _, h in cells) + 12)


def split_row(row, widths, available, size, index):
    """Split only the display of a long row; retain all original source strings.

    Every continuation repeats the source row ordinal. Paragraph split objects
    retain their exact text content; no string is truncated or silently omitted.
    """
    require(len(row) == len(widths), 'Report row has a different column count.')
    pending = [paragraph(value, width - 12, size=size)[0] for value, width in zip(row, widths)]
    fragments = []
    while any(part is not None for part in pending[1:]):
        cells, remainder = [], []
        for column, (part, width) in enumerate(zip(pending, widths)):
            if column == 0:
                label = str(index + 1) + (' / continued' if fragments else '')
                cell, height = paragraph(label, width - 12, size=size)
                cells.append((cell, height)); remainder.append(None)
                continue
            if part is None:
                cell, height = paragraph('', width - 12, size=size)
                cells.append((cell, height)); remainder.append(None)
                continue
            _, height = part.wrap(width - 12, PAGE_HEIGHT)
            if height <= available - 12:
                cells.append((part, height)); remainder.append(None)
            else:
                pieces = part.split(width - 12, available - 12)
                require(len(pieces) == 2, 'A report cell cannot be safely continued across pages.')
                head, tail = pieces
                _, head_height = head.wrap(width - 12, PAGE_HEIGHT)
                require(0 < head_height <= available - 12, 'A continued report cell crosses its page.')
                cells.append((head, head_height)); remainder.append(tail)
        height = max(28, max(h for _, h in cells) + 12)
        require(height <= available, 'A measured row fragment exceeds the page body.')
        fragments.append({'source_row_index': index, 'cells': cells, 'height': height,
                          'continuation': bool(fragments)})
        pending = remainder
    require(fragments, 'A source report row lost all display content.')
    return fragments


def measure_section(section):
    header, header_height = header_parts(section['widths'], section['headers'])
    available = BODY_TOP - BODY_BOTTOM - header_height
    require(available > 30, 'Actual report header exceeds the page body.')
    fragments = []
    for i, row in enumerate(section['rows']):
        fragments.extend(split_row(row, section['widths'], available, section.get('size', 7.6), i))
    pages, page, used = [], [], 0
    for fragment in fragments:
        if page and used + fragment['height'] > available:
            pages.append(page); page, used = [], 0
        page.append(fragment); used += fragment['height']
    if page or not pages:
        pages.append(page)
    require({r['source_row_index'] for r in fragments} == set(range(len(section['rows']))),
            'Actual-text layout omitted a source row.')
    require(all(BODY_TOP - header_height - sum(r['height'] for r in page) >= BODY_BOTTOM for page in pages),
            'Actual-text layout crosses the footer band.')
    return {'header': header, 'header_height': header_height, 'pages': pages,
            'receipt': {'title': section['title'], 'source_rows': len(section['rows']),
                        'row_fragments': len(fragments), 'pages': len(pages),
                        'header_height_points': header_height,
                        'largest_fragment_points': max((r['height'] for r in fragments), default=0),
                        'all_rows_preserved': True, 'status': 'PASS'}}


def heading(c, title, subtitle, page):
    require(pdfmetrics.stringWidth(title, 'ReportBold', 17) <= BODY_WIDTH, 'Report title crosses its band.')
    sub, height = paragraph(subtitle, BODY_WIDTH, size=8.7, color=MUTED)
    require(height <= 38, 'Report subtitle crosses the header divider.')
    c.setFillColor(HexColor(INK)); c.setFont('ReportBold', 17)
    c.drawString(LEFT, 570, title)
    sub.drawOn(c, LEFT, 552 - height)
    c.setStrokeColor(HexColor('#DDE5ED')); c.line(LEFT, 508, RIGHT, 508)
    c.setFont('ReportSans', 7.4); c.setFillColor(HexColor(MUTED))
    c.drawString(LEFT, 21, 'Puma-only source-preserving inventory | no fitted model, statistical test or forecast | visual review pending')
    c.drawRightString(RIGHT, 21, str(page))


def draw_table(c, section, measured, page_rows):
    edges = [LEFT]
    for width in section['widths']:
        edges.append(edges[-1] + width)
    y = BODY_TOP
    hh = measured['header_height']
    c.setFillColor(HexColor('#EAF0F6')); c.rect(LEFT, y - hh, BODY_WIDTH, hh, fill=1, stroke=0)
    for i, (value, height) in enumerate(measured['header']):
        value.drawOn(c, edges[i] + 6, y - 6 - height)
    y -= hh
    for fragment in page_rows:
        height = fragment['height']
        require(y - height >= BODY_BOTTOM, 'Measured row crosses the footer.')
        if fragment['source_row_index'] % 2 == 0:
            c.setFillColor(HexColor('#F5F7FA')); c.rect(LEFT, y - height, BODY_WIDTH, height, fill=1, stroke=0)
        for i, (value, ph) in enumerate(fragment['cells']):
            require(ph + 12 <= height, 'Measured cell crosses its row.')
            value.drawOn(c, edges[i] + 6, y - 6 - ph)
        c.setStrokeColor(HexColor('#DDE5ED')); c.line(LEFT, y - height, RIGHT, y - height)
        y -= height
    return y


def field_label(field):
    # Labels can be shorter than CSV keys; values remain exact recorded strings.
    labels = {'raw_value': 'Original value', 'raw_source_value': 'Source value string',
              'raw_text_value': 'Source text value', 'raw_variable': 'Source variable',
              'raw_unit': 'Source unit', 'raw_method': 'Source method',
              'raw_qualifier': 'Qualifier', 'raw_reporting_limit': 'Reporting limit',
              'confirmed_physical_plot_id': 'Confirmed physical plot',
              'candidate_sample_key_preserved_only': 'Candidate key (preserved only)',
              'quantitative_value_inventory_eligible': 'Value inventory eligible',
              'confirmed_plot_event_inventory_eligible': 'Confirmed plot/event inventory eligible',
              'prospective_single_plot_event_value_eligible': 'Prospective single plot/event value inventory flag'}
    return labels.get(field, field.replace('_', ' '))


def projected_section(path, fields, rows, title, subtitle):
    """All source rows, all discrete declared fields, all strings in row order.

    Only archival JSON and prefixed copies of original serialization are linked
    through the exact companion file rather than repeated as PDF text.
    """
    displayed = [field for field in fields if field not in
                 {'raw_measurement_json', 'raw_record_json', 'microbial_identity_json'} and
                 not field.startswith(('original_measurement__', 'original_record__',
                                       'original_laboratory_measurement__', 'original_laboratory_record__'))]
    require(displayed, 'No discrete report fields remain.')
    identity = {'inventory_row_id','inventory_record_id','source_row_number','inventory_source','source_role',
                'record_id','scope_id','track_id','collection_date','confirmed_sample_key',
                'candidate_sample_key_preserved_only','confirmed_physical_plot_id','analytic_system','domain',
                'microbial_sample_name','analysis_sample','epoch_id','actual_collection_dates'}
    groups = [[], [], [], []]
    for field in displayed:
        if field in identity:
            index = 0
        elif field.startswith('raw_') or field in ('laboratory_received','laboratory_reported',
                                                  'field_depth_protocol','depth_as_reported'):
            index = 1
        elif any(word in field for word in ('status','reason','eligible','excluded','confirmed','aggregation','repeated')):
            index = 3
        else:
            index = 2
        groups[index].append(field)
    labels = ['Recorded identity and source', 'Original measurement and sampling fields',
              'Recorded methodology and support', 'Recorded disposition, eligibility and holds']
    active = [(label, group) for label, group in zip(labels, groups) if group]
    # Empty field groups use no space; every discrete field remains visible.
    widths = [48] + [(BODY_WIDTH - 48) / len(active)] * len(active)
    values = [[str(i + 1), *['\n'.join(field_label(field) + ': ' + row[field] for field in group)
                             for _, group in active]] for i, row in enumerate(rows)]
    return dict(title=title, subtitle=subtitle + ' Companion: ' + path,
                widths=widths, headers=['Source row'] + [label for label, _ in active],
                rows=values, size=7.5, companion=path, displayed_fields=displayed,
                archival_fields_in_companion=[field for field in fields if field not in displayed])


def measure_intro(intro):
    fragments = []
    for title, text in intro:
        remaining = paragraph(text, BODY_WIDTH, size=9.0)[0]
        part_index = 0
        while remaining is not None:
            label = title + (' (continued)' if part_index else '')
            title_p, th = paragraph(label, BODY_WIDTH, size=10.2, bold=True)
            available = BODY_TOP - BODY_BOTTOM - th - 22
            require(available > 30, 'Introductory heading consumes its page body.')
            _, bh = remaining.wrap(BODY_WIDTH, PAGE_HEIGHT)
            if bh > available:
                pieces = remaining.split(BODY_WIDTH, available)
                require(len(pieces) == 2, 'Introductory text cannot be safely continued.')
                body_p, remaining = pieces
                _, bh = body_p.wrap(BODY_WIDTH, PAGE_HEIGHT)
            else:
                body_p, remaining = remaining, None
            height = th + bh + 22
            require(height <= BODY_TOP - BODY_BOTTOM, 'Introductory text fragment crosses its page.')
            fragments.append({'title': title_p, 'title_height': th, 'body': body_p,
                              'body_height': bh, 'height': height})
            part_index += 1
    pages, page, used = [], [], 0
    for fragment in fragments:
        if page and used + fragment['height'] > BODY_TOP - BODY_BOTTOM:
            pages.append(page); page, used = [], 0
        page.append(fragment); used += fragment['height']
    if page or not pages:
        pages.append(page)
    return pages


def render_pdf(path, intro_pages, sections, measured):
    c = canvas.Canvas(str(path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT), pageCompression=1)
    c.setTitle('NRCS measured outcomes: source-preserving inventory and support')
    c.setAuthor('NRCS Puma guarded inventory report')
    page = 0
    for fragments in intro_pages:
        page += 1
        title = 'NRCS measured-outcome inventory' if page == 1 else 'Inventory provenance and limits'
        heading(c, title, 'Source-preserving records, methodology, confirmed joins, support and held cases', page)
        y = BODY_TOP
        for fragment in fragments:
            require(y - fragment['height'] >= BODY_BOTTOM, 'Introduction crosses its footer band.')
            fragment['title'].drawOn(c, LEFT, y - fragment['title_height'])
            y -= fragment['title_height'] + 6
            fragment['body'].drawOn(c, LEFT, y - fragment['body_height'])
            y -= fragment['body_height'] + 16
        c.showPage()
    for section, values in zip(sections, measured):
        for j, fragments in enumerate(values['pages']):
            page += 1
            title = section['title'] + (' (continued)' if j else '')
            heading(c, title, section['subtitle'], page)
            draw_table(c, section, values, fragments)
            c.showPage()
    c.save()
    return page


def verify_package_manifest(run):
    path = safe_file(run, 'PACKAGE_SHA256SUMS.txt')
    before, seen = [], set()
    for line in path.read_text(encoding='utf-8').splitlines():
        require(len(line) > 66 and line[64:66] in ('  ', ' *'), 'Malformed package checksum line.')
        expected, relative = line[:64], line[66:]
        require(len(expected) == 64 and all(c in '0123456789abcdef' for c in expected) and relative not in seen,
                'Invalid or duplicate package lock.')
        seen.add(relative)
        require(sha(safe_file(run, relative)) == expected, 'Packaged source changed: ' + relative)
        before.append({'relative_path': relative, 'sha256': expected})
    require({'CONFIG.json', 'PROTOCOL.txt', 'render_inventory_report.py', 'common.py', 'hpc_guard.py',
             'environment_check.py', 'stage_source.py', 'inventory_outcomes.py', 'inventory_self_checks.py'} <= seen,
            'Required packaged source is not locked.')
    return {'sha256': sha(path), 'rows': before}


def verify_source_receipt(run, config):
    sources = verify_staged_sources(run, config)
    require(sources and all(row['status'] == 'UNCHANGED' for row in sources), 'A source/snapshot/review lock changed.')
    stage_path = safe_file(run, 'audit/SOURCE_STAGE_STATUS.json')
    receipt = load_json(stage_path)
    same_execution(receipt.get('execution'), 'source staging')
    require(receipt.get('status') == 'IMMUTABLE_MEASURED_SOURCES_VERIFIED_AND_STAGED', 'Staging was not completed.')
    require(receipt.get('source_integrity_passed') is True and receipt.get('source_files_changed') is False,
            'Staging preservation flags failed.')
    manifest_relative = 'audit/source_manifest.csv'
    manifest = safe_file(run, manifest_relative)
    require(receipt.get('source_manifest_path') == manifest_relative and
            receipt.get('source_manifest_sha256') == sha(manifest), 'Stage receipt does not pin its source manifest.')
    _, rows = read_csv(manifest, {'source','relative_path','sha256','size_bytes','source_job_id','original_path','snapshot_path'})
    expected = {(alias, relative): checksum for alias, files in config['sources'].items() for relative, checksum in files.items()}
    require(len(rows) == len(expected) and {(r['source'], r['relative_path']) for r in rows} == set(expected),
            'Staging manifest source set differs from CONFIG.')
    for row in rows:
        alias, relative = row['source'], row['relative_path']
        original = safe_file(Path(config['source_runs'][alias]['path']), relative)
        snapshot = safe_file(run / 'source_snapshot' / alias, relative)
        require(Path(row['original_path']).resolve(strict=True) == original and
                Path(row['snapshot_path']).resolve(strict=True) == snapshot and
                row['source_job_id'] == str(config['source_runs'][alias]['analysis_job_id']), 'Source lineage changed.')
        require(row['sha256'] == expected[(alias, relative)] == sha(original) == sha(snapshot) and
                original.stat().st_size == snapshot.stat().st_size == nonnegative_integer(row['size_bytes'], 'source size'),
                'Original/snapshot bytes or size changed.')
    require(receipt.get('source_files_verified_unchanged_after_staging') is True and
            receipt.get('latest_validation_job_id') == '24128032' and
            receipt.get('accepted_review_path') == config['accepted_review_path'] and
            receipt.get('accepted_review_sha256') == config['accepted_review_sha256'], 'Reviewed staging scope changed.')
    accepted = load_json(safe_file(run, config['accepted_review_path']))
    require(sha(safe_file(run, config['accepted_review_path'])) == config['accepted_review_sha256'] and
            accepted.get('status') == 'ACCEPTED_LIMITED_RETROSPECTIVE_DIAGNOSTICS_AND_READINESS_AUDIT' and
            str(accepted.get('main_analysis_job_id')) == '24128032', 'Prior accepted diagnostic review is not pinned.')
    review_relative = 'audit/review_lock_manifest.csv'
    review_path = safe_file(run, review_relative)
    require(receipt.get('review_lock_manifest_path') == review_relative and
            receipt.get('review_lock_manifest_sha256') == sha(review_path), 'Review lock manifest is not pinned.')
    _, reviews = read_csv(review_path, {'relative_path','sha256','size_bytes'})
    require(len(reviews) == len(config['review_locks']) and
            {row['relative_path'] for row in reviews} == set(config['review_locks']), 'Review lock set changed.')
    for row in reviews:
        path = safe_file(run, row['relative_path'])
        require(row['sha256'] == config['review_locks'][row['relative_path']] == sha(path) and
                path.stat().st_size == nonnegative_integer(row['size_bytes'], 'review size'), 'Reviewed decision file changed.')
    validation_relative = 'audit/validation_output_manifest.csv'
    validation_path = safe_file(run, validation_relative)
    require(receipt.get('validation_output_manifest_path') == validation_relative and
            receipt.get('validation_output_manifest_sha256') == sha(validation_path), 'Validation proof manifest not pinned.')
    _, validation = read_csv(validation_path, {'relative_path','sha256','size_bytes'})
    require(len(validation) == len(config['validation_output_locks']) and
            {row['relative_path'] for row in validation} == set(config['validation_output_locks']), 'Validation proof set changed.')
    for row in validation:
        path = safe_file(Path(config['source_runs']['validation']['path']), row['relative_path'])
        require(row['sha256'] == config['validation_output_locks'][row['relative_path']] == sha(path) and
                path.stat().st_size == nonnegative_integer(row['size_bytes'], 'validation output size'), 'Accepted validation output changed.')
    return {'source_checks': sources, 'source_manifest_sha256': sha(manifest),
            'review_lock_manifest_sha256': sha(review_path), 'validation_output_manifest_sha256': sha(validation_path),
            'source_stage_status_sha256': sha(stage_path), 'source_stage_status': receipt, 'manifest_rows': rows,
            'review_rows': reviews, 'validation_rows': validation}


def validate_current_start(run):
    path = safe_file(run, 'audit/run_start.json')
    value = load_json(path)
    same_execution(value.get('execution'), 'main runtime start')
    require(value.get('stage') == 'start' and value.get('runtime_interpreter_fallback') is False,
            'Current main has no pinned-runtime start receipt.')
    for name in ('models_fitted', 'statistical_tests_run', 'forecasts_generated',
                 'ten_year_forecast_generated', 'predictive_validation_established'):
        require(value.get(name) is False, 'Runtime start scope changed: ' + name)
    return {'sha256': sha(path), 'record': value}


def intro_blocks(run, status, manifest_rows, sources, tables):
    companion_refs = '\n'.join(row['relative_path'] + ' | SHA256 ' + row['sha256'] for row in manifest_rows)
    rows_note = '; '.join(name + ': ' + str(len(value[1])) + ' recorded rows' for name, value in tables.items())
    return [
        ('Current Puma execution', 'Measured-outcome inventory job ' + execution['job_id'] + ' on ' + execution['node'] +
         ' / puma. The engine and renderer use this actual current allocation. Report metadata row counts below are ' +
         'counts of already-pinned inventory rows, not independent biological replicate counts.'),
        ('Scope and source-preserving decisions', 'This stage copies recorded measurements, method/unit/depth and source labels, '+
         'confirmed plot/date identities, confirmed 16S joins, support and hold reasons. Raw values stay as recorded strings. '+
         'Holds and absent source epochs remain visible. No conversions, new joins, eligibility decisions, response aggregation, '+
         'statistical tests, fitted models or forecasts are performed by the renderer. A prospective value inventory flag '+
         'is a source/support disposition, not established sampling equivalence, model eligibility or predictive validation.'),
        ('Recorded lineage remains separate', 'Historical baseline engine 24124251 remains a whole-job report-stage failure; '+
         'separate repair 24126557 completed. Accepted validation/readiness main 24128032 provides limited retrospective '+
         'known-plot diagnostics, not prospective/external or decadal validation. This current report adds an inventory and '+
         'support audit only; none of those historical statuses is relabelled.'),
        ('How to read the complete tables', 'Source row ordinals refer to CSV row order. Every candidate, record, method/epoch, '+
         'confirmed-domain join, support and held row is retained. Long rows continue with the same source ordinal. Raw JSON '+
         'serialization stays in the exact pinned companion CSVs and is not repeated in the PDF. Strings in the report are '+
         'escaped only for typesetting; no numerical reformatting or first-N truncation is applied. ' + rows_note),
        ('Original and snapshot preservation', str(len(sources['manifest_rows'])) + ' locked source files plus the reviewed '+
         'decision locks were byte-verified before rendering and must pass again after rendering. Original source paths and '+
         'run-local snapshots are both checked. Source values, exclusions, existing technical merges and plot identities stay unchanged.'),
        ('Companion engine output references', companion_refs),
    ]


def preflight(intro, sections):
    intro_pages = measure_intro(intro)
    measured = [measure_section(section) for section in sections]
    for title, subtitle in [('NRCS measured-outcome inventory', 'Source-preserving records, methodology, confirmed joins, support and held cases'),
                            ('Inventory provenance and limits', 'Source-preserving records, methodology, confirmed joins, support and held cases')] + [
                                (s['title'] + ending, s['subtitle']) for s in sections for ending in ('', ' (continued)')]:
        require(pdfmetrics.stringWidth(title, 'ReportBold', 17) <= BODY_WIDTH, 'Actual report title crosses its band.')
        _, height = paragraph(subtitle, BODY_WIDTH, size=8.7, color=MUTED)
        require(height <= 38, 'Actual report subtitle crosses the divider.')
    # This fixed-text-only check runs on genuine Puma after all guards and pins.
    stress = dict(title='Fixed-text pagination check', subtitle='No measurement or statistical fixture values',
                  widths=[64, 94, 162, 400],
                  headers=['Source row ordinal', 'Recorded methodology and unit definitions',
                           'Confirmed physical-plot/date support and recorded eligibility', 'Complete source and hold reasons'],
                  rows=[['row', 'method label\nunit label\ndepth label', 'identity label\ndate label',
                         '\n'.join(['Long recorded source and hold text; preserve every continuation.'] * 180)]], size=7.6)
    tested = measure_section(stress)
    require(tested['receipt']['all_rows_preserved'] is True and tested['receipt']['row_fragments'] > 1,
            'Long-row continuation preflight failed.')
    return intro_pages, measured, dict(status='PASS', execution=execution, all_actual_text_measured=True,
        introductory_pages=len(intro_pages), expected_report_pages=len(intro_pages) + sum(len(m['pages']) for m in measured),
        fixed_text_multiline_header_check_passed=True, fixed_text_long_row_continuation_passed=True,
        rows_truncated=False, scientific_figures_generated=False, sections=[m['receipt'] for m in measured])


ENGINE_FILES = {'results/' + name for name in (
    'all_raw_measurements.csv','measurement_inventory.csv','record_inventory.csv',
    'confirmed_microbiome_joins.csv','support_inventory.csv','track_availability.csv',
    'source_index_inventory.csv','transcription_reconciliation.csv','INVENTORY_SCHEMA.json')}
ENGINE_FILES |= {'audit/inventory_self_checks.json','audit/INVENTORY_SOURCE_PRESERVATION.json'}
TABLE_SCHEMA = {
    'measurement_inventory.csv': {'inventory_row_id','record_id','scope_id','track_id','inventory_source','source_role',
        'collection_date','raw_variable','raw_value','raw_source_value','raw_text_value','raw_unit','raw_method',
        'raw_qualifier','raw_reporting_limit','raw_source_id','raw_source_location','raw_source_field',
        'raw_preferred_pdf_source_id','raw_preferred_pdf_page','raw_lab_id','raw_sample_label','raw_notes',
        'field_depth_protocol','depth_as_reported','mapping_status','mapping_reason','reviewed_response_definition',
        'reviewed_method','reviewed_unit','method_source_id','method_source_sha256','record_identity_status','record_identity_reason','record_identity_confirmed',
        'quantitative_value_inventory_eligible','confirmed_plot_event_inventory_eligible','measurement_disposition',
        'measurement_reason','aggregation_rule_status','model_eligible','forecast_eligible','raw_measurement_json','raw_record_json',
        'prospective_value_status','prospective_single_plot_event_value_eligible','plot_event_group_measurement_count',
        'source_record_response_row_count','exact_source_row_duplicate_count','duplicate_source_record_response_rows','response_conflict_status'},
    'record_inventory.csv': {'inventory_record_id','record_id','inventory_source','scope_id','collection_date',
        'confirmed_sample_key','candidate_sample_key_preserved_only','confirmed_physical_plot_id','analytic_system',
        'record_identity_status','record_identity_reason','record_excluded','record_identity_confirmed','raw_record_json','methodology_source'},
    'confirmed_microbiome_joins.csv': {'record_id','scope_id','domain','collection_date','confirmed_sample_key',
        'confirmed_physical_plot_id','candidate_sample_key_preserved_only','join_status','join_reason','analysis_sample',
        'microbial_primary_included','microbial_depth_sensitivity_included','microbial_merge_status','confirmed_join',
        'methodology_source','model_eligible','forecast_eligible','microbial_identity_json'},
    'support_inventory.csv': {'track_id','scope_id','collection_date','source_measurement_rows','distinct_source_lab_record_ids',
        'compatible_reported_definition_rows','finite_uncensored_compatible_unexcluded_rows','held_source_rows',
        'distinct_confirmed_physical_plots','distinct_confirmed_plot_events','finite_soil_only_or_unconfirmed_identity_rows',
        'repeated_plot_event_lab_rows_present','bacteria_distinct_confirmed_joined_plot_events',
        'bacteria_distinct_preserved_specimen_ids','archaea_distinct_confirmed_joined_plot_events',
        'archaea_distinct_preserved_specimen_ids','status','reason','methodology_source','model_eligible','forecast_eligible'},
    'track_availability.csv': {'track_id','scope_id','epoch_id','preferred_pdf_source_id','compatible_measurement_rows',
        'candidate_measurement_rows','actual_collection_dates','status','reason','methodology_source',
        'no_observation_created','model_eligible','forecast_eligible'},
    'source_index_inventory.csv': {'source_id','filename','sha256'},
    'transcription_reconciliation.csv': {'source_record_key','legacy_record_id','scope_id','report_heading',
        'corrected_inventory_row_id','status','reason'},
}
SCHEMA_KEYS = {'measurement_inventory.csv':'candidate_measurement_schema','record_inventory.csv':'record_schema',
               'confirmed_microbiome_joins.csv':'join_schema','support_inventory.csv':'support_schema',
               'track_availability.csv':'availability_schema'}


def verify_engine(run, config, sources):
    path = safe_file(run, 'results/INVENTORY_STATUS.json')
    status = load_json(path)
    same_execution(status.get('execution'), 'inventory engine')
    require(status.get('status') == ENGINE_STATUS and status.get('inventory_computed') is True and
            status.get('scientific_inventory_completed') is True, 'Inventory engine did not complete on the current allocation.')
    for field in ('source_integrity_passed','source_files_verified_unchanged_after_audit','package_integrity_passed'):
        require(status.get(field) is True, 'Inventory completion check failed: ' + field)
    for field in ('source_files_changed','measurement_values_transformed','measurement_values_aggregated',
                  'new_plot_or_specimen_aliases_created','microbial_response_values_joined','technical_replicates_split',
                  'models_fitted','statistical_tests_performed','statistical_tests_run','forecasts_generated','predictive_validation_established','ten_year_forecast_generated',
                  'numerical_ten_year_forecast','model_eligibility_established','calibration_established','sampling_equivalence_established'):
        require(status.get(field) is False, 'Inventory scope/preservation flag changed: ' + field)
    require(status.get('source_corrected_job') == '24067160' and status.get('source_functional_job') == '24067682',
            'Inventory source origin changed.')
    manifest_relative = 'results/ENGINE_OUTPUT_MANIFEST.csv'
    manifest_path = safe_file(run, manifest_relative)
    require(status.get('output_manifest') == manifest_relative and status.get('output_manifest_sha256') == sha(manifest_path),
            'Inventory status does not pin its output manifest.')
    _, manifest = read_csv(manifest_path, {'relative_path','sha256','size_bytes'})
    require(len(manifest) == len(ENGINE_FILES) and {row['relative_path'] for row in manifest} == ENGINE_FILES,
            'Exact inventory engine output set changed or contains duplicates.')
    require(set(config['engine_output_files']) == ENGINE_FILES, 'Configured engine output set differs from renderer contract.')
    for row in manifest:
        checksum = row['sha256']
        require(len(checksum) == 64 and all(c in '0123456789abcdef' for c in checksum), 'Invalid engine output checksum.')
        item = safe_file(run, row['relative_path'])
        require(sha(item) == checksum and item.stat().st_size == nonnegative_integer(row['size_bytes'], 'engine output size'),
                'Engine output changed: ' + row['relative_path'])
    self_path = safe_file(run, 'audit/inventory_self_checks.json')
    self_checks = load_json(self_path)
    same_execution(self_checks.get('execution'), 'inventory self-checks')
    require(self_checks.get('status') == 'PASS' and self_checks.get('passed') is True and
            status.get('self_checks_sha256') == sha(self_path), 'Inventory self-check receipt not passed/pinned.')
    preservation_path = safe_file(run, 'audit/INVENTORY_SOURCE_PRESERVATION.json')
    preservation = load_json(preservation_path)
    same_execution(preservation.get('execution'), 'inventory source preservation')
    require(status.get('source_preservation') == 'audit/INVENTORY_SOURCE_PRESERVATION.json' and
            status.get('source_preservation_sha256') == sha(preservation_path) and
            preservation.get('status') == 'ALL_STAGED_ORIGINALS_SNAPSHOTS_AND_REVIEW_LOCKS_UNCHANGED' and
            preservation.get('source_files_changed') is False and preservation.get('source_integrity_passed') is True and
            preservation.get('source_files_verified_unchanged_after_audit') is True and
            preservation.get('before') == preservation.get('after') == sources['source_checks'] and
            preservation.get('source_manifest') == 'audit/source_manifest.csv' and
            preservation.get('source_manifest_sha256') == sources['source_manifest_sha256'], 'Inventory preservation linkage failed.')
    controls = preservation.get('control_sha256_before_after')
    required_controls = {'RESPONSE_TRACKS.json','PROTOCOL.txt','inventory_core.py','inventory_outcomes.py','inventory_self_checks.py'}
    require(isinstance(controls, dict) and set(controls) == required_controls and
            all(sha(safe_file(run, name)) == expected for name, expected in controls.items()), 'Frozen engine/protocol controls changed.')
    require(status.get('response_tracks_sha256') == controls['RESPONSE_TRACKS.json'] and
            status.get('protocol_sha256') == controls['PROTOCOL.txt'], 'Inventory protocol/track pins changed.')
    schema = load_json(safe_file(run, 'results/INVENTORY_SCHEMA.json'))
    same_execution(schema.get('execution'), 'inventory schema')
    require(schema.get('status') == 'MEASURED_INVENTORY_OUTPUT_SCHEMA', 'Missing inventory schema receipt.')
    for field in ('all_raw_source_rows_preserved','out_of_scope_values_not_scientifically_evaluated',
                  'raw_json_retains_types_nulls_and_exact_strings','original_fields_never_overwritten_by_reviewed_mappings',
                  'laboratory_transcriptions_are_audit_copies_not_independent_support'):
        require(schema.get(field) is True, 'Source-preserving inventory schema check failed: ' + field)
    tables = {}
    for name, required in TABLE_SCHEMA.items():
        fields, rows = read_csv(safe_file(run, 'results/' + name), required)
        if name in SCHEMA_KEYS:
            declared = schema.get(SCHEMA_KEYS[name])
            require(isinstance(declared, list) and len(declared) == len(set(declared)) and
                    required <= set(declared) and fields[:len(declared)] == declared,
                    'CSV discrete schema differs from pinned schema receipt: ' + name)
        for row in rows:
            for flag in ('model_eligible','forecast_eligible'):
                if flag in row:
                    require(row[flag] in ('False','false'), 'A recorded inventory row claims model/forecast eligibility.')
        tables[name] = (fields, rows)
    raw_fields, raw_rows = read_csv(safe_file(run, 'results/all_raw_measurements.csv'),
                                   {'inventory_row_id','inventory_source','source_role','raw_measurement_json'})
    require(len({r['inventory_row_id'] for r in raw_rows}) == len(raw_rows), 'Raw inventory source identifiers duplicated.')
    raw_by_id = {r['inventory_row_id']:r for r in raw_rows}
    candidate_fields, candidate_rows = tables['measurement_inventory.csv']
    require(len({r['inventory_row_id'] for r in candidate_rows}) == len(candidate_rows), 'Candidate source identifiers duplicated.')
    for row in candidate_rows:
        require(row['inventory_row_id'] in raw_by_id and
                all(raw_by_id[row['inventory_row_id']].get(field) == row[field] for field in candidate_fields),
                'Candidate measurement projection differs from its complete raw inventory row.')
        if row['method_source_id']:
            require(row['method_source_id'] in config['source_report_paths'] and
                    row['method_source_sha256'] == sha(safe_file(run,config['source_report_paths'][row['method_source_id']])),
                    'Recorded method evidence hash differs from its pinned report.')
    expected_counts = {'all_raw_measurements':len(raw_rows),
        'candidate_measurement_inventory':len(tables['measurement_inventory.csv'][1]),
        'record_inventory':len(tables['record_inventory.csv'][1]),
        'confirmed_microbiome_join_inventory':len(tables['confirmed_microbiome_joins.csv'][1])}
    require(status.get('reported_output_row_counts') == expected_counts, 'Recorded engine row counts differ from complete output files.')
    return {'status':status,'status_sha256':sha(path),'manifest':manifest,'manifest_sha256':sha(manifest_path),
            'schema':schema,'preservation':preservation,'self_checks':self_checks,'tables':tables,
            'raw_fields':raw_fields,'raw_row_count':len(raw_rows),'control_hashes':controls}


def protocol_section(run):
    tracks = load_json(safe_file(run, 'RESPONSE_TRACKS.json'))
    require(isinstance(tracks.get('tracks'), list) and isinstance(tracks.get('scope_records'), dict), 'Frozen methodology declarations missing.')
    rows = []
    for track in tracks['tracks']:
        rows.append({'declaration_kind':'RESPONSE_TRACK','declaration_key':str(track['track_id']),
                     'recorded_definition':json.dumps(track, ensure_ascii=False, separators=(',', ':'))})
    for key, value in tracks['scope_records'].items():
        rows.append({'declaration_kind':'SCOPE_AND_SOURCE_EPOCHS','declaration_key':key,
                     'recorded_definition':json.dumps(value, ensure_ascii=False, separators=(',', ':'))})
    return projected_section('RESPONSE_TRACKS.json', ['declaration_kind','declaration_key','recorded_definition'], rows,
        'Frozen methodology and source scopes', 'Exact prespecified declarations, not new harmonization decisions or statistical eligibility.')


def make_sections(run, tables):
    sections = [protocol_section(run)]
    titles = {
        'track_availability.csv': ('Response/source-epoch availability', 'Absent response epochs remain explicit audit rows; no observation was created.'),
        'support_inventory.csv': ('Source, plot and event support', 'Laboratory rows, confirmed plots/events and domain specimens remain different units; no response values were aggregated.'),
        'confirmed_microbiome_joins.csv': ('Confirmed-domain joins and holds', 'Only the engine recorded explicit corrected/functional specimen identities; no response values or aliases were joined.'),
        'measurement_inventory.csv': ('Candidate measurements and holds', 'Complete declared candidates/comparators, unchanged raw values/units/methods, prospective-value holds and reviewed reasons.'),
        'record_inventory.csv': ('Complete source record inventory', 'Includes out-of-scope and audit-only transcription records; candidate keys and source dates remain recorded fields.'),
        'transcription_reconciliation.csv': ('Source transcription reconciliation', 'Complete scoped laboratory audit-copy evidence; transcription rows never add independent support.'),
        'source_index_inventory.csv': ('Complete corrected source index', 'Original source identifiers and metadata are preserved; complete raw JSON remains in the pinned companion files.'),
    }
    for name in titles:
        title, subtitle = titles[name]
        fields, rows = tables[name]
        sections.append(projected_section('results/' + name, fields, rows, title, subtitle))
    return sections


def main():
    require(len(sys.argv) == 2, 'Usage: render_inventory_report.py RUN_PATH')
    run = Path(sys.argv[1]).resolve(strict=True)
    require(run.is_dir() and run == Path(__file__).resolve().parent, 'Use the immutable current run copy of this renderer.')
    config = load_json(safe_file(run, 'CONFIG.json'))
    require(config.get('package') == PACKAGE, 'Wrong inventory report package.')
    require(config.get('user_intent', {}).get('measured_inventory_work_authorized') is True and
            config['user_intent'].get('current_files_only') is True, 'Current authorized inventory/source scope changed.')
    for field in ('models_fitted','statistical_tests_performed','forecasts_generated','measurement_values_aggregated',
                  'predictive_validation_established','ten_year_forecast_generated','numerical_ten_year_forecast','numerical_forecast_execution_in_this_stage'):
        require(config.get(field) is False, 'Configured inventory scope changed: ' + field)
    sentinel = run / 'ANALYSES_COMPLETE.txt'
    status_path = run / 'results/REPORT_STATUS.json'
    report_path = run / REPORT_RELATIVE
    layout_path = run / LAYOUT_RELATIVE
    report_manifest = run / 'results/report_output_manifest.csv'
    require(not any(path.exists() for path in (sentinel,status_path,report_path,layout_path,report_manifest)),
            'Never overwrite a report; submit a new isolated Puma run.')
    package_before = verify_package_manifest(run)
    runtime_start = validate_current_start(run)
    environment = validate_environment(run, config)
    sources = verify_source_receipt(run, config)
    engine = verify_engine(run, config, sources)
    fonts = register_fonts()
    sections = make_sections(run, engine['tables'])
    intro = intro_blocks(run,engine['status'],engine['manifest'],sources,engine['tables'])
    intro_pages, measured, layout = preflight(intro,sections)
    rendered_pages = render_pdf(report_path,intro_pages,sections,measured)
    page_count = len(PdfReader(report_path).pages)
    require(page_count == rendered_pages == layout['expected_report_pages'], 'Actual report page count differs from measured pagination.')
    layout.update(report_layout_checks_passed=True, report_pages=page_count, fonts_embedded=True,
                  embedded_font_files=fonts, report_displays_every_projected_row=True,
                  table_projections=[{'companion':s['companion'],'displayed_fields':s['displayed_fields'],
                                     'archival_fields_in_companion':s['archival_fields_in_companion'],
                                     'source_rows':len(s['rows'])} for s in sections])
    save(layout_path,layout)
    output_rows = [{'relative_path':REPORT_RELATIVE,'sha256':sha(report_path),'size_bytes':report_path.stat().st_size,
                    'file_type':'inventory_review_pdf','description':'Complete candidate, record, methodology, source, support, join and hold projections; no fits/tests/forecasts'},
                   {'relative_path':LAYOUT_RELATIVE,'sha256':sha(layout_path),'size_bytes':layout_path.stat().st_size,
                    'file_type':'actual_layout_receipt','description':'Current-Puma exact-text measured headers, rows, continuations, page count and preserved row order'}]
    table(report_manifest,output_rows,['relative_path','sha256','size_bytes','file_type','description'])
    # Every original/snapshot/review/package/engine byte is rechecked after the PDF.
    require(verify_package_manifest(run) == package_before, 'Packaged source changed during reporting.')
    require(validate_current_start(run) == runtime_start and validate_environment(run,config) == environment,
            'Current runtime evidence changed during reporting.')
    after_sources = verify_source_receipt(run,config)
    require(after_sources == sources and verify_engine(run,config,after_sources) == engine,
            'Original/snapshot/review/engine evidence changed during reporting.')
    require(all(sha(Path(row['path'])) == row['sha256'] for row in fonts), 'Embedded font source changed during reporting.')
    _, final_outputs = read_csv(report_manifest,{'relative_path','sha256','size_bytes'})
    require(len(final_outputs) == 2 and {r['relative_path'] for r in final_outputs} == {REPORT_RELATIVE,LAYOUT_RELATIVE},
            'Report output set differs from its exact two-file contract.')
    for row in final_outputs:
        path = safe_file(run,row['relative_path'])
        require(sha(path) == row['sha256'] and path.stat().st_size == nonnegative_integer(row['size_bytes'],'report output size'),
                'Report output changed before completion.')
    final = dict(status=REPORT_STATUS,execution=execution,completed_utc=datetime.now(timezone.utc).isoformat(),
        report_relative_path=REPORT_RELATIVE,report_path=REPORT_RELATIVE,report_sha256=sha(report_path),report_pages=page_count,
        figure_count=0,figures_generated=False,
        output_manifest_path='results/report_output_manifest.csv',output_manifest='results/report_output_manifest.csv',
        output_manifest_sha256=sha(report_manifest),report_output_manifest_path='results/report_output_manifest.csv',
        report_output_manifest_sha256=sha(report_manifest),report_output_files_verified=2,
        engine_output_manifest_path='results/ENGINE_OUTPUT_MANIFEST.csv',engine_output_manifest_sha256=engine['manifest_sha256'],
        engine_output_files_verified=len(engine['manifest']),inventory_status_sha256=engine['status_sha256'],
        report_layout_checks_passed=True,report_layout_receipt_path=LAYOUT_RELATIVE,report_layout_receipt_sha256=sha(layout_path),
        source_integrity_passed=True,source_preservation_passed=True,source_files_verified_unchanged_after_report=True,
        source_files_changed=False,source_manifest_sha256=sources['source_manifest_sha256'],
        source_stage_status_sha256=sources['source_stage_status_sha256'],review_lock_manifest_sha256=sources['review_lock_manifest_sha256'],
        validation_output_manifest_sha256=sources['validation_output_manifest_sha256'],
        source_preservation_path='audit/INVENTORY_SOURCE_PRESERVATION.json',
        source_preservation_sha256=sha(safe_file(run,'audit/INVENTORY_SOURCE_PRESERVATION.json')),
        measurement_values_transformed=False,measurement_values_aggregated=False,eligibility_recomputed_by_report=False,
        source_rows_filtered_by_report=False,models_fitted=False,statistical_tests_performed=False,statistical_tests_run=False,
        forecasts_generated=False,ten_year_forecast_generated=False,numerical_ten_year_forecast=False,predictive_validation_established=False,
        historical_statuses_modified=False,user_authorized_work=True,current_files_only=True,prior_validation_source_job_id='24128032',original_engine_job_id='24124251',
        successful_report_repair_job_id='24126557',scientific_review_pending=True,visual_review_pending=True,
        inference='SOURCE_PRESERVING_MEASUREMENT_METHOD_IDENTITY_SUPPORT_INVENTORY_ONLY')
    save(status_path,final)
    temporary = sentinel.with_name(sentinel.name + '.tmp')
    temporary.write_text('Measured-outcome inventory and report completed on Puma job ' + execution['job_id'] +
                         '; scientific and visual review pending.\nREPORT_STATUS_SHA256=' + sha(status_path) + '\n',encoding='utf-8')
    temporary.replace(sentinel)  # Last filesystem write by the renderer.
    print(json.dumps(final,indent=2,allow_nan=False))


if __name__ == '__main__':
    main()
