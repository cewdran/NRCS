#!/usr/bin/env python3
"""Guarded standard-library collector after any main exit; no scientific work."""
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
receipt = json.loads((run / 'submission.json').read_text())
require(main.isdigit() and receipt.get('jobs', {}).get('analysis') == main,
        'Collector main job differs from submission receipt.')
require(receipt.get('jobs', {}).get('collector') == execution['job_id'],
        'Collector allocation differs from submission receipt.')
cfg = json.loads((run / 'CONFIG.json').read_text())
state, code, node_list = 'UNKNOWN', '', ''
stdout, stderr = '', ''
for attempt in range(4):
    try:
        proc = subprocess.run(['sacct', '-j', main, '--starttime=' + receipt['accounting_since'],
                               '--parsable2', '--format=JobIDRaw,State,ExitCode,NodeList'],
                              capture_output=True, text=True, timeout=30)
        stdout, stderr = proc.stdout, proc.stderr
        for line in stdout.splitlines()[1:]:
            parts = line.split('|')
            if len(parts) >= 4 and parts[0] == main:
                state, code, node_list = parts[1:4]
    except (OSError, subprocess.TimeoutExpired) as exc:
        stderr = str(exc)
    if state not in ('UNKNOWN', 'PENDING', 'RUNNING', 'COMPLETING'):
        break
    if attempt < 3:
        time.sleep(5)
(run / 'audit/sacct_analysis.tsv').write_text(stdout)
(run / 'audit/sacct_stderr.txt').write_text(stderr)

errors, records = [], {}
for name in ('audit/run_start.json', 'audit/run_end.json', 'audit/software/environment.json',
             'audit/SOURCE_STAGE_STATUS.json', 'audit/inventory_self_checks.json',
             'results/INVENTORY_STATUS.json', 'results/REPORT_STATUS.json'):
    try:
        value = json.loads(safe_file(run, name).read_text())
        require(isinstance(value, dict), 'Receipt is not an object: ' + name)
        records[name] = value
    except (OSError, ValueError, RuntimeError) as exc:
        errors.append(name + ': ' + str(exc))


def verify_manifest(name, expected):
    rows = readcsv(safe_file(run, name))
    require(rows, 'Empty output manifest: ' + name)
    seen = set()
    for row in rows:
        relative = row['relative_path']
        require(relative not in seen, 'Duplicate output manifest entry: ' + relative)
        seen.add(relative)
        target = safe_file(run, relative)
        require(sha(target) == row['sha256'] and target.stat().st_size == int(row['size_bytes']),
                'Output hash/size changed: ' + relative)
    require(seen == set(expected), 'Exact output set differs: ' + name)
    return rows


