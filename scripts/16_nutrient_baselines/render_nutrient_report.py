#!/usr/bin/env python3
"""Puma-only presentation of frozen Whipstone Annual LOI organic-matter and KCl-nitrate diagnostics.

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

PACKAGE = 'NRCS_Whipstone_OM_NO3_baselines_HPC_v1'
REPORT_SUCCESS = 'WHIPSTONE_OM_NO3_BASELINES_REPORT_COMPLETED_REVIEW_PENDING'
METHODS = ('PERSISTENCE', 'EQUAL_PLOT_TRAINING_MEAN')
METHOD_LABEL = {'PERSISTENCE': 'Persistence', 'EQUAL_PLOT_TRAINING_MEAN': 'Equal-plot training mean'}
REPORT_RELATIVE = 'results/NRCS_Whipstone_OM_NO3_baselines_review.pdf'
LAYOUT_RELATIVE = 'audit/report_layout_receipt.json'
TRACK_IDS = ('W_OM', 'W_NO3')
TRACK_LABEL = {'W_OM':'LOI organic matter', 'W_NO3':'KCl-extracted nitrate-N'}
ERROR_UNIT = {'W_OM':'percentage points', 'W_NO3':'ppm N'}
METRIC_DISPLAY_DECIMALS = {'W_OM':3, 'W_NO3':2}
METRIC_DISPLAY_NOTE = ('Displayed MAE/RMSE are rounded to 3 decimals for OM and 2 for nitrate-N; '
    'exact saved values remain in the unchanged CSV companion. This is display rounding only, not assay precision.')
FIGURE_NAMES = tuple('figures/whipstone_annual_'+track+'_'+suffix+'.pdf'
    for track in TRACK_IDS for suffix in ('observed_trajectories','2024_observed_vs_baselines','2024_absolute_errors'))
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


def display_metric(value, track):
    """Format a saved metric for display; preserve a saved empty metric as empty."""
    require(track in METRIC_DISPLAY_DECIMALS, 'Unknown response for metric display.')
    if value == '':
        return ''
    return format(finite(value, 'saved metric display'), '.'+str(METRIC_DISPLAY_DECIMALS[track])+'f')


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
             'render_nutrient_report.py','hpc_guard.py','common.py','nutrient_core.py','analyze_nutrients.py','nutrient_self_checks.py',
             'stage_source.py','environment_check.py'} <= seen, 'Required source is not package-locked.')
    return {'manifest_sha256': sha(manifest), 'rows': rows}


def verify_sources(run, cfg):
    integrity = verify_staged_sources(run, cfg)
    require(integrity and all(r['status'] == 'UNCHANGED' for r in integrity), 'Source byte preservation failed.')
    path = safe_file(run, 'audit/SOURCE_STAGE_STATUS.json')
    stage = load_json(path)
    same_execution(stage.get('execution'), 'source staging')
    require(stage.get('status') == cfg['stage_success_status'] == 'MEASURED_OM_NO3_SOURCES_STAGED_VERIFIED' and
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
    prior_path=safe_file(run,cfg['prior_ph_acceptance_path'])
    prior=load_json(prior_path)
    require(stage.get('latest_prior_ph_job_id')=='24149165' and
            stage.get('prior_ph_acceptance_path')==cfg['prior_ph_acceptance_path'] and
            stage.get('prior_ph_acceptance_sha256')==cfg['prior_ph_acceptance_sha256']==sha(prior_path) and
            prior.get('status')=='ACCEPTED_WHIPSTONE_ANNUAL_PH_RETROSPECTIVE_POINT_DIAGNOSTICS' and
            prior.get('main_execution',{}).get('job_id')=='24149165' and
            prior.get('predictive_validation_established') is False and prior.get('ten_year_forecast_generated') is False,
            'Prior pH scoped acceptance linkage differs.')
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
    c.drawString(LEFT,21,'Puma | separate OM / nitrate known-plot diagnostics | no causal or forecast claim | review pending')
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
    c.setTitle('Whipstone Annual: LOI organic matter and KCl nitrate baseline diagnostics')
    for i,page in enumerate(pages,1):
        heading(c,'Whipstone Annual: organic matter and nitrate',
                'Separate response units; whole 2024 annual target round trained from 2023 only; retrospective point diagnostics',i)
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


def response_map(spec):
    responses=spec.get('responses')
    require(isinstance(responses,list) and len(responses)==2 and
            {r.get('track_id') for r in responses}==set(TRACK_IDS),'Exact separate response specification differs.')
    result={r['track_id']:r for r in responses}
    require(all(r.get('reported_unit') and r.get('reported_response_definition') for r in responses),
            'Response unit/definition missing.')
    folds=spec.get('folds')
    require(isinstance(folds,list) and len(folds)==2 and
            {f.get('track_id') for f in folds}==set(TRACK_IDS),'Exact track-specific folds differ.')
    for f in folds:
        require(f.get('fold_id')==f['track_id']+'_2024_FROM_2023' and
                f.get('training_round_ids')==['2023'] and f.get('target_round_id')=='2024' and
                f.get('cutoff_date')=='2024-01-01','Whole 2024 annual-round training specification differs.')
    require({m['method_id'] for m in spec['methods']}==set(METHODS),'Fixed baseline methods differ.')
    return result


def per_track_receipts(status,spec,responses,dynamic):
    raw=status.get('per_track_status')
    require(isinstance(raw,list) and len(raw)==2,'Exact per-track receipt list missing.')
    require({r.get('track_id') for r in raw}==set(TRACK_IDS),'Per-track status set differs.')
    values={r['track_id']:r for r in raw}
    for track,r in values.items():
        require(r.get('fold_id')==track+'_2024_FROM_2023' and
                r.get('unit')==responses[track]['reported_unit'] and
                r.get('reported_response_definition')==responses[track]['reported_response_definition'] and
                r.get('status'),'Per-track receipt scope/unit/definition differs.')
        require(all(type(r.get(k)) is bool for k in dynamic),'Per-track computed flag is not Boolean.')
        require(len({r[k] for k in dynamic})==1,'Per-track baseline computed flags disagree.')
    for k in dynamic:
        require(type(status.get(k)) is bool and status[k]==any(r[k] for r in raw),
                'Global computed flag does not match actual per-track support: '+k)
    return values


def verify_engine(run,contract,spec):
    require(contract.get('package')==PACKAGE and contract.get('report_success_status')==REPORT_SUCCESS and
            len(contract['report_output_files'])==8 and
            set(contract['report_output_files'])=={REPORT_RELATIVE,LAYOUT_RELATIVE,*FIGURE_NAMES},
            'Frozen eight-file report contract differs.')
    require(contract['engine_status_path']=='audit/NUTRIENT_ENGINE_STATUS.json' and
            contract['engine_manifest_path']=='audit/NUTRIENT_ENGINE_OUTPUT_MANIFEST.csv' and
            contract['source_preservation_path']=='audit/NUTRIENT_SOURCE_PRESERVATION.json' and
            contract['self_check_receipt_path']=='audit/nutrient_self_checks.json' and
            len(contract['engine_output_files'])==16 and
            set(contract['engine_output_files'])==set(contract['csv_schemas'])|set(contract['json_schemas']),
            'Canonical engine evidence paths/set differ.')
    responses=response_map(spec)
    path=regular_output(run,contract['engine_status_path']); status=load_json(path)
    same_execution(status.get('execution'),'OM/nitrate engine')
    require(set(contract['engine_status_required_keys'])<=set(status) and
            status.get('status')==contract['engine_success_status']==
            'WHIPSTONE_OM_NO3_BASELINE_DIAGNOSTICS_COMPLETED_REVIEW_PENDING','Current engine did not complete.')
    for key,value in contract['successful_flags'].items():
        require(status.get(key) is value,'Fixed engine scope flag differs: '+key)
    dynamic=contract['dynamic_engine_flags']
    require(len(dynamic)==5 and set(dynamic)=={'models_fitted','baseline_models_fitted','baseline_parameters_computed',
            'baseline_predictions_computed','training_baseline_means_computed'},'Dynamic computed flag contract differs.')
    tracks=per_track_receipts(status,spec,responses,dynamic)
    require(status.get('response_spec_sha256')==sha(safe_file(run,'RESPONSE_SPEC.json')) and
            status.get('output_contract_sha256')==sha(safe_file(run,'OUTPUT_CONTRACT.json')),
            'Current engine protocol/contract pins differ.')
    mp=regular_output(run,contract['engine_manifest_path'])
    require(status.get('engine_output_manifest_path')==contract['engine_manifest_path'] and
            status.get('engine_output_manifest_sha256')==sha(mp),'Engine manifest link differs.')
    _,manifest=read_csv(mp,contract['manifest_columns']); expected=set(contract['engine_output_files'])
    require(len(manifest)==len(expected) and {r['relative_path'] for r in manifest}==expected,
            'Exact engine manifest set differs/contains duplicates.')
    for r in manifest:
        fp=regular_output(run,r['relative_path'])
        require(sha(fp)==r['sha256'] and fp.stat().st_size==integer(r['size_bytes'],'engine size'),
                'Engine output hash/size differs.')
    tables={}; receipts={}
    for relative,d in contract['csv_schemas'].items():
        fields,rows=read_csv(regular_output(run,relative),d['required_columns'])
        require(d.get('additional_source_provenance_columns_allowed') is True or fields==d['required_columns'],
                'Undeclared CSV columns.')
        tables[relative]=(fields,rows)
    for relative,d in contract['json_schemas'].items():
        value=load_json(regular_output(run,relative))
        require(set(d['required_keys'])<=set(value),'Engine JSON schema missing: '+relative)
        receipts[relative]=value
    preserve=receipts[contract['source_preservation_path']]
    same_execution(preserve.get('execution'),'source preservation')
    require(preserve.get('status')=='ALL_SOURCE_BYTES_AND_ORIGINAL_RESPONSE_ROWS_VERIFIED_UNCHANGED' and
            preserve.get('source_integrity_passed') is True and
            preserve.get('source_files_verified_unchanged') is True and preserve.get('source_files_changed') is False and
            preserve.get('source_measurement_values_modified') is False and
            status.get('source_preservation_sha256')==sha(safe_file(run,contract['source_preservation_path'])),
            'Engine source-preservation proof differs.')
    fixtures=receipts[contract['self_check_receipt_path']]
    same_execution(fixtures.get('execution'),'Puma nutrient scientific fixtures')
    require(fixtures.get('status')=='PASS' and fixtures.get('passed') is True and fixtures.get('checks') and
            all(r.get('passed') is True for r in fixtures['checks']) and
            fixtures.get('fixture_kind')=='SCIENTIFIC_SYNTHETIC_EDGE_CASES_HPC_ONLY' and
            fixtures.get('scientific_source_data_read') is False and
            fixtures.get('response_spec_sha256')==sha(safe_file(run,'RESPONSE_SPEC.json')) and
            fixtures.get('output_contract_sha256')==sha(safe_file(run,'OUTPUT_CONTRACT.json')) and
            status.get('fixture_receipt_sha256')==sha(safe_file(run,contract['self_check_receipt_path'])),
            'Puma scientific fixture receipt differs.')
    freeze=receipts['results/prediction_freeze.json']; same_execution(freeze.get('execution'),'prediction freeze')
    training={p:sha(safe_file(run,p)) for p in ('results/training_membership.csv','results/training_plot_means.csv')}
    expected_folds=[{**f,'unit':responses[f['track_id']]['reported_unit'],
                    'reported_response_definition':responses[f['track_id']]['reported_response_definition']} for f in spec['folds']]
    actual_folds=freeze.get('folds')
    require(isinstance(actual_folds,list) and len(actual_folds)==2 and
            {f.get('fold_id') for f in actual_folds}=={f['fold_id'] for f in expected_folds},'Exact freeze-fold set differs.')
    for expected_fold in expected_folds:
        actual=next(f for f in actual_folds if f['fold_id']==expected_fold['fold_id'])
        require(all(actual.get(k)==expected_fold[k] for k in ('track_id','fold_id','training_round_ids','target_round_id',
                'cutoff_date','unit','reported_response_definition')),'Freeze track/fold/cutoff/definition metadata differs.')
    require(freeze.get('status')=='ALL_DECLARED_ROUND_PREDICTIONS_FROZEN_BEFORE_TARGET_SCORING' and
            freeze.get('training_files')==training and
            status.get('prediction_freeze_sha256')==sha(safe_file(run,'results/prediction_freeze.json')) and
            freeze.get('freeze_before_any_target_scoring') is True and
            freeze.get('target_outcome_used_for_training') is False and
            freeze.get('retrospective_execution_order_only') is True and freeze.get('source_outcomes_previously_viewed') is True and
            freeze.get('predictions_sha256')==sha(safe_file(run,'results/predictions.csv')) and
            freeze.get('fits_sha256')==sha(safe_file(run,'results/fits.json')),'Target-blind saved freeze links differ.')
    fits=receipts['results/fits.json']
    require(fits.get('status')=='TRAINING_ONLY_BASELINE_PARAMETERS_FROZEN' and
            fits.get('reported_scale')=='OWN_ORIGINAL_REPORTED_RESPONSE_UNITS' and
            fits.get('per_track_status')==status['per_track_status'] and
            fits.get('future_target_membership_or_value_used_for_training') is False and
            fits.get('methods')==list(METHODS),'Fits do not retain target-blind prespecified baselines.')
    return {'status':status,'status_sha256':sha(path),'manifest':manifest,'manifest_sha256':sha(mp),
            'tables':tables,'receipts':receipts,'per_track':tracks,'responses':responses}


def validate_display_rows(engine,spec):
    tables={p:r for p,(_fields,r) in engine['tables'].items()}; responses=engine['responses']
    observations=tables['results/eligible_observations.csv']; errors=tables['results/plot_errors.csv']
    folds={f['fold_id']:f for f in spec['folds']}; rounds={r['round_id']:set(r['actual_collection_dates']) for r in spec['rounds']}
    require({'2023','2024'}<=set(rounds) and rounds['2024']=={'2024-07-06','2024-08-14'},'Actual target dates differ.')
    obs_by_id={}; seen_events=set()
    for r in observations:
        track=r['track_id']; key=(track,r['observation_id'])
        require(track in TRACK_IDS and r['observation_id']==r['inventory_row_id'] and key not in obs_by_id,
                'Observed source identity missing/duplicated.')
        require(r['confirmed_physical_plot_id'] and r['annual_round_id'] in ('2023','2024') and
                r['collection_date'] in rounds[r['annual_round_id']] and r['unit']==responses[track]['reported_unit'] and
                r['reported_response_definition']==responses[track]['reported_response_definition'],
                'Observed track/plot/date/unit/definition differs.')
        event=(track,r['confirmed_physical_plot_id'],r['annual_round_id'])
        require(event not in seen_events,'Two actual eligible events for a track/plot/annual round.')
        seen_events.add(event); finite(r['reported_value'],'saved observed coordinate'); obs_by_id[key]=r
    metrics={}; groups={}; targets={}
    for r in tables['results/performance.csv']:
        key=(r['track_id'],r['method']); fold=folds.get(r['fold_id'])
        require(key[0] in TRACK_IDS and key[1] in METHODS and key not in metrics and fold and
                fold['track_id']==key[0] and r['target_round_id']=='2024' and
                r['unit']==responses[key[0]]['reported_unit'],'Metric scope/unit differs.')
        require(csv_bool(r['folds_independent'],'fold independence') is False,'Tracks/folds pooled as independent.')
        n=integer(r['n_scored_plots'],'method n')
        if n:
            require('NOT_ESTIMATED' not in r['status'],'Positive support marked not estimated.')
            for f in ('MAE','RMSE'): require(finite(r[f],'saved '+f)>=0,'Negative saved error metric.')
        else:
            require(r['MAE']==r['RMSE']=='' and 'NOT_ESTIMATED' in r['status'] and r['reason'],
                    'Zero support must retain blank metrics and complete skip reason.')
        require(r['reason'],'Missing saved metric support reason.')
        metrics[key]=r; groups[key]=[]; targets[key]=[]
    require(set(metrics)=={(t,m) for t in TRACK_IDS for m in METHODS},'Complete track/method metrics missing.')
    predictions={}
    for r in tables['results/predictions.csv']:
        key=(r['track_id'],r['method'],r['confirmed_physical_plot_id']); fold=folds.get(r['fold_id'])
        require(key not in predictions and key[0] in TRACK_IDS and key[1] in METHODS and fold and
                fold['track_id']==key[0] and r['target_round_id']=='2024' and r['cutoff_date']=='2024-01-01',
                'Frozen prediction identity/cutoff differs.')
        finite(r['predicted_value'],'saved prediction coordinate'); predictions[key]=r
    seen=set()
    for r in errors:
        key=(r['track_id'],r['method']); fold=folds.get(r['fold_id'])
        identity=(*key,r['target_record_id'],r['target_observation_id'])
        require(key in metrics and identity not in seen and fold and fold['track_id']==key[0] and
                r['target_round_id']=='2024' and r['unit']==responses[key[0]]['reported_unit'],
                'Target ledger identity/scope/unit differs.')
        seen.add(identity); targets[key].append(r)
        scored=csv_bool(r['scored'],'saved score flag')
        if scored:
            require(all(csv_bool(r[f],f) for f in ('target_eligible','known_training_plot','prediction_available')),
                    'Scored target support flags disagree.')
            obs=obs_by_id.get((key[0],r['target_observation_id'])); prediction=predictions.get((*key,r['confirmed_physical_plot_id']))
            require(obs and prediction and all(obs[a]==r[b] for a,b in (
                ('record_id','target_record_id'),('confirmed_physical_plot_id','confirmed_physical_plot_id'),
                ('collection_date','collection_date'),('reported_value','observed_value'))) and
                r['predicted_value']==prediction['predicted_value'],'Saved scored values lose exact observation/freeze links.')
            require(r['collection_date'] in rounds['2024'],'Actual target date differs.')
            for f in ('observed_value','predicted_value','signed_error','absolute_error','squared_error'): finite(r[f],'saved '+f)
            groups[key].append(r)
        else:
            require(r['absolute_error']==r['signed_error']==r['squared_error']=='' and r['reason_codes'],
                    'Unscored target has metrics or lacks skip reasons.')
    for key,rows in groups.items():
        require(len(rows)==integer(metrics[key]['n_scored_plots'],'saved n') and
                len({r['confirmed_physical_plot_id'] for r in rows})==len(rows),'Saved metric cohort differs.')
        declared=next(r['record_ids'] for r in spec['rounds'] if r['round_id']=='2024')
        require(declared and len(declared)==len(set(declared)) and
                {r['target_record_id'] for r in targets[key]}==set(declared),'Complete declared target ledger differs.')
    paired=set()
    for r in tables['results/paired_performance.csv']:
        key=(r['track_id'],r['method']); require(key in metrics and key not in paired,'Paired metric identity differs.')
        paired.add(key); sets=[{p['confirmed_physical_plot_id'] for p in groups[(key[0],m)]} for m in METHODS]
        n=integer(r['n_common_plots'],'paired n')
        require(n==len(sets[0]&sets[1]) and r['fold_id']==key[0]+'_2024_FROM_2023' and r['target_round_id']=='2024' and
                r['unit']==responses[key[0]]['reported_unit'] and
                r['reported_response_definition']==responses[key[0]]['reported_response_definition'],
                'Same-round common support/unit/definition differs.')
        if n:
            for f in ('MAE','RMSE'): require(finite(r[f],'saved paired metric')>=0,'Negative paired metric.')
        else: require(r['MAE']==r['RMSE']=='' and 'NOT_ESTIMATED' in r['status'],'Missing paired skip status.')
        require(r['reason'] and r['pairing_scope'],'Paired scope/reason missing.')
    require(paired==set(metrics),'Complete paired track/method rows missing.')
    for track in TRACK_IDS:
        computed=engine['per_track'][track]['baseline_predictions_computed']
        require(computed==any(k[0]==track for k in predictions),'Per-track computed receipt conflicts with saved predictions.')
    return tables,observations,metrics,groups,targets


def apply_axes(ax):
    ax.spines[['top','right']].set_visible(False); ax.grid(axis='y',color='#DDE5ED',linewidth=.6,zorder=0)
    ax.tick_params(labelsize=8); ax.xaxis.set_major_formatter(FuncFormatter(compact)); ax.yaxis.set_major_formatter(FuncFormatter(compact))


def figure_bounds(fig,context):
    fig.canvas.draw(); renderer=fig.canvas.get_renderer(); box=fig.bbox; artists=list(fig.texts)
    for legend in fig.legends: artists.extend(legend.get_texts()); artists.append(legend.get_title())
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
    receipt=figure_bounds(fig,context); fig.savefig(path,format='pdf',facecolor='white'); plt.close(fig); return receipt


def observed_figure(path,track,observations,response,spec):
    rows=[r for r in observations if r['track_id']==track]; plots=sorted({r['confirmed_physical_plot_id'] for r in rows})
    require(len(plots)<=16,'Frozen physical-plot figure needs a separately reviewed larger layout.')
    ncols=4; nrows=max(1,math.ceil(len(plots)/ncols)); unit=response['reported_unit']
    fig,axes=plt.subplots(nrows,ncols,figsize=(11,8.5),squeeze=False)
    fig.subplots_adjust(left=.085,right=.97,bottom=.16,top=.83,wspace=.48,hspace=1.08)
    fig.suptitle('Whipstone Annual: '+TRACK_LABEL[track]+' observed plots',fontsize=14,y=.968)
    fig.text(.5,.925,'Actual 2023/2024 dates; unchanged reported values. Segments connect adjacent observed rounds only.',ha='center',fontsize=8.7)
    fig.text(.5,.092,'Units: '+unit+'. Separate response definition; assay calibration and sampling-depth equivalence are not established.',ha='center',fontsize=8.3)
    fig.text(.5,.065,'2025 comparable source form is absent: no value or target diagnostic is imputed. No fit, trend or forecast.',ha='center',fontsize=8.3)
    values=[finite(r['reported_value'],'saved observation') for r in rows]
    padding=max(.05,(max(values)-min(values))*.10) if values else .05
    low,high=(min(values)-padding,max(values)+padding) if values else (0,1)
    for index,ax in enumerate(axes.flat):
        if index>=len(plots):
            ax.axis('off')
            if not plots and index==0: ax.text(.5,.5,'No eligible recorded observations.\nSee the saved source hold ledger.\nNo missing value is drawn as zero.',ha='center',va='center',transform=ax.transAxes,fontsize=9)
            continue
        plot=plots[index]; points=sorted((r for r in rows if r['confirmed_physical_plot_id']==plot),key=lambda r:r['collection_date'])
        for left,right in zip(points,points[1:]):
            if left['annual_round_id']=='2023' and right['annual_round_id']=='2024':
                ax.plot([date.fromisoformat(left['collection_date']),date.fromisoformat(right['collection_date'])],
                    [finite(left['reported_value'],'value'),finite(right['reported_value'],'value')],color='#8AA0B0',linewidth=1,zorder=2)
        xs=[date.fromisoformat(r['collection_date']) for r in points]
        ax.scatter(xs,[finite(r['reported_value'],'value') for r in points],color=COLORS[0],s=25,zorder=3)
        ax.set_title('\n'.join(textwrap.wrap(plot,35)),fontsize=7.6,pad=9)
        ax.set_ylim(low,high); ax.set_xticks(xs); ax.set_xticklabels([d.strftime('%Y\n%m-%d') for d in xs],fontsize=6.8)
        ax.yaxis.set_major_formatter(FuncFormatter(compact)); ax.tick_params(axis='y',labelsize=7)
        ax.spines[['top','right']].set_visible(False); ax.grid(axis='y',color='#E2E8EE',linewidth=.6); ax.margins(x=.13)
        if index%ncols==0: ax.set_ylabel(unit,fontsize=8,labelpad=7)
    return finish_figure(fig,path,track+' observed plot trajectories')


def scatter_figure(path,track,response,metrics,groups):
    rows=[r for m in METHODS for r in groups[(track,m)]]; unit=response['reported_unit']
    dates=sorted({r['collection_date'] for r in rows}); markers={d:m for d,m in zip(dates,('o','s','^','D'))}
    fig,axes=plt.subplots(1,2,figsize=(11,8.5)); fig.subplots_adjust(left=.10,right=.95,bottom=.27,top=.80,wspace=.35)
    fig.suptitle('2024 Annual round: '+TRACK_LABEL[track]+' versus frozen baselines',fontsize=14,y=.968)
    fig.text(.5,.925,'2023 training only; 2024-07-06 and 2024-08-14 held together. Separate units; no pooling across responses.',ha='center',fontsize=8.7)
    values=[finite(r[k],'saved point') for r in rows for k in ('observed_value','predicted_value')]
    pad=max(.05,(max(values)-min(values))*.08) if values else .05
    lo,hi=(min(values)-pad,max(values)+pad) if values else (0,1)
    panel_headers=[]
    for ax,method in zip(axes,METHODS):
        metric=metrics[(track,method)]; points=groups[(track,method)]; apply_axes(ax)
        center=(ax.get_position().x0+ax.get_position().x1)/2
        title=fig.text(center,.875,METHOD_LABEL[method],ha='center',va='center',fontsize=12)
        metric_text=('n='+metric['n_scored_plots']+'; MAE='+display_metric(metric['MAE'],track)+
            '; RMSE='+display_metric(metric['RMSE'],track)) if points else 'n=0; not estimated'
        metric_header=fig.text(center,.832,metric_text,ha='center',va='center',fontsize=9)
        panel_headers.append((ax,title,metric_header))
        ax.set_xlabel('Frozen baseline ('+unit+')',fontsize=10,labelpad=9)
        ax.set_ylabel('Observed reported value ('+unit+')',fontsize=10,labelpad=9)
        if points:
            ax.plot([lo,hi],[lo,hi],color='#9AA7B2',linestyle='--',linewidth=.9,zorder=1)
            for r in points: ax.scatter(finite(r['predicted_value'],'prediction'),finite(r['observed_value'],'observation'),
                color=COLORS[0],marker=markers[r['collection_date']],s=45,edgecolors='white',linewidth=.5,zorder=3)
            ax.set_xlim(lo,hi); ax.set_ylim(lo,hi)
        else: ax.text(.5,.5,'Not estimated: n=0\n'+textwrap.fill(metric['reason'],36),ha='center',va='center',transform=ax.transAxes,fontsize=9)
    handles=[Line2D([],[],color=COLORS[0],marker=markers[d],linestyle='',label=d) for d in dates]
    if handles: fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.14),ncol=2,fontsize=8,frameon=False)
    fig.text(.5,.095,'Dashed line: identity reference, not a fit. Errors: '+ERROR_UNIT[track]+'. Display-rounded metrics; exact values in CSV.',ha='center',fontsize=8.3)
    fig.text(.5,.065,'Coincident points may overlap; exact physical plots/dates remain in pinned companions. No treatment labels inferred.',ha='center',fontsize=8.3)
    fig.text(.5,.035,'Retrospective unblinded reported-definition comparison; no causal effect, calibrated interval or prospective validation.',ha='center',fontsize=8.1)
    # Actual saved-text geometry is checked only on the guarded Puma allocation.
    # The reserved header row must remain outside every data axes, not merely
    # inside the overall PDF page boundary.
    fig.canvas.draw(); renderer=fig.canvas.get_renderer()
    for ax,title,metric_header in panel_headers:
        title_box=title.get_window_extent(renderer); metric_box=metric_header.get_window_extent(renderer)
        require(metric_box.y0>=ax.bbox.y1+4 and title_box.y0>=metric_box.y1+5,
                'Saved metric header overlaps data axes or method title: '+track)
        require(metric_box.x0>=ax.bbox.x0 and metric_box.x1<=ax.bbox.x1,
                'Saved metric header crosses its reserved panel width: '+track)
    receipt=finish_figure(fig,path,track+' 2024 observed versus baselines')
    receipt.update(saved_metric_headers_outside_data_axes=True,
        displayed_metric_decimal_places=METRIC_DISPLAY_DECIMALS[track],exact_metric_values_in_csv=True)
    return receipt


def error_figure(path,track,response,metrics,targets):
    saved={(r['target_record_id'],r['target_observation_id']):r for m in METHODS for r in targets[(track,m)]}
    records=sorted(saved,key=lambda k:(saved[k]['confirmed_physical_plot_id'],saved[k]['collection_date'],k))
    labels=[]
    for record in records:
        r=saved[record]; label=r['confirmed_physical_plot_id'] or 'Unconfirmed identity; record '+record[0]
        if sum(k[0]==record[0] for k in records)>1: label+=' | source '+(record[1] or '(expected absent)')
        labels.append('\n'.join(textwrap.wrap(label,48))+'\n'+(r['collection_date'] or '(date absent)'))
    # This sizing is text geometry only: held duplicate source rows are never
    # collapsed or omitted to satisfy an arbitrary fixed figure-row limit.
    row_points=max(34,max((len(s.splitlines())*10+12 for s in labels),default=34))
    height=max(8.5,len(records)*row_points/(72*.63)+1.0)
    fig,ax=plt.subplots(figsize=(11,height)); fig.subplots_adjust(left=.43,right=.95,bottom=.19,top=.82)
    fig.suptitle('2024 Annual round: '+TRACK_LABEL[track]+' saved absolute errors',fontsize=14,y=.968)
    fig.text(.5,.925,'2023-only frozen baselines; actual July/August dates retained. Missing dots do not mean zero error.',ha='center',fontsize=8.7)
    ax.spines[['top','right']].set_visible(False); ax.grid(axis='x',color='#DDE5ED',linewidth=.6)
    lookup={(r['method'],r['target_record_id'],r['target_observation_id']):r for m in METHODS for r in targets[(track,m)]}
    for i,record in enumerate(records):
        skip=[]
        for method,offset,color,marker in zip(METHODS,(-.13,.13),COLORS,('o','s')):
            e=lookup.get((method,*record)); require(e is not None,'Method missing from complete target ledger.')
            if csv_bool(e['scored'],'saved scored flag'): ax.scatter(finite(e['absolute_error'],'saved absolute error'),i+offset,s=38,color=color,marker=marker,zorder=3)
            else: skip.append(METHOD_LABEL[method]+': not scored; see ledger')
        if skip: ax.text(.02,i,'\n'.join(textwrap.fill(s,48) for s in skip),transform=ax.get_yaxis_transform(),fontsize=6.5,va='center')
    ax.set_yticks(range(len(records))); ax.set_yticklabels(labels,fontsize=7.5); ax.set_ylim(len(records)-.5,-.5)
    ax.xaxis.set_major_formatter(FuncFormatter(compact)); ax.set_xlabel('Absolute error ('+ERROR_UNIT[track]+')',fontsize=10,labelpad=10)
    if not records: ax.text(.5,.5,'No declared target rows recorded.\nSee saved support and skip tables.',transform=ax.transAxes,ha='center',va='center')
    handles=[Line2D([],[],color=c,marker=m,linestyle='',label=METHOD_LABEL[k]+'; n='+metrics[(track,k)]['n_scored_plots']) for k,c,m in zip(METHODS,COLORS,('o','s'))]
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.10),ncol=2,fontsize=9,frameon=False)
    fig.text(.5,.075,'Method-specific and same-round common-plot support are reported separately. No pooled response scores or winner.',ha='center',fontsize=8.5)
    fig.text(.5,.04,'All skip reasons remain in the report ledger. No imputation, fitted interval, trend or numerical forecast.',ha='center',fontsize=8.5)
    return finish_figure(fig,path,track+' 2024 absolute errors')


def section(title,subtitle,companion,headers,widths,rows,fields):
    return {'title':title,'subtitle':subtitle+' Companion: '+companion,'companion':companion,
            'headers':headers,'widths':widths,'rows':rows,'displayed_fields':fields,'size':8.0}


def report_sections(tables,observations,responses):
    sections=[]
    for track in TRACK_IDS:
        response=responses[track]; unit=response['reported_unit']; saved=[r for r in observations if r['track_id']==track]
        plots=sorted({r['confirmed_physical_plot_id'] for r in saved}); rows=[]
        for plot in plots:
            row=[plot]
            for annual in ('2023','2024'):
                visits=[r for r in saved if r['confirmed_physical_plot_id']==plot and r['annual_round_id']==annual]
                require(len(visits)<=1,'Individual source visit projection is ambiguous.')
                if visits:
                    v=visits[0]
                    row.append(v['collection_date']+' | '+v['reported_value']+' '+unit+'\n'+
                        v['record_id']+' | '+v['preferred_pdf_source_id']+' p. '+v['preferred_pdf_page']+'\n'+v['observation_id'])
                else: row.append('No eligible recorded visit.\nNo value imputed; see saved holds.')
            rows.append(row)
        sections.append(section(track+': individual recorded '+TRACK_LABEL[track]+' visits',
            response['reported_response_definition']+'; units '+unit+'. Each cell is one unchanged source observation/date/reference, not a value aggregate. No 2025 comparable form.',
            'results/eligible_observations.csv',['Confirmed physical plot','2023 actual visit / value / source','2024 actual visit / value / source'],
            [232,244,244],rows,['track_id','confirmed_physical_plot_id','annual_round_id','collection_date',
                'reported_value','unit','record_id','preferred_pdf_source_id','preferred_pdf_page','observation_id']))
    perf=tables['results/performance.csv']
    sections.append(section('Separate response: method-specific point diagnostics',
        'Whole 2024 round from 2023 only. OM MAE/RMSE: percentage points; nitrate-N: ppm N. No pooled scores or winner. '+METRIC_DISPLAY_NOTE,
        'results/performance.csv',['Track / reported unit','Method / target round','n','MAE','RMSE','Saved status / reason'],
        [105,154,35,86,86,254],[[r['track_id']+'\n'+ERROR_UNIT[r['track_id']],METHOD_LABEL[r['method']]+'\n2024 annual round',r['n_scored_plots'],
            display_metric(r['MAE'],r['track_id']),display_metric(r['RMSE'],r['track_id']),r['status']+'\n'+r['reason']] for r in perf],
        ['track_id','unit','fold_id','target_round_id','method','n_scored_plots','MAE','RMSE','status','reason']))
    paired=tables['results/paired_performance.csv']
    sections.append(section('Separate response: same-round common-plot paired diagnostics',
        'Same-response/round common targets only. OM errors: percentage points; nitrate-N: ppm N. No cross-response paired score. '+METRIC_DISPLAY_NOTE,
        'results/paired_performance.csv',['Track / unit','Method','Common n','MAE','RMSE','Saved status / pairing scope / reason'],
        [92,142,54,84,84,264],[[r['track_id']+'\n'+ERROR_UNIT[r['track_id']],METHOD_LABEL[r['method']],r['n_common_plots'],
            display_metric(r['MAE'],r['track_id']),display_metric(r['RMSE'],r['track_id']),
            r['status']+'\n'+r['pairing_scope']+'\n'+r['reason']] for r in paired],
        ['track_id','unit','fold_id','target_round_id','method','n_common_plots','MAE','RMSE','status','pairing_scope','reason']))
    support=tables['results/cohort_support.csv']
    sections.append(section('Track-level source and known-plot support',
        'Saved training observations, training plots, declared targets and same-round paired plots are distinct units. No inferential power or external validation.',
        'results/cohort_support.csv',['Track / fold / cutoff','Actual dates / unit','Saved training / target / scoring support','Saved status / reason'],
        [162,144,194,220],[[r['track_id']+'\n'+r['fold_id']+'\nCutoff '+r['cutoff_date'],
            'Training: '+r['training_actual_dates']+'\nTarget: '+r['target_actual_dates']+'\nUnit: '+r['unit'],
            'Training observations: '+r['n_training_observations']+'\nTraining plots: '+r['n_training_plots']+
            '\nDeclared target records: '+r['n_declared_target_records']+'\nEligible target plots: '+r['n_target_eligible_plots']+
            '\nKnown target plots: '+r['n_target_known_plots']+'\nPersistence scored: '+r['n_scored_persistence']+
            '\nMean scored: '+r['n_scored_equal_plot_training_mean']+'\nCommon paired plots: '+r['n_common_paired_plots'],
            r['status']+'\n'+r['reason']] for r in support],['track_id','fold_id','cutoff_date','unit','training_actual_dates','target_actual_dates',
             'n_training_observations','n_training_plots','n_declared_target_records','n_target_eligible_plots','n_target_known_plots',
             'n_scored_persistence','n_scored_equal_plot_training_mean','n_common_paired_plots','status','reason']))
    baseline=tables['results/baseline_support.csv']
    sections.append(section('Prespecified support gates, by response and method',
        'A support gate allows a descriptive computation only. Zero training support is retained honestly; both methods require the same known prior plot.',
        'results/baseline_support.csv',['Track / unit / method','Training n / plots','Saved gate / known-plot requirement','Saved reason'],
        [206,104,170,240],[[r['track_id']+' | '+r['unit']+'\n'+METHOD_LABEL[r['method']],
            r['n_training_observations']+' observations\n'+r['n_training_plots']+' plots',r['support_gate_status']+
            '\nKnown training plot: '+r['known_training_plot_required']+'\nMinimum prior: '+r['minimum_prior_observations_for_plot']+
            '\nMinimum plots: '+r['minimum_training_plots']+'\nPower/validation: '+r['power_or_validation_established'],r['reason']] for r in baseline],
        ['track_id','unit','method','n_training_observations','n_training_plots','support_gate_status','known_training_plot_required',
         'minimum_prior_observations_for_plot','minimum_training_plots','power_or_validation_established','reason']))
    held=[r for r in tables['results/observation_audit.csv'] if csv_bool(r['expected_source_row'],'saved response scope') and
        not csv_bool(r['eligible'],'saved observation eligibility')]
    sections.append(section('Frozen OM / nitrate source holds and absent records',
        'Every saved expected-source hold/absent row is shown. Complete audit companion retains outside-scope/copy rows; eligibility is not recomputed.',
        'results/observation_audit.csv',['Track / record / observation / physical plot','Actual date / raw value / unit','Saved status / all reason codes'],
        [270,145,305],[[r['track_id']+'\n'+r['record_id']+'\n'+r['observation_id']+'\n'+r['confirmed_physical_plot_id'],
            r['collection_date']+'\nRaw value: '+r['raw_value']+'\nRaw unit: '+r['raw_unit'],r['eligibility_status']+'\n'+
            r['eligibility_reason_codes']+'\n'+r['duplicate_or_conflict_status']] for r in held],
        ['track_id','expected_source_row','record_id','observation_id','confirmed_physical_plot_id','collection_date','raw_value',
         'raw_unit','eligibility_status','eligibility_reason_codes','duplicate_or_conflict_status']))
    skipped=[r for r in tables['results/plot_errors.csv'] if not csv_bool(r['scored'],'saved scored flag')]
    sections.append(section('Declared 2024 targets not scored',
        'Every unscored response/method/target record is shown, including source holds and cold starts. Missing values/errors are not zeros.',
        'results/plot_errors.csv',['Track / unit / method','Target record / physical plot / actual date','Saved status / all reason codes'],
        [208,242,270],[[r['track_id']+' | '+r['unit']+'\n'+METHOD_LABEL[r['method']],r['target_record_id']+'\n'+r['target_observation_id']+'\n'+
            r['confirmed_physical_plot_id']+'\n'+r['collection_date'],r['status']+'\n'+r['reason_codes']] for r in skipped],
        ['track_id','unit','method','target_record_id','target_observation_id','confirmed_physical_plot_id','collection_date','status','reason_codes']))
    return sections


def intro_blocks(engine):
    references=[]
    for r in engine['manifest']:
        if r['relative_path'] in ('results/eligible_observations.csv','results/observation_audit.csv','results/plot_errors.csv',
            'results/plot_changes.csv','results/prediction_freeze.json'):
            references.append(r['relative_path']+'\nSHA256 '+r['sha256'])
    definitions='; '.join(t+': '+engine['responses'][t]['reported_response_definition']+' ('+engine['responses'][t]['reported_unit']+')' for t in TRACK_IDS)
    flags='; '.join(t+': '+engine['per_track'][t]['status']+'; baseline parameters computed='+str(engine['per_track'][t]['baseline_parameters_computed']) for t in TRACK_IDS)
    return [
      ('Current execution and unchanged source provenance',
       'Puma job '+execution['job_id']+' on '+execution['node']+' rendered saved engine outputs from this allocation. '
       'Authoritative observations are unchanged corrected workbook rows from accepted measured inventory 24147988; laboratory representations '
       'remain audit copies, not additional observations. Original sources, staged copies, reviews, code/runtime receipts and engine files are '
       'reverified before and after reporting. Earlier failed/repaired/accepted runs and scheduler states remain unchanged.'),
      ('Two separate response definitions and one whole target round',
       definitions+'. Each track retains its own reported units, physical-plot identities and actual source dates. '
       'OM differences and error metrics are percentage points, not relative percent changes; nitrate-N errors are ppm N. No conversion is made. '
       '2024-07-06 and 2024-08-14 share the same 2023-only training cutoff and freeze for each response. No 2025 comparable LOI/KCl nitrate '
       'form is available here; no 2025 value, zero or diagnostic is invented. Persistence uses the same plot\'s latest eligible prior value. '
       'Equal-plot training mean averages prior values within each training plot and weights those plot means equally. Both are fixed known-plot '
       'descriptive point baselines. Training membership does not depend on future target presence or values. '+flags+'.'),
      ('Interpretation and method limitations',
       'This is a retrospective, unblinded design: source outcomes had already been viewed. The freeze records execution order, not '
       'preregistration, untouched validation or verified historical assay-result availability. LOI organic matter and KCl nitrate retain distinct '
       'source definitions; definition compatibility does not establish interyear calibration, sampling-depth equivalence or a soil-process model. '
       'S33/S37 depth text is 0-8 without a printed unit. Source sample labels are not inferred treatment assignments. A support gate establishes '
       'neither power nor predictive validity. No causal effect, tuned/trend/complex model, p-value, calibrated interval, cross-response pooled score, '
       'winner, prospective/external validation or numerical ten-year forecast is claimed.'),
      ('Complete companion evidence',
       'The report projects unchanged individual visits and all method/paired/support rows, expected-response holds and unscored target records. '
       'Complete outside-scope and transcription-copy audit rows, raw fields, training bases and per-target saved values/errors remain in the '
       'exact hashed companions. No source response or scientific metric is recomputed by this renderer. '
       'Engine manifest audit/NUTRIENT_ENGINE_OUTPUT_MANIFEST.csv, SHA256 '+engine['manifest_sha256']+'.\n'+'\n'.join(references))]


def preflight(intro,sections):
    stress=section('Fixed-text multiline layout check','Layout text only; no scientific values.',
        'not_a_scientific_fixture',['Narrow multiline header requiring several explicit lines','Full source/support/disposition reason retained'],
        [80,640],[['Fixed row identifier','Long source reason retained in full. '*160]],['fixed_text'])
    test=measure_table(stress)
    require(test['header_height']>28 and len(test['fragments'])>=2 and any(r['continued'] for r in test['fragments']),
            'Puma fixed-text dynamic layout check failed.')
    pages,receipt=paginate(intro,sections)
    receipt.update(fixed_text_multiline_header_check_passed=True,fixed_text_long_row_continuation_passed=True,
        report_layout_checks_passed=True,report_displays_every_projected_row=True,
        observation_display_is_individual_visit_projection_not_aggregation=True,
        projected_fields=[{'companion':s['companion'],'displayed_fields':s['displayed_fields'],
                          'full_discrete_fields_in_pinned_companion':True} for s in sections])
    return pages,receipt


def main():
    require(len(sys.argv)==2,'Usage: render_nutrient_report.py RUN_PATH')
    run=Path(sys.argv[1]).resolve(strict=True)
    require(run.is_dir() and run==Path(__file__).resolve().parent,'Use the isolated current-run renderer copy.')
    cfg=load_json(safe_file(run,'CONFIG.json'))
    require(cfg.get('package')==PACKAGE and cfg.get('user_intent',{}).get('new_stage_authorized') is True and
        cfg['user_intent'].get('current_files_only') is True and cfg.get('baseline_point_diagnostics_authorized') is True and
        cfg.get('reported_response_arithmetic_training_baseline_authorized') is True and cfg.get('training_baseline_means_authorized') is True,
        'Authorized reported-response baseline scope differs.')
    for k in ('statistical_tests_authorized','confidence_or_prediction_intervals_authorized',
              'numerical_forecast_execution_in_this_stage','predictive_validation_established','ten_year_forecast_generated'):
        require(cfg.get(k) is False,'Configured restricted scope differs: '+k)
    contract=load_json(safe_file(run,'OUTPUT_CONTRACT.json')); spec=load_json(safe_file(run,'RESPONSE_SPEC.json'))
    sentinel=run/contract['completion_sentinel_path']; status_path=run/contract['report_status_path']
    report=run/REPORT_RELATIVE; layout_path=run/LAYOUT_RELATIVE; manifest_path=run/contract['report_manifest_path']
    require(not any(p.exists() for p in (sentinel,status_path,report,layout_path,manifest_path,run/'figures')),
            'Never overwrite outputs; submit a new isolated Puma run.')
    package=verify_package(run); runtime=validate_environment(run,cfg); sources=verify_sources(run,cfg); engine=verify_engine(run,contract,spec)
    tables,observations,metrics,groups,targets=validate_display_rows(engine,spec)
    fonts=register_fonts(); sections=report_sections(tables,observations,engine['responses'])
    pages,layout=preflight(intro_blocks(engine),sections)
    plt.rcParams.update({'font.family':'DejaVu Sans','pdf.fonttype':42,'axes.labelcolor':INK,'text.color':INK,
                        'axes.unicode_minus':False,'savefig.facecolor':'white'})
    (run/'figures').mkdir(); checks=[]
    for i,track in enumerate(TRACK_IDS):
        response=engine['responses'][track]
        checks.extend([observed_figure(run/FIGURE_NAMES[i*3],track,observations,response,spec),
            scatter_figure(run/FIGURE_NAMES[i*3+1],track,response,metrics,groups),
            error_figure(run/FIGURE_NAMES[i*3+2],track,response,metrics,targets)])
    require(all(len(PdfReader(run/p).pages)==1 for p in FIGURE_NAMES),'Each vector figure must contain one page.')
    temporary=run/'results/_nutrient_report_text_pages.pdf'; render_text(temporary,pages)
    require(len(PdfReader(temporary).pages)==layout['text_pages'],'Measured text-page count differs.')
    writer=PdfWriter()
    for path in [temporary,*[run/p for p in FIGURE_NAMES]]:
        for page in PdfReader(path).pages: writer.add_page(page)
    writer.add_metadata({'/Title':'Whipstone Annual: LOI organic matter and KCl nitrate retrospective baselines',
        '/Subject':'Separate reported response units; 2023-only training to whole 2024 annual round; no validation or forecast claim'})
    with report.open('wb') as stream: writer.write(stream)
    temporary.unlink(); count=len(PdfReader(report).pages)
    require(count==layout['text_pages']+6,'Combined report page count differs.')
    layout.update(report_pages=count,figure_pages=6,figure_checks=checks,fonts_embedded=True,embedded_font_files=fonts,
        scientific_figures_generated=True,inferential_or_forecast_figures_generated=False,tracks_kept_separate=True,
        metric_display_decimal_places=METRIC_DISPLAY_DECIMALS,metric_display_note=METRIC_DISPLAY_NOTE,
        exact_saved_metric_csv_files_unchanged=True)
    save(layout_path,layout); outputs=[]
    for relative in [REPORT_RELATIVE,LAYOUT_RELATIVE,*FIGURE_NAMES]:
        fp=regular_output(run,relative)
        outputs.append({'relative_path':relative,'sha256':sha(fp),'size_bytes':fp.stat().st_size,
            'file_type':'layout_receipt' if relative==LAYOUT_RELATIVE else 'vector_scientific_figure_pdf' if relative.startswith('figures/') else 'review_pdf'})
    table(manifest_path,outputs,['relative_path','sha256','size_bytes','file_type'])
    require(verify_package(run)==package and validate_environment(run,cfg)==runtime and verify_sources(run,cfg)==sources and
            verify_engine(run,contract,spec)==engine,'Current package/runtime/source/engine evidence changed during reporting.')
    require(all(sha(Path(f['path']))==f['sha256'] for f in fonts),'Embedded font source bytes changed.')
    _,members=read_csv(manifest_path,contract['manifest_columns'])
    require(len(members)==8 and {r['relative_path'] for r in members}==set(contract['report_output_files']),
            'Exact eight report outputs differ.')
    for r in members:
        fp=regular_output(run,r['relative_path'])
        require(sha(fp)==r['sha256'] and fp.stat().st_size==integer(r['size_bytes'],'report size'),'Report output changed.')
    dynamic={k:engine['status'][k] for k in contract['dynamic_engine_flags']}
    final=dict(status=REPORT_SUCCESS,execution=execution,completed_utc=datetime.now(timezone.utc).isoformat(),
        report_relative_path=REPORT_RELATIVE,report_path=REPORT_RELATIVE,report_sha256=sha(report),report_pages=count,
        figure_count=6,figures_generated=True,report_output_manifest_path=contract['report_manifest_path'],
        report_output_manifest_sha256=sha(manifest_path),output_manifest_path=contract['report_manifest_path'],
        output_manifest=contract['report_manifest_path'],output_manifest_sha256=sha(manifest_path),report_output_files_verified=8,
        engine_status_sha256=engine['status_sha256'],engine_output_manifest_path=contract['engine_manifest_path'],
        engine_output_manifest_sha256=engine['manifest_sha256'],engine_output_files_verified=len(engine['manifest']),
        report_layout_checks_passed=True,report_layout_receipt_path=LAYOUT_RELATIVE,report_layout_receipt_sha256=sha(layout_path),
        source_integrity_passed=True,source_preservation_passed=True,source_files_verified_unchanged_after_report=True,
        source_files_changed=False,source_stage_status_sha256=sources['source_stage_status_sha256'],
        source_manifest_sha256=sources['source_manifest_sha256'],review_lock_manifest_sha256=sources['review_lock_manifest_sha256'],
        validation_output_manifest_sha256=sources['validation_output_manifest_sha256'],
        source_preservation_path=contract['source_preservation_path'],source_preservation_sha256=sha(safe_file(run,contract['source_preservation_path'])),
        response_spec_sha256=sha(safe_file(run,'RESPONSE_SPEC.json')),output_contract_sha256=sha(safe_file(run,'OUTPUT_CONTRACT.json')),
        per_track_status=engine['status']['per_track_status'],report_stage_fits_predictions_errors_or_metrics_computed=False,
        eligibility_recomputed_by_report=False,forecasts_generated=False,numerical_ten_year_forecast=False,statistical_tests_performed=False,
        response_tracks_pooled=False,rounds_pooled_as_independent=False,winner_selected=False,historical_statuses_modified=False,
        user_authorized_work=True,current_files_only=True,source_inventory_job_id='24147988',prior_ph_job_id='24149165',
        inference='LIMITED_RETROSPECTIVE_UNBLINDED_KNOWN_PLOT_SEPARATE_REPORTED_RESPONSE_POINT_DIAGNOSTICS_ONLY',
        **contract['report_required_flags'],**dynamic)
    save(status_path,final)
    lines=[line.format(current_main_job_id=execution['job_id'],report_status_sha256=sha(status_path)) for line in contract['completion_sentinel_lines']]
    require(len(lines)==2,'Completion sentinel must have exactly two lines.')
    tmp=sentinel.with_name(sentinel.name+'.tmp'); tmp.write_text('\n'.join(lines)+'\n',encoding='utf-8'); tmp.replace(sentinel)
    # Last filesystem write by renderer is the final sentinel replacement.
    print(json.dumps(final,indent=2,allow_nan=False))


if __name__=='__main__':
    main()


