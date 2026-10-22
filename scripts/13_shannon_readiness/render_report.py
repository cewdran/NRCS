#!/usr/bin/env python3
"""Render Puma-only retrospective annual-round backtests and forecast readiness.

Scientific values are read from pinned engine outputs. This stage performs no
response computation, baseline fitting, scoring, interval estimation or testing.
"""
from hpc_guard import require_hpc
execution = require_hpc()

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import csv
import importlib.metadata
import json
import math
import sys
import textwrap

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, FuncFormatter
from matplotlib.lines import Line2D
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, LongTable, TableStyle
from common import require, save, sha, table

METHODS = ('PERSISTENCE', 'EQUAL_PLOT_TRAINING_MEAN', 'EQUAL_PLOT_TRAINING_ARM_MEAN')
METHOD_LABEL = {'PERSISTENCE': 'Persistence', 'EQUAL_PLOT_TRAINING_MEAN': 'Equal-plot training mean',
                'EQUAL_PLOT_TRAINING_ARM_MEAN': 'Equal-plot training arm mean'}
METHOD_COLOR = dict(zip(METHODS, ('#166A8F', '#B96521', '#6F5094')))
METHOD_MARKER = dict(zip(METHODS, ('o', 's', '^')))
SCOPES = ('PRIMARY', 'DEPTH_SENSITIVITY')
SCOPE_LABEL = {'PRIMARY': 'Primary', 'DEPTH_SENSITIVITY': 'Depth sensitivity'}
DOMAINS = ('bacteria', 'archaea')
DOMAIN_LABEL = {'bacteria': 'Bacteria', 'archaea': 'Archaea'}
FOLDS = ('TRAIN2023_TEST2024_ANNUAL_ROUND', 'TRAIN2023_2024_TEST2025')
FOLD_LABEL = {FOLDS[0]: '2024 annual round: new backtest', FOLDS[1]: '2025 annual round: reused evidence'}
ORIGIN_KIND = {FOLDS[0]: 'NEW_2024_BACKTEST', FOLDS[1]: 'REUSED_2025_ENGINE'}
HELD_OUT_DATES = {FOLDS[0]: ('2024-07-06', '2024-08-14'), FOLDS[1]: ('2025-08-17',)}
CUTOFF = {FOLDS[0]: '2024-01-01', FOLDS[1]: '2025-01-01'}
BACKTEST_SUCCESS = 'TEMPORAL_BACKTEST_COMPLETED_REVIEW_PENDING'
REPORT_SUCCESS = 'TEMPORAL_BACKTEST_REPORT_COMPLETED_REVIEW_PENDING'
INK, MUTED = '#172738', '#536578'
ORIGIN_FIELDS = {'fold_id', 'origin_job_id', 'origin_kind'}
CSV_FIELDS = {
 'performance.csv': {'domain','scope','method','status','n_evaluated_plots','evaluation_plot_ids','MAE','RMSE','reason'} | ORIGIN_FIELDS,
 'paired_performance.csv': {'comparison_type','domain','scope','method_A','method_B','scope_A','scope_B',
     'n_common_plots','common_plot_ids','MAE_A','MAE_B','RMSE_A','RMSE_B','mean_delta_abs_error','delta_RMSE','status','reason'} | ORIGIN_FIELDS,
 'plot_errors.csv': {'domain','scope','method','biological_plot_id','treatment','held_out_date','predicted_Shannon',
     'observed_Shannon','signed_error','absolute_error','squared_error','last_prior_date','training_digest','prediction_digest'} | ORIGIN_FIELDS,
}

def safe_file(base, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and '..' not in relative.parts,
            'Unsafe manifest/source path: ' + str(relative))
    path = (base / relative).resolve(strict=True)
    require(path.is_file() and path.is_relative_to(base.resolve(strict=True)),
            'File is outside its verified root: ' + str(relative))
    return path

def read_csv(path, required):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)),
                'Missing or duplicate CSV header: ' + str(path))
        require(required.issubset(set(reader.fieldnames)),
                'Missing CSV fields in ' + str(path) + ': ' + repr(sorted(required - set(reader.fieldnames))))
        rows = list(reader)
    require(all(None not in row for row in rows), 'Malformed CSV row: ' + str(path))
    return rows

def finite(value, context):
    number = float(value)
    require(math.isfinite(number), 'Nonfinite number in ' + context)
    return number

def integer(value, context):
    number = finite(value, context)
    require(number >= 0 and number.is_integer(), 'Invalid nonnegative integer in ' + context)
    return int(number)

def compact(value, _position=None):
    """Readable labels without redundant trailing zeros or long tiny decimals."""
    if not math.isfinite(value):
        return ''
    if value == 0:
        return '0'
    if abs(value) < 0.001 or abs(value) >= 10000:
        return f'{value:.2g}'
    return f'{value:.3f}'.rstrip('0').rstrip('.')

def metric_text(value):
    return '-' if value in ('', None) else compact(finite(value, 'reported metric'))

def same_execution(record, context, expected=None):
    expected = execution if expected is None else expected
    require(isinstance(record, dict), 'Missing execution record: ' + context)
    for key in ('job_id', 'cluster', 'node'):
        require(str(record.get(key)) == str(expected[key]),
                'Stale or different allocation in ' + context + ': ' + key)

def validate_current_environment(run, config):
    environment = json.loads((run / 'audit' / 'software' / 'environment.json').read_text())
    require(environment.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED',
            'Current repair runtime environment was not verified.')
    same_execution(environment.get('execution'), 'current repair environment')
    require(environment.get('versions') == config['runtime_expected_versions'] and
            environment.get('requirements_sha256') == config['runtime_requirements_sha256'] and
            environment.get('runtime_package') == config['runtime_package'],
            'Current environment differs from the pinned repair runtime.')
    require(Path(environment['executable']).resolve(strict=True) == Path(sys.executable).resolve(strict=True),
            'Repair interpreter differs from the verified corrected runtime.')
    for name, expected in environment['versions'].items():
        require(importlib.metadata.version(name) == expected, 'Report package version changed: ' + name)
    return environment

def apply_axes(ax):
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.xaxis.set_major_formatter(FuncFormatter(compact))
    ax.yaxis.set_major_formatter(FuncFormatter(compact))
    ax.tick_params(labelsize=8, length=3, color='#96A3B2')
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('bottom', 'left'):
        ax.spines[side].set_color('#B0BAC6')
    ax.grid(color='#E4E9EF', linewidth=0.6, zorder=0)

