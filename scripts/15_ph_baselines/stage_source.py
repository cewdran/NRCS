#!/usr/bin/env python3
"""Stage immutable Whipstone pH sources and verify accepted provenance on Puma."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
import csv
import datetime
import json
import shutil
import subprocess
import sys
from common import require, save, sha, readcsv, table, safe_file, verify_staged_sources, verify_acceptance_notes

run = Path(sys.argv[1]).resolve(strict=True)
cfg = json.loads((run / 'CONFIG.json').read_text())
require(cfg.get('package') == 'NRCS_Whipstone_pH_baselines_HPC_v1', 'Unexpected package.')
env = json.loads((run / 'audit/software/environment.json').read_text())
require(env.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED'
        and all(env.get('execution', {}).get(k) == execution[k] for k in ('job_id', 'cluster', 'node'))
        and env.get('software_installed') is False, 'Pinned current runtime has not passed.')
require(env.get('versions') == cfg['runtime_expected_versions']
        and env.get('requirements_sha256') == cfg['runtime_requirements_sha256'], 'Runtime pins changed.')
for key in ('ten_year_forecast_generated',
            'numerical_ten_year_forecast', 'predictive_validation_established',
            'numerical_forecast_execution_in_this_stage', 'statistical_tests_authorized',
            'confidence_or_prediction_intervals_authorized', 'source_measurement_values_modified',
            'plot_event_measurement_response_aggregation_authorized'):
    require(cfg.get(key) is False, 'Whipstone pH scope changed: ' + key)
require(cfg.get('training_baseline_means_authorized') is True,
        'Separate derived training baseline means are not authorized.')
expected = {'baseline_parameters_computed': True, 'baseline_models_fitted': True,
            'baseline_predictions_computed': True, 'complex_models_fitted': False,
            'predictive_validation_established': False, 'ten_year_forecast_generated': False}
for key, value in cfg['expected_completed_output_flags'].items():
    require(key in expected and value is expected[key], 'Completed-output scope flag changed: ' + key)
require(set(cfg['expected_completed_output_flags']) == set(expected), 'Completed-output flag set differs.')
require(cfg.get('baseline_point_diagnostics_authorized') is True
        and cfg.get('reported_ph_arithmetic_training_baseline_authorized') is True
        and cfg.get('user_intent', {}).get('new_stage_authorized') is True,
        'This separate baseline stage is not authorized by its frozen controls.')
require(cfg.get('stage_success_status') == 'MEASURED_PH_SOURCES_STAGED_VERIFIED',
        'Unexpected staging status contract.')
expected_jobs = {'corrected': '24067160', 'functional': '24067682',
                 'feasibility_v2': '24110649', 'validation': '24128032',
                 'measured_inventory': '24147988'}
require(set(cfg['source_runs']) == set(expected_jobs) == set(cfg['sources']), 'Unexpected source aliases.')
require(all(str(cfg['source_runs'][a]['analysis_job_id']) == j for a, j in expected_jobs.items()),
        'Source job identity changed.')
review_path = cfg['accepted_review_path']
review = json.loads(safe_file(run, review_path).read_text())
require(cfg['review_locks'].get(review_path) == cfg['accepted_review_sha256']
        == sha(safe_file(run, review_path)), 'Accepted measured review is not pinned.')
require(review.get('status') == 'ACCEPTED_SOURCE_PRESERVING_MEASURED_OUTCOME_INVENTORY'
        and review.get('main_analysis_job_id') == '24147988'
        and review.get('main_cluster') == 'puma' and review.get('main_node') == 'r7u11n1'
        and review.get('main_scheduler_state') == 'COMPLETED'
        and review.get('main_scheduler_exit_code') == '0:0' and review.get('pipeline_exit_code') == 0
        and review.get('current_hpc_pending_review_receipts_modified') is False
        and review.get('predictive_validation_established') is False
        and review.get('ten_year_forecast_generated') is False
        and review.get('collector_final_accounting_established') is False,
        'Accepted measured inventory provenance or limits changed.')
# The frozen path maps close every companion-note reference without changing
# the historical acceptance record or its original relative-path strings.
for relative, expected in cfg['review_locks'].items():
    require(sha(safe_file(run, relative)) == expected, 'Review asset changed: ' + relative)
validation_review_path = 'review/ACCEPTED_RETURNED_REVIEW_24128032.json'
validation_review = json.loads(safe_file(run, validation_review_path).read_text())
require(sha(safe_file(run, validation_review_path)) == cfg['review_locks'][validation_review_path]
        and validation_review.get('status') == 'ACCEPTED_LIMITED_RETROSPECTIVE_DIAGNOSTICS_AND_READINESS_AUDIT'
        and validation_review.get('main_analysis_job_id') == '24128032'
        and validation_review.get('original_engine_whole_job', {}).get('job_id') == '24124251'
        and validation_review['original_engine_whole_job'].get('state') == 'FAILED'
        and validation_review.get('separate_report_repair_main', {}).get('job_id') == '24126557'
        and validation_review['separate_report_repair_main'].get('state') == 'COMPLETED'
        and validation_review.get('predictive_validation_established') is False
        and validation_review.get('ten_year_forecast_generated') is False,
        'Historical validation lineage or scope changed.')
verify_acceptance_notes(run, cfg)

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
vpins = validation_review['source_pins']['returned_file_sha256']
require(all(cfg['sources']['validation'].get(n) == h for n, h in vpins.items()),
        'Latest review byte pins differ from source config.')
vend = json.loads((validation / 'audit/run_end.json').read_text())
require(vend['execution']['node'] == 'r7u09n1'
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
report_relative = validation_review['source_pins']['report_pdf_relative_path']
require(vreport.get('report_relative_path') == report_relative
        and vreport.get('report_sha256') == validation_review['source_pins']['report_pdf_sha256']
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

# Acceptance is dated external evidence; original inventory/report receipts
# must remain review-pending byte-identical receipts from job 24147988.
measured = snapshot_root / 'measured_inventory'
mi = json.loads((measured / 'results/INVENTORY_STATUS.json').read_text())
mr = json.loads((measured / 'results/REPORT_STATUS.json').read_text())
me = json.loads((measured / 'audit/run_end.json').read_text())
mp = json.loads((measured / 'audit/INVENTORY_SOURCE_PRESERVATION.json').read_text())
for name, record in [('inventory', mi), ('report', mr), ('end', me), ('preservation', mp)]:
    require(all(record.get('execution', {}).get(k) == v for k, v in
                [('job_id', '24147988'), ('cluster', 'puma'), ('node', 'r7u11n1')]),
            'Accepted measured execution differs: ' + name)
require(me.get('pipeline_exit_code') == 0 and me.get('runtime_interpreter_fallback') is False,
        'Accepted measured pipeline/runtime did not complete.')
require(mi.get('status') == 'HPC_MEASURED_OUTCOME_INVENTORY_COMPLETED_REVIEW_PENDING'
        and mi.get('inventory_computed') is True and mi.get('scientific_inventory_completed') is True
        and mr.get('status') == 'MEASURED_OUTCOME_INVENTORY_REPORT_COMPLETED_REVIEW_PENDING'
        and mr.get('report_layout_checks_passed') is True and mr.get('figure_count') == 0
        and mr.get('figures_generated') is False,
        'Accepted original measured receipts changed or were rewritten.')
for record in (mi, mr, mp):
    require(record.get('source_integrity_passed') is True and record.get('source_files_changed') is False,
            'Accepted measured source preservation differs.')
for record in (mi, mr):
    for key in ('models_fitted', 'statistical_tests_performed', 'measurement_values_aggregated',
                'predictive_validation_established', 'ten_year_forecast_generated', 'numerical_ten_year_forecast'):
        require(record.get(key) is False, 'Accepted inventory scope changed: ' + key)
    require(record.get('scientific_review_pending') is True,
            'Original measured pending-review receipt was rewritten.')
require(mi.get('output_manifest_sha256') == sha(measured / 'results/ENGINE_OUTPUT_MANIFEST.csv')
        and mr.get('engine_output_manifest_sha256') == mi['output_manifest_sha256']
        and mr.get('inventory_status_sha256') == sha(measured / 'results/INVENTORY_STATUS.json')
        and mr.get('report_output_manifest_sha256') == sha(measured / 'results/report_output_manifest.csv')
        and mr.get('report_sha256') == sha(measured / 'results/NRCS_measured_outcomes_inventory_review.pdf')
        and mr.get('report_sha256') == review['source_pins']['returned_file_sha256']['results/NRCS_measured_outcomes_inventory_review.pdf'],
        'Accepted measured manifest/report linkage differs.')
require((measured / 'ANALYSES_COMPLETE.txt').read_text() ==
        'Measured-outcome inventory and report completed on Puma job 24147988; scientific and visual review pending.\n'
        + 'REPORT_STATUS_SHA256=' + sha(measured / 'results/REPORT_STATUS.json') + '\n',
        'Accepted measured completion sentinel differs.')
for relative, expected in cfg['sources']['measured_inventory'].items():
    pinned_by_review = review['source_pins']['returned_file_sha256'].get(relative)
    require(pinned_by_review is None or pinned_by_review == expected,
            'Measured source lock disagrees with accepted decision: ' + relative)
measured_fixture = json.loads((measured / 'audit/inventory_self_checks.json').read_text())
require(measured_fixture.get('status') == 'PASS' and measured_fixture.get('passed') is True
        and measured_fixture.get('real_source_data_read_by_fixtures') is False
        and measured_fixture.get('checks') and all(r.get('passed') is True for r in measured_fixture['checks'])
        and all(measured_fixture.get('execution', {}).get(k) == v for k, v in
                [('job_id', '24147988'), ('cluster', 'puma'), ('node', 'r7u11n1')]),
        'Accepted measured Puma fixture receipt differs.')

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
    'status': 'MEASURED_PH_SOURCES_STAGED_VERIFIED', 'execution': execution,
    'completed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'source_manifest_path': 'audit/source_manifest.csv',
    'source_manifest_sha256': sha(run / 'audit/source_manifest.csv'),
    'review_lock_manifest_path': 'audit/review_lock_manifest.csv',
    'review_lock_manifest_sha256': sha(run / 'audit/review_lock_manifest.csv'),
    'validation_output_manifest_path': 'audit/validation_output_manifest.csv',
    'validation_output_manifest_sha256': sha(run / 'audit/validation_output_manifest.csv'),
    'accepted_review_path': cfg['accepted_review_path'], 'accepted_review_sha256': cfg['accepted_review_sha256'],
    'latest_validation_job_id': '24128032', 'latest_measured_inventory_job_id': '24147988', 'source_integrity_passed': True,
    'source_files_verified_unchanged_after_staging': True, 'source_files_changed': False,
    'stage_scope': 'Administrative byte/provenance staging only; new pH baselines have not run.',
    'original_engine_whole_job_state': 'FAILED', 'original_engine_job_id': '24124251',
    'successful_report_repair_job_id': '24126557', 'historic_status_modified': False,
    'prior_collector_final_accounting_established': False,
    'models_fitted': False, 'statistical_tests_performed': False, 'forecasts_generated': False,
    'ten_year_forecast_generated': False, 'predictive_validation_established': False,
})
print('MEASURED_PH_SOURCES_STAGED_VERIFIED')
