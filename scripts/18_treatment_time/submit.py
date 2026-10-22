"""Submit an isolated thirteen-job Puma DAG using the standard library only."""
from pathlib import Path
import argparse
import datetime
import fcntl
import os
import re
import shutil
import subprocess
import uuid
from hpc_guard import puma_cluster
from common import require,json_file,save,sha,utc,verify_manifest,workers

IDS=['oatman_scalar','petitesirah_scalar','annual_scalar','chester_scalar',
     'oatman_profile','petitesirah_profile','annual_profile','chester_profile','oatman_soil','grenache_soil']
DEFAULTS={'stage':{'cpus':4,'memory':'20G','time':'04:00:00'},
          'worker':{'cpus':2,'memory':'10G','time':'08:00:00'},
          'combine':{'cpus':2,'memory':'10G','time':'02:00:00'},
          'collector':{'cpus':1,'memory':'5G','time':'01:00:00'}}
READY='6acaa39f5023c9f61a7cbe69d6c5687f634efb2b56e3907158003a41e1f6bf30'

def environment():
    return {k:v for k,v in os.environ.items() if not k.startswith('SBATCH_') and
        k not in {'BASH_ENV','ENV','SHELLOPTS','BASHOPTS','PYTHONPATH','PYTHONHOME','PYTHONOPTIMIZE'}}

def seconds(text):
    match=re.fullmatch(r'(?:(\d+)-)?(\d+):(\d{2}):(\d{2})',text)
    require(match is not None,'Unsupported Slurm duration: '+str(text))
    d,h,m,s=match.groups(); require(int(m)<60 and int(s)<60,'Invalid duration')
    return int(d or 0)*86400+int(h)*3600+int(m)*60+int(s)