def register_report_fonts():
    directory = Path(matplotlib.get_data_path()) / 'fonts' / 'ttf'
    pdfmetrics.registerFont(TTFont('ReportSans', str(directory / 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont('ReportBold', str(directory / 'DejaVuSans-Bold.ttf')))
    pdfmetrics.registerFontFamily('ReportSans', normal='ReportSans', bold='ReportBold',
                                italic='ReportSans', boldItalic='ReportBold')

def measured_paragraph(text, width, size=9, leading=None, bold=False, color=INK):
    style = ParagraphStyle('body', fontName='ReportBold' if bold else 'ReportSans',
                           fontSize=size, leading=leading or size * 1.34,
                           textColor=HexColor(color))
    content = Paragraph(text, style)
    _, height = content.wrap(width, 612)
    return content, height

def table_paragraph(text, width, size, leading, bold=False):
    require(width > 12, 'Table column is narrower than its padding.')
    style = ParagraphStyle('table', fontName='ReportBold' if bold else 'ReportSans',
                           fontSize=size, leading=leading, textColor=HexColor(INK),
                           splitLongWords=True)
    content = Paragraph(escape(str(text)), style)
    _, height = content.wrap(width - 12, 612)
    return content, height

def measure_table(widths, headers, rows, size=8, minimum_row_height=30):
    """Measure the exact objects drawn later; no fixed wrapped-text ceiling."""
    require(len(headers) == len(widths) and abs(sum(widths) - 720) < 0.001,
            'Table columns must occupy the declared 720-point report body.')
    header = [table_paragraph(text, width, 7.7, 9.4, bold=True)
              for text, width in zip(headers, widths)]
    header_height = max(24, max(height for _, height in header) + 12)
    measured_rows = []
    for row in rows:
        require(len(row) == len(widths), 'Malformed report table row.')
        cells = [table_paragraph(value, width, size, size * 1.28)
                 for value, width in zip(row, widths)]
        height = max(minimum_row_height, max(height for _, height in cells) + 12)
        measured_rows.append((cells, height))
    return header, header_height, measured_rows

def paginate_measured_rows(header_height, measured_rows, top=502, bottom=40):
    """Preserve every row in order, repeating its measured header on each page."""
    available = top - bottom - header_height
    require(available > 0, 'Table header exceeds the usable page body.')
    pages, page, used = [], [], 0
    for cells, height in measured_rows:
        require(height <= available,
                'A single table row exceeds a full page; explicit text continuation is required.')
        if page and used + height > available:
            pages.append(page)
            page, used = [], 0
        page.append((cells, height))
        used += height
    if page or not pages:
        pages.append(page)
    return pages

def draw_measured_table(c, top, widths, header, header_height, rows, first_row_index=0):
    x, y = 36, top
    edges = [x]
    for width in widths:
        edges.append(edges[-1] + width)
    c.setFillColor(HexColor('#EAF0F6'))
    c.rect(x, y - header_height, sum(widths), header_height, fill=1, stroke=0)
    for i, (content, height) in enumerate(header):
        content.drawOn(c, edges[i] + 6, y - 6 - height)
    y -= header_height
    for index, (cells, row_height) in enumerate(rows, first_row_index):
        require(y - row_height >= 40, 'Measured table crosses the footer band.')
        if index % 2 == 0:
            c.setFillColor(HexColor('#F5F7FA'))
            c.rect(x, y - row_height, sum(widths), row_height, fill=1, stroke=0)
        for i, (content, height) in enumerate(cells):
            content.drawOn(c, edges[i] + 6, y - 6 - height)
        c.setStrokeColor(HexColor('#DDE5ED'))
        c.line(x, y - row_height, 756, y - row_height)
        y -= row_height
    return y

def validate_page_heading(title, subtitle):
    require(pdfmetrics.stringWidth(title, 'ReportBold', 17) <= 720,
            'Report page title exceeds its horizontal band.')
    _, height = measured_paragraph(subtitle, 720, size=8.7, color=MUTED)
    require(height <= 31, 'Report subtitle crosses the header divider.')
    return height


def verify_locked_sources(run, config):
    checks = []
    for alias, files in config['sources'].items():
        original = Path(config['source_runs'][alias]['path']).resolve(strict=True)
        snapshot = (run / 'engine_source/source_snapshot' / alias).resolve(strict=True)
        for relative, expected in files.items():
            require(len(expected) == 64 and all(c in '0123456789abcdef' for c in expected), 'Malformed source lock.')
            original_path, snapshot_path = safe_file(original, relative), safe_file(snapshot, relative)
            require(sha(original_path) == expected == sha(snapshot_path), 'Original/snapshot changed: ' + alias + '/' + relative)
            checks.append({'source_alias': alias, 'relative_path': relative, 'sha256': expected, 'status': 'UNCHANGED'})
    require(len(checks) == 30, 'The fixed upstream source-lock set changed.')
    return checks


def verify_staged_sources(run, config):
    evidence = {}
    for alias in ('engine_source', 'repair_source'):
        manifest = run / 'audit' / (alias + '_manifest.csv')
        rows = read_csv(manifest, {'relative_path', 'sha256', 'size_bytes', 'original_path', 'snapshot_path'})
        require(rows, 'Empty source staging manifest: ' + alias)
        original = Path(config[alias]['path']).resolve(strict=True)
        snapshot = (run / alias).resolve(strict=True)
        require(snapshot != original and not snapshot.is_relative_to(original), 'Source/run roots overlap.')
        seen = set()
        for row in rows:
            relative = row['relative_path']
            require(relative not in seen, 'Duplicate staging file: ' + relative)
            seen.add(relative)
            before, staged = safe_file(original, relative), safe_file(snapshot, relative)
            require(Path(row['original_path']).resolve(strict=True) == before and
                    Path(row['snapshot_path']).resolve(strict=True) == staged, 'Staging path projection differs.')
            require(sha(before) == row['sha256'] == sha(staged) and
                    before.stat().st_size == staged.stat().st_size == integer(row['size_bytes'], 'staging size'),
                    'Original/staged file changed: ' + alias + '/' + relative)
        if alias == 'repair_source':
            expected_reports = {'results/NRCS_Shannon_hindcast_review.pdf'} | {
                'figures/'+domain+'_'+suffix+'.pdf' for domain in DOMAINS
                for suffix in ('observed_vs_baselines','primary_absolute_errors','depth_sensitivity_absolute_errors')}
            required = set(config[alias]['files']) | expected_reports | {
                'audit/ENGINE_SOURCE_STATUS.json','audit/engine_source_manifest.csv','ANALYSES_COMPLETE.txt'}
            require(seen == required, 'The declared repair-source staging set changed.')
            require(all(sha(safe_file(snapshot,relative)) == expected for relative,expected in config[alias]['files'].items()),
                    'Repair-source manifest differs from the six reviewed CONFIG locks.')
        evidence[alias] = {'manifest_sha256': sha(manifest), 'rows': rows}
    require(config['engine_source']['analysis_job_id'] == '24124251' and
            config['repair_source']['analysis_job_id'] == '24126557', 'Frozen source provenance changed.')
    evidence['upstream'] = verify_locked_sources(run, config)
    return evidence


def verify_engine_manifest(run, status):
    manifest = run / 'results/output_manifest.csv'
    require(status.get('output_manifest_path') == 'results/output_manifest.csv' and
            status.get('output_manifest_sha256') == sha(manifest), 'Backtest status does not pin its output manifest.')
    rows = read_csv(manifest, {'relative_path','sha256','size_bytes'})
    require(rows, 'Empty backtest output manifest.')
    seen = set()
    for row in rows:
        relative = row['relative_path']
        require(relative not in seen and relative.startswith('results/'), 'Duplicate/outside engine manifest path.')
        seen.add(relative)
        path = safe_file(run, relative)
        require(len(row['sha256']) == 64 and all(c in '0123456789abcdef' for c in row['sha256']), 'Invalid output checksum.')
        require(sha(path) == row['sha256'] and path.stat().st_size == integer(row['size_bytes'], 'output size'),
                'Backtest output differs from manifest: ' + relative)
        if path.suffix == '.csv':
            read_csv(path, set())
        elif path.suffix == '.json':
            json.loads(path.read_text())
    expected = {'results/new_2024/'+name for name in (
        'training_fits.json','training_membership.csv','training_plot_means.csv','baseline_support.csv',
        'predictions.csv','prediction_freeze.json','plot_errors.csv','performance.csv','paired_errors.csv',
        'paired_performance.csv','cohort_qc.csv','cohort_support.csv')} | {'results/'+name for name in (
        'response_count_verification.csv','RAW_COUNT_AUDIT.json','plot_errors.csv','performance.csv','paired_errors.csv',
        'paired_performance.csv','cohort_support.csv','forecast_requirements.csv','FORECAST_READINESS.json',
        'source_integrity_after_backtest.csv','REUSED_2025_OUTPUTS.json')}
    require(seen == expected, 'The exact 23-file backtest output manifest set changed.')
    return rows, sha(manifest)


def origin_check(row):
    fold = row['fold_id']
    require(fold in FOLDS and row['origin_kind'] == ORIGIN_KIND[fold], 'Invalid fold/origin kind.')
    expected_job = execution['job_id'] if fold == FOLDS[0] else '24124251'
    require(str(row['origin_job_id']) == str(expected_job), 'Incorrect new/reused origin job.')


def validate_rows(errors, performance, paired):
    metrics, groups, keys = {}, defaultdict(list), set()
    for row in performance:
        origin_check(row)
        require(row['domain'] in DOMAINS and row['scope'] in SCOPES and row['method'] in METHODS, 'Unknown performance group.')
        key = (row['fold_id'], row['domain'], row['scope'], row['method'])
        require(key not in metrics, 'Duplicate performance group.')
        metrics[key] = row
        n = integer(row['n_evaluated_plots'], 'performance n')
        ids = row['evaluation_plot_ids'].split(';') if row['evaluation_plot_ids'] else []
        require(n == len(ids) == len(set(ids)), 'Performance n/IDs disagree.')
        require(row['status'], 'Missing recorded performance status.')
        if n:
            require(row['status'] == 'DESCRIPTIVE_POINT_DIAGNOSTIC', 'Estimated method has unexpected status.')
            require(finite(row['MAE'], 'MAE') >= 0 and finite(row['RMSE'], 'RMSE') >= 0, 'Negative metric.')
        else:
            require(row['reason'], 'Skipped method has no recorded reason.')
            require(row['MAE'] == row['RMSE'] == '', 'Skipped method has metric values.')
            require(row['status'] != 'DESCRIPTIVE_POINT_DIAGNOSTIC', 'Skip incorrectly labelled estimated.')
    expected = {(f,d,s,m) for f in FOLDS for d in DOMAINS for s in SCOPES for m in METHODS}
    require(set(metrics) == expected and len(performance) == 24, 'All 24 prescribed performance/skip rows must be explicit.')
    for row in errors:
        origin_check(row)
        group = (row['fold_id'], row['domain'], row['scope'], row['method'])
        require(group in metrics, 'Error group lacks a performance row.')
        key = group + (row['biological_plot_id'],)
        require(row['biological_plot_id'] and key not in keys, 'Duplicate/missing error plot.')
        keys.add(key)
        require(row['held_out_date'] in HELD_OUT_DATES[row['fold_id']], 'Unexpected actual test date.')
        require(row['last_prior_date'] < CUTOFF[row['fold_id']], 'Origin date violates the historical cutoff.')
        for name in ('observed_Shannon','predicted_Shannon','absolute_error','squared_error'):
            require(finite(row[name], name) >= 0, 'Negative unsigned report value.')
        finite(row['signed_error'], 'signed error')
        require(row['training_digest'] and row['prediction_digest'], 'Missing frozen prediction lineage.')
        groups[group].append(row)
    for key, row in metrics.items():
        declared = set(row['evaluation_plot_ids'].split(';')) if row['evaluation_plot_ids'] else set()
        require(declared == {r['biological_plot_id'] for r in groups[key]}, 'Error rows/performance cohorts differ.')
    paired_keys = set()
    for row in paired:
        origin_check(row)
        require(row['domain'] in DOMAINS and row['scope_A'] in SCOPES and row['scope_B'] in SCOPES and
                row['method_A'] in METHODS and row['method_B'] in METHODS, 'Invalid paired comparison group.')
        key = (row['fold_id'],row['comparison_type'],row['domain'],row['scope_A'],row['scope_B'],row['method_A'],row['method_B'])
        require(key not in paired_keys, 'Duplicate paired key.')
        paired_keys.add(key)
        n = integer(row['n_common_plots'], 'paired n')
        ids = row['common_plot_ids'].split(';') if row['common_plot_ids'] else []
        require(n == len(ids) == len(set(ids)), 'Paired n/IDs disagree.')
        left = {r['biological_plot_id'] for r in groups[(row['fold_id'],row['domain'],row['scope_A'],row['method_A'])]}
        right = {r['biological_plot_id'] for r in groups[(row['fold_id'],row['domain'],row['scope_B'],row['method_B'])]}
        require(set(ids) == left & right, 'Paired diagnostics do not use exact common plots within the same round.')
        require(row['status'], 'Paired status missing.')
        fields = ('MAE_A','MAE_B','RMSE_A','RMSE_B','mean_delta_abs_error','delta_RMSE')
        if n:
            require(row['status'] == 'DESCRIPTIVE_PAIRED_DIAGNOSTIC', 'Paired result has unexpected status.')
            for name in fields:
                finite(row[name], 'paired '+name)
        else:
            require(row['reason'], 'Unestimated paired comparison lacks a reason.')
            require(all(row[name] == '' for name in fields), 'Unestimated paired comparison has numeric values.')
            require(row['status'] != 'DESCRIPTIVE_PAIRED_DIAGNOSTIC', 'Empty paired comparison labelled estimated.')
    expected_pairs = {
        (f,'WITHIN_SCOPE_METHODS',d,s,s,a,b) for f in FOLDS for d in DOMAINS for s in SCOPES
        for i,a in enumerate(METHODS) for b in METHODS[i+1:]
    } | {(f,'MATCHED_TEST_COHORT_DIFFERENT_TRAINING_SCOPES',d,'PRIMARY','DEPTH_SENSITIVITY',m,m)
         for f in FOLDS for d in DOMAINS for m in METHODS}
    require(paired_keys == expected_pairs, 'Prescribed paired comparison/skip rows are missing.')
    return metrics, groups


def verify_reused_rows(run, inputs):
    original_root = run / 'engine_source/results'
    proofs = {}
    for filename in CSV_FIELDS:
        original = read_csv(original_root / filename, CSV_FIELDS[filename] - ORIGIN_FIELDS)
        reused = [r for r in inputs[filename] if r['fold_id'] == FOLDS[1]]
        stripped = [{k:v for k,v in r.items() if k not in ORIGIN_FIELDS} for r in reused]
        require(stripped == original, 'Reused 2025 CSV strings/order changed: ' + filename)
        proofs[filename] = sha(original_root / filename)
    return proofs


def scatter_figure(path, domain, groups, metrics):
    fig, axes = plt.subplots(2, 3, figsize=(11,8.5), layout='constrained')
    fig.set_constrained_layout_pads(w_pad=0.08,h_pad=0.09,wspace=0.08,hspace=0.14)
    treatments = sorted({r['treatment'] for key,rows in groups.items() if key[0] == FOLDS[0] and key[1] == domain for r in rows})
    palette = ('#177E89','#D07823','#7553A0','#497A3D','#985C65','#596979')
    require(len(treatments) <= len(palette), 'Too many recorded treatment labels.')
    colors = dict(zip(treatments,palette))
    values = [finite(r[n],n) for key,rows in groups.items() if key[0] == FOLDS[0] and key[1] == domain
              for r in rows for n in ('observed_Shannon','predicted_Shannon')]
    low,high = (min(values),max(values)) if values else (0.0,1.0)
    pad = max((high-low)*0.07,0.08)
    low,high = max(0,low-pad),high+pad
    markers = {'2024-07-06':'o','2024-08-14':'D'}
    for si,scope in enumerate(SCOPES):
        for mi,method in enumerate(METHODS):
            ax = axes[si,mi]
            key = (FOLDS[0],domain,scope,method)
            rows,reported = groups[key],metrics[key]
            n = integer(reported['n_evaluated_plots'],'scatter n')
            ax.set_title(METHOD_LABEL[method]+'\n'+SCOPE_LABEL[scope]+f' | n = {n} plots',fontsize=9.2,color=INK,pad=8)
            if rows:
                for treatment in treatments:
                    for date,marker in markers.items():
                        subset = [r for r in rows if r['treatment'] == treatment and r['held_out_date'] == date]
                        ax.scatter([finite(r['observed_Shannon'],'observed') for r in subset],
                                   [finite(r['predicted_Shannon'],'prediction') for r in subset],
                                   color=colors[treatment],marker=marker,s=33,alpha=0.9,
                                   linewidths=0.45,edgecolors='white',zorder=3)
                ax.plot([low,high],[low,high],color='#818F9F',linestyle='--',linewidth=0.85,zorder=1)
            else:
                ax.text(0.5,0.63,'Not estimated / no evaluated plots',ha='center',va='center',
                        transform=ax.transAxes,fontsize=8.1,color=MUTED)
                status_label = reported['status'].replace('_',' ').lower()
                ax.text(0.5,0.48,'\n'.join(textwrap.wrap(status_label,38)),ha='center',va='center',
                        transform=ax.transAxes,fontsize=7.2,color=MUTED)
                ax.text(0.5,0.28,'\n'.join(textwrap.wrap(reported['reason'],38)),ha='center',va='center',
                        transform=ax.transAxes,fontsize=7.2,color=MUTED)
            ax.set_xlim(low,high)
            ax.set_ylim(low,high)
            ax.set_xlabel('Observed held-out Shannon (natural log)',fontsize=8.1,labelpad=5)
            ax.set_ylabel('Baseline prediction (natural log)',fontsize=8.1,labelpad=8)
            apply_axes(ax)
    fig.suptitle(DOMAIN_LABEL[domain]+' | Whipstone Annual | 2024 annual round\n'
                 '2024-07-06 and 2024-08-14 held out together; 2023-only training',fontsize=12.2,color=INK)
    handles = [Line2D([],[],marker='o',linestyle='',color=colors[t],markersize=6,
                      label='\n'.join(textwrap.wrap(t.replace('_',' '),26))) for t in treatments]
    if handles:
        fig.legend(handles=handles,loc='outside lower center',ncol=2,fontsize=7.8,title_fontsize=7.8,frameon=False,
                   title='Color = treatment; circle = 2024-07-06; diamond = 2024-08-14; dashed line = equality\n'
                         'Distinct plot subsets of one annual round; no intervals or ten-year forecast')
    validate_figure_bounds(fig,'2024 '+domain+' scatter')
    fig.savefig(path,format='pdf',metadata={'Title':DOMAIN_LABEL[domain]+' 2024 annual-round observed versus baselines'})
    plt.close(fig)


def error_figure(path,domain,scope,groups,metrics):
    rows = [r for m in METHODS for r in groups[(FOLDS[0],domain,scope,m)]]
    plots = sorted({r['biological_plot_id'] for r in rows})
    dates = {}
    for row in rows:
        plot = row['biological_plot_id']
        require(plot not in dates or dates[plot] == row['held_out_date'], 'A physical plot has multiple target dates in one round.')
        dates[plot] = row['held_out_date']
    fig,ax = plt.subplots(figsize=(11,8.5),layout='constrained')
    fig.set_constrained_layout_pads(w_pad=0.11,h_pad=0.10)
    y_by_plot = {plot:i for i,plot in enumerate(plots)}
    skip_notes = []
    for method,offset in zip(METHODS,(-0.19,0,0.19)):
        key = (FOLDS[0],domain,scope,method)
        subset,reported = groups[key],metrics[key]
        n = integer(reported['n_evaluated_plots'],'error n')
        ax.scatter([finite(r['absolute_error'],'absolute error') for r in subset],
                   [y_by_plot[r['biological_plot_id']]+offset for r in subset],
                   label=METHOD_LABEL[method]+f' (n={n})',marker=METHOD_MARKER[method],s=38,
                   color=METHOD_COLOR[method],edgecolors='white',linewidths=0.4,zorder=3)
        if not n:
            skip_notes.append(METHOD_LABEL[method]+': not estimated; '+reported['reason'])
    if plots:
        labels = ['\n'.join(textwrap.wrap(plot,37))+'\n'+dates[plot] for plot in plots]
        ax.set_yticks(range(len(plots)),labels)
        ax.set_ylim(len(plots)-0.5,-0.5)
    else:
        ax.set_yticks([])
        ax.text(0.5,0.5,'All methods explicitly skipped; see the recorded support table.',
                transform=ax.transAxes,ha='center',va='center',fontsize=11,color=MUTED)
    ax.set_xlim(left=0)
    ax.set_xlabel('Absolute held-out error in natural-log Shannon units',fontsize=10,labelpad=9)
    ax.set_ylabel('Recorded biological plot ID / actual test date',fontsize=8.6,labelpad=10)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.xaxis.set_major_formatter(FuncFormatter(compact))
    ax.tick_params(axis='y',labelsize=7.2,length=0,pad=7)
    ax.tick_params(axis='x',labelsize=9)
    for side in ('top','right','left'):
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color('#B0BAC6')
    ax.grid(axis='x',color='#E4E9EF',linewidth=0.7,zorder=0)
    ax.set_title(DOMAIN_LABEL[domain]+' | '+SCOPE_LABEL[scope]+'\nWhipstone Annual | 2024 annual round: July/August held out together',
                 fontsize=12.2,color=INK,pad=15)
    legend_title = '2023-only frozen training; points have no inferential intervals'
    if skip_notes:
        legend_title += '\n'+'\n'.join('\n'.join(textwrap.wrap(note,112)) for note in skip_notes)
    fig.legend(loc='outside lower center',ncol=3,fontsize=7.7,frameon=False,title=legend_title,title_fontsize=7.4)
    validate_figure_bounds(fig,'2024 '+domain+' '+scope+' errors')
    fig.savefig(path,format='pdf',metadata={'Title':DOMAIN_LABEL[domain]+' '+scope+' 2024 annual-round absolute errors'})
    plt.close(fig)


def page_header(c,title,subtitle):
    validate_page_heading(title,subtitle)
    c.setFont('ReportBold',17)
    c.setFillColor(HexColor(INK))
    c.drawString(36,575,title)
    paragraph, height = measured_paragraph(subtitle,720,size=8.7,color=MUTED)
    paragraph.drawOn(c,36,557-height)
    c.setStrokeColor(HexColor('#D9E1E9'))
    c.line(36,520,756,520)
    footer, height = measured_paragraph('Generated on Puma | scientific and visual review pending | retrospective point diagnostics',720,size=7.2,color=MUTED)
    footer.drawOn(c,36,22-height)


def draw_platypus_table(c,top,widths,header,header_height,rows,first_row_index=0):
    data = [[p for p,_ in header]] + [[p for p,_ in cells] for cells,_ in rows]
    heights = [header_height] + [height for _,height in rows]
    styles = [('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
              ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
              ('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),HexColor('#EAF0F6')),
              ('LINEBELOW',(0,0),(-1,-1),0.35,HexColor('#DDE5ED'))]
    for i in range(len(rows)):
        if (first_row_index+i)%2 == 0:
            styles.append(('BACKGROUND',(0,i+1),(-1,i+1),HexColor('#F5F7FA')))
    content = LongTable(data,colWidths=widths,rowHeights=heights,repeatRows=1,splitByRow=1,
                        style=TableStyle(styles),hAlign='LEFT')
    width,height = content.wrapOn(c,720,top-40)
    require(abs(width-720)<0.001 and abs(height-sum(heights))<0.01 and top-height>=40,
            'Measured Platypus table differs from preflight or crosses footer.')
    content.drawOn(c,36,top-height)
    return top-height


def table_section(c,section):
    header,hh,rows = measure_table(section['widths'],section['headers'],section['rows'],section.get('size',7.7),section.get('minimum_row_height',30))
    pages = paginate_measured_rows(hh,rows)
    first = 0
    for index,chunk in enumerate(pages):
        page_header(c,section['title']+(' (continued)' if index else ''),section['subtitle'])
        draw_platypus_table(c,502,section['widths'],header,hh,chunk,first)
        first += len(chunk)
        c.showPage()
    require(first == len(section['rows']), 'A table row was lost in pagination.')
    return {'title':section['title'],'pages':len(pages),'rows_preserved':first,'header_height_points':hh,
            'largest_row_height_points':max((h for _,h in rows),default=0),'status':'PASS'}


def report_intro_blocks(config):
    return [
        ('One blocked 2024 annual round; 2023-only training',
         'The new retrospective backtest trains only on <b>2023-11-16</b>, with cutoff <b>2024-01-01</b>. '
         '<b>2024-07-06 and 2024-08-14 are held out together</b> as distinct plot subsets of one annual sampling round. '
         'The same frozen 2023 fits are used for both actual collection dates; July responses do not train August predictions. '
         'Every physical plot contributes at most one held-out observation per method in this round. '
         'All scope predictions are frozen before any target scoring. This is an already-observed retrospective diagnostic, '
         'not an untouched prospective trial. Historical assay availability has not been verified.'),
        ('Preserved 2025 evidence and separate execution provenance',
         'The 2025 round retains original <b>2025-08-17</b> predictions, errors, metrics and common-plot comparisons from '
         '<b>numerical engine job 24124251</b> on Puma r7u03n1. Its historical overall job remains FAILED (1:0 / pipeline 1) '
         'because of a PDF table-header overflow. <b>Report-only repair job 24126557</b> subsequently completed; '
         'its returned 11-page PDF and source receipts are pinned. The new report labels these 2025 rows '
         '<b>REUSED_2025_ENGINE</b>; they are not refitted or rescored. '
         f'<b>Current 2024 backtest/report:</b> Puma job {escape(str(execution["job_id"]))}, '
         f'node {escape(str(execution["node"]))}. Original/staged engine files, the successful repair-source files '
         'and all 30 upstream source locks are checked before and after rendering.'),
        ('Response, methods and comparison limits',
         'Recorded Shannon is <b>H = -sum(p ln p)</b> from positive-ASV proportions of unrarefied target-filtered counts, '
         'in natural-log units (nats). Three prespecified baselines are persistence, equal-plot training mean, '
         'and equal-plot training arm mean. The arm baseline is withheld unless all four training arms have equal '
         'physical-plot support with at least two plots per arm. No fallback is substituted. '
         'Bacteria and archaea and primary/depth scopes remain separate. Comparisons use exact common plots within '
         'one annual round and each scope\'s original fixed fits. Depth scopes can change cohorts and training origins. '
         '<b>No rounds are pooled as independent plot samples; no winning baseline is selected.</b> '
         'Read counts are QC, not microbial biomass, and treatment labels do not establish causal effects.'),
        ('Forecast decision: authorized work, insufficient numerical evidence',
         '<b>Numerical ten-year forecasts are WITHHELD: not supported by the current data.</b> '
         'The user has authorized predictive-validation and ten-year work; this decision is an evidence limit. '
         'Two observed annual target rounds from repeated known plots do not establish prospective/external predictive '
         'validation, new-plot/farm transfer, calibrated intervals or long-horizon dynamics. The readiness table retains '
         'each evidence requirement and blocking status. <b>No fitted forecast curve, interval, p-value or causal '
         'treatment effect is generated.</b> Scientific and visual review of this returned report remain pending.'),
    ]


def draw_intro(c,blocks):
    subtitle = 'New 2024 blocked annual-round backtest | reused 2025 engine evidence | numerical forecasts withheld'
    title = 'NRCS: temporal backtest and readiness'
    page_header(c,title,subtitle)
    top,pages = 501,1
    for heading,body in blocks:
        h = measured_paragraph(heading,720,size=10.4,bold=True)
        b = measured_paragraph(body,720,size=9.1)
        height = h[1]+6+b[1]+18
        require(height<=461, 'An introductory block is taller than one page.')
        if top-height<40:
            c.showPage()
            page_header(c,'Provenance and limits (continued)',subtitle)
            top,pages = 501,pages+1
        h[0].drawOn(c,36,top-h[1])
        b[0].drawOn(c,36,top-h[1]-6-b[1])
        top -= height
    require(top>=40, 'Introductory block overlaps footer.')
    c.showPage()
    return pages


def origin_text(row):
    label = 'NEW 2024' if row['fold_id'] == FOLDS[0] else 'REUSED 2025'
    return label+'; job '+row['origin_job_id']


def report_sections(performance,paired,support,qc,requirements,readiness):
    sections = []
    for fold in FOLDS:
        year = '2024' if fold == FOLDS[0] else '2025'
        dates = '2024-07-06 and 2024-08-14, held out together' if fold == FOLDS[0] else '2025-08-17, original evidence reused without rescoring'
        selected = sorted([r for r in performance if r['fold_id'] == fold],
                          key=lambda r:(DOMAINS.index(r['domain']),SCOPES.index(r['scope']),METHODS.index(r['method'])))
        sections.append(dict(title=year+' annual round: baseline performance',
            subtitle='n = evaluated physical plots; MAE/RMSE in natural-log Shannon units. Actual target dates: '+dates+'.',
            widths=[50,80,145,30,48,48,219,100],
            headers=['Domain','Scope','Prespecified baseline','n','MAE','RMSE','Recorded status','Numerical origin'],
            rows=[[DOMAIN_LABEL[r['domain']],SCOPE_LABEL[r['scope']],METHOD_LABEL[r['method']],r['n_evaluated_plots'],
                   metric_text(r['MAE']),metric_text(r['RMSE']),r['status'],origin_text(r)] for r in selected],size=7.6))
        comparisons = [r for r in paired if r['fold_id'] == fold]
        sections.append(dict(title=year+' annual round: common-plot comparisons',
            subtitle='n = exact common plots in this round. Mean AE = mean absolute-error difference; both differences are B minus A. Fixed predictions; no refit, interval or test.',
            widths=[52,94,165,28,66,66,249],
            headers=['Domain','Scopes A -> B','Baselines A -> B','n','Mean AE B-A','RMSE B-A','Recorded status and reason'],
            rows=[[DOMAIN_LABEL[r['domain']],SCOPE_LABEL[r['scope_A']]+' -> '+SCOPE_LABEL[r['scope_B']],
                   METHOD_LABEL[r['method_A']]+' -> '+METHOD_LABEL[r['method_B']],r['n_common_plots'],
                   metric_text(r['mean_delta_abs_error']),metric_text(r['delta_RMSE']),
                   r['status']+(': '+r['reason'] if r['reason'] else '')] for r in comparisons],size=7.4,minimum_row_height=36))
    skipped = [r for r in performance if integer(r['n_evaluated_plots'],'skip n')==0]
    if skipped:
        sections.append(dict(title='Explicitly skipped baselines',
            subtitle='n=0 means not estimated; blank metrics are not zero error. The recorded support decision and full reason are retained.',
            widths=[110,105,155,110,240],headers=['Annual round / origin','Domain / scope','Prespecified baseline','Recorded status','Recorded reason'],
            rows=[[origin_text(r),DOMAIN_LABEL[r['domain']]+' / '+SCOPE_LABEL[r['scope']],METHOD_LABEL[r['method']],
                   r['status'],r['reason']] for r in skipped],size=7.8,minimum_row_height=36))
    arm_short = {'Blood_meal_and_Feather_meal':'Blood + feather meal',
                 'Blood_meal_and_Feather_meal_and_cover_crop':'Blood + feather meal + cover crop',
                 'Control':'Control','Cover_crop_only':'Cover crop only'}
    support_rows = []
    for r in support:
        counts = json.loads(r['training_arm_plot_counts'])
        require(isinstance(counts,dict) and set(counts).issubset(arm_short), 'Unknown training arm in support table.')
        arm_counts = '; '.join(arm_short[a]+': '+str(counts[a]) for a in sorted(counts))
        support_rows.append([DOMAIN_LABEL[r['domain']],SCOPE_LABEL[r['scope']],METHOD_LABEL[r['method']],
             r['n_training_plots'],r['n_training_rows'],arm_counts,r['status']+(': '+r['reason'] if r['reason'] else '')])
    sections.append(dict(title='2024 backtest: frozen training support',
        subtitle='Only eligible 2023-11-16 rows train the new fold. Arm support is counted per physical plot; future responses or availability do not select training.',
        widths=[50,75,140,35,35,220,165],
        headers=['Domain','Scope','Baseline','Training plots','Training rows','Training physical plots by arm','Recorded status and reason'],
        rows=support_rows,size=7.5,minimum_row_height=36))
    target_qc = [r for r in qc if r['partition']=='HELD_OUT_ANNUAL_ROUND']
    require(target_qc, 'The 2024 held-out sample identity table is empty.')
    sections.append(dict(title='2024 annual round: actual sample identities',
        subtitle='All recorded held-out rows, including exclusions and missing-history holds. July/August are distinct subsets of one annual round; source IDs stay unchanged.',
        widths=[48,70,170,92,65,95,180],
        headers=['Domain','Scope','Recorded physical plot ID','Analysis sample','Actual date','Treatment','Disposition and reason'],
        rows=[[DOMAIN_LABEL[r['domain']],SCOPE_LABEL[r['scope']],r['biological_plot_id'],r['analysis_sample'],r['collection_date'],
               r['treatment'].replace('_',' '),r['disposition']+': '+r['reason']] for r in target_qc],size=7.2,minimum_row_height=36))
    sections.append(dict(title='Ten-year forecast evidence requirements',
        subtitle='Decision: '+readiness['numerical_forecast_decision']+' / '+readiness['reason']+'. Authorized work uses current NRCS files only; no numerical ten-year forecast has been generated.',
        widths=[170,155,45,350],headers=['Requirement','Recorded evidence status','Blocks forecast?','Recorded evidence'],
        rows=[[r['requirement'].replace('_',' '),r['status'],r['blocking_for_numerical_forecast'],r['evidence']+' Source: '+r['source_reference']] for r in requirements],
        size=8.0,minimum_row_height=42))
    return sections


def preflight_layout(run,config,sections):
    register_report_fonts()
    validate_page_heading('NRCS: temporal backtest and readiness',
                          'New 2024 blocked annual-round backtest | reused 2025 engine evidence | numerical forecasts withheld')
    validate_page_heading('Provenance and limits (continued)',
                          'New 2024 blocked annual-round backtest | reused 2025 engine evidence | numerical forecasts withheld')
    top,intro_pages = 501,1
    for heading,body in report_intro_blocks(config):
        h,b = measured_paragraph(heading,720,size=10.4,bold=True),measured_paragraph(body,720,size=9.1)
        height = h[1]+6+b[1]+18
        require(height<=461, 'Intro text exceeds one page.')
        if top-height<40:
            top,intro_pages = 501,intro_pages+1
        top-=height
        require(top>=40, 'Actual intro text crosses footer.')
    receipts=[]
    for section in sections:
        validate_page_heading(section['title'],section['subtitle'])
        validate_page_heading(section['title']+' (continued)',section['subtitle'])
        header,hh,rows = measure_table(section['widths'],section['headers'],section['rows'],section.get('size',7.7),section.get('minimum_row_height',30))
        chunks = paginate_measured_rows(hh,rows)
        require(sum(len(chunk) for chunk in chunks)==len(section['rows']), 'Actual table preflight drops a row.')
        require(all(502-hh-sum(height for _,height in chunk)>=40 for chunk in chunks), 'Actual table crosses footer.')
        require(all(hh>=height+12 for _,height in header), 'Actual header clips a Paragraph.')
        receipts.append(dict(title=section['title'],rows_preserved=len(rows),pages=len(chunks),
                             header_height_points=hh,largest_row_height_points=max((h for _,h in rows),default=0),status='PASS'))
    # Also test the historic narrow multiline headers on genuine Puma, without scientific fixture values.
    header,hh,rows = measure_table([58,105,179,39,73,66,200],
        ['Domain','Scopes A -> B','Baselines A -> B','n common','Mean abs-error difference','RMSE difference','Recorded status'],
        [['Domain','Primary -> Depth sensitivity',METHOD_LABEL[METHODS[2]]+' -> '+METHOD_LABEL[METHODS[2]],'n','difference','difference',
          'NOT_COMPARABLE: No exact common evaluated plots; skipped baselines remain unestimated.']]*30)
    require(sum(len(page) for page in paginate_measured_rows(hh,rows))==30, 'Fixed-text layout check drops rows.')
    receipt=dict(status='PASS',execution=execution,introductory_pages=intro_pages,
                 all_actual_text_measured=True,historic_header_stress_check_passed=True,
                 scientific_figures_generated_before_preflight=False,sections=receipts)
    save(run/'audit/report_layout_preflight.json',receipt)
    return receipt


def text_report(path,config,sections):
    c=canvas.Canvas(str(path),pagesize=(792,612),pageCompression=1)
    c.setTitle('NRCS annual-round backtests and numerical forecast readiness')
    c.setAuthor('NRCS Puma guarded report')
    draw_intro(c,report_intro_blocks(config))
    receipts=[table_section(c,section) for section in sections]
    c.save()
    return receipts


def load_json(path):
    value=json.loads(path.read_text(encoding='utf-8'))
    require(isinstance(value,dict), 'Expected an object receipt: '+str(path))
    return value


def csv_bool(value,context):
    require(value in ('True','False','true','false'), 'Noncanonical CSV boolean: '+context)
    return value.lower() == 'true'


def validate_staging_receipts(run,config,evidence):
    old_origin={'job_id':'24124251','cluster':'puma','node':'r7u03n1'}
    repair_origin={'job_id':'24126557','cluster':'puma','node':'r7u03n1'}
    stage_path,repair_path=run/'audit/ENGINE_SOURCE_STATUS.json',run/'audit/REPAIR_SOURCE_STATUS.json'
    stage,repair=load_json(stage_path),load_json(repair_path)
    same_execution(stage.get('execution'),'engine staging')
    same_execution(repair.get('execution'),'successful repair staging')
    require(stage.get('status')=='IMMUTABLE_ENGINE_SOURCE_VERIFIED_AND_STAGED' and
            stage.get('engine_origin_execution')==old_origin and stage.get('files_staged')==124 and
            stage.get('engine_source_job_id')=='24124251' and stage.get('engine_source_scheduler_state')=='FAILED' and
            stage.get('engine_source_scheduler_exit_code')=='1:0' and stage.get('engine_source_pipeline_exit_code')==1,
            'Original engine staging/provenance gate failed.')
    require(repair.get('status')=='SUCCESSFUL_REPORT_REPAIR_SOURCE_VERIFIED_AND_STAGED' and
            repair.get('repair_origin_execution')==repair_origin and repair.get('repair_source_job_id')=='24126557' and
            repair.get('repair_source_scheduler_state')=='COMPLETED' and repair.get('repair_source_scheduler_exit_code')=='0:0' and
            repair.get('repair_source_pipeline_exit_code')==0,'Successful repair origin gate failed.')
    for alias,receipt in (('engine_source',stage),('repair_source',repair)):
        require(receipt.get('source_integrity_passed') is True and receipt.get('source_files_changed') is False and
                receipt.get('source_files_verified_unchanged_after_staging') is True and
                receipt.get(alias+'_manifest_path')=='audit/'+alias+'_manifest.csv' and
                receipt.get(alias+'_manifest_sha256')==evidence[alias]['manifest_sha256'] and
                receipt.get('files_staged')==len(evidence[alias]['rows']), 'Staging receipt/manifest linkage failed: '+alias)
    require(stage.get('repair_source_status_sha256')==sha(repair_path) and stage.get('successful_report_source_verified') is True,
            'Successful repair receipt is not pinned by engine staging.')
    lineage_path=run/'audit/repair_engine_lineage_checks.csv'
    require(repair.get('repair_engine_lineage_checks_path')=='audit/repair_engine_lineage_checks.csv' and
            repair.get('repair_engine_lineage_checks_sha256')==sha(lineage_path),'Repair lineage receipt pin failed.')
    lineage=read_csv(lineage_path,{'relative_path','sha256','size_bytes','original_engine_path',
        'successful_repair_engine_path','current_engine_snapshot_path','verified_identical_bytes'})
    engine_rows={r['relative_path']:r for r in evidence['engine_source']['rows']}
    require(len(lineage)==124 and {r['relative_path'] for r in lineage}==set(engine_rows),'Repair lineage set differs.')
    for row in lineage:
        relative=row['relative_path']
        paths=(safe_file(Path(config['engine_source']['path']),relative),
               safe_file(Path(config['repair_source']['path'])/'engine_source',relative),safe_file(run/'engine_source',relative))
        declared=(row['original_engine_path'],row['successful_repair_engine_path'],row['current_engine_snapshot_path'])
        require(all(Path(name).resolve(strict=True)==path for name,path in zip(declared,paths)) and
                row['sha256']==engine_rows[relative]['sha256'] and
                all(sha(path)==row['sha256'] and path.stat().st_size==integer(row['size_bytes'],'lineage size') for path in paths) and
                csv_bool(row['verified_identical_bytes'],'lineage'), 'Original/repair/current lineage changed: '+relative)
    old_status=load_json(run/'engine_source/results/PILOT_STATUS.json')
    same_execution(old_status.get('execution'),'preserved 2025 engine',old_origin)
    require(old_status.get('status')=='PILOT_POINT_DIAGNOSTICS_COMPLETED_REVIEW_PENDING' and
            stage.get('engine_pilot_status_sha256')==sha(run/'engine_source/results/PILOT_STATUS.json') and
            old_status.get('output_manifest_sha256')==stage.get('engine_output_manifest_sha256')==sha(run/'engine_source/results/output_manifest.csv'),
            'Preserved 2025 engine status linkage failed.')
    for relative,expected in config['review_locks'].items():
        require(sha(safe_file(run,relative))==expected, 'Accepted review lock changed.')
    return {'stage':stage,'repair':repair,'lineage_sha256':sha(lineage_path),'lineage':lineage,'old_status':old_status}


def validate_engine_receipts(run,status):
    same_execution(status.get('execution'),'current backtest engine')
    require(status.get('status')==BACKTEST_SUCCESS,'Current backtest did not complete.')
    for key in ('source_integrity_passed','source_preservation_passed','source_files_verified_unchanged_after_backtest',
                'new_2024_baseline_fits_computed','new_2024_predictions_computed','reused_2025_outputs_byte_identical'):
        require(status.get(key) is True,'Backtest success flag missing: '+key)
    for key in ('source_files_changed','reused_2025_fits_predictions_errors_recomputed','reused2025_fits_recomputed',
                'predictive_validation_established','ten_year_forecast_generated','numerical_ten_year_forecast_generated',
                'intervals_generated','p_values_computed','winner_selected','rounds_pooled_as_independent_plots','historical_assay_availability_verified'):
        require(status.get(key) is False,'Unsupported or changed backtest flag: '+key)
    freeze_path=run/'results/new_2024/prediction_freeze.json'
    freeze=load_json(freeze_path)
    same_execution(freeze.get('execution'),'new2024 prediction freeze')
    require(freeze.get('status')=='FROZEN_BEFORE_SCORING' and freeze.get('fold_id')==FOLDS[0] and
            freeze.get('training_dates')==['2023-11-16'] and freeze.get('held_out_actual_dates')==list(HELD_OUT_DATES[FOLDS[0]]) and
            freeze.get('cutoff_date')=='2024-01-01' and freeze.get('training_response_partition')=='2023_ONLY' and
            freeze.get('same_fit_used_for_both_actual_dates') is True and freeze.get('future_availability_used_for_training') is False and
            freeze.get('all_scope_predictions_frozen_before_any_scoring') is True and
            freeze.get('predictions_csv_sha256')==status.get('new_2024_predictions_csv_sha256')==sha(run/'results/new_2024/predictions.csv') and
            freeze.get('training_fits_sha256')==sha(run/'results/new_2024/training_fits.json') and
            status.get('new_2024_prediction_freeze_sha256')==sha(freeze_path),'New2024 freeze lineage failed.')
    require(set(freeze.get('scope_prediction_digests',{}))=={d+'__'+s for d in DOMAINS for s in SCOPES},'Freeze scope labels changed.')
    raw_path=run/'results/RAW_COUNT_AUDIT.json'
    raw=load_json(raw_path)
    same_execution(raw.get('execution'),'current raw-count audit')
    require(status.get('raw_count_audit')=='results/RAW_COUNT_AUDIT.json' and status.get('raw_count_audit_sha256')==sha(raw_path) and
            raw.get('status')=='PASS' and raw.get('source_metric_overwritten') is False and raw.get('observed_response_retained') is True and
            raw.get('audit_table')=='results/response_count_verification.csv' and
            raw.get('audit_table_sha256')==sha(run/'results/response_count_verification.csv'),'Current response audit linkage failed.')
    raw_rows=read_csv(run/'results/response_count_verification.csv',{'status','source_metric_overwritten'})
    require(raw_rows and all(r['status']=='PASS' and not csv_bool(r['source_metric_overwritten'],'raw audit') for r in raw_rows),
            'A recorded raw-count response verification failed.')
    fixtures={}
    for filename in ('self_checks.json','fold_self_checks.json'):
        receipt=load_json(run/'audit'/filename)
        same_execution(receipt.get('execution'),'current '+filename)
        require(receipt.get('status')=='PASS' and receipt.get('scientific_fixtures_executed_on_puma') is True and
                receipt.get('checks') and all(r['status']=='PASS' for r in receipt['checks']), 'Recorded Puma fixtures did not pass.')
        fixtures[filename]=receipt
    reused_path=run/'results/REUSED_2025_OUTPUTS.json'
    reused=load_json(reused_path)
    same_execution(reused.get('execution'),'unchanged 2025 output reuse')
    require(reused.get('status')=='VERIFIED_BYTE_IDENTICAL_REUSE_WITHOUT_RESCORING' and
            reused.get('fits_predictions_errors_recomputed') is False and reused.get('combined_rows_preserve_original_string_fields') is True and
            reused.get('file_hashes')==status.get('reused_2025_file_hashes'),'Recorded 2025 reuse proof failed.')
    expected_reuse={'training_fits.json','training_membership.csv','predictions.csv','prediction_freeze.json',
                    'plot_errors.csv','performance.csv','paired_errors.csv','paired_performance.csv','cohort_support.csv'}
    require(set(reused['file_hashes'])==expected_reuse and all(sha(run/'engine_source/results'/n)==h for n,h in reused['file_hashes'].items()),
            'Preserved 2025 output file bytes changed.')
    return {'freeze':freeze,'raw':raw,'fixtures':fixtures,'reused':reused}


def validate_readiness(run,status):
    path=run/'results/FORECAST_READINESS.json'
    readiness=load_json(path)
    same_execution(readiness.get('execution'),'forecast readiness audit')
    require(status.get('forecast_readiness')=='results/FORECAST_READINESS.json' and status.get('forecast_readiness_sha256')==sha(path) and
            readiness.get('status')=='NUMERICAL_TEN_YEAR_FORECAST_WITHHELD_NOT_SUPPORTED_CURRENT_DATA' and
            readiness.get('numerical_forecast_decision')=='WITHHELD' and readiness.get('reason')=='NOT_SUPPORTED_CURRENT_DATA',
            'Forecast decision/status linkage changed.')
    for key in ('readiness_audit_completed','user_authorizes_work','current_files_only','forecasts_not_generated_because_of_evidence'):
        require(readiness.get(key) is True,'Readiness receipt flag missing: '+key)
    for key in ('predictive_validation_established','ten_year_forecast_generated','numerical_ten_year_forecast_generated',
                'unsupported_numerical_forecast_invented'):
        require(readiness.get(key) is False,'Unsupported forecast claim: '+key)
    require(readiness.get('forecast_requirements_path')=='results/forecast_requirements.csv' and
            readiness.get('forecast_requirements_sha256')==sha(run/'results/forecast_requirements.csv'),'Requirements register pin failed.')
    requirements=read_csv(run/'results/forecast_requirements.csv',{'requirement','status','evidence','blocking_for_numerical_forecast','source_reference','source_reference_hashes'})
    expected={'work_authorization','response_and_identity','temporal_validation','independent_long_horizon_or_process_calibration',
              'measured_starting_states','assay_and_outcome_harmonization','historical_and_future_driver_series',
              'parameter_driver_and_structural_uncertainty','independent_sample_reuse_audit',
              'historical_assay_availability','scenario_and_outcome_harmonization'}
    require(len(requirements)==len(expected) and {r['requirement'] for r in requirements}==expected,'Readiness requirements missing/duplicated.')
    require(all(r['status'] and r['evidence'] and r['source_reference'] for r in requirements),'Readiness row lacks evidence/reference.')
    blockers=[r['requirement'] for r in requirements if csv_bool(r['blocking_for_numerical_forecast'],'requirements')]
    require(blockers==readiness.get('blocking_requirements'),'Readiness blocking set differs from the register.')
    normalized=[]
    for row in requirements:
        refs=json.loads(row['source_reference_hashes'])
        declared=[value.partition('#') for value in row['source_reference'].split(';')]
        require(len(refs)==len(declared),'Requirement source references/hash records differ.')
        for ref,(relative,separator,pointer) in zip(refs,declared):
            require(ref.get('relative_path')==relative and ref.get('json_pointer')==(pointer if separator else '') and
                    sha(safe_file(run,relative))==ref.get('sha256'),'Readiness evidence reference changed.')
            if separator:
                value=json.loads(safe_file(run,relative).read_text())
                require(pointer.startswith('/'),'Invalid JSON evidence pointer.')
                for key in pointer[1:].split('/'):
                    key=key.replace('~1','/').replace('~0','~')
                    require(isinstance(value,dict) and key in value,'Missing JSON evidence pointer.')
                    value=value[key]
        normalized.append(dict(row,blocking_for_numerical_forecast=csv_bool(row['blocking_for_numerical_forecast'],'requirements')))
    require(normalized==readiness.get('requirements'),'Readiness JSON/register evidence rows differ.')
    return readiness,requirements


def validate_support(run):
    support=read_csv(run/'results/new_2024/baseline_support.csv',{'domain','scope','method','status','n_training_plots',
        'n_training_rows','training_arm_plot_counts','training_digest','reason','fit_uses_held_out_response','fit_conditioned_on_held_out_availability'})
    require(len(support)==12 and {(r['domain'],r['scope'],r['method']) for r in support}==
            {(d,s,m) for d in DOMAINS for s in SCOPES for m in METHODS},'Training support rows are missing/duplicated.')
    require(all(not csv_bool(r['fit_uses_held_out_response'],'support') and
                not csv_bool(r['fit_conditioned_on_held_out_availability'],'support') for r in support), 'Support uses held-out information.')
    qc=read_csv(run/'results/new_2024/cohort_qc.csv',{'domain','scope','biological_plot_id','collection_date',
        'analysis_sample','sample_key','treatment','partition','disposition','reason'})
    require(qc and all(r['domain'] in DOMAINS and r['scope'] in SCOPES for r in qc),'Missing/unknown QC rows.')
    for r in qc:
        if r['partition']=='HELD_OUT_ANNUAL_ROUND':
            require(r['collection_date'] in HELD_OUT_DATES[FOLDS[0]] and r['analysis_sample'] and r['biological_plot_id'],
                    'Held-out sample identity/date missing.')
        elif r['partition']=='TRAINING_ALLOWED':
            require(r['collection_date']=='2023-11-16','Training support includes a future date.')
        else:
            require(r['partition']=='OUTSIDE_FOLD','Unknown frozen QC partition.')
    return support,qc


def validate_figure_bounds(fig,context):
    """Measure actual drawn text on Puma, excluding undrawn out-of-view ticks."""
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    box=fig.bbox
    texts=list(fig.texts)
    for legend in fig.legends:
        texts.extend(legend.get_texts())
        texts.append(legend.get_title())
    for ax in fig.axes:
        texts.extend([ax.title,ax.xaxis.label,ax.yaxis.label,*ax.texts])
        for axis in (ax.xaxis,ax.yaxis):
            low,high=sorted(axis.get_view_interval())
            for tick in axis.get_major_ticks():
                if low<=tick.get_loc()<=high:
                    texts.extend([tick.label1,tick.label2])
        annotation_boxes=[]
        for artist in ax.texts:
            if artist.get_visible() and artist.get_text():
                bounds=artist.get_window_extent(renderer)
                require(bounds.x0>=ax.bbox.x0-1 and bounds.x1<=ax.bbox.x1+1 and
                        bounds.y0>=ax.bbox.y0-1 and bounds.y1<=ax.bbox.y1+1,
                        'Skipped-panel explanatory text crosses its panel: '+context)
                annotation_boxes.append(bounds)
        for i,left in enumerate(annotation_boxes):
            require(all(not left.overlaps(right) for right in annotation_boxes[i+1:]),
                    'Skipped-panel explanatory text overlaps: '+context)
    for artist in texts:
        if artist.get_visible() and artist.get_text():
            bounds=artist.get_window_extent(renderer)
            require(bounds.x0>=box.x0-1 and bounds.x1<=box.x1+1 and bounds.y0>=box.y0-1 and bounds.y1<=box.y1+1,
                    'Figure text crosses page boundary in '+context+': '+artist.get_text())


def main():
    require(len(sys.argv)==2,'Usage: render_report.py RUN_PATH')
    run=Path(sys.argv[1]).resolve(strict=True)
    require(run.is_dir(),'Run path is not a directory.')
    config=load_json(run/'CONFIG.json')
    require(config.get('package')=='NRCS_validation_forecast_readiness_HPC_v1','Wrong report package.')
    require(config['backtest_protocol']['new_fold']['fold_id']==FOLDS[0] and
            config['backtest_protocol']['reused_fold']['fold_id']==FOLDS[1] and
            config['backtest_protocol']['new_fold']['held_out_dates']==list(HELD_OUT_DATES[FOLDS[0]]) and
            config['backtest_protocol']['new_fold']['training_dates']==['2023-11-16'], 'Frozen annual-round protocol changed.')
    require(config['user_intent']['predictive_validation_and_ten_year_work_authorized'] is True and
            config['user_intent']['independent_information_available']=='CURRENT_NRCS_FILES_ONLY','Current work/evidence scope changed.')
    sentinel=run/'ANALYSES_COMPLETE.txt'
    status_path=run/'results/REPORT_STATUS.json'
    require(not sentinel.exists() and not status_path.exists(),'Use a new isolated run; completion output already exists.')
    package_names=('CONFIG.json','PROTOCOL.txt','render_report.py','common.py','hpc_guard.py','backtest.py',
                   'baseline_core.py','fold_self_checks.py','self_checks.py','stage_source.py')
    package_pins={n:sha(safe_file(run,n)) for n in package_names}
    environment=validate_current_environment(run,config)
    sources=verify_staged_sources(run,config)
    stages=validate_staging_receipts(run,config,sources)
    backtest_path=run/'results/BACKTEST_STATUS.json'
    backtest_hash=sha(backtest_path)
    backtest=load_json(backtest_path)
    receipts=validate_engine_receipts(run,backtest)
    manifest,manifest_hash=verify_engine_manifest(run,backtest)
    inputs={name:read_csv(run/'results'/name,fields) for name,fields in CSV_FIELDS.items()}
    metrics,groups=validate_rows(inputs['plot_errors.csv'],inputs['performance.csv'],inputs['paired_performance.csv'])
    reused_rows=verify_reused_rows(run,inputs)
    readiness,requirements=validate_readiness(run,backtest)
    readiness_hash=sha(run/'results/FORECAST_READINESS.json')
    support,qc=validate_support(run)
    sections=report_sections(inputs['performance.csv'],inputs['paired_performance.csv'],support,qc,requirements,readiness)
    layout=preflight_layout(run,config,sections)
    plt.rcParams.update({'font.family':'DejaVu Sans','pdf.fonttype':42,'axes.labelcolor':INK,
                         'text.color':INK,'axes.unicode_minus':False,'savefig.facecolor':'white'})
    figures=run/'figures'
    figures.mkdir(exist_ok=False)
    outputs,figure_paths=[],[]
    for domain in DOMAINS:
        path=figures/(domain+'_2024_observed_vs_baselines.pdf')
        scatter_figure(path,domain,groups,metrics)
        figure_paths.append(path)
        outputs.append(dict(relative_path=str(path.relative_to(run)),sha256=sha(path),size_bytes=path.stat().st_size,
            file_type='figure_pdf',description=DOMAIN_LABEL[domain]+' 2024 annual-round observed versus 2023-only baselines; actual dates encoded by marker'))
        for scope in SCOPES:
            path=figures/(domain+'_2024_'+scope.lower()+'_absolute_errors.pdf')
            error_figure(path,domain,scope,groups,metrics)
            figure_paths.append(path)
            outputs.append(dict(relative_path=str(path.relative_to(run)),sha256=sha(path),size_bytes=path.stat().st_size,
                file_type='figure_pdf',description=DOMAIN_LABEL[domain]+' '+SCOPE_LABEL[scope]+' 2024 per-plot errors with full physical IDs and actual dates'))
    require(len(figure_paths)==6 and all(len(PdfReader(p).pages)==1 for p in figure_paths),'The six vector figures must each be one page.')
    temporary=run/'results/_report_text_pages.pdf'
    actual_layout=text_report(temporary,config,sections)
    require([{k:v for k,v in row.items() if k!='largest_row_height_points'} for row in actual_layout]==
            [{k:v for k,v in row.items() if k!='largest_row_height_points'} for row in layout['sections']],
            'Drawn table pagination differs from actual-text preflight.')
    report=run/'results/NRCS_validation_forecast_readiness_review.pdf'
    writer=PdfWriter()
    text_pages=len(PdfReader(temporary).pages)
    for path in [temporary,*figure_paths]:
        for page in PdfReader(path).pages:
            writer.add_page(page)
    writer.add_metadata({'/Title':'NRCS temporal backtest and numerical forecast readiness',
                         '/Subject':'Puma-only retrospective known-plot diagnostics; numerical ten-year forecast withheld'})
    with report.open('wb') as stream:
        writer.write(stream)
    temporary.unlink()
    page_count=len(PdfReader(report).pages)
    require(page_count==text_pages+6 and text_pages==layout['introductory_pages']+sum(s['pages'] for s in layout['sections']),
            'Combined report page count differs from measured sections and figure pages.')
    outputs.append(dict(relative_path=str(report.relative_to(run)),sha256=sha(report),size_bytes=report.stat().st_size,
                        file_type='review_pdf',description='24 explicit fold/method performance rows, within-round common-plot comparisons, skips, support, identity audit and readiness evidence'))
    report_manifest=run/'results/report_output_manifest.csv'
    table(report_manifest,outputs,['relative_path','sha256','size_bytes','file_type','description'])
    # Recheck every scientific/status/source byte after all report and figure creation.
    require(sha(backtest_path)==backtest_hash and load_json(backtest_path)==backtest,'Backtest status changed during reporting.')
    final_manifest,final_manifest_hash=verify_engine_manifest(run,backtest)
    require(final_manifest==manifest and final_manifest_hash==manifest_hash,'Engine outputs changed during rendering.')
    final_sources=verify_staged_sources(run,config)
    require(final_sources==sources and validate_staging_receipts(run,config,final_sources)==stages,'Original/staged provenance changed during rendering.')
    require(validate_engine_receipts(run,backtest)==receipts and verify_reused_rows(run,inputs)==reused_rows,
            'Engine freeze/audit/reuse evidence changed during rendering.')
    require(validate_readiness(run,backtest)==(readiness,requirements) and sha(run/'results/FORECAST_READINESS.json')==readiness_hash,
            'Readiness evidence changed during rendering.')
    require(validate_current_environment(run,config)==environment and
            {n:sha(safe_file(run,n)) for n in package_names}==package_pins,'Current environment or package source changed during rendering.')
    for row in read_csv(report_manifest,{'relative_path','sha256','size_bytes'}):
        path=safe_file(run,row['relative_path'])
        require(sha(path)==row['sha256'] and path.stat().st_size==integer(row['size_bytes'],'report output size'),
                'Report output changed before completion.')
    save(run/'audit/report_source_preservation.json',dict(status='PASS',execution=execution,
        engine_source_manifest_sha256=sources['engine_source']['manifest_sha256'],repair_source_manifest_sha256=sources['repair_source']['manifest_sha256'],
        original_engine_files_verified=len(sources['engine_source']['rows']),repair_source_files_verified=len(sources['repair_source']['rows']),
        upstream_source_files_verified=len(sources['upstream']),repair_engine_lineage_files_verified=len(stages['lineage']),
        package_source_hashes=package_pins,engine_outputs_unchanged=True,source_files_changed=False))
    final=dict(status=REPORT_SUCCESS,execution=execution,completed_utc=datetime.now(timezone.utc).isoformat(),
        report_relative_path=str(report.relative_to(run)),report_path=str(report.relative_to(run)),report_sha256=sha(report),report_pages=page_count,
        figure_count=6,figures_generated=True,new_2024_figure_count=6,reused_2025_figures_regenerated=False,
        output_manifest_path=str(report_manifest.relative_to(run)),output_manifest=str(report_manifest.relative_to(run)),output_manifest_sha256=sha(report_manifest),
        report_output_manifest_path=str(report_manifest.relative_to(run)),report_output_manifest_sha256=sha(report_manifest),report_output_files_verified=7,
        engine_output_manifest_path='results/output_manifest.csv',engine_output_manifest_sha256=manifest_hash,engine_output_files_verified=len(manifest),
        backtest_status_sha256=backtest_hash,forecast_readiness_sha256=readiness_hash,
        report_layout_checks_passed=True,report_layout_receipt_path='audit/report_layout_preflight.json',report_layout_receipt_sha256=sha(run/'audit/report_layout_preflight.json'),
        source_preservation_passed=True,source_integrity_passed=True,source_files_verified_unchanged_after_report=True,source_files_changed=False,
        original_engine_files_verified_unchanged_after_report=True,successful_repair_files_verified_unchanged_after_report=True,
        engine_source_manifest_sha256=sources['engine_source']['manifest_sha256'],repair_source_manifest_sha256=sources['repair_source']['manifest_sha256'],
        original_engine_job_id='24124251',successful_report_repair_job_id='24126557',new_2024_numerical_origin_job_id=execution['job_id'],
        reused_2025_fits_predictions_errors_recomputed=False,report_stage_fits_predictions_errors_computed=False,
        ten_year_forecast_generated=False,numerical_ten_year_forecast_generated=False,predictive_validation_established=False,
        scientific_review_pending=True,visual_review_pending=True,intervals_generated=False,p_values_computed=False,
        rounds_pooled_as_independent_plots=False,winner_selected=False,
        inference='RETROSPECTIVE_WITHIN_ROUND_KNOWN_PLOT_POINT_DIAGNOSTICS_ONLY')
    save(status_path,final)
    temporary_sentinel=sentinel.with_name(sentinel.name+'.tmp')
    temporary_sentinel.write_text(f'Temporal backtest and forecast-readiness report completed on Puma job {execution["job_id"]}; scientific and visual review pending.\n'
                                  +'REPORT_STATUS_SHA256='+sha(status_path)+'\n',encoding='utf-8')
    temporary_sentinel.replace(sentinel)  # Last filesystem write by this renderer.
    print(json.dumps(final,indent=2,allow_nan=False))


if __name__=='__main__':
    main()