pipeline = False
end, report = records.get('audit/run_end.json', {}), records.get('results/REPORT_STATUS.json', {})
if not errors:
    try:
        node = end.get('execution', {}).get('node')
        for name, record in records.items():
            origin = record.get('execution', {})
            require(origin.get('job_id') == main and origin.get('cluster') == 'puma'
                    and origin.get('node') == node and bool(node), 'Current execution mismatch: ' + name)
        start = records['audit/run_start.json']
        env = records['audit/software/environment.json']
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
                and env.get('python') == ready.get('python') == start.get('python_version') == end.get('python_version') == cfg['runtime_expected_python']
                and env.get('python', '').startswith('3.11.4 ')
                and ready.get('executable') == env.get('executable')
                and ready.get('versions') == cfg['runtime_expected_versions']
                and ready.get('requirements_sha256') == cfg['runtime_requirements_sha256'],
                'Pinned environment/READY/build verification failed.')
        fixture = records['audit/inventory_self_checks.json']
        require(fixture.get('status') == 'PASS' and fixture.get('passed') is True and bool(fixture.get('checks'))
                and fixture.get('fixture_kind') == 'SYNTHETIC_SOURCE_PRESERVATION_AND_IDENTITY_EDGE_CASES'
                and fixture.get('real_source_data_read_by_fixtures') is False
                and all(row.get('passed') is True for row in fixture['checks']), 'Puma self checks did not pass.')
        stage = records['audit/SOURCE_STAGE_STATUS.json']
        require(stage.get('status') == 'IMMUTABLE_MEASURED_SOURCES_VERIFIED_AND_STAGED'
                and stage.get('source_integrity_passed') is True
                and stage.get('source_files_verified_unchanged_after_staging') is True
                and stage.get('source_files_changed') is False and stage.get('latest_validation_job_id') == '24128032',
                'Staging was not completed with preserved sources.')
        review_path = cfg['accepted_review_path']
        require(stage.get('accepted_review_path') == review_path and stage.get('accepted_review_sha256')
                == cfg['accepted_review_sha256'] == sha(safe_file(run, review_path)), 'Latest acceptance lock changed.')
        review = json.loads(safe_file(run, review_path).read_text())
        require(review.get('status') == 'ACCEPTED_LIMITED_RETROSPECTIVE_DIAGNOSTICS_AND_READINESS_AUDIT'
                and review.get('main_analysis_job_id') == '24128032', 'Prior acceptance differs.')
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
                    and row['source_job_id'] == cfg['source_runs'][alias]['analysis_job_id']
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
        inventory = records['results/INVENTORY_STATUS.json']
        require(inventory.get('status') == 'HPC_MEASURED_OUTCOME_INVENTORY_COMPLETED_REVIEW_PENDING'
                and inventory.get('inventory_computed') is True
                and inventory.get('scientific_inventory_completed') is True
                and inventory.get('source_integrity_passed') is True
                and inventory.get('source_files_verified_unchanged_after_audit') is True
                and inventory.get('source_files_changed') is False, 'Inventory completion/preservation failed.')
        for record in (inventory, report):
            for name in ('models_fitted', 'statistical_tests_performed', 'forecasts_generated',
                         'measurement_values_aggregated', 'predictive_validation_established',
                         'ten_year_forecast_generated', 'numerical_ten_year_forecast'):
                require(record.get(name) is False, 'Inventory/report scope changed: ' + name)
            require(record.get('scientific_review_pending') is True, 'Scientific review no longer pending.')
        engine_manifest = 'results/ENGINE_OUTPUT_MANIFEST.csv'
        require(inventory.get('output_manifest_path') == engine_manifest and inventory.get('output_manifest_sha256')
                == sha(safe_file(run, engine_manifest)), 'Engine output manifest receipt changed.')
        verify_manifest(engine_manifest, cfg['engine_output_files'])
        preservation_path = 'audit/INVENTORY_SOURCE_PRESERVATION.json'
        preservation = json.loads(safe_file(run, preservation_path).read_text())
        require(preservation.get('execution', {}).get('job_id') == main
                and preservation.get('execution', {}).get('node') == node
                and preservation.get('execution', {}).get('cluster') == 'puma'
                and preservation.get('source_integrity_passed') is True
                and preservation.get('source_files_changed') is False
                and preservation.get('source_files_verified_unchanged_after_audit') is True,
                'Engine preservation receipt differs.')
        require(report.get('status') == 'MEASURED_OUTCOME_INVENTORY_REPORT_COMPLETED_REVIEW_PENDING'
                and report.get('source_integrity_passed') is True
                and report.get('source_files_verified_unchanged_after_report') is True
                and report.get('source_files_changed') is False and report.get('figure_count') == 0
                and report.get('figures_generated') is False and report.get('report_layout_checks_passed') is True,
                'Report completion/layout/preservation did not pass.')
        require(report.get('inventory_status_sha256') == sha(safe_file(run, 'results/INVENTORY_STATUS.json'))
                and report.get('engine_output_manifest_path') == engine_manifest
                and report.get('engine_output_manifest_sha256') == sha(safe_file(run, engine_manifest)),
                'Report does not pin its current inventory.')
        report_manifest, report_relative = 'results/report_output_manifest.csv', 'results/NRCS_measured_outcomes_inventory_review.pdf'
        require(report.get('report_output_manifest_path') == report_manifest
                and report.get('report_output_manifest_sha256') == sha(safe_file(run, report_manifest)),
                'Report output manifest receipt differs.')
        verify_manifest(report_manifest, {report_relative, 'audit/report_layout_receipt.json'})
        require(report.get('report_relative_path') == report_relative
                and report.get('report_sha256') == sha(safe_file(run, report_relative))
                and isinstance(report.get('report_pages'), int) and report['report_pages'] > 0,
                'Report PDF hash/path/page receipt differs.')
        layout = json.loads(safe_file(run, 'audit/report_layout_receipt.json').read_text())
        require(layout.get('execution', {}).get('job_id') == main
                and layout.get('execution', {}).get('node') == node
                and layout.get('execution', {}).get('cluster') == 'puma'
                and layout.get('status') == 'PASS', 'Report layout receipt failed.')
        require((run / 'ANALYSES_COMPLETE.txt').read_text() ==
                'Measured-outcome inventory and report completed on Puma job ' + main
                + '; scientific and visual review pending.\nREPORT_STATUS_SHA256='
                + sha(run / 'results/REPORT_STATUS.json') + '\n', 'Completion sentinel job/hash mismatch.')
        paths = set()
        for line in safe_file(run, 'PACKAGE_SHA256SUMS.txt').read_text().splitlines():
            digest, name = line.split('  ', 1)
            require(name not in paths and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest),
                    'Malformed package manifest.')
            paths.add(name)
            require(sha(safe_file(run, name)) == digest, 'Package changed during execution: ' + name)
        require({'CONFIG.json', 'PROTOCOL.txt', 'RESPONSE_TRACKS.json', 'RUN.sh', 'COLLECT.sh',
                 'inventory_core.py', 'inventory_self_checks.py', 'inventory_outcomes.py',
                 'render_inventory_report.py', 'environment_check.py', 'record_runtime.py',
                 'common.py', 'hpc_guard.py', 'stage_source.py', 'collect.py'} <= paths,
                'Package manifest omits a necessary immutable control.')
        if state != 'UNKNOWN':
            require(node_list and node_list not in ('None', '(null)'), 'Main accounting node absent.')
            nodes = subprocess.check_output(['scontrol', 'show', 'hostnames', node_list], text=True, timeout=30).splitlines()
            require(node in nodes, 'Main accounting node differs from runtime.')
        pipeline = True
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))