def preflight(config):
    require(config.get('account','dinusha1')=='dinusha1','Unexpected account')
    require(config.get('partition','standard')=='standard','Unexpected partition')
    require(workers(config)==IDS,'Expected ten-worker inventory/order differs')
    resources=config.get('resources',DEFAULTS)
    require(resources==DEFAULTS,'Frozen role resources differ from this release')
    package=Path(config.get('runtime_package','/xdisk/dinusha1/dinusha1/NRCS_corrected_rerun_HPC_v3'))
    interpreter=Path(config.get('runtime_python',str(package/'runtime_env/bin/python')))
    require(package.is_absolute() and interpreter.is_absolute(),'Runtime paths must be absolute')
    require((package/'runtime_env/READY.json').is_file(),'Existing corrected READY record is missing')
    require(sha(package/'runtime_env/READY.json')==READY,'Existing corrected READY bytes changed')
    require(os.path.lexists(interpreter),'Existing corrected interpreter entry is missing')
    part=subprocess.run(['scontrol','show','partition','standard','-o'],text=True,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    require(part.returncode==0,'Partition query failed: '+part.stderr)
    fields=dict(re.findall(r'(\w+)=([^\s]+)',part.stdout))
    require(fields.get('PartitionName')=='standard' and fields.get('State')=='UP','Standard partition is not UP')
    maximum=fields.get('MaxTime','')
    require(maximum,'Partition does not report MaxTime')
    if maximum.upper() not in {'UNLIMITED','INFINITE'}:
        require(all(seconds(x['time'])<=seconds(maximum) for x in resources.values()),'A role exceeds partition MaxTime')
    if fields.get('MaxCPUsPerNode','').isdigit():
        require(all(x['cpus']<=int(fields['MaxCPUsPerNode']) for x in resources.values()),'CPU request exceeds partition limit')
    if fields.get('MaxMemPerNode','').isdigit() and int(fields['MaxMemPerNode'])>0:
        require(all(int(x['memory'][:-1])*1024<=int(fields['MaxMemPerNode']) for x in resources.values()),'Expected memory exceeds partition limit')
    return str(interpreter),resources,{'runtime_python':str(interpreter),'ready_sha256':READY,
        'interpreter_resolves_on_login_node':interpreter.exists(),'compute_verification_established':False,
        'partition_record':part.stdout.strip(),'runtime_installation_performed':False,
        'memory_policy':'CPU-only request; expected allocation is 5 GiB per CPU; actual scheduler resources audited.'}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--package',required=True)
    parser.add_argument('--new-run',action='store_true'); args=parser.parse_args()
    package=Path(args.package).resolve();puma_cluster()
    with (package/'SUBMIT.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise SystemExit('Submission already in progress; use CHECK.sh.')
        latest=package/'LATEST_RUN.txt'
        if latest.is_file() and not args.new_run:
            prior=Path(latest.read_text().strip()).resolve()
            require(prior.is_relative_to(package/'runs') and (prior/'submission.json').is_file(),'Unsafe or incomplete prior run')
            print('EXISTING_RUN='+str(prior));print('No new jobs submitted. Run CHECK.sh.');return
        entries=verify_manifest(package,'SHA256SUMS.txt')
        required={'CONFIG.json','SOURCE_MANIFEST.json','common.py','hpc_guard.py','environment_check.py',
            'stage_source.py','source_guard.py','numeric_self_checks.py','worker.py','combine.py','collector.py',
            'RUN_COMMON.sh','RUN_STAGE.sh','RUN_WORKER.sh','RUN_COMBINE.sh','COLLECT.sh','check.py','submit.py','record_runtime.py','compute_guard.py'}
        require(required.issubset({str(x) for x in entries}),'Release manifest omits a mandatory file')
        config=json_file(package/'CONFIG.json');runtime,resources,proof=preflight(config)
        download=Path(config.get('download_directory',str(package.parent))).resolve()
        require(download.is_dir(),'Download directory missing')
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        run=package/'runs'/('run_'+stamp+'_'+uuid.uuid4().hex[:8]);run.mkdir(parents=True)
        for relative in entries:
            target=run/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(package/relative,target);target.chmod(target.stat().st_mode & ~0o222)
        shutil.copy2(package/'SHA256SUMS.txt',run/'PACKAGE_SHA256SUMS.txt');(run/'PACKAGE_SHA256SUMS.txt').chmod(0o444)
        verify_manifest(run)
        for d in ['logs','results','audit/stage','audit/combine','audit/collector']:
            (run/d).mkdir(parents=True,exist_ok=True)
        for wid in IDS:
            (run/'workers'/wid).mkdir(parents=True);(run/'audit/workers'/wid).mkdir(parents=True)
        receipt={'schema_version':1,'state':'STAGED_NOT_SUBMITTED','created_utc':utc(),
            'package_name':config.get('package',package.name),'package_directory':str(package),
            'run_directory':str(run),'download_directory':str(download),'cluster':'puma','account':'dinusha1',
            'partition':'standard','jobs':{},'worker_ids':IDS,'dependencies':{},'commands':[],
            'resources':resources,'runtime_login_preflight':proof,'package_manifest_sha256':sha(run/'PACKAGE_SHA256SUMS.txt'),
            'source_manifest_sha256':sha(run/'SOURCE_MANIFEST.json'),'scientific_execution_established':False,
            'scientific_review_pending':True,'visual_review_pending':True,'ten_year_forecast_generated':False,
            'predictive_validation_established':False,'source_data_staged_on_login':False}
        frozen=False;release_path=package/'checks'/run.name/'SUBMISSION_RELEASE.json';release={}
        def persist():
            require(not frozen,'Refusing to mutate frozen submission')
            save(run/'submission.json',receipt)
            temp=latest.with_name(latest.name+'.tmp.'+uuid.uuid4().hex);temp.write_text(str(run)+'\n');os.replace(temp,latest)
        def submit(role,script,resource,extra=(),dependency=None,hold=False):
            command=['sbatch','--parsable','--account=dinusha1','--partition=standard','--nodes=1','--ntasks=1',
                '--export=ALL','--chdir='+str(run),'--job-name=nrcs_tt_'+role.replace(':','_')[:65],
                '--cpus-per-task='+str(resources[resource]['cpus']),'--time='+resources[resource]['time'],
                '--output='+str(run/'logs'/(role.replace(':','_')+'.log')),
                '--error='+str(run/'logs'/(role.replace(':','_')+'.log'))]
            if hold:command.append('--hold')
            if dependency:command.extend(['--dependency='+dependency,'--kill-on-invalid-dep=yes']);receipt['dependencies'][role]=dependency
            command.extend([str(run/script),str(run),runtime,*extra])
            item={'utc':utc(),'argv':command};receipt['commands'].append(item);persist()
            proc=subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment(),timeout=90)
            item.update({'return_code':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr});persist()
            require(proc.returncode==0,'sbatch failed: '+proc.stderr)
            job=proc.stdout.strip().split(';')[0]
            require(job.isdigit() and job not in receipt['jobs'].values(),'Unexpected or duplicate sbatch ID')
            receipt['jobs'][role]=job;persist();return job
        persist()
        try:
            stage=submit('stage','RUN_STAGE.sh','stage',hold=True)
            tasks=[submit('worker:'+wid,'RUN_WORKER.sh','worker',[wid],'afterok:'+stage) for wid in IDS]
            combine=submit('combine','RUN_COMBINE.sh','combine',dependency='afterok:'+':'.join([stage,*tasks]))
            submit('collector','COLLECT.sh','collector',dependency='afterany:'+':'.join([stage,*tasks,combine]))
            require(len(receipt['jobs'])==13,'Submission inventory incomplete')
            verify_manifest(run)
            receipt['state']='SUBMITTED_RELEASE_REQUESTED_NOT_YET_COMPLETE';receipt['receipt_frozen_before_stage_release']=True
            receipt['release_outcome_record']=str(release_path);persist();frozen=True
            release={'run_directory':str(run),'stage_job_id':stage,'submission_sha256':sha(run/'submission.json'),
                'issued_utc':utc(),'release_confirmed':False};save(release_path,release)
            proc=subprocess.run(['scontrol','release',stage],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment(),timeout=90)
            release.update({'finished_utc':utc(),'return_code':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr,
                'release_confirmed':proc.returncode==0});save(release_path,release)
            require(proc.returncode==0,'Stage release failed: '+proc.stderr)
        except Exception as exc:
            cancellations=[]
            for role,job in receipt['jobs'].items():
                try:
                    p=subprocess.run(['scancel',job],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
                    cancellations.append({'role':role,'job_id':job,'return_code':p.returncode,'stderr':p.stderr})
                except Exception as err:cancellations.append({'role':role,'job_id':job,'error':str(err)})
            if frozen:
                release.update({'state':'RELEASE_FAILED_OR_UNCONFIRMED','error':str(exc),'cancellations':cancellations});save(release_path,release)
            else:
                receipt.update({'state':'SUBMISSION_FAILED','error':str(exc),'cancellations':cancellations});persist()
            raise
        for role,job in receipt['jobs'].items():print(role.upper().replace(':','_')+'_JOB_ID='+job)
        print('PARALLEL_WORKER_COUNT=10');print('NEWLY_SUBMITTED_JOB_COUNT=13');print('STAGE_RELEASE_CONFIRMED=true')
        print('RESULT_DIRECTORY='+str(run));print('CHECK_COMMAND=bash '+str(package/'CHECK.sh'))
        print('Submission does not establish computation, scientific acceptance or forecasts.')

if __name__=='__main__': main()
