"""Guarded compute-node validation and compact, streamed output packaging."""
from hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
import argparse
import os
import re
import sys
import zipfile
from common import (require,json_file,save,sha,utc,verify_manifest,scheduler_rows,
                    verify_runtime,workers,audit_directory)

CAP=128*1024*1024

def identity(value,job,node,label):
    ident=value.get('execution',{})
    require(ident.get('job_id')==job and ident.get('cluster')=='puma' and ident.get('node')==node,
            label+': actual execution identity differs')

def validate(run,receipt,rows):
    errors=[];proof={}
    def attempt(label,function):
        try:proof[label]=function()
        except Exception as exc:errors.append(label+': '+str(exc))
    ids=receipt['worker_ids'];jobs=receipt['jobs']
    def release():
        path=Path(receipt['package_directory'])/'checks'/run.name/'SUBMISSION_RELEASE.json'
        value=json_file(path)
        require(value.get('run_directory')==str(run) and value.get('stage_job_id')==jobs['stage']
                and value.get('submission_sha256')==sha(run/'submission.json'), 'Release/frozen-submission chain differs')
        require(value.get('release_confirmed') is True,'Stage release was not confirmed')
        require(sha(run/'PACKAGE_SHA256SUMS.txt')==receipt.get('package_manifest_sha256')
                and sha(run/'SOURCE_MANIFEST.json')==receipt.get('source_manifest_sha256'),
                'Package/source manifest changed after frozen submission')
        return {'passed':True,'release_sha256':sha(path),'submission_sha256':sha(run/'submission.json')}
    attempt('submission_release',release)
    for role in ['stage',*['worker:'+x for x in ids],'combine']:
        attempt('runtime:'+role,lambda role=role:verify_runtime(run,role,jobs[role],rows.get(jobs[role],{})))
    def stage():
        status=json_file(run/'audit/stage/STAGE_STATUS.json')
        require(status.get('completed') is True,'Stage is not complete')
        identity(status,jobs['stage'],proof['runtime:stage']['node'],'Stage status')
        source=json_file(run/'audit/stage/SOURCE_VERIFICATION.json')
        require(source.get('passed') is True and source.get('source_files_changed') is False,'Stage source proof failed')
        checks=json_file(run/'audit/stage/NUMERIC_SELF_CHECKS.json')
        require(checks.get('passed') is True and checks.get('numerical_checks_executed_on_puma') is True,
                'Numerical self-checks did not pass on Puma')
        identity(checks,jobs['stage'],proof['runtime:stage']['node'],'Numerical self-checks')
        return {'completed':True,'self_checks_passed':True,'source_verified':True}
    attempt('stage',stage)
    for wid in ids:
        def worker(wid=wid):
            directory=run/'workers'/wid;status=json_file(directory/'WORKER_STATUS.json')
            runtime=proof['runtime:worker:'+wid]
            identity(status,jobs['worker:'+wid],runtime['node'],'Worker '+wid)
            for key,value in [('completed',True),('numerical_self_checks_passed',True),
                              ('source_files_changed',False),('package_integrity_passed',True)]:
                require(status.get(key) is value,'Worker '+wid+' invalid '+key)
            require(status.get('self_checks_sha256')==sha(run/'audit/stage/NUMERIC_SELF_CHECKS.json'),
                    'Worker numerical self-check receipt hash differs')
            manifest=directory/'OUTPUT_SHA256SUMS.txt'
            require(sha(manifest)==status.get('output_manifest_sha256'),'Worker output manifest hash differs')
            paths=verify_manifest(directory,'OUTPUT_SHA256SUMS.txt')
            require(any(str(p).endswith(('.csv','.csv.gz','.tsv','.tsv.gz')) for p in paths),'Worker has no manifested result table')
            return {'completed':True,'output_manifest_sha256':sha(manifest),'manifest_files':len(paths)}
        attempt('worker:'+wid,worker)
    def combine():
        status=json_file(run/'results/COMBINE_STATUS.json')
        require(status.get('completed') is True,'Combined science incomplete')
        identity(status,jobs['combine'],proof['runtime:combine']['node'],'Combine')
        for key,value in [('source_files_changed',False),('package_integrity_passed',True),('numerical_self_checks_passed',True)]:
            require(status.get(key) is value,'Combine invalid '+key)
        manifest=run/'results/OUTPUT_SHA256SUMS.txt'
        require(sha(manifest)==status.get('output_manifest_sha256'),'Combined output manifest hash differs')
        paths=verify_manifest(run/'results','OUTPUT_SHA256SUMS.txt')
        report=json_file(run/'results/REPORT_STATUS.json')
        require(report.get('created') is True,'Report was not created')
        require(any(str(x) in {'multiplicity_family_audit.csv','multiplicity_audit.csv'} for x in paths),'Multiplicity-family audit is missing')
        require(any(x.suffix=='.pdf' for x in paths),'Manifested report PDF is missing')
        return {'completed':True,'output_manifest_sha256':sha(manifest),'manifest_files':len(paths),'report_created':True}
    attempt('combine',combine)
    def current_sources():
        from source_guard import verify_sources
        value=verify_sources(run,check_original=True)
        require(isinstance(value,dict) and value.get('passed') is True and value.get('source_files_changed') is False,
                'Current source hash verification did not pass')
        save(run/'audit/collector/SOURCE_VERIFICATION.json',value)
        return {'passed':True,'source_files_changed':False,'proof_sha256':sha(run/'audit/collector/SOURCE_VERIFICATION.json')}
    attempt('current_sources',current_sources)
    return proof,errors

