"""Pending-aware live Puma scheduler and final packaging check; standard library only."""
from pathlib import Path
import argparse
import re
import subprocess
from hpc_guard import puma_cluster
from common import (require,json_file,save,sha,utc,verify_manifest,scheduler_rows,
                    verify_runtime,load_status,TERMINAL,workers)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',required=True)
    parser.add_argument('--run-root');args=parser.parse_args()
    package=Path(args.package).resolve();puma_cluster()
    run=Path(args.run_root or (package/'LATEST_RUN.txt').read_text().strip()).resolve()
    require(run.is_relative_to(package/'runs') and run.is_dir(),'Unsafe/missing run')
    verify_manifest(run);receipt=json_file(run/'submission.json');config=json_file(run/'CONFIG.json')
    require(sha(run/'PACKAGE_SHA256SUMS.txt')==receipt.get('package_manifest_sha256')
            and sha(run/'SOURCE_MANIFEST.json')==receipt.get('source_manifest_sha256'),
            'Package/source manifest differs from frozen submission')
    require(receipt.get('run_directory')==str(run) and receipt.get('cluster')=='puma','Receipt identity differs')
    ids=workers(config);expected=['stage',*['worker:'+x for x in ids],'combine','collector']
    jobs=receipt['jobs'];require(set(jobs)==set(expected),'Scheduled role inventory differs')
    require(len(jobs.values())==len(set(jobs.values())),'New jobs share a scheduler identity')
    rows,raw=scheduler_rows(jobs.values());external=package/'checks'/run.name
    release,release_error=load_status(external/'SUBMISSION_RELEASE.json')
    require(not release or (release.get('submission_sha256')==sha(run/'submission.json')
        and release.get('run_directory')==str(run) and release.get('stage_job_id')==jobs['stage']),
        'Frozen submission/release identity differs')
    queue=subprocess.run(['squeue','--noheader','--jobs',','.join(jobs.values()),'--format=%i|%T|%N|%R'],
        text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    all_terminal=all(rows.get(job,{}).get('state') in TERMINAL for job in jobs.values())
    all_ok=all(rows.get(job,{}).get('state')=='COMPLETED' and rows.get(job,{}).get('exit_code')=='0:0' for job in jobs.values())
    collector_job=jobs['collector'];collector_row=rows.get(collector_job,{})
    terminal=collector_row.get('state') in TERMINAL
    status,status_error=load_status(run/'HPC_STATUS.json')
    bundle,bundle_error=load_status(run/'audit/collector/BUNDLE_STATUS.json')
    final={'verified':False,'pending':not terminal};errors=[];archives={};packaged=False
    if terminal:
        try:final=verify_runtime(run,'collector',collector_job,collector_row)
        except Exception as exc:errors.append('Collector final completion: '+str(exc))
        if status_error:errors.append('HPC_STATUS: '+status_error)
        if bundle_error:errors.append('BUNDLE_STATUS: '+bundle_error)
        if not release or release.get('release_confirmed') is not True:errors.append('Stage release is not confirmed')
        if bundle:
            identity=bundle.get('execution',{})
            if not (identity.get('job_id')==collector_job and identity.get('cluster')=='puma' and identity.get('node')==final.get('node')):
                errors.append('Final bundle execution identity differs')
            if bundle.get('packaging_success') is not True:errors.append('Packaging did not succeed')
            if bundle.get('errors'):errors.extend('Packaging: '+str(x) for x in bundle['errors'])
            if not (run/'HPC_STATUS.json').is_file() or sha(run/'HPC_STATUS.json')!=bundle.get('scientific_status_snapshot_sha256'):
                errors.append('Frozen scientific status snapshot hash differs')
            destination=Path(receipt['download_directory']).resolve()
            for kind in ['review','results']:
                try:
                    item=bundle['archives'][kind];path=Path(item['path']);side=Path(item['checksum_path'])
                    require(path.is_absolute() and path.parent.resolve()==destination and path.is_file() and not path.is_symlink(),'Unsafe or missing '+kind+' archive')
                    require(side==Path(str(path)+'.sha256.txt') and side.is_file() and not side.is_symlink(),'Missing/unsafe checksum sidecar')
                    require(item.get('zip_integrity_verified') is True and item.get('bytes')==path.stat().st_size,'Archive verification metadata differs')
                    require(re.fullmatch('[a-f0-9]{64}',str(item.get('sha256',''))) is not None,'Invalid archive digest')
                    require(sha(path)==item['sha256'],'Archive byte hash differs')
                    require(side.read_text()==item['sha256']+'  '+path.name+'\n','Checksum text differs')
                    if kind=='review':require(path.stat().st_size<=128*1024*1024 and item.get('within_cap') is True,'Review archive exceeds cap')
                    archives[kind]=item
                except Exception as exc:errors.append(kind+': '+str(exc))
            packaged=not errors and set(archives)=={'review','results'}
    ready=terminal and final.get('verified') is True and packaged
    scientific=ready and all_ok and status.get('scientific_scripts_completed') is True and bundle.get('scientific_scripts_completed') is True
    check={'checked_utc':utc(),'run_directory':str(run),'cluster':'puma',
        'receipt_state_not_scheduler_state':receipt.get('state'),'release':release,'release_error':release_error,
        'scheduled_stages':{role:rows.get(job,{'job_id':job,'state':'NOT_YET_RECORDED'}) for role,job in jobs.items()},
        'all_new_jobs_terminal':all_terminal,'all_new_jobs_completed_successfully':all_ok,
        'collector_final_completion':final,'collector_status':status.get('status','PENDING_OR_MISSING'),
        'packaging_success':packaged,'downloads_ready':ready,'scientific_scripts_completed':scientific,
        'errors':errors,'archives':archives,'scientific_review_pending':True,'visual_review_pending':True,
        'ten_year_forecast_generated':False,'predictive_validation_established':False,
        'live_squeue_return_code':queue.returncode,'live_squeue_stderr':queue.stderr}
    save(external/'LIVE_CHECK.json',check)
    (external/'LIVE_SACCT_MAIN.txt').write_text(raw);(external/'LIVE_SQUEUE.txt').write_text(queue.stdout)
    if terminal:save(external/'FINAL_COMPLETION.json',check)
    print('RUN_DIRECTORY='+str(run));print('LIVE_CHECK_RECORD='+str(external/'LIVE_CHECK.json'))
    print('RECEIPT_STATE_NOT_SCHEDULER_STATE='+str(receipt.get('state')))
    for role in expected:
        row=rows.get(jobs[role],{});print(role+' job='+jobs[role]+' state='+row.get('state','NOT_YET_RECORDED')+' exit='+row.get('exit_code',''))
    for label,value in [('ALL_NEW_JOBS_COMPLETED_SUCCESSFULLY',all_ok),('ALL_NEW_JOBS_TERMINAL',all_terminal),
        ('COLLECTOR_FINAL_COMPLETION_VERIFIED',final.get('verified') is True),('SCIENTIFIC_SCRIPTS_COMPLETED',scientific),
        ('DOWNLOADS_READY',ready)]:print(label+'='+str(value).lower())
    print('COLLECTOR_STATUS='+str(check['collector_status']))
    if not terminal:print('COLLECTOR_COMPLETION_CHECK=PENDING; collector has not reached a terminal scheduler state.')
    else:
        print('PACKAGING_SUCCESS='+str(packaged).lower());print('FINAL_CHECK_ERROR_COUNT='+str(len(errors)))
        for error in errors:print('FINAL_CHECK_ERROR='+error)
    if ready:
        for kind,item in archives.items():
            print('DOWNLOAD_'+kind.upper()+'='+item['path']);print('DOWNLOAD_'+kind.upper()+'_CHECKSUM='+item['checksum_path'])
        print('FINAL_COMPLETION_RECORD='+str(external/'FINAL_COMPLETION.json'))
    if terminal and status.get('validation_errors'):
        print('RETURNED_VALIDATION_ERROR_COUNT='+str(len(status['validation_errors'])))
        for error in status['validation_errors'][:20]:print('RETURNED_VALIDATION_ERROR='+str(error))
    print('Scientific and visual review remain pending until returned outputs are inspected.')

if __name__=='__main__':main()
