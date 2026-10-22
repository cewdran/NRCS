#!/usr/bin/env python3
"""Preserve success/failure evidence after any terminal audit job state."""
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
import sys, json, subprocess, datetime, zipfile, time
from common import sha, save, table

run, main = Path(sys.argv[1]).resolve(), sys.argv[2]
receipt = json.loads((run / 'submission.json').read_text())
args = ['sacct', '-j', main, '--starttime=' + receipt['accounting_since'], '--parsable2',
        '--format=JobIDRaw,JobName,State,ExitCode,Start,End,Elapsed,AllocCPUS,ReqMem,MaxRSS,NodeList']
state, code = 'UNKNOWN', ''
for attempt in range(4):
    process = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in process.stdout.splitlines()[1:]:
        fields = line.split('|')
        if fields[0] == main:
            state, code = fields[2], fields[3]
    if state not in ['UNKNOWN', 'RUNNING', 'COMPLETING', 'PENDING']:
        break
    if attempt < 3:
        time.sleep(5)
(run / 'audit/sacct_analysis.tsv').write_text(process.stdout)
(run / 'audit/sacct_stderr.txt').write_text(process.stderr)
end, runtime_status_error = {}, None
if (run / 'audit/run_end.json').is_file():
    try:
        end = json.loads((run / 'audit/run_end.json').read_text())
        if not isinstance(end, dict):
            raise ValueError('Runtime end record is not a JSON object')
    except (ValueError, OSError) as exc:
        end = {}
        runtime_status_error = str(exc)
pipeline = False
feasibility_status, feasibility_status_error = {}, None
source_files_changed = None
if ((run / 'ANALYSES_COMPLETE.txt').is_file()
        and (run / 'results/FEASIBILITY_STATUS.json').is_file()
        and end.get('pipeline_exit_code') == 0):
    try:
        feasibility_status = json.loads((run / 'results/FEASIBILITY_STATUS.json').read_text())
        if not isinstance(feasibility_status, dict):
            raise ValueError('Feasibility status is not a JSON object')
        engine_execution = feasibility_status.get('execution', {})
        if not isinstance(engine_execution, dict):
            raise ValueError('Feasibility execution record is not a JSON object')
        verified = (
            feasibility_status.get('status') == 'FEASIBILITY_AUDIT_COMPLETED_REVIEW_PENDING'
            and str(engine_execution.get('job_id')) == main
            and engine_execution.get('cluster') == 'puma'
            and feasibility_status.get('numerical_models_fitted') is False
            and feasibility_status.get('ten_year_forecast_generated') is False
            and feasibility_status.get('eligibility_review_pending') is True
            and feasibility_status.get('source_integrity_passed') is True
            and feasibility_status.get('source_files_verified_unchanged_after_audit') is True
            and feasibility_status.get('source_files_changed') is False)
        if not verified:
            raise ValueError('Feasibility status lacks the required successful job, audit-only scope or source verification')
        pipeline = True
        source_files_changed = False
    except (ValueError, OSError) as exc:
        feasibility_status_error = str(exc)
if pipeline and state == 'COMPLETED' and code == '0:0':
    label = 'HPC_TEN_YEAR_FEASIBILITY_AUDIT_COMPLETED_REVIEW_PENDING'
elif pipeline and state in ['UNKNOWN', 'RUNNING', 'COMPLETING']:
    label = 'PIPELINE_COMPLETED_SCHEDULER_CONFIRMATION_PENDING'
else:
    label = 'HPC_RUN_FAILED_OR_INCOMPLETE'
save(run / 'HPC_STATUS.json', {
    'status': label, 'analysis_job_id': main, 'collector_execution': execution,
    'scheduler_state': state, 'scheduler_exit_code': code,
    'pipeline_exit_code': end.get('pipeline_exit_code'),
    'audit_execution_completed': pipeline,
    'numerical_models_fitted': False,
    'ten_year_forecast_generated': False,
    'eligibility_review_pending': True,
    'collected_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'review': 'Review recorded model eligibility and reasons before authorizing any subsequent model fitting.',
    'source_files_changed': source_files_changed,
    'runtime_status_validation_error': runtime_status_error,
    'feasibility_status_validation_error': feasibility_status_error,
})
(run / 'AUDIT_README.txt').write_text(
    'TEN-YEAR MODEL FEASIBILITY AUDIT REVIEW BUNDLE\n\n'
    'This bundle preserves a Puma-only feasibility audit attempt. Consult status and run_end before treating tables as complete.\n'
    'The authorized scope performs no numerical model fitting and generates no ten-year forecast. Eligibility review remains pending.\n'
    'Check HPC_STATUS.json and scheduler accounting before using the audit. Partial or absent outputs from failed attempts are preserved.\n'
    'results/ may contain recorded support and candidate-design tables plus FEASIBILITY_STATUS.json; source_snapshot/ may contain the locked small sources inspected by the audit.\n'
    'audit/ preserves Python/platform, actual Slurm execution, source checksums, runtime exit status and scheduler accounting.\n'
    'A source_files_changed value of false is reported only after the engine verifies the pinned original sources unchanged after the completed audit; otherwise it is null.\n'
    'REMOTE_ONLY_FILES.csv lists any intentionally omitted large source artifacts retained at their recorded HPC paths.\n'
    'The actively written collector.log is omitted. Inspect scheduler accounting for the collector completion state.\n')
all_files = sorted(path for path in run.rglob('*') if path.is_file()
                   and '__pycache__' not in path.parts
                   and path.name not in ['collector.log', 'OUTPUT_SHA256SUMS.txt', 'REMOTE_ONLY_FILES.csv'])
included, remote = [], []
for path in all_files:
    relative = path.relative_to(run)
    digest = sha(path)
    if relative.parts[0] in ['predictions', 'original_matrices'] or (
            relative.parts[0] == 'source_snapshot' and path.stat().st_size > 8 * 1024 * 1024):
        remote.append({'relative_path': str(relative), 'absolute_HPC_path': str(path),
                       'bytes': path.stat().st_size, 'sha256': digest,
                       'reason': 'Large source artifact retained on HPC; omitted from compact review bundle'})
    else:
        included.append((path, digest))
table(run / 'REMOTE_ONLY_FILES.csv', remote,
      fields=['relative_path', 'absolute_HPC_path', 'bytes', 'sha256', 'reason'])
included.append((run / 'REMOTE_ONLY_FILES.csv', sha(run / 'REMOTE_ONLY_FILES.csv')))
(run / 'OUTPUT_SHA256SUMS.txt').write_text(''.join(
    digest + '  ' + str(path.relative_to(run)) + '\n' for path, digest in included))
archive = Path(receipt['download_directory']) / (
    'NRCS_ten_year_feasibility_HPC_v2_' + main + '_' + run.name + '.zip')
temporary = archive.with_suffix('.zip.partial')
if archive.exists() or temporary.exists():
    raise SystemExit('Archive already exists; do not overwrite provenance: ' + str(archive))
with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
    for path in [path for path, _ in included] + [run / 'OUTPUT_SHA256SUMS.txt']:
        handle.write(path, path.relative_to(run.parent))
with zipfile.ZipFile(temporary) as handle:
    if handle.testzip() is not None:
        raise RuntimeError('Archive integrity check failed')
temporary.rename(archive)
archive.with_suffix('.zip.sha256.txt').write_text(sha(archive) + '  ' + archive.name + '\n')
(run.parent / (run.name + '_DOWNLOAD.txt')).write_text(str(archive) + '\n')
print((run / 'HPC_STATUS.json').read_text())
print('DOWNLOAD_RESULTS=' + str(archive))