def select_files(run):
    """Explicit output roots; never include original matrices, snapshots or environments."""
    selected=set()
    for p in run.iterdir():
        if p.is_file() and not p.is_symlink() and p.suffix in {'.py','.sh','.json','.txt','.md'}:
            selected.add(p)
    for folder in ['results','workers','audit']:
        root=run/folder
        if not root.exists():continue
        for p in root.rglob('*'):
            if not p.is_file() or p.is_symlink():continue
            rel=p.relative_to(run)
            if any(x in {'source_snapshot','runtime_env','__pycache__','matplotlib_cache'} for x in rel.parts):continue
            if p.name.endswith(('.pyc','.partial')) or '.tmp.' in p.name:continue
            if rel in {Path('audit/collector/EXECUTION_END.json'),Path('audit/collector/BUNDLE_STATUS.json')}:continue
            selected.add(p)
    logs=run/'logs'
    if logs.is_dir():
        for p in logs.glob('*.log'):
            if p.name=='collector.log' or p.is_symlink():continue
            tail=run/'audit/collector/log_tails'/p.name
            tail.parent.mkdir(parents=True,exist_ok=True)
            with p.open('rb') as f:f.seek(max(0,p.stat().st_size-131072));tail.write_bytes(f.read())
            selected.add(tail)
    return sorted(selected,key=lambda p:str(p.relative_to(run)))

