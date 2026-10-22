"""Stdlib-only single-job preflight/submission, never renders on login nodes."""
from pathlib import Path
import datetime,hashlib,json,os,re,secrets,subprocess
import sys
BASE=Path(__file__).resolve().parent

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def clean_env():
 blocked={'BASH_ENV','ENV','SHELLOPTS','BASHOPTS','SBATCH_GET_USER_ENV'}
 return {k:v for k,v in os.environ.items() if k not in blocked and not k.startswith('SBATCH_')}

def preflight():
 for line in (BASE/'SHA256SUMS.txt').read_text().splitlines():
  digest,rel=line.split('  ',1)
  if sha(BASE/rel)!=digest:raise RuntimeError('Package bytes changed: '+rel)
 cfg=json.loads((BASE/'CONFIG.json').read_text());cluster=subprocess.check_output(['scontrol','show','config'],text=True,timeout=30,env=clean_env())
 if not re.search(r'^\s*ClusterName\s*=\s*puma\s*$',cluster,re.M):raise RuntimeError('Puma required')
 for spec in cfg['source_files']:
  p=Path(spec['path'])
  if not p.is_file() or p.stat().st_size!=spec['bytes'] or sha(p)!=spec['sha256']:raise RuntimeError('Missing/changed saved v3 source: '+str(p))
 ready=Path(cfg['runtime_ready'])
 if not ready.is_file():raise RuntimeError('Pinned runtime READY file missing: '+str(ready))
 observed=sha(ready)
 if observed!=cfg['runtime_ready_sha256']:raise RuntimeError('Pinned runtime READY hash changed: '+str(ready)+'; expected='+cfg['runtime_ready_sha256']+'; observed='+observed)
 # Same login policy as successful v3: a module-managed link target may be
 # unavailable here. Compute bootstrap checks execution after module loading.
 if not os.path.lexists(cfg['runtime_python']):raise RuntimeError('Pinned runtime interpreter entry missing: '+cfg['runtime_python'])
 if not (Path(cfg['runtime_python']).is_symlink() or Path(cfg['runtime_python']).is_file()):raise RuntimeError('Pinned runtime interpreter is not a file/link: '+cfg['runtime_python'])
 return cfg

def main():
 try:cfg=preflight()
 except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as exc:
  failure={'state':'PREFLIGHT_FAILED_NO_JOB_SUBMITTED','job_submitted':False,'rendering_completed':False,'downloads_ready':False,'error':str(exc),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
  (BASE/'PREFLIGHT_STATUS.json').write_text(json.dumps(failure,indent=2)+'\n')
  print('LAUNCH_ERROR='+str(exc),file=sys.stderr);print('NO_JOB_SUBMITTED=true',file=sys.stderr)
  return 2
 (BASE/'PREFLIGHT_STATUS.json').write_text(json.dumps({'state':'PREFLIGHT_PASSED','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},indent=2)+'\n')
 run=BASE/'runs'/('run_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+secrets.token_hex(4));(run/'audit').mkdir(parents=True);(run/'logs').mkdir();job=None
 cmd=['sbatch','--parsable','--hold','--account='+cfg['account'],'--partition='+cfg['partition'],'--nodes=1','--ntasks=1','--cpus-per-task=2','--mem=12G','--time=01:00:00','--job-name=OFR_layout_refresh','--chdir='+str(run),'--output='+str(run/'logs'/'%j_refresh.out'),'--error='+str(run/'logs'/'%j_refresh.err'),'--export=ALL',str(BASE/'RUN.sh'),str(BASE),str(run),cfg['runtime_python']]
 try:
  job=subprocess.check_output(cmd,text=True,timeout=60,env=clean_env()).strip().split(';')[0]
  if not job.isdigit():raise RuntimeError('Invalid sbatch receipt')
  receipt={'state':'SUBMITTED_NOT_COMPLETE','run_directory':str(run),'job_id':job,'rendering_completed':False,'downloads_ready':False,'scientific_values_recomputed':False};(run/'submission.json').write_text(json.dumps(receipt,indent=2)+'\n');(BASE/'LATEST_RUN.txt').write_text(str(run)+'\n');subprocess.run(['scontrol','release',job],check=True,timeout=30,env=clean_env())
 except BaseException:
  if job and job.isdigit():subprocess.run(['scancel',job],check=False,timeout=30,env=clean_env())
  raise
 print(json.dumps(receipt,indent=2));print('CHECK_COMMAND=bash '+str(BASE/'CHECK.sh'))
if __name__=='__main__':sys.exit(main())
