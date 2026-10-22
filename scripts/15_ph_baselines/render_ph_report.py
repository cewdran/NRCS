#!/usr/bin/env python3
"""Puma-only presentation of frozen Whipstone Annual reported-pH diagnostics.

The renderer copies saved observations, predictions, errors and metrics. It does
not fit, score, average responses, infer treatment effects or make forecasts.
"""
from hpc_guard import require_hpc
execution = require_hpc()

from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
import csv
import importlib.metadata
import importlib.util
import json
import math
import sys
import textwrap

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from common import require, save, sha, table, safe_file, verify_staged_sources

PACKAGE = 'NRCS_Whipstone_pH_baselines_HPC_v1'
REPORT_SUCCESS = 'WHIPSTONE_PH_BASELINES_REPORT_COMPLETED_REVIEW_PENDING'
METHODS = ('PERSISTENCE', 'EQUAL_PLOT_TRAINING_MEAN')
METHOD_LABEL = {'PERSISTENCE': 'Persistence', 'EQUAL_PLOT_TRAINING_MEAN': 'Equal-plot training mean'}
REPORT_RELATIVE = 'results/NRCS_Whipstone_pH_baselines_review.pdf'
LAYOUT_RELATIVE = 'audit/report_layout_receipt.json'
FIGURE_NAMES = (
 'figures/whipstone_annual_ph_observed_trajectories.pdf',
 'figures/whipstone_annual_ph_2024_observed_vs_baselines.pdf',
 'figures/whipstone_annual_ph_2025_observed_vs_baselines.pdf',
 'figures/whipstone_annual_ph_2024_absolute_errors.pdf',
 'figures/whipstone_annual_ph_2025_absolute_errors.pdf')
INK, MUTED = '#172738', '#536578'
COLORS = ('#176B87', '#A23B72', '#B87012', '#47724D', '#705AA1', '#935547')
PAGE_WIDTH, PAGE_HEIGHT = 792, 612
LEFT, RIGHT, BODY_TOP, BODY_BOTTOM = 36, 756, 500, 40
BODY_WIDTH = RIGHT - LEFT


def load_json(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    require(isinstance(value, dict), 'Expected an object receipt: ' + str(path))
    return value


def regular_output(run, relative):
    """Check the unresolved manifest entry and each run-relative parent first."""
    require(isinstance(relative,str) and relative and not Path(relative).is_absolute() and
            all(p not in ('..','.') for p in Path(relative).parts),'Unsafe output entry.')
    raw=run/relative
    cursor=raw
    while cursor!=run:
        require(not cursor.is_symlink(),'Linked output or output parent: '+relative)
        require(run in cursor.parents,'Output entry escapes current run.')
        cursor=cursor.parent
    return safe_file(run,relative)


def read_csv(path, required=()):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        require(fields and len(fields) == len(set(fields)), 'Missing/duplicate CSV header: ' + str(path))
        require(set(required) <= set(fields), 'Required CSV header missing: ' + str(path))
        rows = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in rows),
            'Malformed CSV row: ' + str(path))
    return fields, rows


def integer(value, context):
    require(isinstance(value, str) and value.isdigit(), 'Noncanonical integer: ' + context)
    return int(value)


def finite(value, context):
    try:
        number = float(value)
    except (ValueError, TypeError) as error:
        raise RuntimeError('Invalid plot coordinate: ' + context) from error
    require(math.isfinite(number), 'Nonfinite plot coordinate: ' + context)
    return number


def compact(value, _position=None):
    if value == 0:
        return '0'
    if abs(value) < 0.001 or abs(value) >= 10000:
        return format(value, '.2g')
    return format(value, '.4g')


def same_execution(record, context):
    require(isinstance(record, dict) and all(str(record.get(k)) == str(execution[k])
            for k in ('job_id', 'cluster', 'node')), 'Different Puma allocation: ' + context)


def validate_environment(run, cfg):
    value = load_json(safe_file(run, 'audit/software/environment.json'))
    same_execution(value.get('execution'), 'report environment')
    require(value.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED' and
            value.get('software_installed') is False, 'Pinned runtime was not verified unchanged.')
    require(value.get('versions') == cfg['runtime_expected_versions'] and
            value.get('requirements_sha256') == cfg['runtime_requirements_sha256'] and
            value.get('runtime_package') == cfg['runtime_package'], 'Runtime pin mismatch.')
    require(value.get('python') == sys.version == cfg['runtime_expected_python'] and
            Path(value['executable']).resolve(strict=True) == Path(sys.executable).resolve(strict=True),
            'Report interpreter differs from verified runtime.')
    require(value.get('runtime_ready_sha256') == cfg['runtime_ready_sha256'] ==
            sha(safe_file(run, 'audit/software/original_runtime_READY.json')), 'Runtime READY pin changed.')
    for name, expected in cfg['runtime_expected_versions'].items():
        require(importlib.metadata.version(name) == expected, 'Installed runtime version changed: ' + name)
    start = load_json(safe_file(run, 'audit/run_start.json'))
    same_execution(start.get('execution'), 'current run start')
    require(start.get('stage') == 'start' and start.get('runtime_interpreter_fallback') is False and
            Path(start['interpreter']).resolve(strict=True) == Path(sys.executable).resolve(strict=True),
            'Current run interpreter/provenance differs.')
    return {'environment': value, 'start': start}


def verify_package(run):
    manifest = safe_file(run, 'PACKAGE_SHA256SUMS.txt')
    rows, seen = [], set()
    for line in manifest.read_text(encoding='utf-8').splitlines():
        require(len(line) > 66 and line[64:66] in ('  ', ' *'), 'Malformed package lock.')
        expected, relative = line[:64], line[66:]
        require(len(expected) == 64 and all(c in '0123456789abcdef' for c in expected) and
                relative not in seen, 'Invalid/duplicate package lock.')
        seen.add(relative)
        require(sha(safe_file(run, relative)) == expected, 'Package source changed: ' + relative)
        rows.append({'relative_path': relative, 'sha256': expected})
    require({'CONFIG.json','PROTOCOL.txt','RESPONSE_SPEC.json','OUTPUT_CONTRACT.json',
             'render_ph_report.py','hpc_guard.py','common.py','analyze_ph.py','ph_self_checks.py',
             'stage_source.py','environment_check.py'} <= seen, 'Required source is not package-locked.')
    return {'manifest_sha256': sha(manifest), 'rows': rows}


