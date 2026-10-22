#!/usr/bin/env python3
"""Submit four isolated Puma join-repair jobs; reuse completed workers and chunks."""
from pathlib import Path
import argparse
import datetime
import fcntl
import os
import shutil
import subprocess
import uuid
from hpc_guard import puma_cluster
from orchestration_common import json_file, load_shards, require, save, sha, utc, verify_manifest
from recovery_common import (FAILED_WORKER_IDS, ORIGINAL_WORKER_JOBS, SOURCE_CHUNK_JOBS,
                             JOIN_REPAIR_PINS, SCHEDULED_ROLES, original_receipt_preflight,
                             partition_preflight, recovery_plan, recovery_runtime_preflight,
                             resource_arguments, resource_plan, role_layout)

REQUIRED_PACKAGE_FILES = {
    'CONFIG.json', 'SHARDS.json', 'SOURCES.json', 'submit.py', 'check.py',
    'START_HERE.sh', 'CHECK.sh', 'RUN_STAGE.sh', 'RUN_JOIN.sh',
    'RUN_COMBINE.sh', 'COLLECT.sh', 'stage_source.py', 'join_chunks.py',
    'combine.py', 'collector.py', 'compute_guard.py', 'record_runtime.py', 'RUN_COMMON.sh',
    'hpc_guard.py', 'environment_check.py', 'recovery_common.py', 'orchestration_common.py',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    parser.add_argument('--new-run', action='store_true',
                        help='Explicitly request an additional isolated recovery run.')
    args = parser.parse_args()
    package = Path(args.package).resolve()
    require(package.is_dir(), 'Package directory is missing.')
    puma_cluster()
    with (package / 'SUBMIT.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another submission is in progress. Run CHECK.sh after it returns.')
        latest = package / 'LATEST_RUN.txt'
        if latest.is_file() and not args.new_run:
            previous = Path(latest.read_text().strip()).resolve()
            require(previous.is_relative_to(package / 'runs'), 'Unsafe LATEST_RUN entry.')
            require((previous / 'submission.json').is_file(),
                    'LATEST_RUN has no receipt. Inspect it before using --new-run.')
            print('EXISTING_RUN=' + str(previous))
            print('No additional jobs submitted. Use CHECK.sh; --new-run requests a new isolated recovery.')
            return
        entries = verify_manifest(package, 'SHA256SUMS.txt')
        require(REQUIRED_PACKAGE_FILES.issubset({str(item) for item in entries}),
                'Package manifest omits a required recovery executable or configuration file.')
        config = json_file(package / 'CONFIG.json')
        require(config.get('account', 'dinusha1') == 'dinusha1', 'Pinned recovery account differs.')
        require(config.get('partition', 'standard') == 'standard', 'Pinned recovery partition differs.')
        plan = recovery_plan(config)
        shards = load_shards(package)
        require({item['id'] for item in shards} == set(ORIGINAL_WORKER_JOBS),
                'Logical worker inventory differs from the original twelve shards.')
        resources = resource_plan(config)
        original = original_receipt_preflight(plan)
        runtime = recovery_runtime_preflight(config, plan)
        partition = partition_preflight('standard', resources)
        download = Path(config.get('download_directory', str(package.parent))).resolve()
        require(download.is_dir(), 'Download directory is missing: ' + str(download))
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        run = package / 'runs' / ('run_' + stamp + '_' + uuid.uuid4().hex[:8])
        run.mkdir(parents=True)
        for relative in entries:
            destination = run / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package / relative, destination)
            destination.chmod(destination.stat().st_mode & ~0o222)
        shutil.copy2(package / 'SHA256SUMS.txt', run / 'PACKAGE_SHA256SUMS.txt')
        (run / 'PACKAGE_SHA256SUMS.txt').chmod(0o444)
        verify_manifest(run)
        scheduled_roles = list(SCHEDULED_ROLES)
        stage_layout = {role: {'audit_directory': role_layout(role)[0],
                               'status_path': role_layout(role)[1]}
                        for role in scheduled_roles}
        for name in ['logs', 'results', 'workers/chunks']:
            (run / name).mkdir(parents=True, exist_ok=True)
        for role in scheduled_roles:
            (run / stage_layout[role]['audit_directory']).mkdir(parents=True, exist_ok=True)
        for item in plan['retry_chunks']:
            (run / 'workers/chunks' / item['id']).mkdir(parents=True, exist_ok=True)
            (run / 'audit/chunks' / item['id']).mkdir(parents=True, exist_ok=True)
        (run / 'audit/history/source_stage').mkdir(parents=True, exist_ok=True)
        for shard in shards:
            (run / 'workers' / shard['id']).mkdir(parents=True, exist_ok=True)
            if shard['id'] not in FAILED_WORKER_IDS:
                (run / 'audit/workers' / shard['id']).mkdir(parents=True, exist_ok=True)
        receipt = {
            'state': 'STAGED_NOT_SUBMITTED', 'created_utc': utc(),
            'package_name': config.get('package', package.name),
            'package_directory': str(package), 'run_directory': str(run),
            'download_directory': str(download), 'cluster': 'puma', 'account': 'dinusha1',
            'partition': 'standard', 'jobs': {}, 'scheduled_job_roles': scheduled_roles,
            'worker_shards': [item['id'] for item in shards],
            'retry_chunks': plan['retry_chunks'], 'retry_chunk_count': 6,
            'new_retry_chunk_count': 0, 'reused_retry_chunk_count': 6,
            'chunk_jobs_resubmitted': False, 'worker_jobs_resubmitted': False,
            'source_chunk_jobs': dict(SOURCE_CHUNK_JOBS),
            'source_stage_job_id': JOIN_REPAIR_PINS['source_stage_job_id'],
            'join_repair': dict(plan['join_repair']),
            'preserved_worker_ids': plan['preserved_worker_ids'],
            'failed_worker_ids': plan['failed_worker_ids'],
            'reuse_origin_worker_jobs': plan['reuse_origin_worker_jobs'],
            'recovery': plan, 'original_receipt_preflight': original,
            'runtime_login_preflight': runtime, 'partition_preflight': partition,
            'resources': resources, 'stage_layout': stage_layout,
            'memory_request_policy': 'CPU-only submission; expected memory is 5 GiB per requested CPU; actual scheduler resources are audited.',
            'package_manifest_sha256': sha(run / 'PACKAGE_SHA256SUMS.txt'),
            'commands': [], 'dependencies': {},
            'scientific_execution_established': False, 'scientific_review_pending': True,
            'visual_review_pending': True, 'numerical_ten_year_forecast': False,
            'predictive_validation_established': False,
            'source_data_staged_on_login': False,
            'worker_job_aliases_are_logical_provenance': True,
        }

        receipt_frozen = False
        release_outcome = {}
        release_path = package / 'checks' / run.name / 'SUBMISSION_RELEASE.json'

        def persist():
            require(not receipt_frozen, 'Refusing to mutate the frozen submission receipt after release.')
            save(run / 'submission.json', receipt)
            temporary = latest.with_name(latest.name + '.' + uuid.uuid4().hex + '.tmp')
            temporary.write_text(str(run) + '\n')
            os.replace(temporary, latest)

        def call(command):
            command_record = {'utc': utc(), 'argv': command}
            receipt['commands'].append(command_record)
            persist()
            environment = dict(os.environ)
            for key in ['BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'SBATCH_GET_USER_ENV',
                        'PYTHONPATH', 'PYTHONHOME', 'PYTHONOPTIMIZE']:
                environment.pop(key, None)
            proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=environment, timeout=90)
            command_record.update({'return_code': proc.returncode, 'stdout': proc.stdout,
                                   'stderr': proc.stderr})
            persist()
            require(proc.returncode == 0, 'Command failed: ' + repr(command) + '\n' + proc.stderr)
            return proc.stdout.strip()

        def submit(role, script, script_args, resource_role, dependency=None, hold=False):
            log = str(run / 'logs' / (role.replace(':', '_') + '.log'))
            command = ['sbatch', '--parsable', '--account=dinusha1', '--partition=standard',
                       '--nodes=1', '--ntasks=1', '--export=ALL', '--chdir=' + str(run),
                       '--job-name=nrcs_recover_' + role.replace(':', '_')[:70]]
            if hold:
                command.append('--hold')
            if dependency:
                command.extend(['--dependency=' + dependency, '--kill-on-invalid-dep=yes'])
                receipt['dependencies'][role] = dependency
            command.extend(resource_arguments(resources[resource_role]))
            command.extend(['--output=' + log, '--error=' + log, str(run / script), str(run)])
            command.extend(script_args)
            reply = call(command)
            job = reply.split(';')[0]
            require(job.isdigit(), 'Unexpected sbatch reply: ' + reply)
            require(job not in [receipt['jobs'][key] for key in scheduled_roles
                               if key in receipt['jobs']], 'Duplicate newly submitted scheduler job ID.')
            historical_ids = set(ORIGINAL_WORKER_JOBS.values()) | set(SOURCE_CHUNK_JOBS.values()) | {
                str(value) for key, value in JOIN_REPAIR_PINS.items() if key.endswith('_job_id')}
            require(job not in historical_ids, 'New job collides with a historical scheduler identity.')
            receipt['jobs'][role] = job
            persist()
            return job

        persist()
        try:
            stage = submit('stage', 'RUN_STAGE.sh', [runtime['runtime_python']], 'stage', hold=True)
            join = submit('join', 'RUN_JOIN.sh', [runtime['runtime_python']], 'join',
                          dependency='afterok:' + stage)
            combine = submit('combine', 'RUN_COMBINE.sh', [runtime['runtime_python']], 'combine',
                             dependency='afterok:' + join + ':' + stage)
            submit('collector', 'COLLECT.sh', [runtime['runtime_python']], 'collector',
                   dependency='afterany:' + ':'.join([stage, join, combine]))
            for chunk, job in SOURCE_CHUNK_JOBS.items():
                receipt['jobs']['chunk:' + chunk] = job
            for shard, job in plan['reuse_origin_worker_jobs'].items():
                receipt['jobs']['worker:' + shard] = job
            for shard in plan['failed_worker_ids']:
                receipt['jobs']['worker:' + shard] = join
            receipt['logical_worker_provenance'] = {
                shard: ({'kind': 'reused_original_worker', 'original_job_id': job,
                         'original_run': plan['old_run']} if shard not in FAILED_WORKER_IDS else
                        {'kind': 'assembled_from_completed_source_chunks', 'assembly_job_id': join,
                         'source_run': plan['join_repair']['source_run'],
                         'chunk_jobs_reused_without_rerun': True,
                         'chunk_ids': [item['id'] for item in plan['retry_chunks']
                                       if item['shard_id'] == shard],
                         'chunk_job_ids': [receipt['jobs']['chunk:' + item['id']]
                                           for item in plan['retry_chunks'] if item['shard_id'] == shard]})
                for shard, job in ORIGINAL_WORKER_JOBS.items()
            }
            require(len(scheduled_roles) == 4 and all(role in receipt['jobs'] for role in scheduled_roles),
                    'Join repair must have exactly four newly scheduled jobs.')
            verify_manifest(run)
            release_command = ['scontrol', 'release', stage]
            receipt['state'] = 'SUBMITTED_RELEASE_REQUESTED_NOT_YET_COMPLETE'
            receipt['release_requested_utc'] = utc()
            receipt['receipt_frozen_before_stage_release'] = True
            receipt['release_outcome_record'] = str(release_path)
            receipt['commands'].append({'utc': utc(), 'argv': release_command,
                'outcome_record': str(release_path), 'issued_after_receipt_freeze': True})
            persist()
            # Stage may start as soon as scontrol accepts the release. Do not
            # write this immutable receipt, or its LATEST entry, after this point.
            receipt_frozen = True
            release_outcome = {'run_directory': str(run), 'cluster': 'puma',
                'stage_job_id': stage, 'submission_sha256': sha(run / 'submission.json'),
                'release_command': release_command, 'issued_utc': utc(),
                'release_confirmed': False, 'newly_scheduled_job_roles': scheduled_roles}
            save(release_path, release_outcome)
            release_environment = dict(os.environ)
            for key in ['BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'SBATCH_GET_USER_ENV',
                        'PYTHONPATH', 'PYTHONHOME', 'PYTHONOPTIMIZE']:
                release_environment.pop(key, None)
            release = subprocess.run(release_command, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=release_environment, timeout=90)
            release_outcome.update({'finished_utc': utc(), 'return_code': release.returncode,
                'stdout': release.stdout, 'stderr': release.stderr,
                'release_confirmed': release.returncode == 0})
            save(release_path, release_outcome)
            require(release.returncode == 0, 'Held stage release failed: ' + release.stderr)
        except Exception as exc:
            if receipt_frozen:
                # Even an unconfirmed release might have started the stage.
                # Keep frozen bytes and record failure/cancellation externally.
                release_outcome.update({'state': 'RELEASE_FAILED_OR_UNCONFIRMED',
                    'error': str(exc), 'failed_utc': utc(), 'cancellations': []})
                for role in scheduled_roles:
                    if role not in receipt['jobs']:
                        continue
                    command = ['scancel', receipt['jobs'][role]]
                    record = {'role': role, 'argv': command}
                    try:
                        cancellation = subprocess.run(command, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=90)
                        record.update({'return_code': cancellation.returncode,
                            'stdout': cancellation.stdout, 'stderr': cancellation.stderr})
                    except Exception as cancellation:
                        record['error'] = str(cancellation)
                    release_outcome['cancellations'].append(record)
                save(release_path, release_outcome)
            else:
                receipt['state'] = 'SUBMISSION_FAILED'
                receipt['error'] = str(exc)
                persist()
                for role in scheduled_roles:
                    if role in receipt['jobs']:
                        try:
                            call(['scancel', receipt['jobs'][role]])
                        except Exception as cancellation:
                            receipt.setdefault('cancellation_errors', []).append(str(cancellation))
                persist()
            raise SystemExit(str(exc))
        for role in scheduled_roles:
            print(role.upper().replace(':', '_') + '_JOB_ID=' + receipt['jobs'][role])
        print('NEWLY_SUBMITTED_JOB_COUNT=4')
        print('STAGE_RELEASE_CONFIRMED=true')
        print('SUBMISSION_RELEASE_RECORD=' + str(release_path))
        print('COMPUTE_RUNTIME_VERIFICATION=PENDING')
        print('REUSED_COMPLETED_CHUNK_COUNT=6')
        print('SCIENTIFIC_WORKER_OR_CHUNK_JOBS_RESUBMITTED=false')
        print('REUSED_ORIGINAL_WORKER_COUNT=10')
        print('RESULT_DIRECTORY=' + str(run))
        print('CHECK_COMMAND=bash ' + str(package / 'CHECK.sh'))
        print('Submission does not establish completion or scientific acceptance.')


if __name__ == '__main__':
    main()
