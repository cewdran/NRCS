#!/usr/bin/env python3
"""Copy immutable measured inventory sources and validate provenance on Puma."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
import csv
import datetime
import json
import shutil
import subprocess
import sys
from common import require, save, sha, readcsv, table, safe_file, verify_staged_sources

run = Path(sys.argv[1]).resolve(strict=True)
cfg = json.loads((run / 'CONFIG.json').read_text())
require(cfg.get('package') == 'NRCS_measured_outcomes_HPC_v1', 'Unexpected package.')
env = json.loads((run / 'audit/software/environment.json').read_text())
require(env.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED'
        and all(env.get('execution', {}).get(k) == execution[k] for k in ('job_id', 'cluster', 'node'))
        and env.get('software_installed') is False, 'Pinned current runtime has not passed.')
require(env.get('versions') == cfg['runtime_expected_versions']
        and env.get('requirements_sha256') == cfg['runtime_requirements_sha256'], 'Runtime pins changed.')
for key in ('models_fitted', 'statistical_tests_performed', 'forecasts_generated',
            'measurement_values_aggregated', 'ten_year_forecast_generated',
            'numerical_ten_year_forecast', 'predictive_validation_established',
            'numerical_forecast_execution_in_this_stage'):
    require(cfg.get(key) is False, 'Measured inventory scope changed: ' + key)
expected_jobs = {'corrected': '24067160', 'functional': '24067682',
                 'feasibility_v2': '24110649', 'validation': '24128032'}
require(set(cfg['source_runs']) == set(expected_jobs) == set(cfg['sources']), 'Unexpected source aliases.')
require(all(str(cfg['source_runs'][a]['analysis_job_id']) == j for a, j in expected_jobs.items()),
        'Source job identity changed.')
review = json.loads(safe_file(run, cfg['accepted_review_path']).read_text())
require(cfg['review_locks'].get(cfg['accepted_review_path']) == cfg['accepted_review_sha256']
        == sha(safe_file(run, cfg['accepted_review_path'])), 'Accepted review is not pinned.')
require(review.get('status') == 'ACCEPTED_LIMITED_RETROSPECTIVE_DIAGNOSTICS_AND_READINESS_AUDIT'
        and review.get('main_analysis_job_id') == '24128032'
        and review.get('main_scheduler_state') == 'COMPLETED'
        and review.get('main_scheduler_exit_code') == '0:0' and review.get('pipeline_exit_code') == 0,
        'Latest accepted validation review differs.')
require(review.get('original_engine_whole_job', {}).get('job_id') == '24124251'
        and review['original_engine_whole_job'].get('state') == 'FAILED'
        and review.get('separate_report_repair_main', {}).get('job_id') == '24126557'
        and review['separate_report_repair_main'].get('state') == 'COMPLETED'
        and review.get('predictive_validation_established') is False
        and review.get('ten_year_forecast_generated') is False,
        'Historical lineage or scope changed.')
for record in review['review_records'].values():
    for kind in ('json', 'text'):
        relative = 'review/' + record[kind + '_path']
        require(cfg['review_locks'].get(relative) == record[kind + '_sha256'],
                'Accepted review does not pin its companion note: ' + relative)
for relative, expected in cfg['review_locks'].items():
    require(sha(safe_file(run, relative)) == expected, 'Review asset changed: ' + relative)

snapshot_root = run / 'source_snapshot'
require(not snapshot_root.exists(), 'Never overwrite a source snapshot; submit a fresh isolated run.')
source_rows, provenance_rows, accounting_text = [], [], []
for alias in sorted(cfg['sources']):
    source = cfg['source_runs'][alias]
    original_root = Path(source['path']).resolve(strict=True)
    job = str(source['analysis_job_id'])
    require(original_root != run and not run.is_relative_to(original_root), 'Source run must stay immutable.')
    for relative, expected in sorted(cfg['sources'][alias].items()):
        original = safe_file(original_root, relative)
        require(sha(original) == expected, 'Pinned original changed before staging: ' + alias + '/' + relative)
    status = json.loads(safe_file(original_root, 'HPC_STATUS.json').read_text())
    end = json.loads(safe_file(original_root, 'audit/run_end.json').read_text())
    origin = end.get('execution', {})
    require(str(status.get('analysis_job_id')) == job and status.get('scheduler_state') == 'COMPLETED'
            and status.get('scheduler_exit_code') == '0:0' and status.get('pipeline_exit_code') == 0,
            'Source main job did not complete: ' + alias)
    require(end.get('stage') == 'end' and end.get('pipeline_exit_code') == 0
            and str(origin.get('job_id')) == job and origin.get('cluster') == 'puma' and origin.get('node'),
            'Source runtime provenance failed: ' + alias)
    proc = subprocess.run(['sacct', '-j', job, '--starttime=2026-09-30', '--parsable2',
                           '--format=JobIDRaw,State,ExitCode,NodeList'],
                          capture_output=True, text=True, check=True, timeout=30)
    entries = [line.split('|') for line in proc.stdout.splitlines()[1:]]
    main_rows = [r for r in entries if len(r) >= 4 and r[0] == job]
    require(len(main_rows) == 1 and main_rows[0][1:3] == ['COMPLETED', '0:0'],
            'Live source main accounting differs: ' + alias)
    nodes = subprocess.check_output(['scontrol', 'show', 'hostnames', main_rows[0][3]],
                                    text=True, timeout=30).splitlines()
    require(origin['node'] in nodes, 'Source recorded node differs from accounting: ' + alias)
    accounting_text.append('SOURCE=' + alias + '\n' + proc.stdout)
    provenance_rows.append({'source': alias, 'source_job_id': job, 'cluster': 'puma',
                            'node': origin['node'], 'main_scheduler_state': 'COMPLETED',
                            'main_scheduler_exit_code': '0:0', 'pipeline_exit_code': 0,
                            'collector_final_state_required': False,
                            'historic_status_modified': False})
    for relative, expected in sorted(cfg['sources'][alias].items()):
        original = safe_file(original_root, relative)
        destination = snapshot_root / alias / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        destination.chmod(0o444)
        require(sha(original) == expected == sha(destination), 'Source changed while copying: ' + relative)
        source_rows.append({'source': alias, 'relative_path': relative, 'sha256': expected,
                            'size_bytes': destination.stat().st_size, 'source_job_id': job,
                            'original_path': str(original), 'snapshot_path': str(destination.resolve())})

validation = snapshot_root / 'validation'
vpins = review['source_pins']['returned_file_sha256']
require(all(cfg['sources']['validation'].get(n) == h for n, h in vpins.items()),
        'Latest review byte pins differ from source config.')
vend = json.loads((validation / 'audit/run_end.json').read_text())
require(vend['execution']['node'] == cfg['validation_origin']['node'] == 'r7u09n1'
        and vend.get('runtime_interpreter_fallback') is False, 'Latest validation runtime changed.')
vstart = json.loads((validation / 'audit/run_start.json').read_text())
venv = json.loads((validation / 'audit/software/environment.json').read_text())
require(all(r['execution']['job_id'] == '24128032' and r['execution']['cluster'] == 'puma'
            and r['execution']['node'] == 'r7u09n1' for r in (vstart, vend, venv)),
        'Latest validation stage executions disagree.')
require(venv.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED'
        and venv.get('versions') == cfg['runtime_expected_versions'] and venv.get('software_installed') is False
        and venv.get('requirements_sha256') == cfg['runtime_requirements_sha256']
        and venv.get('runtime_ready_sha256') == sha(validation / 'audit/software/original_runtime_READY.json'),
        'Accepted validation runtime proof differs.')
vback = json.loads((validation / 'results/BACKTEST_STATUS.json').read_text())
vreport = json.loads((validation / 'results/REPORT_STATUS.json').read_text())
require(vback.get('status') == 'TEMPORAL_BACKTEST_COMPLETED_REVIEW_PENDING'
        and vback.get('engine_source_job_id') == '24124251'
        and vback.get('reused_2025_fits_predictions_errors_recomputed') is False
        and vback.get('reused_2025_outputs_byte_identical') is True
        and vback.get('predictive_validation_established') is False
        and vback.get('ten_year_forecast_generated') is False, 'Accepted validation scope changed.')
require(vreport.get('status') == 'TEMPORAL_BACKTEST_REPORT_COMPLETED_REVIEW_PENDING'
        and vreport.get('original_engine_job_id') == '24124251'
        and vreport.get('successful_report_repair_job_id') == '24126557'
        and vreport.get('backtest_status_sha256') == sha(validation / 'results/BACKTEST_STATUS.json')
        and vreport.get('forecast_readiness_sha256') == sha(validation / 'results/FORECAST_READINESS.json'),
        'Accepted report linkage differs.')
report_relative = review['source_pins']['report_pdf_relative_path']
require(vreport.get('report_relative_path') == report_relative
        and vreport.get('report_sha256') == review['source_pins']['report_pdf_sha256']
        == sha(safe_file(validation, report_relative)), 'Accepted report bytes differ.')
require((validation / 'ANALYSES_COMPLETE.txt').read_text() ==
        'Temporal backtest and forecast-readiness report completed on Puma job 24128032; scientific and visual review pending.\n'
        + 'REPORT_STATUS_SHA256=' + sha(validation / 'results/REPORT_STATUS.json') + '\n',
        'Accepted completion sentinel does not link to the report.')
output_rows, expected_outputs = [], {}
for relative, expected in cfg['validation_output_locks'].items():
    target = safe_file(Path(cfg['source_runs']['validation']['path']), relative)
    require(sha(target) == expected, 'Accepted validation output changed: ' + relative)
    output_rows.append({'relative_path': relative, 'sha256': expected, 'size_bytes': target.stat().st_size})
for manifest_name, receipt_key in [('results/output_manifest.csv', 'output_manifest_sha256'),
                                    ('results/report_output_manifest.csv', 'report_output_manifest_sha256')]:
    manifest = validation / manifest_name
    linked = vback if manifest_name.endswith('/output_manifest.csv') else vreport
    require(linked.get(receipt_key) == sha(manifest), 'Accepted output manifest receipt differs.')
    for row in readcsv(manifest):
        name = row['relative_path']
        require(name not in expected_outputs and row['sha256'] == cfg['validation_output_locks'].get(name),
                'Unexpected or duplicate accepted output entry: ' + name)
        target = safe_file(Path(cfg['source_runs']['validation']['path']), name)
        require(target.stat().st_size == int(row['size_bytes']), 'Accepted output size differs: ' + name)
        expected_outputs[name] = row['sha256']
require(expected_outputs == cfg['validation_output_locks'], 'Accepted output sets differ.')

review_rows = [{'relative_path': n, 'sha256': h, 'size_bytes': safe_file(run, n).stat().st_size}
               for n, h in sorted(cfg['review_locks'].items())]
table(run / 'audit/source_manifest.csv', source_rows)
table(run / 'audit/source_provenance.csv', provenance_rows)
table(run / 'audit/review_lock_manifest.csv', review_rows)
table(run / 'audit/validation_output_manifest.csv', output_rows)
(run / 'audit/source_sacct.tsv').write_text('\n'.join(accounting_text))
checks = verify_staged_sources(run, cfg)
table(run / 'audit/staged_source_integrity.csv', checks)
save(run / 'audit/SOURCE_STAGE_STATUS.json', {
    'status': 'IMMUTABLE_MEASURED_SOURCES_VERIFIED_AND_STAGED', 'execution': execution,
    'completed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'source_manifest_path': 'audit/source_manifest.csv',
    'source_manifest_sha256': sha(run / 'audit/source_manifest.csv'),
    'review_lock_manifest_path': 'audit/review_lock_manifest.csv',
    'review_lock_manifest_sha256': sha(run / 'audit/review_lock_manifest.csv'),
    'validation_output_manifest_path': 'audit/validation_output_manifest.csv',
    'validation_output_manifest_sha256': sha(run / 'audit/validation_output_manifest.csv'),
    'accepted_review_path': cfg['accepted_review_path'], 'accepted_review_sha256': cfg['accepted_review_sha256'],
    'latest_validation_job_id': '24128032', 'source_integrity_passed': True,
    'source_files_verified_unchanged_after_staging': True, 'source_files_changed': False,
    'stage_scope': 'Administrative byte/provenance staging only; measured scientific inventory has not run.',
    'original_engine_whole_job_state': 'FAILED', 'original_engine_job_id': '24124251',
    'successful_report_repair_job_id': '24126557', 'historic_status_modified': False,
    'prior_collector_final_accounting_established': False,
    'models_fitted': False, 'statistical_tests_performed': False, 'forecasts_generated': False,
    'ten_year_forecast_generated': False, 'predictive_validation_established': False,
})
print('IMMUTABLE_MEASURED_SOURCES_VERIFIED_AND_STAGED')
