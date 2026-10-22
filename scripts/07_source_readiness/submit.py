#!/usr/bin/env python3
from pathlib import Path
import sys,os,json,hashlib,datetime,uuid,shutil,subprocess
from hpc_guard import puma_cluster
puma_cluster()
pkg=Path(__file__).resolve().parent
cfg=json.loads((pkg/'CONFIG.json').read_text());dest=Path(cfg['download_directory'])
# This audit uses only the standard library. Preserve the working launcher's
# executable path without resolving virtual-environment symlinks.
python=Path(sys.executable).absolute()
if sys.version_info < (3,7):raise SystemExit('Python 3.7 or newer is required for this audit.')
if not python.is_file() or not os.access(str(python),os.X_OK):
 raise SystemExit('The current Python executable is unavailable: '+str(python))
for rel in cfg['sources']:
 if not (Path(cfg['source_run'])/rel).is_file():raise SystemExit('Required source missing: '+rel)
entries=[]
for line in (pkg/'SHA256SUMS.txt').read_text().splitlines():
 expected,name=line.split('  ',1);rel=Path(name)
 if rel.is_absolute() or '..' in rel.parts:raise SystemExit('Unsafe checksum path')
 if hashlib.sha256((pkg/rel).read_bytes()).hexdigest()!=expected:raise SystemExit('Package checksum mismatch: '+name)
 entries.append(rel)
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
run=pkg/'runs'/('run_'+stamp+'_'+uuid.uuid4().hex[:8]);run.mkdir(parents=True)
for rel in entries:
 target=run/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(pkg/rel,target)
shutil.copy2(pkg/'SHA256SUMS.txt',run/'PACKAGE_SHA256SUMS.txt')
(run/'logs').mkdir();(run/'audit').mkdir()
receipt={'state':'STAGED_NOT_SUBMITTED','created_utc':stamp,'run_directory':str(run),'package_directory':str(pkg),'download_directory':str(dest),'python':str(python),'submission_python_version':sys.version,'package_revision':'v1_fix1','jobs':{},'commands':[],'accounting_since':(datetime.datetime.now()-datetime.timedelta(days=1)).strftime('%Y-%m-%d')}
def save():
 (run/'submission.json').write_text(json.dumps(receipt,indent=2)+'\n');(pkg/'LATEST_RUN.txt').write_text(str(run)+'\n')
def call(args):
 receipt['commands'].append(args);save();env=dict(os.environ)
 for k in ['BASH_ENV','ENV','SHELLOPTS','BASHOPTS','SBATCH_GET_USER_ENV']:env.pop(k,None)
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,env=env,timeout=60)
 if p.returncode:raise RuntimeError('Command failed: '+repr(args)+'\n'+p.stderr)
 return p.stdout.strip()
def jobid(x):
 v=x.split(';')[0]
 if not v.isdigit():raise RuntimeError('Unexpected sbatch reply: '+x)
 return v
main=None;collector=None
try:
 common=['sbatch','--parsable','--account=dinusha1','--partition=standard','--nodes=1','--ntasks=1','--cpus-per-task=1','--export=ALL','--chdir='+str(run)]
 main=jobid(call(common+['--hold','--job-name=nrcs_ready','--time=00:30:00','--output='+str(run/'logs/audit.log'),'--error='+str(run/'logs/audit.log'),str(run/'RUN.sh'),str(run),str(python)]))
 receipt['jobs']['audit']=main;save()
 collector=jobid(call(common+['--job-name=nrcs_collect','--time=00:10:00','--dependency=afterany:'+main,'--output='+str(run/'logs/collector.log'),'--error='+str(run/'logs/collector.log'),str(run/'COLLECT.sh'),str(run),str(python),main]))
 receipt['jobs']['collector']=collector;receipt['state']='SUBMITTED_NOT_YET_COMPLETE';save();call(['scontrol','release',main])
except Exception as exc:
 receipt['state']='SUBMISSION_FAILED';receipt['error']=str(exc);save()
 for j in [main,collector]:
  if j:
   try:call(['scancel',j])
   except Exception:pass
 raise SystemExit(str(exc))
print('AUDIT_JOB_ID='+main+'\nCOLLECTOR_JOB_ID='+collector+'\nRESULT_DIRECTORY='+str(run))
print('CHECK_COMMAND=bash '+str(pkg/'CHECK.sh'))
print('Submission is not completion. This job audits readiness; it does not fit models.')