def archive(run,target,files,cap=None):
    require(not target.exists(),'Refusing to overwrite archive '+str(target))
    temporary=Path(str(target)+'.partial');require(not temporary.exists(),'Partial archive already exists')
    records=[]
    try:
        with zipfile.ZipFile(temporary,'x',allowZip64=True) as out:
            for p in files:
                relative=p.relative_to(run);before=sha(p);size=p.stat().st_size
                info=zipfile.ZipInfo.from_file(p,str(Path(run.name)/relative))
                info.compress_type=zipfile.ZIP_STORED if p.suffix in {'.gz','.png','.zip','.xlsx','.pptx','.docx'} else zipfile.ZIP_DEFLATED
                info._compresslevel=1
                with p.open('rb') as incoming,out.open(info,'w',force_zip64=True) as outgoing:
                    for block in iter(lambda:incoming.read(1024*1024),b''):outgoing.write(block)
                require(p.stat().st_size==size and sha(p)==before,'File changed while packaging: '+str(relative))
                records.append({'path':str(relative),'sha256':before,'bytes':size})
            out.writestr(str(Path(run.name)/'BUNDLE_CONTENT_SHA256SUMS.txt'),
                ''.join(x['sha256']+'  '+x['path']+'\n' for x in records))
        if cap is not None:require(temporary.stat().st_size<=cap,'Review ZIP exceeds fixed 128 MiB cap')
        with zipfile.ZipFile(temporary) as check:
            require(check.testzip() is None,'Archive CRC check failed')
            require(len(check.namelist())==len(set(check.namelist())),'Duplicate archive members')
        os.replace(temporary,target)
        checksum=Path(str(target)+'.sha256.txt');require(not checksum.exists(),'Checksum already exists')
        checksum.write_text(sha(target)+'  '+target.name+'\n')
        return {'path':str(target),'checksum_path':str(checksum),'sha256':sha(target),
            'bytes':target.stat().st_size,'file_count':len(records)+1,'zip_integrity_verified':True,
            'within_cap':cap is None or target.stat().st_size<=cap}
    except Exception:
        if temporary.exists():temporary.unlink()
        raise

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-root',required=True);args=parser.parse_args()
    run=Path(args.run_root).resolve();verify_manifest(run);receipt=json_file(run/'submission.json')
    require(receipt['run_directory']==str(run),'Receipt run differs')
    require(receipt['jobs']['collector']==execution['job_id'],'Wrong collector allocation')
    rows,raw=scheduler_rows(receipt['jobs'].values());audit=run/'audit/collector'
    (audit/'sacct_main_jobs.txt').write_text(raw)
    proof,errors=validate(run,receipt,rows)
    save(audit/'VALIDATION_STATUS.json',{'execution':execution,'utc':utc(),'passed':not errors,'errors':errors,'proof':proof})
    download=Path(receipt['download_directory']).resolve();require(download.is_dir(),'Download directory missing')
    package=receipt['package_name'];require(re.fullmatch('[A-Za-z0-9_-]+',package) is not None,'Unsafe package name')
    stem=package+'_'+receipt['jobs']['combine']+'_'+run.name
    targets={kind:download/(stem+'_'+kind+'.zip') for kind in ['review','results']}
    status={'schema_version':1,'status':'HPC_TREATMENT_TIME_COMPLETED_REVIEW_PENDING' if not errors else 'HPC_RUN_FAILED_OR_INCOMPLETE',
        'collected_utc':utc(),'collector_execution':execution,'collector_running_at_snapshot':True,
        'collector_final_completion_verified':False,'final_completion_instruction':'Run CHECK.sh after the collector exits.',
        'scientific_scripts_completed':not errors,'scientific_review_pending':True,'visual_review_pending':True,
        'source_files_changed':False if proof.get('current_sources',{}).get('passed') else None,
        'package_integrity_passed':True,'validation_errors':errors,
        'ten_year_forecast_generated':False,'predictive_validation_established':False,
        'archive_snapshot_policy':'Collector final END and post-finalization BUNDLE_STATUS are external; live CHECK verifies them.',
        'full_source_matrices_retained_on_puma':True,'downloads_ready':False}
    for kind,path in targets.items():status['download_'+kind]=str(path);status['download_'+kind+'_checksum']=str(path)+'.sha256.txt'
    save(run/'HPC_STATUS.json',status)
    files=select_files(run);archives={};packaging_errors=[]
    for kind in ['results','review']:
        try:archives[kind]=archive(run,targets[kind],files,CAP if kind=='review' else None)
        except Exception as exc:packaging_errors.append(kind+': '+str(exc))
    bundle={'schema_version':1,'execution':execution,'utc':utc(),'archives':archives,
        'packaging_success':not packaging_errors and set(archives)=={'results','review'},'errors':packaging_errors,
        'scientific_scripts_completed':not errors,'review_cap_bytes':CAP,
        'scientific_status_snapshot_sha256':sha(run/'HPC_STATUS.json'),'snapshot_excludes_final_collector_end':True,
        'scientific_review_pending':True,'visual_review_pending':True}
    save(audit/'BUNDLE_STATUS.json',bundle)
    for kind,record in archives.items():
        print('DOWNLOAD_'+kind.upper()+'='+record['path'])
        print('DOWNLOAD_'+kind.upper()+'_CHECKSUM='+record['checksum_path'])
    print('SCIENTIFIC_SCRIPTS_COMPLETED='+str(not errors).lower())
    print('PACKAGING_SUCCESS='+str(bundle['packaging_success']).lower())
    for error in errors+packaging_errors:print('VALIDATION_ERROR='+error)
    require(bundle['packaging_success'],'One or more output archives failed packaging')

if __name__=='__main__':main()