def verify_sources(run, cfg):
    integrity = verify_staged_sources(run, cfg)
    require(integrity and all(r['status'] == 'UNCHANGED' for r in integrity), 'Source byte preservation failed.')
    path = safe_file(run, 'audit/SOURCE_STAGE_STATUS.json')
    stage = load_json(path)
    same_execution(stage.get('execution'), 'source staging')
    require(stage.get('status') == cfg['stage_success_status'] == 'MEASURED_PH_SOURCES_STAGED_VERIFIED' and
            stage.get('source_integrity_passed') is True and
            stage.get('source_files_verified_unchanged_after_staging') is True and
            stage.get('source_files_changed') is False, 'Source staging proof failed.')
    require(stage.get('latest_measured_inventory_job_id') == '24147988' and
            stage.get('latest_validation_job_id') == '24128032' and
            stage.get('accepted_review_path') == cfg['accepted_review_path'] and
            stage.get('accepted_review_sha256') == cfg['accepted_review_sha256'] ==
            sha(safe_file(run, cfg['accepted_review_path'])), 'Accepted source review differs.')
    accepted = load_json(safe_file(run, cfg['accepted_review_path']))
    require(accepted.get('status') == 'ACCEPTED_SOURCE_PRESERVING_MEASURED_OUTCOME_INVENTORY',
            'Measured inventory has not been accepted within its limited scope.')
    expected = {(a, p): h for a, paths in cfg['sources'].items() for p, h in paths.items()}
    mf = safe_file(run, 'audit/source_manifest.csv')
    require(stage.get('source_manifest_path') == 'audit/source_manifest.csv' and
            stage.get('source_manifest_sha256') == sha(mf), 'Source manifest is not pinned.')
    _, rows = read_csv(mf, {'source','relative_path','sha256','size_bytes','source_job_id','original_path','snapshot_path'})
    require(len(rows) == len(expected) and {(r['source'],r['relative_path']) for r in rows} == set(expected),
            'Exact source manifest set differs.')
    for row in rows:
        a, p = row['source'], row['relative_path']
        original = safe_file(Path(cfg['source_runs'][a]['path']), p)
        snapshot = safe_file(run/'source_snapshot'/a, p)
        require(row['sha256'] == expected[(a,p)] == sha(original) == sha(snapshot) and
                Path(row['original_path']).resolve(strict=True) == original and
                Path(row['snapshot_path']).resolve(strict=True) == snapshot and
                row['source_job_id'] == str(cfg['source_runs'][a]['analysis_job_id']) and
                original.stat().st_size == snapshot.stat().st_size == integer(row['size_bytes'],'source size'),
                'Source lineage/hash/size differs: ' + a + '/' + p)
    manifests = {}
    for name, pins, base in (
            ('review_lock', cfg['review_locks'], run),
            ('validation_output', cfg['validation_output_locks'], Path(cfg['source_runs']['validation']['path']))):
        relative = 'audit/' + name + '_manifest.csv'
        mp = safe_file(run, relative)
        require(stage.get(name + '_manifest_path') == relative and
                stage.get(name + '_manifest_sha256') == sha(mp), 'Additional source manifest not pinned: ' + name)
        _, entries = read_csv(mp, {'relative_path','sha256','size_bytes'})
        require(len(entries) == len(pins) and {r['relative_path'] for r in entries} == set(pins),
                'Additional exact source set differs: ' + name)
        for row in entries:
            fp = safe_file(base, row['relative_path'])
            require(row['sha256'] == pins[row['relative_path']] == sha(fp) and
                    fp.stat().st_size == integer(row['size_bytes'],'locked size'), 'Additional source lock differs.')
        manifests[name + '_manifest_sha256'] = sha(mp)
    return {'integrity': integrity, 'stage': stage, 'source_stage_status_sha256': sha(path),
            'source_manifest_sha256': sha(mf), **manifests}


def register_fonts():
    spec = importlib.util.find_spec('matplotlib')
    require(spec and spec.origin, 'Matplotlib font resources missing.')
    directory = Path(spec.origin).parent/'mpl-data'/'fonts'/'ttf'
    paths = [directory/'DejaVuSans.ttf', directory/'DejaVuSans-Bold.ttf']
    require(all(p.is_file() for p in paths), 'Embedded DejaVu font pair missing.')
    pdfmetrics.registerFont(TTFont('ReportSans', str(paths[0])))
    pdfmetrics.registerFont(TTFont('ReportBold', str(paths[1])))
    pdfmetrics.registerFontFamily('ReportSans', normal='ReportSans', bold='ReportBold',
                                italic='ReportSans', boldItalic='ReportBold')
    return [{'path': str(p), 'sha256': sha(p)} for p in paths]


def paragraph(text, width, size=8.2, bold=False, color=INK):
    require(width > 12, 'Paragraph width below padding.')
    style = ParagraphStyle('ph', fontName='ReportBold' if bold else 'ReportSans', fontSize=size,
        leading=size*1.30, textColor=HexColor(color), splitLongWords=True)
    content = escape(str(text)).replace('\r\n','\n').replace('\r','\n').replace('\n','<br/>')
    item = Paragraph(content, style)
    _, height = item.wrap(width, PAGE_HEIGHT)
    return item, height


def measure_table(section):
    widths = section['widths']
    require(abs(sum(widths)-BODY_WIDTH) < 0.001 and len(widths) == len(section['headers']), 'Invalid table widths.')
    headers = [paragraph(v,w-12,7.9,True) for v,w in zip(section['headers'],widths)]
    hh = max(28,max(h for _,h in headers)+12)
    _,title_height = paragraph(section['title'],BODY_WIDTH,10.2,True)
    _,subtitle_height = paragraph(section['subtitle'],BODY_WIDTH,8.1,color=MUTED)
    available = BODY_TOP-BODY_BOTTOM-hh-title_height-subtitle_height-14-16
    require(available > 40, 'Measured table header consumes body.')
    fragments = []
    for i,row in enumerate(section['rows']):
        require(len(row) == len(widths), 'Table row has wrong column count.')
        pending = [paragraph(v,w-12,section.get('size',8.0))[0] for v,w in zip(row,widths)]
        continuation = False
        while any(p is not None for p in pending):
            cells, remains = [], []
            for column,(part,w) in enumerate(zip(pending,widths)):
                if part is None:
                    text = str(row[0])+' (continued)' if column==0 and continuation else ''
                    cells.append(paragraph(text,w-12,section.get('size',8.0))); remains.append(None)
                    continue
                _,h = part.wrap(w-12,PAGE_HEIGHT)
                if h <= available-12:
                    cells.append((part,h)); remains.append(None)
                else:
                    pieces = part.split(w-12,available-12)
                    require(len(pieces) == 2, 'Long report cell cannot safely continue.')
                    head,tail = pieces; _,h = head.wrap(w-12,PAGE_HEIGHT)
                    cells.append((head,h)); remains.append(tail)
            height = max(27,max(h for _,h in cells)+12)
            require(height <= available, 'Measured row fragment crosses body.')
            fragments.append({'source_row':i,'cells':cells,'height':height,'continued':continuation})
            pending,continuation = remains,True
    require({r['source_row'] for r in fragments} == set(range(len(section['rows']))), 'Report omitted a source row.')
    return {'headers':headers,'header_height':hh,'fragments':fragments,'source_rows':len(section['rows'])}


def heading(c,title,subtitle,page):
    title_p,th = paragraph(title,BODY_WIDTH,16,True)
    sub,sh = paragraph(subtitle,BODY_WIDTH,8.6,color=MUTED)
    require(th <= 30 and sh <= 38, 'Page heading crosses body divider.')
    title_p.drawOn(c,LEFT,584-th); sub.drawOn(c,LEFT,548-sh)
    c.setStrokeColor(HexColor('#DDE5ED')); c.line(LEFT,508,RIGHT,508)
    c.setFont('ReportSans',7.2); c.setFillColor(HexColor(MUTED))
    c.drawString(LEFT,21,'Puma | reported-pH known-plot diagnostics | no causal or forecast claim | review pending')
    c.drawRightString(RIGHT,21,str(page))


def table_header(c,widths,measured,y):
    hh = measured['header_height']
    c.setFillColor(HexColor('#EAF0F6')); c.rect(LEFT,y-hh,BODY_WIDTH,hh,fill=1,stroke=0)
    x=LEFT
    for w,(p,h) in zip(widths,measured['headers']):
        p.drawOn(c,x+6,y-6-h); x+=w
    return y-hh


def table_row(c,widths,row,y,index):
    h=row['height']
    require(y-h >= BODY_BOTTOM, 'Drawn row crosses footer band.')
    if index%2 == 0:
        c.setFillColor(HexColor('#F3F6F9')); c.rect(LEFT,y-h,BODY_WIDTH,h,fill=1,stroke=0)
    x=LEFT
    for w,(p,ph) in zip(widths,row['cells']):
        p.drawOn(c,x+6,y-6-ph); x+=w
    c.setStrokeColor(HexColor('#DDE5ED')); c.line(LEFT,y-h,RIGHT,y-h)
    return y-h


