#!/usr/bin/env python3
"""Collect a compact review bundle after any terminal analysis state."""
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
import sys, json, subprocess, datetime, zipfile, time
from common import sha, save, table

run, main = Path(sys.argv[1]).resolve(), sys.argv[2]
receipt = json.loads((run/'submission.json').read_text())
args = ['sacct','-j',main,'--starttime='+receipt['accounting_since'],'--parsable2',
        '--format=JobIDRaw,JobName,State,ExitCode,Start,End,Elapsed,AllocCPUS,ReqMem,MaxRSS,NodeList']
state, code = 'UNKNOWN',''
for attempt in range(4):
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout.splitlines()[1:]:
        fields = line.split('|')
        if fields[0] == main:
            state, code = fields[2], fields[3]
    if state not in ['UNKNOWN','RUNNING','COMPLETING','PENDING']:
        break
    if attempt < 3:
        time.sleep(5)
(run/'audit/sacct_analysis.tsv').write_text(proc.stdout)
(run/'audit/sacct_stderr.txt').write_text(proc.stderr)
end = json.loads((run/'audit/run_end.json').read_text()) if (run/'audit/run_end.json').exists() else {}
pipeline = (run/'ANALYSES_COMPLETE.txt').is_file() and end.get('pipeline_exit_code') == 0
if pipeline and state == 'COMPLETED' and code == '0:0':
    label = 'HPC_SENSITIVITY_COMPLETED_REVIEW_PENDING'
elif pipeline and state in ['UNKNOWN','RUNNING','COMPLETING']:
    label = 'PIPELINE_COMPLETED_SCHEDULER_CONFIRMATION_PENDING'
else:
    label = 'HPC_RUN_FAILED_OR_INCOMPLETE'
save(run/'HPC_STATUS.json', {'status':label, 'analysis_job_id':main, 'collector_execution':execution,
    'scheduler_state':state, 'scheduler_exit_code':code, 'pipeline_exit_code':end.get('pipeline_exit_code'),
    'scientific_scripts_completed':pipeline, 'source_functional_job':'24067682', 'corrected_metadata_source_job':'24067160',
    'collected_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'scientific_review':'Review reproduced baseline and domain-disagreement sensitivity before interpreting archaeal functions.',
    'models_fitted':False, 'ten_year_forecast_generated':False})
(run/'AUDIT_README.txt').write_text(
    'COMPACT FUNCTIONAL REVIEW BUNDLE\n\n'
    'All scientific preprocessing and prediction were scheduled on Puma. Check HPC_STATUS.json before use.\n'
    'results/ contains baseline reproduction checks, per-sample and per-function sensitivity comparisons and flagged ASV details.\n'
    'sensitivity_inputs/ contains the exact original and filtered counts used in this sensitivity check.\n'
    'source_snapshot/ contains the locked prediction source files; original input sources remain unmodified.\n'
    'REMOTE_ONLY_FILES.csv lists omitted intermediate, per-ASV trait and original candidate files with SHA-256.\n'
    'These files remain at the original absolute run paths on Puma; no files were deleted by collection.\n'
    'Setup environment and reference databases remain in the package runtime directory; their versions/hashes are in audit/.\n'
    'The actively written collector.log is omitted. Inspect scheduler accounting for collector completion.\n'
    'Predicted functions are genetic potential, not measured processes or validated plant benefits.\n')
all_files = sorted(p for p in run.rglob('*') if p.is_file() and '__pycache__' not in p.parts
                   and p.name not in ['collector.log','OUTPUT_SHA256SUMS.txt','REMOTE_ONLY_FILES.csv'])
included, remote = [], []
for path in all_files:
    relative = path.relative_to(run)
    digest = sha(path)
    if relative.parts[0] == 'predictions' or relative.parts[:2] == ('source_snapshot','candidates'):
        remote.append({'relative_path':str(relative), 'absolute_HPC_path':str(path), 'bytes':path.stat().st_size,
                       'sha256':digest,'reason':'Full prediction intermediates or original source candidate; retained on HPC'})
    else:
        included.append((path,digest))
table(run/'REMOTE_ONLY_FILES.csv',remote,fields=['relative_path','absolute_HPC_path','bytes','sha256','reason'])
included.append((run/'REMOTE_ONLY_FILES.csv',sha(run/'REMOTE_ONLY_FILES.csv')))
(run/'OUTPUT_SHA256SUMS.txt').write_text(''.join(digest+'  '+str(path.relative_to(run))+'\n' for path,digest in included))
archive = Path(receipt['download_directory'])/('NRCS_functional_sensitivity_HPC_v1_'+main+'_'+run.name+'.zip')
temporary = archive.with_suffix('.zip.partial')
if archive.exists() or temporary.exists():
    raise SystemExit('Archive already exists; do not overwrite provenance: '+str(archive))
with zipfile.ZipFile(temporary,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as handle:
    for path in [p for p,_ in included]+[run/'OUTPUT_SHA256SUMS.txt']:
        handle.write(path,path.relative_to(run.parent))
with zipfile.ZipFile(temporary) as handle:
    if handle.testzip() is not None:
        raise RuntimeError('Archive integrity check failed')
temporary.rename(archive)
archive.with_suffix('.zip.sha256.txt').write_text(sha(archive)+'  '+archive.name+'\n')
(run.parent/(run.name+'_DOWNLOAD.txt')).write_text(str(archive)+'\n')
print((run/'HPC_STATUS.json').read_text())
print('DOWNLOAD_RESULTS='+str(archive))
