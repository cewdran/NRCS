#!/usr/bin/env python3
"""Archive completed or failed HPC execution, preserving the distinction."""
from payload.hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
import sys,json,subprocess,datetime,hashlib,zipfile,time

run=Path(sys.argv[1]);main=sys.argv[2];audit=run/'audit';receipt=json.loads((run/'submission.json').read_text())
args=['sacct','-j',main,'--starttime='+receipt['accounting_since'],
 '--parsable2','--format=JobIDRaw,JobName,State,ExitCode,Start,End,Elapsed,AllocCPUS,ReqMem,MaxRSS,NodeList']
# The prior local date avoids UTC/Phoenix and sacct default-midnight ambiguities.
accounting='';scheduler_state='UNKNOWN';scheduler_exit=''
for attempt in range(4):
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 accounting=p.stdout;ptext=p.stderr
 for line in accounting.splitlines()[1:]:
  fields=line.split('|')
  if fields[0]==main:scheduler_state=fields[2];scheduler_exit=fields[3]
 if scheduler_state not in ['UNKNOWN','RUNNING','COMPLETING','PENDING']:break
 if attempt<3:time.sleep(5)
(audit/'sacct_analysis.tsv').write_text(accounting)
(audit/'sacct_stderr.txt').write_text(ptext)
runtime=json.loads((audit/'run_end.json').read_text())if(audit/'run_end.json').exists()else{}
pipeline_ok=(run/'ANALYSES_COMPLETE.txt').is_file()and runtime.get('pipeline_exit_code')==0
if pipeline_ok and scheduler_state=='COMPLETED'and scheduler_exit=='0:0':status='HPC_COMPUTATION_COMPLETED_VISUAL_REVIEW_PENDING'
elif pipeline_ok and scheduler_state in ['UNKNOWN','RUNNING','COMPLETING']:status='PIPELINE_COMPLETED_SCHEDULER_CONFIRMATION_PENDING'
else:status='HPC_RUN_FAILED_OR_INCOMPLETE'
result={'status':status,'collected_utc':datetime.datetime.utcnow().isoformat()+'Z','analysis_job_id':main,
 'collector_execution':execution,'scheduler_state':scheduler_state,'scheduler_exit_code':scheduler_exit,
 'pipeline_exit_code':runtime.get('pipeline_exit_code'),'scientific_scripts_completed':pipeline_ok,
 'visual_review':'Pending inspection of the newly generated HPC figures and PDF.',
 'prior_results':'Non-HPC results remain a historical comparison only.'}
(run/'HPC_STATUS.json').write_text(json.dumps(result,indent=2)+'\n')
(run/'AUDIT_README.txt').write_text('HPC_STATUS.json distinguishes pipeline completion, scheduler state and pending visual review.\n'
 'logs/analysis.log records commands and full analysis output. audit/ records Slurm, software, setup, inputs and concordance.\n'
 'payload/tables and payload/figures are newly generated on HPC. payload/reference_non_HPC is explicitly historical.\n'
 'collector.log is omitted from the archive because it is still being written during collection.\n')
files=sorted(p for p in run.rglob('*')if p.is_file()and '__pycache__'not in p.parts and 'matplotlib_cache'not in p.parts
 and p.name not in ['collector.log','OUTPUT_SHA256SUMS.txt'])
(run/'OUTPUT_SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(run).as_posix()+'\n'for p in files))
download_directory=Path(receipt['download_directory'])
if not download_directory.is_dir():raise SystemExit('Download directory is missing: '+str(download_directory))
archive=download_directory/('NRCS_corrected_rerun_HPC_v3_'+main+'_'+run.name+'.zip')
with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED)as z:
 for p in files+[run/'OUTPUT_SHA256SUMS.txt']:z.write(p,p.relative_to(run.parent))
with zipfile.ZipFile(archive)as z:assert z.testzip()is None
archive.with_suffix('.zip.sha256.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
(run.parent/(run.name+'_DOWNLOAD.txt')).write_text(str(archive)+'\n')
print(json.dumps(result,indent=2));print('DOWNLOAD_RESULTS='+str(archive))