def paginate(intro,sections):
    """One exact-text display plan is shared by preflight and final drawing."""
    pages=[]; blocks=[]; used=0
    for title,text in intro:
        hp,hh=paragraph(title,BODY_WIDTH,10.4,True)
        pending=paragraph(text,BODY_WIDTH,9.1)[0]
        while pending is not None:
            _,bh=pending.wrap(BODY_WIDTH,PAGE_HEIGHT)
            capacity=BODY_TOP-BODY_BOTTOM-hh-20
            if bh > capacity:
                pieces=pending.split(BODY_WIDTH,capacity)
                require(len(pieces)==2,'Intro cannot safely continue.')
                bp,pending=pieces; _,bh=bp.wrap(BODY_WIDTH,PAGE_HEIGHT)
            else:
                bp,pending=pending,None
            total=hh+bh+20
            if blocks and used+total > BODY_TOP-BODY_BOTTOM:
                pages.append({'kind':'intro','blocks':blocks}); blocks=[]; used=0
            require(total <= BODY_TOP-BODY_BOTTOM,'Intro block crosses footer.')
            blocks.append({'header':hp,'header_height':hh,'body':bp,'body_height':bh,'height':total}); used+=total
    if blocks: pages.append({'kind':'intro','blocks':blocks})
    intro_count=len(pages)
    receipts=[]; pieces=[]; remaining=BODY_TOP-BODY_BOTTOM
    for section in sections:
        measured=measure_table(section)
        title,h=paragraph(section['title'],BODY_WIDTH,10.2,True)
        subtitle,sh=paragraph(section['subtitle'],BODY_WIDTH,8.1,color=MUTED)
        section_heading=h+sh+14
        start=len(pages); fragment_pages=0; offset=0
        source=measured['fragments'] or [None]
        while offset<len(source):
            first=source[offset]
            minimum=section_heading+measured['header_height']+(first['height'] if first else 30)+16
            if pieces and minimum>remaining:
                pages.append({'kind':'tables','pieces':pieces}); pieces=[]; remaining=BODY_TOP-BODY_BOTTOM
            require(minimum<=BODY_TOP-BODY_BOTTOM,'Section heading and one row exceed body.')
            available=remaining-section_heading-measured['header_height']-16
            chosen=[]; used_rows=0
            while offset<len(source):
                r=source[offset]
                rh=r['height'] if r else 30
                if chosen and used_rows+rh>available: break
                require(rh<=available,'Measured row cannot fit beneath actual section heading.')
                chosen.append(r); used_rows+=rh; offset+=1
            total=section_heading+measured['header_height']+used_rows+16
            pieces.append({'section':section,'measured':measured,'title':title,'title_height':h,
                'subtitle':subtitle,'subtitle_height':sh,'rows':chosen,'height':total,'row_start':offset-len(chosen)})
            remaining-=total; fragment_pages+=1
            if offset<len(source):
                pages.append({'kind':'tables','pieces':pieces}); pieces=[]; remaining=BODY_TOP-BODY_BOTTOM
        receipts.append({'title':section['title'],'companion':section['companion'],
            'source_rows':len(section['rows']),'display_fragments':len(measured['fragments']),
            'pages_touched':fragment_pages,'header_height_points':measured['header_height'],
            'largest_row_fragment_points':max((r['height'] for r in measured['fragments']),default=0),
            'all_rows_preserved':True,'status':'PASS'})
    if pieces: pages.append({'kind':'tables','pieces':pieces})
    return pages,{'status':'PASS','execution':execution,'all_actual_text_measured':True,
        'introductory_pages':intro_count,'text_pages':len(pages),'sections':receipts,
        'rows_truncated':False,'scientific_figures_generated_before_preflight':False}


def render_text(path,pages):
    c=canvas.Canvas(str(path),pagesize=(PAGE_WIDTH,PAGE_HEIGHT),pageCompression=1)
    c.setTitle('Whipstone Annual reported pH: observed trajectories and known-plot baselines')
    for i,page in enumerate(pages,1):
        heading(c,'Whipstone Annual reported soil pH',
                'Separate 2024 and 2025 retrospective rounds; complete support and skip reasons in pinned companions',i)
        y=BODY_TOP
        if page['kind']=='intro':
            for b in page['blocks']:
                b['header'].drawOn(c,LEFT,y-b['header_height']); y-=b['header_height']+6
                b['body'].drawOn(c,LEFT,y-b['body_height']); y-=b['body_height']+14
                require(y>=BODY_BOTTOM,'Drawn intro crosses footer.')
        else:
            for piece in page['pieces']:
                piece['title'].drawOn(c,LEFT,y-piece['title_height']); y-=piece['title_height']+4
                piece['subtitle'].drawOn(c,LEFT,y-piece['subtitle_height']); y-=piece['subtitle_height']+10
                y=table_header(c,piece['section']['widths'],piece['measured'],y)
                for j,r in enumerate(piece['rows']):
                    if r is None:
                        p,h=paragraph('No rows recorded.',BODY_WIDTH-12,8.2); p.drawOn(c,LEFT+6,y-6-h); y-=30
                    else:
                        y=table_row(c,piece['section']['widths'],r,y,piece['row_start']+j)
                y-=16
                require(y>=BODY_BOTTOM,'Drawn table section crosses footer.')
        c.showPage()
    c.save()


def csv_bool(value,context):
    require(value in ('True','False'), 'Noncanonical Boolean: '+context)
    return value=='True'


