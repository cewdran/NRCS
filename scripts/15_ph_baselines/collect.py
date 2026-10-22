#!/usr/bin/env python3
"""Guarded byte-only after-any collector; stage success is separate from archive success."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
import datetime
import json
import os
import subprocess
import sys
import time
import zipfile
from common import require, save, sha, readcsv, table, safe_file, verify_staged_sources

run, main = Path(sys.argv[1]).resolve(strict=True), sys.argv[2]
(run / 'audit').mkdir(exist_ok=True)
errors, records = [], {}
receipt, cfg, contract = {}, {}, {}
state, code, node_list, stdout, stderr = 'UNKNOWN', '', '', '', ''
pipeline = False
verified_package, package_manifest_hash = {}, None


def load(name):
    value = json.loads(safe_file(run, name).read_text())
    require(isinstance(value, dict), 'Receipt is not an object: ' + name)
    return value


def verify_manifest(name, expected):
    rows = readcsv(safe_file(run, name))
    require(rows and len(rows) == len(expected), 'Output manifest size differs: ' + name)
    seen = set()
    for row in rows:
        relative = row['relative_path']
        require(relative not in seen, 'Duplicate output manifest entry: ' + relative)
        seen.add(relative)
        path = run / relative
        require(not any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(run)),
                'Linked output manifest entry: ' + relative)
        target = safe_file(run, relative)
        require(sha(target) == row['sha256'] and target.stat().st_size == int(row['size_bytes']),
                'Output hash/size changed: ' + relative)
    require(seen == set(expected), 'Exact output set differs: ' + name)
    return rows


def current(record, node, label):
    origin = record.get('execution', {})
    require(origin.get('job_id') == main and origin.get('cluster') == 'puma'
            and origin.get('node') == node and bool(node), 'Current execution mismatch: ' + label)


try:
    require(main.isdigit(), 'Invalid main job identifier.')
    receipt, cfg = load('submission.json'), load('CONFIG.json')
    require(receipt.get('jobs', {}).get('analysis') == main
            and receipt.get('jobs', {}).get('collector') == execution['job_id'],
            'Collector allocation or main differs from submission receipt.')
    require(cfg.get('package') == 'NRCS_Whipstone_pH_baselines_HPC_v1', 'Unexpected collector package.')
    contract = load(cfg['output_contract_path'])
    require(contract.get('package') == cfg['package'], 'Output contract package differs.')
except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
    errors.append('Collector controls: ' + str(exc))

if receipt:
    for attempt in range(4):
        try:
            proc = subprocess.run(['sacct', '-j', main, '--starttime=' + receipt['accounting_since'],
                                   '--parsable2', '--format=JobIDRaw,State,ExitCode,NodeList'],
                                  capture_output=True, text=True, timeout=30)
            stdout, stderr = proc.stdout, proc.stderr
            entries = [line.split('|') for line in stdout.splitlines()[1:]]
            matches = [r for r in entries if len(r) >= 4 and r[0] == main]
            require(len(matches) <= 1, 'Ambiguous main accounting rows.')
            if matches:
                state, code, node_list = matches[0][1:4]
        except (OSError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
            stderr = str(exc)
        if state not in ('UNKNOWN', 'PENDING', 'RUNNING', 'COMPLETING'):
            break
        if attempt < 3:
            time.sleep(5)
(run / 'audit/sacct_analysis.tsv').write_text(stdout)
(run / 'audit/sacct_stderr.txt').write_text(stderr)

for name in ('audit/run_start.json', 'audit/run_end.json', 'audit/software/environment.json',
             'audit/SOURCE_STAGE_STATUS.json', 'audit/ph_self_checks.json',
             'audit/PH_ENGINE_STATUS.json', 'audit/PH_SOURCE_PRESERVATION.json', 'results/REPORT_STATUS.json'):
    try:
        records[name] = load(name)
    except (OSError, ValueError, RuntimeError) as exc:
        errors.append(name + ': ' + str(exc))
end = records.get('audit/run_end.json', {})
report = records.get('results/REPORT_STATUS.json', {})
engine = records.get('audit/PH_ENGINE_STATUS.json', {})

if not errors:
    try:
        node = end.get('execution', {}).get('node')
        for name, record in records.items():
            current(record, node, name)
        start, env = records['audit/run_start.json'], records['audit/software/environment.json']
        require(start.get('stage') == 'start' and end.get('stage') == 'end'
                and end.get('pipeline_exit_code') == 0, 'Main pipeline did not complete.')
        require(start.get('interpreter') == end.get('interpreter') == env.get('executable')
                == receipt['environment_python'] and start.get('runtime_interpreter_fallback') is False
                and end.get('runtime_interpreter_fallback') is False, 'Scientific stages did not use pinned runtime.')
        ready_path = safe_file(run, 'audit/software/original_runtime_READY.json')
        ready = json.loads(ready_path.read_text())
        require(env.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED'
                and env.get('versions') == cfg['runtime_expected_versions']
                and env.get('requirements_sha256') == cfg['runtime_requirements_sha256']
                and env.get('runtime_package') == cfg['runtime_package'] and env.get('software_installed') is False
                and env.get('runtime_ready_sha256') == cfg['runtime_ready_sha256'] == sha(ready_path)
                and env.get('python') == ready.get('python') == start.get('python_version')
                == end.get('python_version') == cfg['runtime_expected_python']
                and env.get('python', '').startswith('3.11.4 ')
                and ready.get('executable') == env.get('executable')
                and ready.get('versions') == cfg['runtime_expected_versions']
                and ready.get('requirements_sha256') == cfg['runtime_requirements_sha256'],
                'Pinned environment/READY/build verification failed.')
        fixture = records['audit/ph_self_checks.json']
        require(fixture.get('status') == 'PASS' and fixture.get('passed') is True
                and bool(fixture.get('checks'))
                and fixture.get('fixture_kind') == 'SCIENTIFIC_SYNTHETIC_EDGE_CASES_HPC_ONLY'
                and fixture.get('scientific_source_data_read') is False
                and fixture.get('response_spec_sha256') == sha(safe_file(run, cfg['response_spec_path']))
                and fixture.get('output_contract_sha256') == sha(safe_file(run, cfg['output_contract_path']))
                and all(row.get('passed') is True for row in fixture['checks']), 'Puma self checks did not pass.')
        stage = records['audit/SOURCE_STAGE_STATUS.json']
        require(stage.get('status') == cfg['stage_success_status'] == 'MEASURED_PH_SOURCES_STAGED_VERIFIED'
                and stage.get('source_integrity_passed') is True
                and stage.get('source_files_verified_unchanged_after_staging') is True
                and stage.get('source_files_changed') is False
                and stage.get('latest_validation_job_id') == '24128032'
                and stage.get('latest_measured_inventory_job_id') == '24147988', 'Source staging failed.')
        review_path = cfg['accepted_review_path']
        require(stage.get('accepted_review_path') == review_path and stage.get('accepted_review_sha256')
                == cfg['accepted_review_sha256'] == sha(safe_file(run, review_path)), 'Accepted inventory lock changed.')
        review = load(review_path)
        require(review.get('status') == 'ACCEPTED_SOURCE_PRESERVING_MEASURED_OUTCOME_INVENTORY'
                and review.get('main_analysis_job_id') == '24147988'
                and review.get('current_hpc_pending_review_receipts_modified') is False,
                'Accepted inventory decision differs.')
        source_manifest = 'audit/source_manifest.csv'
        require(stage.get('source_manifest_path') == source_manifest and stage.get('source_manifest_sha256')
                == sha(safe_file(run, source_manifest)), 'Source manifest receipt changed.')
        expected_sources = {(a, n): h for a, files in cfg['sources'].items() for n, h in files.items()}
        source_rows = readcsv(safe_file(run, source_manifest))
        require(len(source_rows) == len(expected_sources) and
                {(r['source'], r['relative_path']) for r in source_rows} == set(expected_sources),
                'Exact staged source set differs.')
        for row in source_rows:
            alias, relative = row['source'], row['relative_path']
            original = safe_file(Path(cfg['source_runs'][alias]['path']), relative)
            snapshot = safe_file(run / 'source_snapshot' / alias, relative)
            require(row['sha256'] == expected_sources[(alias, relative)] == sha(original) == sha(snapshot)
                    and row['source_job_id'] == str(cfg['source_runs'][alias]['analysis_job_id'])
                    and Path(row['original_path']).resolve(strict=True) == original
                    and Path(row['snapshot_path']).resolve(strict=True) == snapshot
                    and original.stat().st_size == snapshot.stat().st_size == int(row['size_bytes']),
                    'Source staging lineage changed: ' + alias + '/' + relative)
        for key, path, expected in (
            ('review_lock_manifest', 'audit/review_lock_manifest.csv', cfg['review_locks']),
            ('validation_output_manifest', 'audit/validation_output_manifest.csv', cfg['validation_output_locks'])):
            require(stage.get(key + '_path') == path and stage.get(key + '_sha256') == sha(safe_file(run, path)),
                    'Stage companion manifest changed: ' + path)
            rows = readcsv(safe_file(run, path))
            require(len(rows) == len(expected) and {r['relative_path']: r['sha256'] for r in rows} == expected,
                    'Stage companion exact lock set differs: ' + path)
        source_checks = verify_staged_sources(run, cfg)
        require(source_checks and all(r['status'] == 'UNCHANGED' for r in source_checks), 'Sources changed.')
        table(run / 'audit/collector_source_integrity.csv', source_checks)
        require(engine.get('status') == contract['engine_success_status'], 'Engine status is not complete.')
        for key in contract['engine_status_required_keys']:
            require(key in engine, 'Missing engine receipt key: ' + key)
        for record, flags in ((engine, contract['successful_flags']), (report, contract['report_required_flags'])):
            for key, value in flags.items():
                require(record.get(key) is value, 'Completed-output scope changed: ' + key)
        for key, value in cfg['expected_completed_output_flags'].items():
            require(engine.get(key) is value and report.get(key) is value, 'CONFIG completion scope changed: ' + key)
        require(engine.get('source_integrity_passed') is True
                and engine.get('source_files_verified_unchanged_after_audit') is True
                and engine.get('source_files_changed') is False, 'Engine source preservation failed.')
        engine_manifest = contract['engine_manifest_path']
        require(engine.get('engine_output_manifest_path') == engine_manifest
                and engine.get('engine_output_manifest_sha256') == sha(safe_file(run, engine_manifest)),
                'Engine output manifest receipt changed.')
        verify_manifest(engine_manifest, contract['engine_output_files'])
        require(engine.get('fixture_receipt_sha256') == sha(safe_file(run, contract['self_check_receipt_path']))
                and engine.get('prediction_freeze_sha256') == sha(safe_file(run, 'results/prediction_freeze.json')),
                'Engine fixture/prediction-freeze links differ.')
        freeze = load('results/prediction_freeze.json')
        current(freeze, node, 'prediction freeze')
        spec = load(cfg['response_spec_path'])
        require(freeze.get('status') == 'ALL_DECLARED_ROUND_PREDICTIONS_FROZEN_BEFORE_TARGET_SCORING'
                and freeze.get('freeze_before_any_target_scoring') is True
                and freeze.get('target_outcome_used_for_training') is False
                and freeze.get('retrospective_execution_order_only') is True
                and freeze.get('source_outcomes_previously_viewed') is spec['source_outcomes_previously_viewed']
                and freeze.get('predictions_sha256') == sha(safe_file(run, 'results/predictions.csv'))
                and freeze.get('fits_sha256') == sha(safe_file(run, 'results/fits.json')),
                'Saved training/prediction freeze provenance differs.')
        frozen_rounds = freeze.get('folds', [])
        require(len(frozen_rounds) == len(spec['folds'])
                and {r['fold_id'] for r in frozen_rounds} == {r['fold_id'] for r in spec['folds']},
                'Frozen annual fold set differs.')
        for observed, declared in zip(frozen_rounds, spec['folds']):
            require(all(observed.get(k) == declared[k] for k in
                        ('fold_id', 'target_round_id', 'training_round_ids', 'cutoff_date')),
                    'Frozen annual-round/cutoff metadata differs.')
        training_names = {'results/training_membership.csv', 'results/training_plot_means.csv'}
        require(set(freeze.get('training_files', {})) == training_names
                and all(sha(safe_file(run, n)) == freeze['training_files'][n] for n in training_names),
                'Frozen training membership/plot-mean byte pins differ.')
        preservation_path = contract['source_preservation_path']
        preservation = records[preservation_path]
        require(engine.get('source_preservation_path') == preservation_path
                and engine.get('source_preservation_sha256') == sha(safe_file(run, preservation_path))
                and preservation.get('source_integrity_passed') is True
                and preservation.get('source_files_changed') is False
                and preservation.get('source_files_verified_unchanged_after_audit') is True
                and preservation.get('source_measurement_values_modified') is False,
                'Engine preservation receipt differs.')
        require(engine.get('response_spec_sha256') == sha(safe_file(run, cfg['response_spec_path']))
                and engine.get('output_contract_sha256') == sha(safe_file(run, cfg['output_contract_path'])),
                'Engine does not pin frozen response/output controls.')
        require(report.get('status') == contract['report_success_status']
                and report.get('source_integrity_passed') is True
                and report.get('source_preservation_passed') is True
                and report.get('source_files_verified_unchanged_after_report') is True
                and report.get('source_files_changed') is False and report.get('figure_count') == 5
                and report.get('figures_generated') is True and report.get('report_layout_checks_passed') is True,
                'Report completion/layout/preservation did not pass.')
        require(report.get('engine_status_sha256') == sha(safe_file(run, contract['engine_status_path']))
                and report.get('engine_output_manifest_path') == engine_manifest
                and report.get('engine_output_manifest_sha256') == sha(safe_file(run, engine_manifest)),
                'Report does not pin its current engine.')
        require(report.get('source_preservation_path') == preservation_path
                and report.get('source_preservation_sha256') == sha(safe_file(run, preservation_path))
                and report.get('response_spec_sha256') == sha(safe_file(run, cfg['response_spec_path']))
                and report.get('output_contract_sha256') == sha(safe_file(run, cfg['output_contract_path']))
                and report.get('report_stage_fits_predictions_errors_or_metrics_computed') is False
                and report.get('eligibility_recomputed_by_report') is False
                and report.get('rounds_pooled_as_independent') is False
                and report.get('winner_selected') is False,
                'Report source/control pins or report-only scope differ.')
        report_manifest = contract['report_manifest_path']
        require(report.get('report_output_manifest_path') == report_manifest
                and report.get('report_output_manifest_sha256') == sha(safe_file(run, report_manifest)),
                'Report output manifest receipt differs.')
        verify_manifest(report_manifest, contract['report_output_files'])
        report_relative = 'results/NRCS_Whipstone_pH_baselines_review.pdf'
        require(report.get('report_relative_path') == report_relative
                and report.get('report_sha256') == sha(safe_file(run, report_relative))
                and isinstance(report.get('report_pages'), int) and report['report_pages'] > 0,
                'Report PDF hash/path/page receipt differs.')
        layout = load('audit/report_layout_receipt.json')
        current(layout, node, 'report layout')
        require(layout.get('status') == 'PASS'
                and report.get('report_layout_receipt_path') == 'audit/report_layout_receipt.json'
                and report.get('report_layout_receipt_sha256') == sha(safe_file(run, 'audit/report_layout_receipt.json')),
                'Report layout receipt failed or linkage differs.')
        sentinel = '\n'.join(line.format(current_main_job_id=main,
                             report_status_sha256=sha(safe_file(run, contract['report_status_path'])))
                             for line in contract['completion_sentinel_lines']) + '\n'
        require(safe_file(run, contract['completion_sentinel_path']).read_text() == sentinel,
                'Completion sentinel job/hash mismatch.')
        paths = set()
        for line in safe_file(run, 'PACKAGE_SHA256SUMS.txt').read_text().splitlines():
            digest, name = line.split('  ', 1)
            require(name not in paths and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest),
                    'Malformed package manifest.')
            paths.add(name)
            require(sha(safe_file(run, name)) == digest, 'Package changed during execution: ' + name)
            verified_package[name] = digest
        require({'CONFIG.json', 'PROTOCOL.txt', 'RESPONSE_SPEC.json', 'OUTPUT_CONTRACT.json',
                 'RUN.sh', 'COLLECT.sh', 'ph_core.py', 'ph_self_checks.py', 'analyze_ph.py',
                 'render_ph_report.py', 'environment_check.py', 'record_runtime.py',
                 'common.py', 'hpc_guard.py', 'stage_source.py', 'collect.py'} <= paths,
                'Package manifest omits a necessary immutable control.')
        package_manifest_hash = sha(safe_file(run, 'PACKAGE_SHA256SUMS.txt'))
        if state != 'UNKNOWN':
            require(node_list and node_list not in ('None', '(null)'), 'Main accounting node absent.')
            nodes = subprocess.check_output(['scontrol', 'show', 'hostnames', node_list],
                                            text=True, timeout=30).splitlines()
            require(node in nodes, 'Main accounting node differs from runtime.')
        pipeline = True
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))

label = ('HPC_WHIPSTONE_PH_BASELINES_AND_REPORT_COMPLETED_REVIEW_PENDING'
         if pipeline and state == 'COMPLETED' and code == '0:0'
         else 'WHIPSTONE_PH_STAGES_COMPLETED_SCHEDULER_CONFIRMATION_PENDING'
         if pipeline and state in ('UNKNOWN', 'RUNNING', 'COMPLETING')
         else 'HPC_WHIPSTONE_PH_BASELINES_FAILED_OR_INCOMPLETE')
status = {
    'status': label, 'analysis_job_id': main, 'collector_execution': execution,
    'collector_interpreter': sys.executable, 'collector_standard_library_only': True,
    'collector_interpreter_fallback': bool(os.environ.get('NRCS_RUNTIME_FALLBACK_REASON')),
    'collector_interpreter_fallback_reason': os.environ.get('NRCS_RUNTIME_FALLBACK_REASON') or None,
    'collector_final_accounting_established': False,
    'scheduler_state': state, 'scheduler_exit_code': code, 'main_accounting_node_list': node_list,
    'pipeline_exit_code': end.get('pipeline_exit_code'), 'scientific_and_report_stages_completed': pipeline,
    'latest_accepted_measured_inventory_source_job_id': '24147988',
    'latest_accepted_validation_source_job_id': '24128032', 'historical_receipts_modified': False,
    'completion_validation_errors': errors, 'figure_count': 5 if pipeline else None,
    'source_files_changed': False if pipeline else None,
    'package_integrity_passed': True if pipeline else None,
    'source_integrity_passed': True if pipeline else None,
    'source_files_verified_unchanged_before_archive': True if pipeline else None,
    'source_files_verified_unchanged_after_collection': None,
    'integrity_validation_scope': 'MAIN_STAGE_AND_COLLECTOR_VALIDATION_BEFORE_ARCHIVE_CREATION',
    'completed_output_flags_scope': 'VALIDATED_MAIN_SCIENTIFIC_AND_REPORT_STAGES',
    'scientific_review_pending': True, 'visual_review_pending': True,
    'archive_creation_status': 'PENDING_AT_RECEIPT_CAPTURE',
    'archive_success_record': 'Separate post-archive collection receipt; archived stage status is immutable.',
    'collected_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'review': 'Observed pH trajectories and retrospective simple-baseline point diagnostics require review; predictive validation and numerical ten-year forecasts are not established.',
}
for key, value in contract.get('successful_flags', {}).items():
    status[key] = value if pipeline else None
# Retain pending review even when completed-output evidence is absent.
status['scientific_review_pending'] = status['visual_review_pending'] = True
save(run / 'HPC_STATUS.json', status)
(run / 'AUDIT_README.txt').write_text('''WHIPSTONE pH BASELINE AND REPORT ATTEMPT
Consult HPC_STATUS, main accounting and completion errors before treating stages as complete.
Simple training baseline parameters and predictions may be computed only on genuine Puma.
No predictive validity or numerical ten-year forecast is established by this package.
All original runs, raw measurements and historical pending-review receipts stay unchanged.
Scientific/visual review remains pending. Collector is captured while running; its final
accounting is not included. The active collector.log is omitted. Archived HPC_STATUS records
stage validation and pending archive creation; a separate post-archive collection receipt
records archive CRC/hash success without rewriting archived evidence.
Large immutable snapshots, if any, are listed in REMOTE_ONLY_FILES.csv with Puma paths.
''')
archive, temporary = None, None
try:
    require(receipt.get('download_directory') and cfg.get('package'), 'Cannot resolve archive destination from controls.')
    excluded = {run / 'logs/collector.log', run / 'OUTPUT_SHA256SUMS.txt', run / 'REMOTE_ONLY_FILES.csv'}
    included, remote = [], []
    for path in sorted(run.rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts or path in excluded:
            continue
        require(not path.is_symlink(), 'Refuse linked archive evidence: ' + str(path))
        relative, digest = path.relative_to(run), sha(path)
        if relative.parts[0] == 'source_snapshot' and path.stat().st_size > 8 * 1024 * 1024:
            remote.append({'relative_path': str(relative), 'absolute_HPC_path': str(path),
                           'bytes': path.stat().st_size, 'sha256': digest,
                           'reason': 'Large immutable source retained on Puma; compact archive omits bytes'})
        else:
            included.append((path, digest))
    table(run / 'REMOTE_ONLY_FILES.csv', remote, ['relative_path', 'absolute_HPC_path', 'bytes', 'sha256', 'reason'])
    included.append((run / 'REMOTE_ONLY_FILES.csv', sha(run / 'REMOTE_ONLY_FILES.csv')))
    (run / 'OUTPUT_SHA256SUMS.txt').write_text(''.join(d + '  ' + str(p.relative_to(run)) + '\n' for p, d in included))
    archive = Path(receipt['download_directory']) / (cfg['package'] + '_' + main + '_' + run.name + '.zip')
    temporary = archive.with_suffix('.zip.partial')
    checksum_path = archive.with_suffix('.zip.sha256.txt')
    collection_path = archive.with_suffix('.zip.collection.json')
    require(not any(p.exists() for p in (archive, temporary, checksum_path, collection_path)),
            'Archive or post-archive evidence already exists; preserve original evidence.')
    with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in [p for p, _ in included] + [run / 'OUTPUT_SHA256SUMS.txt']:
            bundle.write(path, path.relative_to(run.parent))
    with zipfile.ZipFile(temporary) as bundle:
        require(bundle.testzip() is None, 'Archive CRC failed.')
    # Byte-only stability checks close the period during archive writing.
    require(all(sha(p) == d for p, d in included), 'Archived input changed during archive creation.')
    if pipeline:
        verify_staged_sources(run, cfg)
        require(sha(safe_file(run, 'PACKAGE_SHA256SUMS.txt')) == package_manifest_hash
                and all(sha(safe_file(run, n)) == h for n, h in verified_package.items()),
                'Verified package changed while the archive was created.')
    temporary.rename(archive)
    archive_hash = sha(archive)
    with checksum_path.open('x') as stream:
        stream.write(archive_hash + '  ' + archive.name + '\n')
    collection = {'status': 'ARCHIVE_COMPLETED_CRC_AND_SHA256_VERIFIED',
                  'execution': execution, 'analysis_job_id': main,
                  'main_stage_status': label, 'main_scheduler_state': state,
                  'main_scheduler_exit_code': code, 'scientific_and_report_stages_completed': pipeline,
                  'archive_path': str(archive), 'archive_sha256': archive_hash,
                  'archive_crc_passed': True, 'collector_final_accounting_established': False,
                  'post_archive_source_integrity_passed': True if pipeline else None,
                  'post_archive_package_integrity_passed': True if pipeline else None,
                  'source_files_verified_unchanged_after_collection': True if pipeline else None,
                  'archived_hpc_status_sha256': sha(run / 'HPC_STATUS.json'),
                  'archived_output_manifest_sha256': sha(run / 'OUTPUT_SHA256SUMS.txt'),
                  'completed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    with collection_path.open('x') as stream:
        stream.write(json.dumps(collection, indent=2, allow_nan=False) + '\n')
    (run.parent / (run.name + '_DOWNLOAD.txt')).write_text(str(archive) + '\n')
    print(json.dumps(status, indent=2)); print('DOWNLOAD_RESULTS=' + str(archive))
    print('ARCHIVE_COLLECTION_RECEIPT=' + str(collection_path))
except (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile) as exc:
    failure = {'status': 'ARCHIVE_FAILED_OR_INCOMPLETE', 'execution': execution,
               'analysis_job_id': main, 'main_stage_status': label,
               'scientific_and_report_stages_completed': pipeline,
               'archive_path': str(archive) if archive else None,
               'partial_archive_path': str(temporary) if temporary else None,
               'error': str(exc), 'collector_final_accounting_established': False,
               'recorded_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    save(run / 'audit/ARCHIVE_FAILURE.json', failure)
    status['status'] = 'HPC_WHIPSTONE_PH_COLLECTION_FAILED_OR_INCOMPLETE'
    status['main_stage_status'] = label
    status['archive_creation_status'] = 'FAILED_OR_INCOMPLETE'
    status['archive_error'] = str(exc)
    status['pre_archive_validation'] = {
        k: status.get(k) for k in ('source_files_changed', 'source_integrity_passed',
                                  'source_files_verified_unchanged_before_archive', 'package_integrity_passed')}
    for key in ('source_files_changed', 'source_integrity_passed',
                'source_files_verified_unchanged_after_collection', 'package_integrity_passed'):
        status[key] = None
    status['integrity_validation_scope'] = 'POST_ARCHIVE_INTEGRITY_NOT_ESTABLISHED; consult pre_archive_validation'
    save(run / 'HPC_STATUS.json', status)
    print(json.dumps(status, indent=2)); raise SystemExit(1)
