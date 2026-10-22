#!/usr/bin/env python3
"""Refresh PDFs from locked Puma outputs; do not recompute statistical results."""
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
import datetime, json, shutil, subprocess, sys
from common import require, sha, save, readcsv

run = Path(sys.argv[1]).resolve()
cfg = json.loads((run / 'CONFIG.json').read_text())
source = Path(cfg['source_run']['path'])
snapshot = run / 'source_snapshot' / 'original_comparisons'
verified = []
for name, expected in cfg['sources'].items():
    rel = Path(name)
    require(not rel.is_absolute() and '..' not in rel.parts, 'Unsafe source path')
    target = snapshot / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / rel, target)
    require(sha(target) == expected, 'Source checksum mismatch: ' + name)
    verified.append({'source': str(source / rel), 'sha256': expected})
state = json.loads((snapshot / 'HPC_STATUS.json').read_text())
require(state.get('analysis_job_id') == cfg['source_run']['analysis_job_id']
        and state.get('scheduler_state') == 'COMPLETED'
        and state.get('scheduler_exit_code') == '0:0'
        and state.get('pipeline_exit_code') == 0, 'Comparison source did not complete successfully')
comparison = json.loads((snapshot / 'results/COMPARISON_STATUS.json').read_text())
require(comparison['execution']['job_id'] == cfg['source_run']['analysis_job_id']
        and comparison['original_scientific_data_changed'] is False, 'Invalid comparison source provenance')
old_environment = json.loads((snapshot / 'audit/runtime_environment/environment.json').read_text())
new_environment = json.loads((run / 'audit/runtime_environment/environment.json').read_text())
for key in ['versions', 'references', 'pipeline_script_sha256']:
    require(old_environment[key] == new_environment[key], 'Runtime changed: ' + key)
save(run / 'audit/SOURCE_VERIFICATION.json', verified)

results = run / 'results'
results.mkdir()
input_hashes = {}
for name in cfg['render_input_files']:
    target = results / name
    shutil.copy2(snapshot / 'results' / name, target)
    input_hashes[name] = sha(target)
shutil.copy2(snapshot / 'results/FEATURE_PANEL.csv', run / 'FEATURE_PANEL.csv')
summary = (
    'NRCS FUNCTIONAL FIGURE REFRESH - PUMA EXECUTION ' + execution['job_id'] + '\n'
    'Numerical comparison results were computed by source job 24074507.\n'
    'This job refreshes axis layout only; no tests, PCA or normalization were rerun.\n\n'
    + (snapshot / 'results/RUN_SUMMARY.txt').read_text())
(results / 'RUN_SUMMARY.txt').write_text(summary)
input_hashes['RUN_SUMMARY.txt'] = sha(results / 'RUN_SUMMARY.txt')
command = ['Rscript', '--vanilla', str(run / 'render_report.R'), str(run)]
save(run / 'audit/commands.json', [{'argv': command, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}])
with (run / 'logs/report.log').open('w') as log:
    subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
for name, digest in input_hashes.items():
    require(sha(results / name) == digest, 'Rendering changed a source table: ' + name)
manifest = readcsv(results / 'figure_manifest.csv')
require(len(manifest) == cfg['expected_pdf_count'], 'Unexpected refreshed PDF count')
for row in manifest:
    rel = Path(row['file'])
    require(not rel.is_absolute() and '..' not in rel.parts, 'Unsafe figure path')
    require((run / rel).is_file() and (run / rel).stat().st_size > 1000, 'Missing refreshed PDF: ' + row['file'])
save(results / 'FIGURE_REFRESH_STATUS.json', {
    'execution': execution,
    'status': 'FIGURE_REFRESH_COMPLETED_VISUAL_REVIEW_PENDING',
    'source_comparison_job': cfg['source_run']['analysis_job_id'],
    'statistical_tests_rerun': False,
    'PCA_recomputed': False,
    'normalization_rerun': False,
    'source_files_changed': False,
    'source_table_copies_verified_unchanged_after_rendering': True,
    'render_input_sha256': input_hashes,
    'PDF_count': len(manifest),
    'change': cfg['change'],
    'visual_review': 'Pending inspection of refreshed PDFs returned from Puma'
})
(run / 'ANALYSES_COMPLETE.txt').write_text('Figure refresh completed on Puma; visual review pending.\n')
print('FIGURE_REFRESH_COMPLETED_JOB=' + execution['job_id'])