def verify_engine(run,contract,spec):
    require(contract.get('package')==PACKAGE and contract.get('report_success_status')==REPORT_SUCCESS and
            set(contract['report_output_files'])=={REPORT_RELATIVE,LAYOUT_RELATIVE,*FIGURE_NAMES},
            'Frozen report contract differs.')
    status_path=regular_output(run,contract['engine_status_path'])
    status=load_json(status_path)
    same_execution(status.get('execution'),'pH engine')
    require(set(contract['engine_status_required_keys'])<=set(status) and
            status.get('status')==contract['engine_success_status']==
            'WHIPSTONE_PH_BASELINE_DIAGNOSTICS_COMPLETED_REVIEW_PENDING','Current engine did not complete.')
    for key,value in contract['successful_flags'].items():
        require(status.get(key) is value,'Engine scope flag differs: '+key)
    require(status.get('response_spec_sha256')==sha(safe_file(run,'RESPONSE_SPEC.json')) and
            status.get('output_contract_sha256')==sha(safe_file(run,'OUTPUT_CONTRACT.json')),
            'Engine did not pin current frozen protocol/contract.')
    mp=regular_output(run,contract['engine_manifest_path'])
    require(status.get('engine_output_manifest_path')==contract['engine_manifest_path'] and
            status.get('engine_output_manifest_sha256')==sha(mp),'Engine manifest link differs.')
    _,manifest=read_csv(mp,contract['manifest_columns'])
    expected=set(contract['engine_output_files'])
    require(len(manifest)==len(expected) and {r['relative_path'] for r in manifest}==expected,
            'Exact engine manifest set differs or contains duplicates.')
    for row in manifest:
        fp=regular_output(run,row['relative_path'])
        require(sha(fp)==row['sha256'] and
                fp.stat().st_size==integer(row['size_bytes'],'engine size'),'Engine output hash/size differs.')
    tables={}
    for relative,declaration in contract['csv_schemas'].items():
        fields,rows=read_csv(safe_file(run,relative),declaration['required_columns'])
        require(declaration.get('additional_source_provenance_columns_allowed') is True or
                fields==declaration['required_columns'],'Undeclared CSV columns.')
        tables[relative]=(fields,rows)
    receipts={}
    for relative,declaration in contract['json_schemas'].items():
        receipt=load_json(safe_file(run,relative))
        require(set(declaration['required_keys'])<=set(receipt),'Engine JSON schema missing: '+relative)
        receipts[relative]=receipt
    preserve=receipts[contract['source_preservation_path']]
    same_execution(preserve.get('execution'),'pH source preservation')
    require(preserve.get('status')=='ALL_SOURCE_BYTES_AND_ORIGINAL_PH_ROWS_VERIFIED_UNCHANGED' and
            preserve.get('source_integrity_passed') is True and
            preserve.get('source_files_verified_unchanged') is True and
            preserve.get('source_files_changed') is False and
            preserve.get('source_measurement_values_modified') is False and
            status.get('source_preservation_sha256')==sha(safe_file(run,contract['source_preservation_path'])),
            'Engine source preservation proof differs.')
    checks=receipts[contract['self_check_receipt_path']]
    same_execution(checks.get('execution'),'Puma pH scientific fixtures')
    require(checks.get('status')=='PASS' and checks.get('passed') is True and checks.get('checks') and
            all(r.get('passed') is True for r in checks['checks']) and
            checks.get('fixture_kind')=='SCIENTIFIC_SYNTHETIC_EDGE_CASES_HPC_ONLY' and
            checks.get('scientific_source_data_read') is False and
            checks.get('response_spec_sha256')==sha(safe_file(run,'RESPONSE_SPEC.json')) and
            checks.get('output_contract_sha256')==sha(safe_file(run,'OUTPUT_CONTRACT.json')) and
            status.get('fixture_receipt_sha256')==sha(safe_file(run,contract['self_check_receipt_path'])),
            'Puma scientific fixtures did not pass.')
    freeze=receipts['results/prediction_freeze.json']
    same_execution(freeze.get('execution'),'training-only prediction freeze')
    training={p:sha(safe_file(run,p)) for p in ('results/training_membership.csv','results/training_plot_means.csv')}
    require(freeze.get('status')=='ALL_DECLARED_ROUND_PREDICTIONS_FROZEN_BEFORE_TARGET_SCORING' and
            status.get('prediction_freeze_sha256')==sha(safe_file(run,'results/prediction_freeze.json')) and
            freeze.get('training_files')==training and
            freeze.get('freeze_before_any_target_scoring') is True and
            freeze.get('target_outcome_used_for_training') is False and
            freeze.get('retrospective_execution_order_only') is True and
            freeze.get('source_outcomes_previously_viewed') is True and
            freeze.get('predictions_sha256')==sha(safe_file(run,'results/predictions.csv')) and
            freeze.get('fits_sha256')==sha(safe_file(run,'results/fits.json')),
            'Saved training-only prediction freeze links/scope differ.')
    fits=receipts['results/fits.json']
    require(fits.get('status')=='TRAINING_ONLY_BASELINE_PARAMETERS_FROZEN' and
            fits.get('reported_scale')=='REPORTED_PH_SCALE' and fits.get('methods')==list(METHODS) and
            fits.get('future_target_membership_or_value_used_for_training') is False,
            'Fits do not record target-blind training.')
    require({m['method_id'] for m in spec['methods']}==set(METHODS) and
            [f['fold_id'] for f in spec['folds']]==['PH_2024_FROM_2023','PH_2025_FROM_2023_2024'],
            'Prespecified methods/folds differ.')
    return {'status':status,'status_sha256':sha(status_path),'manifest':manifest,'manifest_sha256':sha(mp),
            'tables':tables,'receipts':receipts}


def validate_display_rows(engine,spec):
    tables={p:r for p,(_fields,r) in engine['tables'].items()}
    observations=tables['results/eligible_observations.csv']
    errors=tables['results/plot_errors.csv']
    performance=tables['results/performance.csv']
    paired=tables['results/paired_performance.csv']
    fold_map={f['fold_id']:f for f in spec['folds']}
    rounds={r['round_id']:set(r['actual_collection_dates']) for r in spec['rounds']}
    obs_by_id={}; plot_rounds=set()
    for r in observations:
        require(r['observation_id']==r['inventory_row_id'] and r['observation_id'] not in obs_by_id,
                'Observed source identity missing/duplicated.')
        require(r['confirmed_physical_plot_id'] and r['annual_round_id'] in rounds and
                r['collection_date'] in rounds[r['annual_round_id']] and r['unit']=='pH units',
                'Observed plot/date/reported unit differs from protocol.')
        key=(r['confirmed_physical_plot_id'],r['annual_round_id'])
        require(key not in plot_rounds,'Two eligible actual events occur in one physical plot/annual round.')
        plot_rounds.add(key); finite(r['reported_pH'],'recorded pH coordinate'); obs_by_id[r['observation_id']]=r
    metrics={}; groups={}; all_targets={}
    for r in performance:
        key=(r['fold_id'],r['method'])
        require(r['fold_id'] in fold_map and r['method'] in METHODS and key not in metrics and
                r['target_round_id']==fold_map[r['fold_id']]['target_round_id'], 'Performance fold/method differs.')
        require(csv_bool(r['folds_independent'],'fold independence') is False,'Folds were pooled as independent.')
        n=integer(r['n_scored_plots'],'method-specific n')
        if n:
            require('NOT_ESTIMATED' not in r['status'],'Positive support marked not estimated.')
            for field in ('MAE','RMSE'): require(finite(r[field],field)>=0,'Negative saved error metric.')
        else:
            require(r['MAE']==r['RMSE']=='' and 'NOT_ESTIMATED' in r['status'],
                    'Zero support must retain empty metrics and explicit skip status.')
        require(r['reason'],'Missing method support/skip reason.')
        metrics[key]=r; groups[key]=[]; all_targets[key]=[]
    require(set(metrics)=={(f,m) for f in fold_map for m in METHODS},'Complete fold/method metrics missing.')
    seen=set()
    for r in errors:
        key=(r['fold_id'],r['method'])
        require(key in groups and r['target_round_id']==fold_map[r['fold_id']]['target_round_id'],
                'Target ledger fold/method differs.')
        target_key=(r['fold_id'],r['method'],r['target_record_id'])
        require(target_key not in seen,'Duplicate method/target record in ledger.')
        seen.add(target_key); all_targets[key].append(r)
        scored=csv_bool(r['scored'],'target score flag')
        if scored:
            require(all(csv_bool(r[f],f) for f in ('target_eligible','known_training_plot','prediction_available')),
                    'Scored target support flags disagree.')
            obs=obs_by_id.get(r['target_observation_id'])
            require(obs is not None and all(obs[a]==r[b] for a,b in (
                ('record_id','target_record_id'),('confirmed_physical_plot_id','confirmed_physical_plot_id'),
                ('collection_date','collection_date'),('reported_pH','reported_pH'))),
                'Saved score does not reference its exact recorded observation.')
            require(r['collection_date'] in rounds[r['target_round_id']],'Actual target date is not prespecified.')
            for field in ('reported_pH','predicted_pH','signed_error','absolute_error','squared_error'):
                finite(r[field],'saved '+field)
            groups[key].append(r)
        else:
            require(r['absolute_error']==r['signed_error']==r['squared_error']=='' and r['reason_codes'],
                    'Unscored target lost explicit reason or contains errors.')
    for key,rows in groups.items():
        require(len(rows)==integer(metrics[key]['n_scored_plots'],'metric n') and
                len({r['confirmed_physical_plot_id'] for r in rows})==len(rows), 'Saved metric cohort differs.')
    paired_keys=set()
    for r in paired:
        key=(r['fold_id'],r['method'])
        require(key in metrics and key not in paired_keys,'Duplicate/foreign paired performance row.')
        paired_keys.add(key)
        sets=[{x['confirmed_physical_plot_id'] for x in groups[(r['fold_id'],m)]} for m in METHODS]
        require(integer(r['n_common_plots'],'paired n')==len(sets[0]&sets[1]),'Same-round paired support differs.')
        if integer(r['n_common_plots'],'paired n')==0:
            require(r['MAE']==r['RMSE']=='' and 'NOT_ESTIMATED' in r['status'],'Missing paired skip status.')
        else:
            for field in ('MAE','RMSE'): finite(r[field],'saved paired '+field)
        require(r['reason'] and r['pairing_scope'],'Paired limits/reasons missing.')
    require(paired_keys==set(metrics),'Complete common-cohort paired metrics missing.')
    return tables,observations,metrics,groups,all_targets


