from pathlib import Path
import datetime,hashlib,json,os,subprocess,uuid
from hpc_guard import puma_cluster

BASE=Path(__file__).resolve().parent

def submission_environment():
    blocked={'BASH_ENV','ENV','SHELLOPTS','BASHOPTS','SBATCH_GET_USER_ENV'}
    return {k:v for k,v in os.environ.items() if k not in blocked and not k.startswith('SBATCH_')}

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def main():
    puma_cluster()
    c=json.loads((BASE/'CONFIG.json').read_text())
    for line in (BASE/'SHA256SUMS.txt').read_text().splitlines():
        h,p=line.split('  ',1)
        if sha(BASE/p)!=h:raise SystemExit('Package checksum failed: '+p)
    if not Path(c['source_archive']).is_file():raise SystemExit('Keep the v2 results ZIP at '+c['source_archive'])
    if sha(c['source_archive'])!=c['source_archive_sha256']:raise SystemExit('Source v2 archive checksum mismatch; no job submitted.')
    # Like the successful v2 launcher, test the directory entry on login.
    # A module-backed symlink need not resolve until the compute-node setup.
    if not os.path.lexists(c['runtime_python']):
        raise SystemExit('Pinned runtime directory entry is missing: '+c['runtime_python']+'; no job submitted. Return this output.')
    if not Path(c['runtime_ready']).is_file():
        raise SystemExit('Pinned runtime READY file is missing: '+c['runtime_ready']+'; no job submitted. Return this output.')
    if sha(c['runtime_ready'])!=c['runtime_ready_sha256']:
        raise SystemExit('Pinned runtime READY checksum differs; no job submitted. Return this output.')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run=BASE/'runs'/('run_'+stamp+'_'+uuid.uuid4().hex[:8]);run.mkdir(parents=True)
    import shutil
    package=run/'package';package.mkdir()
    for p in BASE.iterdir():
        if p.is_file() and p.name!='LATEST_RUN.txt':shutil.copy2(p,package/p.name)
    cmd=['sbatch','--parsable','--hold','--account='+c['account'],'--partition='+c['partition'],
         '--job-name=NRCS_figure_refresh','--nodes=1','--ntasks=1','--cpus-per-task=2','--mem=8G','--time=02:00:00',
         '--chdir='+str(run),'--export=ALL',
         '--output='+str(run/'stdout.log'),'--error='+str(run/'stderr.log'),
         str(package/'RUN.sh'),str(package),str(run),c['runtime_python']]
    answer=subprocess.check_output(cmd,text=True,timeout=60,env=submission_environment()).strip();job=answer.split(';')[0]
    if not job.isdigit():raise SystemExit('Unexpected sbatch response: '+answer)
    receipt={'job_id':job,'run_directory':str(run),'state':'SUBMITTED_HELD',
             'source_archive_sha256':c['source_archive_sha256'],'downloads_ready':False}
    (run/'submission.json').write_text(json.dumps(receipt,indent=2)+'\n')
    (BASE/'LATEST_RUN.txt').write_text(str(run)+'\n')
    try:subprocess.run(['scontrol','release',job],check=True,timeout=30,env=submission_environment())
    except Exception:
        print('Job remains held; submission receipt is saved. Do not resubmit blindly.');raise
    receipt['state']='SUBMITTED_RELEASE_REQUESTED_NOT_YET_COMPLETE'
    (run/'submission.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2));print('CHECK_COMMAND=bash '+str(BASE/'CHECK.sh'))

if __name__=='__main__':main()
