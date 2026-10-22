#!/usr/bin/env python3
"""Read-only login preflight and Slurm orchestration; no scientific imports."""
from pathlib import Path
import os,json,hashlib,subprocess,datetime,secrets,shutil,re
BASE=Path(__file__).resolve().parent

def submission_environment():
 # Prevent shell-startup injection and inherited SBATCH defaults in batch jobs.
 blocked={'BASH_ENV','ENV','SHELLOPTS','BASHOPTS','SBATCH_GET_USER_ENV'}
 return {k:v for k,v in os.environ.items() if k not in blocked and not k.startswith('SBATCH_')}

def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def main():
 for line in (BASE/'SHA256SUMS.txt').read_text().splitlines():
  digest,path=line.split('  ',1)
  if sha(BASE/path)!=digest:raise RuntimeError('Package bytes changed: '+path)
 cfg=json.loads((BASE/'CONFIG.json').read_text());cluster=subprocess.check_output(['scontrol','show','config'],text=True,timeout=30)
 if not re.search(r'^\s*ClusterName\s*=\s*puma\s*$',cluster,re.M):raise RuntimeError('Submit this package on Puma only')
 for role,s in cfg['sources'].items():
  p=Path(s['path'])
  if not p.is_file() or (s.get('bytes') is not None and p.stat().st_size!=s['bytes']) or sha(p)!=s['sha256']:raise RuntimeError('Missing or changed pinned source '+role+': '+str(p))
 if sha(cfg['runtime_ready'])!=cfg['runtime_ready_sha256']:raise RuntimeError('Pinned existing runtime READY changed')
 if not os.path.lexists(cfg['runtime_python']):raise RuntimeError('Existing corrected runtime interpreter missing')
 run=BASE/'runs'/('run_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+secrets.token_hex(4));run.mkdir(parents=True);(run/'logs').mkdir();(run/'audit').mkdir();jobs={}
 def submit(label,mode,domain=None,dependency=None,held=False):
  mem='12G' if mode in ('worker','extras','collect') else '4G';tm='02:00:00' if mode in ('worker','extras','collect') else '00:20:00';cmd=['sbatch','--parsable','--account='+cfg['account'],'--partition='+cfg['partition'],'--nodes=1','--ntasks=1','--cpus-per-task=2','--mem='+mem,'--time='+tm,'--job-name=OFR_supp_'+label,'--chdir='+str(run),'--output='+str(run/'logs'/('%j_'+label+'.out')),'--error='+str(run/'logs'/('%j_'+label+'.err')),'--export=ALL']
  if dependency:cmd+=['--dependency='+dependency,'--kill-on-invalid-dep=yes']
  if held:cmd+=['--hold']
  cmd += [str(BASE/'RUN.sh'),str(BASE),str(run),mode,domain or 'none',cfg['runtime_python']]
  job=subprocess.check_output(cmd,text=True,timeout=60,env=submission_environment()).strip().split(';')[0]
  if not job.isdigit():raise RuntimeError('Invalid sbatch receipt: '+job)
  jobs[label]=job;return job
 try:
  stage=submit('stage','stage',held=True);b=submit('bacteria','worker','bacteria','afterok:'+stage);a=submit('archaea','worker','archaea','afterok:'+stage);e=submit('extras','extras',dependency='afterok:'+stage);submit('collector','collect',dependency='afterany:'+stage+':'+b+':'+a+':'+e)
  receipt={'state':'SUBMITTED_RELEASE_REQUESTED_NOT_YET_COMPLETE','run_directory':str(run),'jobs':jobs,'scientific_execution_completed':False,'downloads_ready':False,'source_files_pinned':True};(run/'submission.json').write_text(json.dumps(receipt,indent=2));(BASE/'LATEST_RUN.txt').write_text(str(run)+'\n');subprocess.run(['scontrol','release',stage],check=True,timeout=30,env=submission_environment())
 except BaseException:
  if jobs:subprocess.run(['scancel',*jobs.values()],check=False,timeout=30,env=submission_environment())
  (run/'SUBMISSION_FAILED.json').write_text(json.dumps({'jobs':jobs,'submitted_jobs_cancel_requested':True},indent=2));raise
 print(json.dumps(receipt,indent=2));print('CHECK_COMMAND=bash '+str(BASE/'CHECK.sh'))
if __name__=='__main__':main()