def apply_axes(ax):
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y',color='#DDE5ED',linewidth=0.6,zorder=0)
    ax.tick_params(labelsize=8)
    ax.xaxis.set_major_formatter(FuncFormatter(compact))
    ax.yaxis.set_major_formatter(FuncFormatter(compact))


def figure_bounds(fig,context):
    fig.canvas.draw(); renderer=fig.canvas.get_renderer(); box=fig.bbox
    artists=list(fig.texts)
    for legend in fig.legends:
        artists.extend(legend.get_texts()); artists.append(legend.get_title())
    for ax in fig.axes:
        artists.extend([ax.title,ax.xaxis.label,ax.yaxis.label,*ax.texts])
        for axis in (ax.xaxis,ax.yaxis):
            low,high=sorted(axis.get_view_interval())
            for tick in axis.get_major_ticks():
                if low<=tick.get_loc()<=high: artists.extend([tick.label1,tick.label2])
    for artist in artists:
        if artist.get_visible() and artist.get_text():
            b=artist.get_window_extent(renderer)
            require(b.x0>=box.x0-1 and b.x1<=box.x1+1 and b.y0>=box.y0-1 and b.y1<=box.y1+1,
                    'Figure text crosses page boundary: '+context+' / '+artist.get_text())
    return {'name':context,'status':'PASS','actual_text_within_page':True}


def finish_figure(fig,path,context):
    receipt=figure_bounds(fig,context)
    fig.savefig(path,format='pdf',facecolor='white')
    plt.close(fig)
    return receipt


def observed_figure(path,observations,spec):
    plots=sorted({r['confirmed_physical_plot_id'] for r in observations})
    require(len(plots)<=16,'Frozen physical-plot figure needs a separately reviewed larger layout.')
    ncols=4; nrows=max(1,math.ceil(len(plots)/ncols))
    fig,axes=plt.subplots(nrows,ncols,figsize=(11,8.5),squeeze=False)
    fig.subplots_adjust(left=.075,right=.975,bottom=.14,top=.84,wspace=.42,hspace=1.08)
    fig.suptitle('Whipstone Annual: observed reported-pH plot trajectories',fontsize=15,y=.968)
    fig.text(.5,.925,'Actual collection dates; points are measured values. Segments only connect adjacent observed annual rounds.',ha='center',fontsize=9)
    fig.text(.5,.065,'Reported 1:1 definition only; assay calibration and sampling-depth equivalence are not established.',ha='center',fontsize=8.5)
    fig.text(.5,.04,'No imputation, fitted trend, treatment-effect estimate, interval or future horizon. Missing rounds break the segments.',ha='center',fontsize=8.5)
    round_index={r['round_id']:i for i,r in enumerate(spec['rounds'])}
    values=[finite(r['reported_pH'],'observed coordinate') for r in observations]
    low,high=(min(values)-.15,max(values)+.15) if values else (0,1)
    for index,ax in enumerate(axes.flat):
        if index>=len(plots):
            ax.axis('off')
            if not plots and index==0: ax.text(.5,.5,'No eligible recorded pH observations.\nSee source hold ledger.',ha='center',va='center',transform=ax.transAxes)
            continue
        plot=plots[index]
        rows=sorted((r for r in observations if r['confirmed_physical_plot_id']==plot),key=lambda r:r['collection_date'])
        xs=[date.fromisoformat(r['collection_date']) for r in rows]
        ys=[finite(r['reported_pH'],'reported pH') for r in rows]
        for left,right in zip(rows,rows[1:]):
            if round_index[right['annual_round_id']]-round_index[left['annual_round_id']]==1:
                ax.plot([date.fromisoformat(left['collection_date']),date.fromisoformat(right['collection_date'])],
                        [finite(left['reported_pH'],'pH'),finite(right['reported_pH'],'pH')],color='#8AA0B0',linewidth=1,zorder=2)
        ax.scatter(xs,ys,color=COLORS[0],s=25,zorder=3)
        ax.set_title('\n'.join(textwrap.wrap(plot,35)),fontsize=7.6,pad=9)
        ax.set_ylim(low,high); ax.set_xticks(xs)
        ax.set_xticklabels([d.strftime('%Y\n%m-%d') for d in xs],fontsize=6.8)
        ax.yaxis.set_major_formatter(FuncFormatter(compact)); ax.tick_params(axis='y',labelsize=7)
        ax.spines[['top','right']].set_visible(False); ax.grid(axis='y',color='#E2E8EE',linewidth=.6)
        ax.margins(x=.13)
        if index%ncols==0: ax.set_ylabel('Reported pH',fontsize=8,labelpad=6)
    return finish_figure(fig,path,'Observed plot trajectories')


def scatter_figure(path,fold,spec,observations,metrics,groups):
    fid=fold['fold_id']; year=fold['target_round_id']
    rows=[r for m in METHODS for r in groups[(fid,m)]]
    dates=sorted({r['collection_date'] for r in rows})
    markers={d:m for d,m in zip(dates,('o','s','^','D'))}
    fig,axes=plt.subplots(1,2,figsize=(11,8.5))
    fig.subplots_adjust(left=.10,right=.95,bottom=.27,top=.80,wspace=.35)
    fig.suptitle(year+' Annual round: observed pH versus frozen baselines',fontsize=15,y=.968)
    training=', '.join(fold['training_round_ids'])
    fig.text(.5,.925,'Training rounds '+training+' only; all '+year+' target dates held together. No pooling across rounds.',ha='center',fontsize=9)
    numeric=[finite(r[k],'saved point') for r in rows for k in ('reported_pH','predicted_pH')]
    lo,hi=(min(numeric)-.2,max(numeric)+.2) if numeric else (0,1)
    for ax,method in zip(axes,METHODS):
        metric=metrics[(fid,method)]; points=groups[(fid,method)]
        apply_axes(ax); ax.set_title(METHOD_LABEL[method],fontsize=12,pad=12)
        ax.set_xlabel('Frozen baseline pH',fontsize=10,labelpad=9)
        ax.set_ylabel('Observed reported pH',fontsize=10,labelpad=9)
        if points:
            ax.plot([lo,hi],[lo,hi],color='#9AA7B2',linestyle='--',linewidth=.9,zorder=1)
            for r in points:
                ax.scatter(finite(r['predicted_pH'],'saved prediction'),finite(r['reported_pH'],'saved observation'),
                    color=COLORS[0],marker=markers[r['collection_date']],s=45,edgecolors='white',linewidth=.5,zorder=3)
            ax.set_xlim(lo,hi); ax.set_ylim(lo,hi)
            ax.text(.04,.96,'n='+metric['n_scored_plots']+'\nMAE='+compact(finite(metric['MAE'],'saved MAE'))+
                '; RMSE='+compact(finite(metric['RMSE'],'saved RMSE')),transform=ax.transAxes,ha='left',va='top',fontsize=9)
        else:
            ax.text(.5,.5,'Not estimated: n=0\n'+textwrap.fill(metric['reason'],36),ha='center',va='center',
                    transform=ax.transAxes,fontsize=9)
    handles=[Line2D([],[],color=COLORS[0],marker=markers[d],linestyle='',label=d) for d in dates]
    if handles: fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.14),ncol=3,fontsize=8,frameon=False)
    fig.text(.5,.095,'Dashed line: identity reference, not a fit. Metrics are saved method-specific point diagnostics in reported pH units.',ha='center',fontsize=8.3)
    fig.text(.5,.065,'Exact physical plots/dates are retained in the observation and target-ledger companions. No treatment label is inferred.',ha='center',fontsize=8.1)
    fig.text(.5,.035,'Reported-definition compatibility; depth, historical availability and interyear calibration limits remain. No prospective validation.',ha='center',fontsize=8.1)
    return finish_figure(fig,path,year+' observed versus baselines')


