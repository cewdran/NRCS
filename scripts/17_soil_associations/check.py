#!/usr/bin/env python3
"""Read live scheduler identity and returned audit receipts; standard library only."""
from pathlib import Path
import argparse
import re
import subprocess
from hpc_guard import puma_cluster
from orchestration_common import (TERMINAL, json_file, normalize_status, require,
                                  save, scheduler_main_rows, sha, utc, verify_manifest)
from recovery_common import (FAILED_WORKER_IDS, ORIGINAL_WORKER_JOBS, SOURCE_CHUNK_JOBS,
                             JOIN_REPAIR_PINS, SCHEDULED_ROLES, recovery_plan)


def download_value(status, kind, checksum=False):
    key = 'download_' + kind + ('_checksum' if checksum else '')
    if status.get(key):
        return status[key]
    archive = status.get('archives', status.get('archive', {}))
    if isinstance(archive, dict) and isinstance(archive.get(kind), dict):
        record = archive[kind]
        return record.get('checksum_path' if checksum else 'path')
    if kind == 'results':
        return status.get('archive_checksum' if checksum else 'archive_path')
    return None


def final_collector_completion(run, job, row, expected_ready):
    """Require final live accounting and consistent, actual runtime receipts."""
    errors = []
    directory = run / 'audit/collector'
    records = {}
    for name in ['EXECUTION_START.json', 'EXECUTION_END.json', 'ENVIRONMENT_STATUS.json']:
        value, error = normalize_status(directory / name)
        records[name] = value or {}
        if error:
            errors.append(name + ': ' + error)
    if not job or row.get('job_id') != job or row.get('state') != 'COMPLETED' or row.get('exit_code') != '0:0':
        errors.append('Collector live scheduler state is not COMPLETED with exit 0:0.')
    node = records['EXECUTION_END.json'].get('execution', {}).get('node')
    if not node or row.get('node_list') != node:
        errors.append('Collector final runtime node differs from live scheduler accounting.')
    for name, value in records.items():
        execution = value.get('execution', {})
        if execution.get('job_id') != job or execution.get('cluster') != 'puma' or execution.get('node') != node:
            errors.append(name + ': collector job/cluster/node identity differs.')
    if records['EXECUTION_START.json'].get('event') != 'start':
        errors.append('Collector runtime START event is missing.')
    end = records['EXECUTION_END.json']
    if end.get('event') != 'end' or end.get('pipeline_exit_code') != 0:
        errors.append('Collector runtime END does not establish zero pipeline exit.')
    environment = records['ENVIRONMENT_STATUS.json']
    if (environment.get('status') != 'COMPUTE_RUNTIME_VERIFIED' or
            environment.get('package_integrity_passed') is not True or
            environment.get('ready_sha256') != expected_ready):
        errors.append('Collector corrected-runtime environment was not verified.')
    return {'verified': not errors, 'job_id': job, 'node': node,
            'scheduler': row, 'runtime_end': end, 'errors': errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    parser.add_argument('--run-root', help='Inspect a specific isolated recovery run.')
    args = parser.parse_args()
    package = Path(args.package).resolve()
    if args.run_root:
        run = Path(args.run_root).resolve()
    else:
        require((package / 'LATEST_RUN.txt').is_file(), 'No recovery run has been submitted.')
        run = Path((package / 'LATEST_RUN.txt').read_text().strip()).resolve()
    require(run.is_relative_to(package / 'runs') and run.is_dir(), 'Unsafe or missing recovery run.')
    puma_cluster()
    verify_manifest(run)
    config = json_file(run / 'CONFIG.json')
    plan = recovery_plan(config)
    receipt = json_file(run / 'submission.json')
    require(receipt.get('run_directory') == str(run), 'Receipt run directory differs.')
    require(receipt.get('cluster') == 'puma', 'Receipt cluster differs.')
    release_path = package / 'checks' / run.name / 'SUBMISSION_RELEASE.json'
    release_record, release_error = normalize_status(release_path)
    if release_record:
        require(release_record.get('run_directory') == str(run), 'Release outcome run identity differs.')
        require(release_record.get('submission_sha256') == sha(run / 'submission.json'),
                'Submission receipt differs from the pre-release frozen bytes.')
    elif not release_path.is_file():
        release_error = None
    expected = list(SCHEDULED_ROLES)
    require(receipt.get('scheduled_job_roles') == expected, 'Scheduled recovery role inventory differs.')
    require(receipt.get('reuse_origin_worker_jobs') == plan['reuse_origin_worker_jobs'],
            'Receipt original worker identity differs.')
    jobs = receipt.get('jobs', {})
    scheduled = {role: jobs[role] for role in expected if role in jobs}
    require(all(str(job).isdigit() for job in scheduled.values()), 'Invalid new scheduler job identity.')
    require(len(set(scheduled.values())) == len(scheduled), 'New recovery roles share a scheduler job ID.')
    for shard, job in plan['reuse_origin_worker_jobs'].items():
        require(jobs.get('worker:' + shard) in (None, job), 'Preserved worker alias was rewritten.')
    for shard in FAILED_WORKER_IDS:
        require(jobs.get('worker:' + shard) in (None, scheduled.get('join')),
                'Assembled logical worker alias differs from actual join job.')
    for chunk, job in SOURCE_CHUNK_JOBS.items():
        require(jobs.get('chunk:' + chunk) in (None, job), 'Completed source chunk alias was rewritten.')
    query_ids = list(dict.fromkeys(list(scheduled.values()) + list(plan['reuse_origin_worker_jobs'].values()) +
                                  list(SOURCE_CHUNK_JOBS.values()) + [plan['source_stage_job_id']]))
    rows, raw = scheduler_main_rows(query_ids)
    queue = subprocess.run(['squeue', '--noheader', '--jobs', ','.join(query_ids),
                            '--format=%i|%T|%N|%R'], text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=45)
    completed = len(scheduled) == 4 and all(
        rows.get(job, {}).get('state') == 'COMPLETED' and rows.get(job, {}).get('exit_code') == '0:0'
        for job in scheduled.values())
    terminal = len(scheduled) == 4 and all(rows.get(job, {}).get('state') in TERMINAL
                                          for job in scheduled.values())
    returned, returned_error = normalize_status(run / 'HPC_STATUS.json')
    bundle_path = run / 'audit/collector/BUNDLE_STATUS.json'
    if not bundle_path.is_file() and (run / 'BUNDLE_STATUS.json').is_file():
        bundle_path = run / 'BUNDLE_STATUS.json'
    bundle, bundle_error = normalize_status(bundle_path)
    collector_job = scheduled.get('collector')
    collector_row = rows.get(collector_job, {})
    collector_terminal = collector_row.get('state') in TERMINAL
    if collector_terminal:
        final_collector = final_collector_completion(
            run, collector_job, collector_row, plan['expected_runtime_ready_sha256'])
    else:
        final_collector = {'verified': False, 'pending': True, 'job_id': collector_job,
                           'node': None, 'scheduler': collector_row, 'runtime_end': None,
                           'errors': []}
        if not (run / 'HPC_STATUS.json').is_file():
            returned_error = None
        if not bundle_path.is_file():
            bundle_error = None

    bundle_execution = (bundle or {}).get('execution', {})
    bundle_identity_verified = (bundle_execution.get('job_id') == collector_job and
        bundle_execution.get('cluster') == 'puma' and
        bool(final_collector['node']) and bundle_execution.get('node') == final_collector['node'])
    packaging_success = (bundle or {}).get('packaging_success') is True
    downloads_ready = packaging_success and final_collector['verified'] and bundle_identity_verified
    archive_path_errors = []
    if downloads_ready:
        download_directory = Path(receipt['download_directory']).resolve()
        for kind in ['review', 'results']:
            record = bundle.get('archives', {}).get(kind, {})
            paths = {}
            for checksum in [False, True]:
                value = download_value(bundle, kind, checksum)
                path = Path(value) if isinstance(value, str) else None
                paths[checksum] = path
                if (path is None or not path.is_absolute() or not path.is_file() or path.is_symlink() or
                        path.parent.resolve() != download_directory):
                    archive_path_errors.append('Final ' + kind + (' checksum' if checksum else ' archive') +
                                               ' path is missing or outside the approved download directory.')
            archive, checksum_path = paths[False], paths[True]
            if archive_path_errors:
                continue
            if (record.get('zip_integrity_verified') is not True or
                    record.get('bytes') != archive.stat().st_size or
                    not re.fullmatch(r'[a-f0-9]{64}', str(record.get('sha256', '')))):
                archive_path_errors.append('Final ' + kind + ' archive metadata lacks exact verified integrity.')
            if checksum_path != Path(str(archive) + '.sha256.txt'):
                archive_path_errors.append('Final ' + kind + ' checksum path differs from archive basename.')
            expected_checksum = str(record.get('sha256', '')) + '  ' + archive.name + '\n'
            if checksum_path.read_text() != expected_checksum:
                archive_path_errors.append('Final ' + kind + ' checksum record differs from packaging proof.')
        downloads_ready = not archive_path_errors
    scientific_scripts_completed = (completed and final_collector['verified'] and downloads_ready and
        (returned or {}).get('scientific_scripts_completed') is True and not (bundle or {}).get('errors'))
    check = {'checked_utc': utc(), 'cluster': 'puma', 'run_directory': str(run),
             'receipt_state_not_scheduler_state': receipt.get('state'),
             'submission_release_record': release_record, 'submission_release_error': release_error,
             'scheduled_job_roles': expected,
             'scheduled_stages': {role: rows.get(job, {'job_id': job, 'state': 'NOT_YET_RECORDED'})
                                  for role, job in scheduled.items()},
             'original_worker_scheduler_rows': {shard: rows.get(job, {'job_id': job, 'state': 'NOT_RECORDED'})
                                                for shard, job in plan['reuse_origin_worker_jobs'].items()},
             'completed_source_chunk_scheduler_rows': {chunk: rows.get(job, {'job_id': job, 'state': 'NOT_RECORDED'})
                                                         for chunk, job in SOURCE_CHUNK_JOBS.items()},
             'source_stage_scheduler_row': rows.get(plan['source_stage_job_id'], {}),
             'completed_source_chunks_reused_without_rerun': True,
             'all_new_jobs_completed_successfully': completed,
             'all_new_jobs_terminal': terminal, 'package_integrity_passed': True,
             'collector_status': returned, 'collector_status_error': returned_error,
             'bundle_status_path': str(bundle_path), 'bundle_status': bundle,
             'bundle_status_error': bundle_error, 'packaging_success': packaging_success,
             'collector_final_completion': final_collector,
             'bundle_execution_identity_verified': bundle_identity_verified,
             'download_paths_ready': downloads_ready, 'archive_path_errors': archive_path_errors,
             'scientific_scripts_completed': scientific_scripts_completed,
             'live_squeue_return_code': queue.returncode,
             'live_squeue_stderr': queue.stderr, 'scientific_review_pending': True,
             'visual_review_pending': True, 'ten_year_forecast_generated': False,
             'predictive_validation_established': False}
    # Live checks must not change files that the collector may be archiving.
    live_directory = package / 'checks' / run.name
    live_directory.mkdir(parents=True, exist_ok=True)
    save(live_directory / 'LIVE_CHECK.json', check)
    (live_directory / 'LIVE_SACCT_MAIN.txt').write_text(raw)
    (live_directory / 'LIVE_SQUEUE.txt').write_text(queue.stdout)
    print('RUN_DIRECTORY=' + str(run))
    print('LIVE_CHECK_RECORD=' + str(live_directory / 'LIVE_CHECK.json'))
    print('RECEIPT_STATE_NOT_SCHEDULER_STATE=' + str(receipt.get('state')))
    print('STAGE_RELEASE_CONFIRMED=' + str((release_record or {}).get('release_confirmed', False)).lower())
    if (release_record or {}).get('error'):
        print('SUBMISSION_RELEASE_ERROR=' + str(release_record['error']))
    if release_error:
        print('SUBMISSION_RELEASE_RECORD_ERROR=' + str(release_error))
    for role in expected:
        job = scheduled.get(role)
        row = rows.get(job, {})
        print(role + ' job=' + str(job or 'NOT_SUBMITTED') + ' state=' +
              str(row.get('state', 'NOT_YET_RECORDED')) + ' exit=' + str(row.get('exit_code', '')))
    print('ALL_NEW_JOBS_COMPLETED_SUCCESSFULLY=' + str(completed).lower())
    print('ALL_NEW_JOBS_TERMINAL=' + str(terminal).lower())
    print('COLLECTOR_STATUS=' + str((returned or {}).get('status', 'PENDING_OR_MISSING')))
    if not collector_terminal:
        print('COLLECTOR_COMPLETION_CHECK=PENDING; collector has not reached a terminal scheduler state.')
    print('REUSED_ORIGINAL_WORKER_COUNT=10')
    print('REUSED_COMPLETED_CHUNK_COUNT=6')
    print('SCIENTIFIC_WORKER_OR_CHUNK_JOBS_RESUBMITTED=false')
    print('COLLECTOR_FINAL_COMPLETION_VERIFIED=' + str(final_collector['verified']).lower())
    print('SCIENTIFIC_SCRIPTS_COMPLETED=' + str(scientific_scripts_completed).lower())
    print('DOWNLOADS_READY=' + str(downloads_ready).lower())
    if bundle:
        print('PACKAGING_SUCCESS=' + str(packaging_success).lower())
        packaging_errors = bundle.get('packaging_errors', [])
        print('PACKAGING_ERROR_COUNT=' + str(len(packaging_errors)))
        for error in packaging_errors[:20]:
            print('PACKAGING_ERROR=' + str(error))
    for error in final_collector['errors'] + archive_path_errors:
        print('FINAL_COLLECTOR_CHECK_ERROR=' + str(error))
    if downloads_ready:
        for kind in ['review', 'results']:
            for checksum in [False, True]:
                value = download_value(bundle, kind, checksum)
                print('DOWNLOAD_' + kind.upper() + ('_CHECKSUM' if checksum else '') + '=' + str(value))
    if returned:
        errors = returned.get('completion_validation_errors', [])
        if errors:
            print('RETURNED_VALIDATION_ERROR_COUNT=' + str(len(errors)))
            for error in errors[:20]:
                print('RETURNED_VALIDATION_ERROR=' + str(error))
    if returned_error:
        print('COLLECTOR_RECEIPT_ERROR=' + returned_error)
    if bundle_error:
        print('BUNDLE_RECEIPT_ERROR=' + bundle_error)
    print('Scientific and visual review remain pending until returned results are inspected.')


if __name__ == '__main__':
    main()
