#!/usr/bin/env python3
"""Stage an immutable run and submit analysis plus after-any audit collection."""
from pathlib import Path
import sys,os,json,hashlib,datetime,uuid,shutil,subprocess
from payload.hpc_guard import puma_cluster

package=Path(sys.argv[1]).resolve()
try:puma_cluster()
except Exception as exc:raise SystemExit('Submit from Puma: '+str(exc))
if not (package/'runtime_env/READY.json').is_file():raise SystemExit('Environment not ready. Run bash START_HERE.sh first.')
entries=[]
for line in (package/'SHA256SUMS.txt').read_text().splitlines():
 sha,name=line.split('  ',1);rel=Path(name)
 if rel.is_absolute()or '..'in rel.parts:raise SystemExit('Unsafe manifest path')
 if hashlib.sha256((package/rel).read_bytes()).hexdigest()!=sha:raise SystemExit('Checksum mismatch: '+name)
 entries.append(rel)
download_directory=Path((package/'DOWNLOAD_DIRECTORY.txt').read_text().strip()).resolve()
if not download_directory.is_dir():raise SystemExit('Download directory is missing: '+str(download_directory))
stamp=datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
run=package/'runs'/('run_'+stamp+'_'+uuid.uuid4().hex[:8]);run.mkdir(parents=True)
for rel in entries:
 dst=run/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(package/rel,dst)
shutil.copy2(package/'SHA256SUMS.txt',run/'PACKAGE_SHA256SUMS.txt')
(run/'audit').mkdir();(run/'logs').mkdir()
setup=Path((package/'runtime_env/SETUP_DIRECTORY.txt').read_text().strip())
if setup.is_dir():shutil.copytree(setup,run/'audit/setup')
receipt={'download_directory':str(download_directory),'package_revision':'v3_corrected','state':'STAGED_NOT_SUBMITTED','created_utc':stamp,'run_directory':str(run),'package_directory':str(package),
 'accounting_since':(datetime.datetime.now()-datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
 'environment_python':str(package/'runtime_env/bin/python'),'account':'dinusha1','partition':'standard','jobs':{},'commands':[]}
def save():
 (run/'submission.json').write_text(json.dumps(receipt,indent=2)+'\n')
 (package/'LATEST_RUN.txt').write_text(str(run)+'\n')
def call(args):
 receipt['commands'].append(args);save()
 env=dict(os.environ)
 for k in ['BASH_ENV','ENV','SHELLOPTS','BASHOPTS','SBATCH_GET_USER_ENV']:env.pop(k,None)
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,env=env)
 if p.returncode:raise RuntimeError('Command failed: '+repr(args)+'\n'+p.stderr)
 return p.stdout.strip()
def jobid(reply):
 value=reply.split(';')[0]
 if not value.isdigit():raise RuntimeError('Unexpected sbatch reply: '+reply)
 return value
main=None
try:
 common=['sbatch','--parsable','--account=dinusha1','--partition=standard','--nodes=1','--ntasks=1','--export=ALL','--chdir='+str(run)]
 main=jobid(call(common+['--hold','--job-name=nrcs_comp','--cpus-per-task=2','--time=12:00:00','--mem=12G',
  '--output='+str(run/'logs/analysis.log'),'--error='+str(run/'logs/analysis.log'),str(run/'RUN.sh'),str(run),str(package/'runtime_env/bin/python')]))
 receipt['jobs']['analysis']=main;save()
 collector=jobid(call(common+['--job-name=nrcs_audit','--cpus-per-task=1','--time=00:15:00',
  '--dependency=afterany:'+main,'--output='+str(run/'logs/collector.log'),'--error='+str(run/'logs/collector.log'),
  str(run/'COLLECT.sh'),str(run),main]))
 receipt['jobs']['collector']=collector;receipt['state']='SUBMITTED_NOT_YET_COMPLETE';save()
 call(['scontrol','release',main]);save()
except Exception as exc:
 receipt['state']='SUBMISSION_FAILED';receipt['error']=str(exc);save()
 if main:
  try:call(['scancel',main])
  except Exception:pass
 raise SystemExit(str(exc))
print('ANALYSIS_JOB_ID='+main+'\nCOLLECTOR_JOB_ID='+collector+'\nRESULT_DIRECTORY='+str(run))
print('CHECK_COMMAND=bash '+str(package/'CHECK.sh'))
print('Receipt status is not scheduler state. No completion is implied by submission.')