def error_figure(path,fold,metrics,all_targets):
    fid=fold['fold_id']; year=fold['target_round_id']
    targets={}
    for m in METHODS:
        for r in all_targets[(fid,m)]:
            targets.setdefault(r['target_record_id'],r)
    records=sorted(targets,key=lambda k:(targets[k]['confirmed_physical_plot_id'],targets[k]['collection_date'],k))
    require(len(records)<=24,'Frozen target ledger needs a separately reviewed larger error layout.')
    fig,ax=plt.subplots(figsize=(11,8.5)); fig.subplots_adjust(left=.43,right=.95,bottom=.19,top=.82)
    fig.suptitle(year+' Annual round: saved absolute errors by physical plot',fontsize=14,y=.968)
    fig.text(.5,.925,'Two training-only baselines; actual dates retained. An absent dot is not a zero error.',ha='center',fontsize=9)
    ax.spines[['top','right']].set_visible(False); ax.grid(axis='x',color='#DDE5ED',linewidth=.6)
    lookup={(r['method'],r['target_record_id']):r for m in METHODS for r in all_targets[(fid,m)]}
    labels=[]
    for i,record in enumerate(records):
        r=targets[record]
        label=r['confirmed_physical_plot_id'] or 'Unconfirmed physical identity; record '+record
        labels.append('\n'.join(textwrap.wrap(label,48))+' | '+(r['collection_date'] or '(date absent)'))
        skipped=[]
        for method,offset,color,marker in zip(METHODS,(-.13,.13),COLORS,('o','s')):
            e=lookup.get((method,record))
            require(e is not None,'One method missing from complete target ledger.')
            if csv_bool(e['scored'],'saved score flag'):
                ax.scatter(finite(e['absolute_error'],'saved absolute error'),i+offset,s=38,color=color,marker=marker,zorder=3)
            else:
                skipped.append(METHOD_LABEL[method]+': '+e['status'])
        if skipped:
            ax.text(.02,i,'\n'.join(textwrap.fill(s,48) for s in skipped),
                    transform=ax.get_yaxis_transform(),fontsize=6.5,va='center')
    ax.set_yticks(range(len(records))); ax.set_yticklabels(labels,fontsize=7.5)
    ax.set_ylim(len(records)-.5,-.5)
    ax.xaxis.set_major_formatter(FuncFormatter(compact)); ax.set_xlabel('Absolute error (reported pH units)',fontsize=10,labelpad=10)
    if not records: ax.text(.5,.5,'No declared target rows recorded.\nSee support and skip tables.',transform=ax.transAxes,ha='center',va='center')
    handles=[Line2D([],[],color=c,marker=m,linestyle='',label=METHOD_LABEL[k]+'; n='+metrics[(fid,k)]['n_scored_plots'])
             for k,c,m in zip(METHODS,COLORS,('o','s'))]
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.10),ncol=2,fontsize=9,frameon=False)
    fig.text(.5,.075,'Per-method support is distinct from the exact same-round common-plot paired support. No pooled n or winner.',ha='center',fontsize=8.5)
    fig.text(.5,.04,'Skip reasons are complete in the report ledger. Retrospective unblinded design; no calibrated interval or future forecast.',ha='center',fontsize=8.5)
    return finish_figure(fig,path,year+' absolute errors')


def section(title,subtitle,companion,headers,widths,rows,fields):
    return {'title':title,'subtitle':subtitle+' Companion: '+companion,'companion':companion,
            'headers':headers,'widths':widths,'rows':rows,'displayed_fields':fields,'size':8.0}


