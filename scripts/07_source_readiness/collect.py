from hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
import sys,json,subprocess,datetime,hashlib,zipfile,time
run=Path(sys.argv[1]).resolve();job=sys.argv[2];r=json.loads((run/'submission.json').read_text())
state='UNKNOWN';code='';accounting=''
for attempt in range(4):
 p=subprocess.run(['sacct','-j',job,'--starttime='+r['accounting_since'],'--parsable2','--format=JobIDRaw,JobName,State,ExitCode,Start,End,Elapsed,AllocCPUS,ReqMem,MaxRSS,NodeList'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=30)
 accounting=p.stdout
 for line in accounting.splitlines()[1:]:
  fields=line.split('|')
  if fields[0]==job:state=fields[2];code=fields[3]
 if state not in ['UNKNOWN','RUNNING','COMPLETING','PENDING']:break
 if attempt<3:time.sleep(5)
(run/'audit/sacct.tsv').write_text(accounting);(run/'audit/sacct_stderr.txt').write_text(p.stderr)
exitpath=run/'audit/pipeline_exit_code.txt';exitcode=int(exitpath.read_text()) if exitpath.exists() else None
complete=(run/'results/AUDIT_COMPLETION.json').exists() and exitcode==0
if complete and state=='COMPLETED' and code=='0:0':status='HPC_READINESS_AUDIT_COMPLETED_REVIEW_REQUIRED'
elif complete and state in ['UNKNOWN','RUNNING','COMPLETING']:status='AUDIT_FINISHED_SCHEDULER_CONFIRMATION_PENDING'
else:status='HPC_READINESS_AUDIT_FAILED_OR_INCOMPLETE'
result={'status':status,'audit_job_id':job,'collector_execution':execution,'scheduler_state':state,'scheduler_exit_code':code,'pipeline_exit_code':exitcode,'audit_completed':complete,'model_fitting_performed':False,'ten_year_forecast_performed':False,'collected_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
(run/'HPC_STATUS.json').write_text(json.dumps(result,indent=2)+'\n')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
files=sorted(p for p in run.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name not in ['collector.log','OUTPUT_SHA256SUMS.txt','DOWNLOAD.txt'])
(run/'OUTPUT_SHA256SUMS.txt').write_text(''.join(sha(p)+'  '+p.relative_to(run).as_posix()+'\n' for p in files))
archive=Path(r['download_directory'])/('NRCS_readiness_HPC_v1_'+job+'_'+run.name+'.zip')
with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
 for p in files+[run/'OUTPUT_SHA256SUMS.txt']:z.write(p,run.name+'/'+p.relative_to(run).as_posix())
with zipfile.ZipFile(archive) as z:
 if z.testzip() is not None:raise RuntimeError('Archive integrity failure')
archive.with_suffix('.zip.sha256.txt').write_text(sha(archive)+'  '+archive.name+'\n')
(run/'DOWNLOAD.txt').write_text(str(archive)+'\n')
print(json.dumps(result,indent=2));print('DOWNLOAD_RESULTS='+str(archive))