label = ('HPC_MEASURED_OUTCOMES_INVENTORY_COMPLETED_REVIEW_PENDING'
         if pipeline and state == 'COMPLETED' and code == '0:0'
         else 'MEASURED_INVENTORY_COMPLETED_SCHEDULER_CONFIRMATION_PENDING'
         if pipeline and state in ('UNKNOWN', 'RUNNING', 'COMPLETING')
         else 'HPC_MEASURED_OUTCOMES_INVENTORY_FAILED_OR_INCOMPLETE')
status = {
    'status': label, 'analysis_job_id': main, 'collector_execution': execution,
    'collector_interpreter': sys.executable, 'collector_standard_library_only': True,
    'collector_interpreter_fallback': bool(os.environ.get('NRCS_RUNTIME_FALLBACK_REASON')),
    'collector_interpreter_fallback_reason': os.environ.get('NRCS_RUNTIME_FALLBACK_REASON') or None,
    'collector_final_accounting_established': False,
    'scheduler_state': state, 'scheduler_exit_code': code, 'main_accounting_node_list': node_list,
    'pipeline_exit_code': end.get('pipeline_exit_code'), 'scientific_and_report_stages_completed': pipeline,
    'inventory_computed': True if pipeline else None, 'latest_accepted_validation_source_job_id': '24128032',
    'historical_receipts_modified': False, 'models_fitted': False, 'statistical_tests_performed': False,
    'statistical_tests_run': False,
    'forecasts_generated': False, 'measurement_values_aggregated': False,
    'predictive_validation_established': False, 'ten_year_forecast_generated': False,
    'numerical_ten_year_forecast': False, 'source_files_changed': False if pipeline else None,
    'package_integrity_passed': True if pipeline else None,
    'source_integrity_passed': True if pipeline else None,
    'source_files_verified_unchanged_after_collection': True if pipeline else None,
    'scientific_review_pending': True, 'visual_review_pending': True,
    'completion_validation_errors': errors, 'figure_count': 0,
    'collected_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'review': 'Review exact source identities, held records, method/unit/depth/date compatibility, and conservative joins. Completed inventory is not model or forecast validation.',
}
save(run / 'HPC_STATUS.json', status)
(run / 'AUDIT_README.txt').write_text('''MEASURED-OUTCOME INVENTORY ATTEMPT
Check HPC_STATUS and main accounting before treating stages as complete.
Only source-preserving inventory/support/compatibility and report stages are authorized here.
No measurement-value aggregation, model, statistical test or forecast is performed.
All original runs and their historical review-pending receipts stay unchanged.
Scientific and visual review remains pending. Collector is captured while running;
its final accounting is not included. The active collector.log is omitted.
Large source snapshots, if any, are listed in REMOTE_ONLY_FILES.csv with Puma paths.
''')
excluded = {run / 'logs/collector.log', run / 'OUTPUT_SHA256SUMS.txt', run / 'REMOTE_ONLY_FILES.csv'}
included, remote = [], []
for path in sorted(run.rglob('*')):
    if not path.is_file() or '__pycache__' in path.parts or path in excluded:
        continue
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
require(not archive.exists() and not temporary.exists(), 'Archive already exists; preserve original evidence.')
with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
    for path in [p for p, _ in included] + [run / 'OUTPUT_SHA256SUMS.txt']:
        bundle.write(path, path.relative_to(run.parent))
with zipfile.ZipFile(temporary) as bundle:
    require(bundle.testzip() is None, 'Archive CRC failed.')
temporary.rename(archive)
with archive.with_suffix('.zip.sha256.txt').open('x') as stream:
    stream.write(sha(archive) + '  ' + archive.name + '\n')
(run.parent / (run.name + '_DOWNLOAD.txt')).write_text(str(archive) + '\n')
print(json.dumps(status, indent=2)); print('DOWNLOAD_RESULTS=' + str(archive))