def report_sections(tables,observations,spec):
    # Side-by-side display of individual unchanged visits, not response aggregation.
    rounds=[r['round_id'] for r in spec['rounds']]
    plot_ids=sorted({r['confirmed_physical_plot_id'] for r in observations})
    observed=[]
    for plot in plot_ids:
        row=[plot]
        for annual in rounds:
            visits=[r for r in observations if r['confirmed_physical_plot_id']==plot and r['annual_round_id']==annual]
            require(len(visits)<=1,'Displayed plot/annual-round source observation is ambiguous.')
            if visits:
                v=visits[0]
                row.append(v['collection_date']+' | pH '+v['reported_pH']+'\n'+v['record_id']+' | '+
                    v['preferred_pdf_source_id']+' p. '+v['preferred_pdf_page']+'\n'+v['observation_id'])
            else:
                row.append('No eligible recorded visit.\nNo value imputed; see hold ledger.')
        observed.append(row)
    sections=[section('Recorded pH visits by physical plot',
        'Each cell is one unchanged source observation, actual date and source reference; empty rounds remain explicit. Reported pH units.',
        'results/eligible_observations.csv',['Confirmed physical plot']+[r+' actual visit / reported pH / source' for r in rounds],
        [198,174,174,174],observed,['confirmed_physical_plot_id','annual_round_id','collection_date','reported_pH',
            'record_id','preferred_pdf_source_id','preferred_pdf_page','observation_id'])]
    perf=tables['results/performance.csv']
    sections.append(section('Method-specific point diagnostics',
        'Separate target rounds and per-method known plots. MAE/RMSE are saved engine metrics in reported pH units; empty metrics mean not estimated.',
        'results/performance.csv',['Target round / training','Method','n','MAE','RMSE','Saved status / reason'],
        [106,142,36,74,74,288],[[r['target_round_id']+'\n'+r['fold_id'],METHOD_LABEL[r['method']],r['n_scored_plots'],
             r['MAE'],r['RMSE'],r['status']+'\n'+r['reason']] for r in perf],
        ['fold_id','target_round_id','method','n_scored_plots','MAE','RMSE','status','reason']))
    paired=tables['results/paired_performance.csv']
    sections.append(section('Same-round common-plot paired diagnostics',
        'Each method is evaluated on exactly the common eligible physical-plot targets within that round. No pooled-fold comparison or winner.',
        'results/paired_performance.csv',['Target round','Method','Common n','MAE','RMSE','Saved status / pairing scope / reason'],
        [74,142,54,74,74,302],[[r['target_round_id'],METHOD_LABEL[r['method']],r['n_common_plots'],r['MAE'],r['RMSE'],
            r['status']+'\n'+r['pairing_scope']+'\n'+r['reason']] for r in paired],
        ['target_round_id','method','n_common_plots','MAE','RMSE','status','pairing_scope','reason']))
    support=tables['results/cohort_support.csv']
    sections.append(section('Round-level source and known-plot support',
        'Training rows, training plots, declared targets and paired physical plots are different units. Counts are saved engine values.',
        'results/cohort_support.csv',['Fold / cutoff','Actual dates','Training / target / scoring support','Saved status / reason'],
        [160,148,192,220],[[r['fold_id']+'\nCutoff '+r['cutoff_date'],
            'Training: '+r['training_actual_dates']+'\nTarget: '+r['target_actual_dates'],
            'Training observations: '+r['n_training_observations']+'\nTraining plots: '+r['n_training_plots']+
            '\nDeclared target records: '+r['n_declared_target_records']+'\nEligible target plots: '+r['n_target_eligible_plots']+
            '\nKnown target plots: '+r['n_target_known_plots']+'\nPersistence scored: '+r['n_scored_persistence']+
            '\nMean scored: '+r['n_scored_equal_plot_training_mean']+'\nCommon paired plots: '+r['n_common_paired_plots'],
            r['status']+'\n'+r['reason']] for r in support],list(engine_field for engine_field in (
                'fold_id','cutoff_date','training_actual_dates','target_actual_dates','n_training_observations','n_training_plots',
                'n_declared_target_records','n_target_eligible_plots','n_target_known_plots','n_scored_persistence',
                'n_scored_equal_plot_training_mean','n_common_paired_plots','status','reason'))))
    baseline=tables['results/baseline_support.csv']
    sections.append(section('Prespecified baseline support gates',
        'A support gate permits a descriptive computation; it establishes neither power nor predictive validity. Both methods require the same known plot.',
        'results/baseline_support.csv',['Fold / method','Training n / plots','Saved gate / known-plot requirement','Saved reason'],
        [206,104,170,240],[[r['fold_id']+'\n'+METHOD_LABEL[r['method']],r['n_training_observations']+' observations\n'+r['n_training_plots']+' plots',
            r['support_gate_status']+'\nKnown training plot: '+r['known_training_plot_required']+
            '\nMinimum prior: '+r['minimum_prior_observations_for_plot']+'\nMinimum plots: '+r['minimum_training_plots']+
            '\nPower/validation: '+r['power_or_validation_established'],r['reason']] for r in baseline],
        ['fold_id','method','n_training_observations','n_training_plots','support_gate_status','known_training_plot_required',
         'minimum_prior_observations_for_plot','minimum_training_plots','power_or_validation_established','reason']))
    held=[r for r in tables['results/observation_audit.csv']
          if csv_bool(r['expected_source_row'],'saved frozen-response scope') and
          not csv_bool(r['eligible'],'saved observation eligibility')]
    sections.append(section('Frozen Whipstone Annual response holds / absent records',
        'All saved expected-source holds/absent rows are shown. Complete original audit companion retains outside-scope/copy rows; eligibility is not recomputed.',
        'results/observation_audit.csv',['Record / observation / physical plot','Actual date / raw value','Saved status / all reason codes'],
        [270,130,320],[[r['record_id']+'\n'+r['observation_id']+'\n'+r['confirmed_physical_plot_id'],
            r['collection_date']+'\nRaw value: '+r['raw_value'],r['eligibility_status']+'\n'+r['eligibility_reason_codes']+
            '\n'+r['duplicate_or_conflict_status']] for r in held],
        ['expected_source_row','record_id','observation_id','confirmed_physical_plot_id','collection_date','raw_value','eligibility_status',
         'eligibility_reason_codes','duplicate_or_conflict_status']))
    skipped=[r for r in tables['results/plot_errors.csv'] if not csv_bool(r['scored'],'saved scored flag')]
    sections.append(section('Target records not scored',
        'Every unscored method/target record is retained, including cold starts and source holds. Missing error metrics are not zeros.',
        'results/plot_errors.csv',['Fold / method','Target record / physical plot / actual date','Saved status / all reason codes'],
        [208,242,270],[[r['fold_id']+'\n'+METHOD_LABEL[r['method']],r['target_record_id']+'\n'+r['confirmed_physical_plot_id']+'\n'+
            r['collection_date'],r['status']+'\n'+r['reason_codes']] for r in skipped],
        ['fold_id','method','target_record_id','confirmed_physical_plot_id','collection_date','status','reason_codes']))
    return sections


def intro_blocks(run,engine,spec):
    references=[]
    for row in engine['manifest']:
        if row['relative_path'] in ('results/eligible_observations.csv','results/observation_audit.csv',
                'results/plot_errors.csv','results/plot_changes.csv','results/prediction_freeze.json'):
            references.append(row['relative_path']+'\nSHA256 '+row['sha256'])
    return [
      ('Execution and source provenance',
       'New Puma job '+execution['job_id']+' on '+execution['node']+' rendered saved engine outputs from the same allocation. '
       'Authoritative observations are corrected workbook measurement rows preserved by accepted inventory job 24147988. '
       'Laboratory transcription representations are audit copies, not additional observations. Original sources, snapshots, reviews, '
       'scientific engine outputs and runtime evidence are checked before and after reporting. Historical failed/repaired job states remain unchanged.'),
      ('Response, design and prespecified baselines',
       'Whipstone Annual reported 1:1 soil pH, on the reported pH scale. Physical plot identities and actual dates remain explicit. '
       'The 2024 annual round groups 2024-07-06 and 2024-08-14 under one 2023-only training cutoff/freeze. '
       'The separate 2025 round uses eligible 2023/2024 training only. Persistence carries forward the same plot\'s latest prior value. '
       'Equal-plot training mean first averages prior reported pH within each training plot, then weights those plot means equally. '
       'This arithmetic is a descriptive pH-scale baseline, not an average of hydrogen concentrations. Both methods require a known training plot; '
       'training membership does not depend on later target presence or outcomes.'),
      ('Interpretation and source-method limits',
       'Source outcomes had already been viewed: the design is retrospective and unblinded. A prediction-file freeze verifies execution order, '
       'not preregistration, historical assay availability or untouched validation. Compatibility is reported definition only; assay calibration '
       'and interyear/depth sampling equivalence are not established. S33/S37 print 0-8 with no depth unit; S22 prints 0-8 in. '
       'Historical assay-result availability is not verified. Raw method cells remain unchanged, including the blank 2025 method cell; '
       'the separately reviewed 1:1 definition is PDF-supported. Source sample labels are not inferred treatment assignments. '
       'No causal effect, tuned/trend model, p-value, calibrated interval, pooled-fold winner, prospective/external validation or ten-year forecast is claimed.'),
      ('Complete companion evidence',
       'This compact report presents individual recorded visits, every method/paired/support row, every frozen-response hold/absent record '
       'and every unscored target record. Outside-scope and transcription-copy audit rows remain in the complete observation-audit companion. '
       'Full raw fields, training membership, per-plot training bases, complete target errors, observed changes and source reason codes remain '
       'in the exact hashed companions. No response values or metrics are recomputed by this renderer. '
       'Engine manifest: audit/PH_ENGINE_OUTPUT_MANIFEST.csv, SHA256 '+engine['manifest_sha256']+'.\n'+ '\n'.join(references))]


def preflight(run,intro,sections):
    # These fixed text-only stress checks run only after the genuine Puma guard.
    stress=section('Fixed-text multiline layout check','Layout text only; no scientific values.',
        'not_a_scientific_fixture',['Narrow multiline header requiring several explicit lines',
        'Full method / support / disposition reason retained'],[80,640],
        [['Fixed row identifier','Long source reason retained in full. '*160]],['fixed_text'])
    test=measure_table(stress)
    require(test['header_height']>28 and len(test['fragments'])>=2 and
            any(r['continued'] for r in test['fragments']),'Fixed-text dynamic layout check failed.')
    pages,receipt=paginate(intro,sections)
    receipt.update(fixed_text_multiline_header_check_passed=True,fixed_text_long_row_continuation_passed=True,
        report_layout_checks_passed=True,report_displays_every_projected_row=True,
        observation_display_is_individual_visit_projection_not_aggregation=True,
        projected_fields=[{'companion':s['companion'],'displayed_fields':s['displayed_fields'],
                          'full_discrete_fields_in_pinned_companion':True} for s in sections])
    return pages,receipt


