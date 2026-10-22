#!/usr/bin/env python3
"""Verify and byte-copy ten workers and six completed chunks on Puma only."""
from pathlib import Path
import argparse
import shutil
import traceback
from hpc_guard import require_hpc
from orchestration_common import json_file, load_shards, require, save, sha, utc, verify_manifest
from recovery_common import recovery_plan
from recovery_validation import (EXPECTED_READY_SHA256, finish_sources, remember_file,
    scheduler_completed, verify_chunk, verify_runtime, verify_source_receipt, verify_worker)


def copy_file(source, destination, records):
    source, destination = Path(source), Path(destination)
    require(source.is_file() and not source.is_symlink(), 'Unsafe input byte copy: ' + str(source))
    digest = remember_file(records, source)
    require(not destination.exists(), 'Refusing to overwrite imported evidence: ' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    require(sha(destination) == digest, 'Imported bytes differ: ' + str(destination))
    destination.chmod(0o444)
    remember_file(records, destination, digest)


def copy_tree(source, destination, records):
    source, destination = Path(source), Path(destination)
    require(source.is_dir() and not source.is_symlink(), 'Missing complete source directory')
    require(not destination.exists() or (destination.is_dir() and not any(destination.iterdir())),
            'Import target must be empty: ' + str(destination))
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in sorted(source.rglob('*')):
        require(not entry.is_symlink(), 'Symlink in completed input tree')
        if entry.is_file():
            require('__pycache__' not in entry.parts and not entry.name.endswith('.pyc'),
                    'Unexpected bytecode in completed input evidence')
            copy_file(entry, destination / entry.relative_to(source), records)
            count += 1
    require(count > 0, 'Empty completed input tree')
    return count


def stage(run, execution):
    audit = run / 'audit/stage'
    audit.mkdir(parents=True, exist_ok=True)
    records = {}
    entries = verify_manifest(run)
    remember_file(records, run / 'PACKAGE_SHA256SUMS.txt')
    for name in entries:
        remember_file(records, run / name)
    config, receipt = json_file(run / 'CONFIG.json'), json_file(run / 'submission.json')
    remember_file(records, run / 'submission.json')
    plan, pins = recovery_plan(config), config['join_repair']
    require(receipt['jobs'].get('stage') == execution['job_id'], 'Wrong actual import allocation')
    source = Path(pins['source_run'])
    require(source.is_absolute() and source.is_dir() and not source.is_symlink(), 'Source run unavailable')
    require(not run.is_relative_to(source) and not source.is_relative_to(run), 'Repair must be isolated')
    for name, key in [('PACKAGE_SHA256SUMS.txt','source_manifest_sha256'),
                      ('submission.json','source_submission_sha256'), ('HPC_STATUS.json','source_status_sha256')]:
        remember_file(records, source / name, pins[key])
    for name in verify_manifest(source):
        remember_file(records, source / name)
    for name, digest in pins['source_code_sha256'].items():
        remember_file(records, source / name, digest)
    old_config, old_receipt = json_file(source / 'CONFIG.json'), json_file(source / 'submission.json')
    require(old_receipt.get('run_directory') == str(source), 'Source receipt path differs')
    require(old_config['analysis'] == config['analysis'] and old_config['recovery'] == config['recovery'],
            'Frozen scientific/source recovery settings changed')
    for name in ['SOURCES.json','SHARDS.json','association_core.py','categorical_core.py',
                 'association_figures.py','adapter_self_checks.py','association_self_checks.py',
                 'categorical_self_checks.py']:
        require(sha(source/name) == sha(run/name), 'Frozen scientific code/registry changed: ' + name)
    for spec in json_file(run / 'SOURCES.json').values():
        remember_file(records, spec['path'], spec['sha256'])
    remember_file(records, Path(config['runtime_package']) / 'runtime_env/READY.json', EXPECTED_READY_SHA256)
    original_jobs, chunks = plan['reuse_origin_worker_jobs'], plan['retry_chunks']
    require(len(original_jobs) == 10 and len(chunks) == 6, 'Wrong completed input inventory')
    jobs = list(original_jobs.values()) + list(pins['source_chunk_jobs'].values()) + [pins['source_stage_job_id']]
    scheduling, raw = scheduler_completed(jobs, audit / 'SOURCE_COMPLETED_SACCT.txt')
    source_stage = source / 'audit/stage/STAGE_STATUS.json'
    remember_file(records, source_stage, pins['source_stage_status_sha256'])
    status = json_file(source_stage)
    require(status.get('completed') is True and status.get('source_files_changed') is False and
            status.get('package_integrity_passed') is True and status.get('imported_worker_count') == 10,
            'Original import stage did not establish preservation')
    require(old_receipt['jobs'].get('stage') == pins['source_stage_job_id'], 'Original stage identity differs')
    verify_runtime(source / 'audit/stage', pins['source_stage_job_id'], status['execution'], records,
                   scheduling, EXPECTED_READY_SHA256)
    source_proof = verify_source_receipt(source / 'audit/stage/SOURCE_VERIFICATION.json',
                                      pins['source_stage_job_id'], records)
    require(source_proof['execution']['node'] == status['execution']['node'], 'Source stage node differs')
    copy_tree(source / 'audit/stage', audit.parent / 'history/source_stage', records)
    for name in ['CONFIG.json','submission.json','HPC_STATUS.json','PACKAGE_SHA256SUMS.txt']:
        copy_file(source / name, audit.parent / 'history/recovery_source' / name, records)
    old_context = {'plan': recovery_plan(old_config), 'receipt': old_receipt}
    imported = []
    for shard in load_shards(run):
        sid = shard['id']
        if sid not in original_jobs:
            continue
        require(receipt['jobs'].get('worker:'+sid) == old_receipt['jobs'].get('worker:'+sid) == original_jobs[sid],
                'Original worker actual job identity changed')
        proof = verify_worker(source, shard, records, scheduling, stages=old_context)
        remember_file(records, source/'workers'/sid/'WORKER_STATUS.json',
                      config['recovery']['preserved_worker_status_sha256'][sid])
        count = copy_tree(source/'workers'/sid, run/'workers'/sid, records)
        copy_tree(source/'audit/workers'/sid, run/'audit/workers'/sid, records)
        imported.append({'kind':'PRESERVED_ORIGINAL_WORKER','id':sid,'job_id':original_jobs[sid],
                         'execution':proof['status']['execution'],'copied_files':count,
                         'output_manifest_sha256':proof['status']['output_manifest_sha256']})
        save(audit/'STAGE_PROGRESS.json',{'phase':'IMPORTED_WORKER','id':sid,'utc':utc(),'execution':execution})
        print('IMPORTED_ORIGINAL_WORKER='+sid,flush=True)
    for spec in chunks:
        cid = spec['id']
        require(receipt['jobs'].get('chunk:'+cid) == old_receipt['jobs'].get('chunk:'+cid) == pins['source_chunk_jobs'][cid],
                'Completed chunk actual job identity changed')
        directory = source/'workers/chunks'/cid
        remember_file(records, directory/'WORKER_STATUS.json',pins['source_chunk_status_sha256'][cid])
        remember_file(records, directory/'OUTPUT_SHA256SUMS.txt',pins['source_chunk_output_manifest_sha256'][cid])
        proof = verify_chunk(source,spec,records,scheduling)
        count = copy_tree(directory,run/'workers/chunks'/cid,records)
        copy_tree(source/'audit/chunks'/cid,run/'audit/chunks'/cid,records)
        imported.append({'kind':'PRESERVED_COMPLETED_CHUNK','id':cid,'job_id':pins['source_chunk_jobs'][cid],
                         'execution':proof['status']['execution'],'copied_files':count,
                         'output_manifest_sha256':proof['status']['output_manifest_sha256']})
        save(audit/'STAGE_PROGRESS.json',{'phase':'IMPORTED_CHUNK','id':cid,'utc':utc(),'execution':execution})
        print('IMPORTED_COMPLETED_CHUNK='+cid,flush=True)
    require(len(imported)==16, 'Incomplete import inventory')
    verify_manifest(source)
    verify_manifest(run)
    proof = finish_sources(records,execution,audit/'SOURCE_VERIFICATION.json')
    save(audit/'IMPORTED_INPUTS.json',{'execution':execution,'source_run':str(source),'inputs':imported,
        'complete_inputs_only':True,'old_partial_join_outputs_imported':False,'historical_receipts_relabelled':False})
    save(audit/'STAGE_STATUS.json',{'completed':True,'source_files_changed':False,'package_integrity_passed':True,
        'execution':execution,'scientific_review_pending':True,'visual_review_pending':True,
        'imported_worker_count':10,'imported_chunk_count':6,'imported_complete_worker_count':10,
        'imported_complete_chunk_count':6,'original_worker_jobs':original_jobs,
        'source_chunk_jobs':pins['source_chunk_jobs'],'source_run':str(source),
        'historical_source_stage_job_id':pins['source_stage_job_id'],
        'historical_partial_join_outputs_read':False,'historical_partial_join_outputs_imported':False,
        'source_file_count':len(proof['files']),'source_verification_sha256':sha(audit/'SOURCE_VERIFICATION.json'),
        'old_partial_join_outputs_imported':False,'historical_receipts_relabelled':False,
        'scientific_inference_recomputed':False,'completed_utc':utc()})
    print('VERIFIED_IMPORT_COMPLETED_WORKERS=10 CHUNKS=6',flush=True)


def main():
    execution = require_hpc()
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-root',required=True)
    run = Path(parser.parse_args().run_root).resolve()
    try:
        stage(run,execution)
    except Exception as error:
        save(run/'audit/stage/STAGE_STATUS.json',{'completed':False,'source_files_changed':None,
            'package_integrity_passed':False,'execution':execution,'scientific_review_pending':True,
            'error':str(error),'traceback':traceback.format_exc(),'failed_utc':utc()})
        raise


if __name__ == '__main__':
    main()