def main():
    require(len(sys.argv)==2,'Usage: render_ph_report.py RUN_PATH')
    run=Path(sys.argv[1]).resolve(strict=True)
    require(run.is_dir() and run==Path(__file__).resolve().parent,'Use the isolated current-run renderer copy.')
    cfg=load_json(safe_file(run,'CONFIG.json'))
    require(cfg.get('package')==PACKAGE and cfg.get('user_intent',{}).get('new_stage_authorized') is True and
            cfg['user_intent'].get('current_files_only') is True and cfg.get('baseline_point_diagnostics_authorized') is True and
            cfg.get('reported_ph_arithmetic_training_baseline_authorized') is True,'Authorized reported-pH scope changed.')
    for key in ('statistical_tests_authorized','confidence_or_prediction_intervals_authorized',
                'numerical_forecast_execution_in_this_stage','predictive_validation_established','ten_year_forecast_generated'):
        require(cfg.get(key) is False,'Configured restricted scope differs: '+key)
    contract=load_json(safe_file(run,'OUTPUT_CONTRACT.json')); spec=load_json(safe_file(run,'RESPONSE_SPEC.json'))
    sentinel=run/contract['completion_sentinel_path']; status_path=run/contract['report_status_path']
    report=run/REPORT_RELATIVE; layout_path=run/LAYOUT_RELATIVE; manifest_path=run/contract['report_manifest_path']
    require(not any(p.exists() for p in (sentinel,status_path,report,layout_path,manifest_path,run/'figures')),
            'Never overwrite scientific reports; submit a new isolated Puma run.')
    package=verify_package(run); runtime=validate_environment(run,cfg); sources=verify_sources(run,cfg)
    engine=verify_engine(run,contract,spec)
    tables,observations,metrics,groups,all_targets=validate_display_rows(engine,spec)
    fonts=register_fonts(); sections=report_sections(tables,observations,spec)
    pages,layout=preflight(run,intro_blocks(run,engine,spec),sections)
    plt.rcParams.update({'font.family':'DejaVu Sans','pdf.fonttype':42,'axes.labelcolor':INK,
        'text.color':INK,'axes.unicode_minus':False,'savefig.facecolor':'white'})
    (run/'figures').mkdir()
    checks=[observed_figure(run/FIGURE_NAMES[0],observations,spec)]
    for i,fold in enumerate(spec['folds']):
        checks.append(scatter_figure(run/FIGURE_NAMES[1+i],fold,spec,observations,metrics,groups))
        checks.append(error_figure(run/FIGURE_NAMES[3+i],fold,metrics,all_targets))
    require(all(len(PdfReader(run/p).pages)==1 for p in FIGURE_NAMES),'Every vector figure must have one page.')
    temporary=run/'results/_ph_report_text_pages.pdf'
    render_text(temporary,pages)
    require(len(PdfReader(temporary).pages)==layout['text_pages'],'Measured text-page count differs.')
    writer=PdfWriter()
    for path in [temporary,*[run/p for p in FIGURE_NAMES]]:
        for page in PdfReader(path).pages: writer.add_page(page)
    writer.add_metadata({'/Title':'Whipstone Annual reported pH: observed plots and retrospective baselines',
        '/Subject':'Separate target-round known-plot point diagnostics; no predictive validation or ten-year forecast'})
    with report.open('wb') as stream: writer.write(stream)
    temporary.unlink()
    count=len(PdfReader(report).pages)
    require(count==layout['text_pages']+5,'Combined report page count differs.')
    layout.update(report_pages=count,figure_pages=5,figure_checks=checks,fonts_embedded=True,embedded_font_files=fonts,
                  scientific_figures_generated=True,inferential_or_forecast_figures_generated=False)
    save(layout_path,layout)
    output=[]
    for p in [REPORT_RELATIVE,LAYOUT_RELATIVE,*FIGURE_NAMES]:
        fp=regular_output(run,p)
        output.append({'relative_path':p,'sha256':sha(fp),'size_bytes':fp.stat().st_size,
            'file_type':'layout_receipt' if p==LAYOUT_RELATIVE else 'vector_scientific_figure_pdf' if p.startswith('figures/') else 'compact_review_pdf'})
    table(manifest_path,output,['relative_path','sha256','size_bytes','file_type'])
    require(verify_package(run)==package and validate_environment(run,cfg)==runtime and
            verify_sources(run,cfg)==sources and verify_engine(run,contract,spec)==engine,
            'Current runtime/package/source/engine byte evidence changed during reporting.')
    require(all(sha(Path(f['path']))==f['sha256'] for f in fonts),'Embedded font sources changed.')
    _,final_outputs=read_csv(manifest_path,contract['manifest_columns'])
    require(len(final_outputs)==7 and {r['relative_path'] for r in final_outputs}==set(contract['report_output_files']),
            'Exact seven report outputs differ.')
    for row in final_outputs:
        fp=regular_output(run,row['relative_path'])
        require(sha(fp)==row['sha256'] and fp.stat().st_size==integer(row['size_bytes'],'report size'),
                'Report output changed before completion.')
    final=dict(status=REPORT_SUCCESS,execution=execution,completed_utc=datetime.now(timezone.utc).isoformat(),
        report_relative_path=REPORT_RELATIVE,report_path=REPORT_RELATIVE,report_sha256=sha(report),report_pages=count,
        figure_count=5,figures_generated=True,report_output_manifest_path=contract['report_manifest_path'],
        report_output_manifest_sha256=sha(manifest_path),output_manifest_path=contract['report_manifest_path'],
        output_manifest=contract['report_manifest_path'],output_manifest_sha256=sha(manifest_path),report_output_files_verified=7,
        engine_status_sha256=engine['status_sha256'],engine_output_manifest_path=contract['engine_manifest_path'],
        engine_output_manifest_sha256=engine['manifest_sha256'],engine_output_files_verified=len(engine['manifest']),
        report_layout_checks_passed=True,report_layout_receipt_path=LAYOUT_RELATIVE,report_layout_receipt_sha256=sha(layout_path),
        source_integrity_passed=True,source_preservation_passed=True,source_files_verified_unchanged_after_report=True,
        source_files_changed=False,source_stage_status_sha256=sources['source_stage_status_sha256'],
        source_manifest_sha256=sources['source_manifest_sha256'],review_lock_manifest_sha256=sources['review_lock_manifest_sha256'],
        validation_output_manifest_sha256=sources['validation_output_manifest_sha256'],
        source_preservation_path=contract['source_preservation_path'],source_preservation_sha256=sha(safe_file(run,contract['source_preservation_path'])),
        response_spec_sha256=sha(safe_file(run,'RESPONSE_SPEC.json')),output_contract_sha256=sha(safe_file(run,'OUTPUT_CONTRACT.json')),
        report_stage_fits_predictions_errors_or_metrics_computed=False,eligibility_recomputed_by_report=False,
        forecasts_generated=False,numerical_ten_year_forecast=False,statistical_tests_performed=False,
        rounds_pooled_as_independent=False,winner_selected=False,historical_statuses_modified=False,
        user_authorized_work=True,current_files_only=True,source_inventory_job_id='24147988',
        inference='LIMITED_RETROSPECTIVE_UNBLINDED_KNOWN_PLOT_REPORTED_PH_POINT_DIAGNOSTICS_ONLY',
        **contract['report_required_flags'])
    save(status_path,final)
    lines=[line.format(current_main_job_id=execution['job_id'],report_status_sha256=sha(status_path))
           for line in contract['completion_sentinel_lines']]
    require(len(lines)==2,'Completion sentinel contract must have exactly two lines.')
    temporary_sentinel=sentinel.with_name(sentinel.name+'.tmp')
    temporary_sentinel.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    temporary_sentinel.replace(sentinel)  # Last filesystem write by the renderer.
    print(json.dumps(final,indent=2,allow_nan=False))


if __name__=='__main__':
    main()

