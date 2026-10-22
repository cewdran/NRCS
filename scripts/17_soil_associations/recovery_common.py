"""Pinned recovery plan and scheduler helpers; standard library only."""
from pathlib import Path
import re
import subprocess
from orchestration_common import json_file, login_runtime_preflight, require, sha

OLD_RUN = '/xdisk/dinusha1/dinusha1/NRCS_correlations_parallel_HPC_v1/runs/run_20261010T005932Z_492b3fb4'
ORIGINAL_SUBMISSION_SHA256 = 'f803f3267ca912bcb9f822acf306aa5e333bd622ea642301d8241d194f57b86d'
ORIGINAL_PACKAGE_MANIFEST_SHA256 = 'c7e859e36e09b097a303558a72781f3f55363b2df34f6be4d244994e4d45fd93'
EXPECTED_RUNTIME_READY_SHA256 = '6acaa39f5023c9f61a7cbe69d6c5687f634efb2b56e3907158003a41e1f6bf30'
ORIGINAL_WORKER_JOBS = {
    'oatman_bacteria_taxa': '24186799',
    'oatman_bacteria_functions': '24186800',
    'oatman_archaea_taxa': '24186801',
    'oatman_archaea_functions': '24186802',
    'pagesprings_bacteria_taxa': '24186803',
    'pagesprings_bacteria_functions': '24186804',
    'pagesprings_archaea_taxa': '24186805',
    'pagesprings_archaea_functions': '24186806',
    'whipstone_bacteria_taxa': '24186807',
    'whipstone_bacteria_functions': '24186808',
    'whipstone_archaea_taxa': '24186809',
    'whipstone_archaea_functions': '24186810',
}
FAILED_WORKER_IDS = ['pagesprings_bacteria_functions', 'whipstone_bacteria_functions']
FEATURE_CLASSES = ['EC', 'KO', 'METACYC']
REUSE_ORIGIN_WORKER_JOBS = {key: value for key, value in ORIGINAL_WORKER_JOBS.items()
                          if key not in FAILED_WORKER_IDS}
RETRY_CHUNKS = [{'id': shard + '_' + feature.lower(), 'shard_id': shard,
                 'feature_class': feature}
                for shard in FAILED_WORKER_IDS for feature in FEATURE_CLASSES]
RESOURCE_DEFAULTS = {
    'stage': {'cpus': 4, 'memory': '20G', 'time': '04:00:00'},
    'join': {'cpus': 4, 'memory': '20G', 'time': '48:00:00'},
    'combine': {'cpus': 4, 'memory': '20G', 'time': '24:00:00'},
    'collector': {'cpus': 4, 'memory': '20G', 'time': '08:00:00'},
}

SOURCE_RUN = '/xdisk/dinusha1/dinusha1/NRCS_correlations_recovery_HPC_v1/runs/run_20261012T190218Z_ed60131a'
SOURCE_MANIFEST_SHA256 = '00f27df3d5f3dc0cb24382f3f68af5689391b2e5c726beaeddecb91fa212d343'
SOURCE_SUBMISSION_SHA256 = 'd1243f4e50d0a0370fe8c5a7d5ca1f4c91dd7f60e8ba066ca8a34d308ef46de2'
SOURCE_STATUS_SHA256 = 'f7aab8834d96d13d3bc405ff76cfef4b79f43fc271d33424ad0eda715ba746b2'
SOURCE_CHUNK_JOBS = {
    'pagesprings_bacteria_functions_ec': '24223734',
    'pagesprings_bacteria_functions_ko': '24223735',
    'pagesprings_bacteria_functions_metacyc': '24223736',
    'whipstone_bacteria_functions_ec': '24223737',
    'whipstone_bacteria_functions_ko': '24223738',
    'whipstone_bacteria_functions_metacyc': '24223739',
}
SCHEDULED_ROLES = ['stage', 'join', 'combine', 'collector']
JOIN_REPAIR_PINS = {
    'source_run': SOURCE_RUN,
    'source_manifest_sha256': SOURCE_MANIFEST_SHA256,
    'source_submission_sha256': SOURCE_SUBMISSION_SHA256,
    'source_status_sha256': SOURCE_STATUS_SHA256,
    'source_stage_job_id': '24223733',
    'source_chunk_jobs': SOURCE_CHUNK_JOBS,
    'source_join_job_id': '24223740',
    'source_combine_job_id': '24223741',
    'source_collector_job_id': '24223742',
}


def recovery_plan(config):
    recovery = config.get('recovery', {})
    require(isinstance(recovery, dict), 'CONFIG.recovery must be an object.')
    pins = {'old_run': OLD_RUN,
            'original_submission_sha256': ORIGINAL_SUBMISSION_SHA256,
            'original_package_manifest_sha256': ORIGINAL_PACKAGE_MANIFEST_SHA256,
            'expected_runtime_ready_sha256': EXPECTED_RUNTIME_READY_SHA256,
            'reuse_origin_worker_jobs': REUSE_ORIGIN_WORKER_JOBS,
            'failed_worker_ids': FAILED_WORKER_IDS,
            'preserved_worker_ids': list(REUSE_ORIGIN_WORKER_JOBS),
            'retry_chunks': RETRY_CHUNKS}
    for key, expected in pins.items():
        observed = recovery.get(key, expected)
        if key == 'preserved_worker_ids':
            require(isinstance(observed, list) and sorted(observed) == sorted(expected),
                    'CONFIG.recovery.' + key + ' differs from the pinned recovery.')
        else:
            require(observed == expected,
                    'CONFIG.recovery.' + key + ' differs from the pinned recovery.')
    repair = config.get('join_repair')
    if repair is None:
        require(config.get('package') == 'NRCS_correlations_recovery_HPC_v1',
                'Only the pinned historical recovery may omit CONFIG.join_repair.')
        return pins
    require(isinstance(repair, dict), 'CONFIG.join_repair must be an object.')
    for key, expected in JOIN_REPAIR_PINS.items():
        require(repair.get(key) == expected,
                'CONFIG.join_repair.' + key + ' differs from the returned source run.')
    require(config.get('package') == 'NRCS_correlations_join_repair_HPC_v1',
            'Targeted join repair package identity differs.')
    pins['join_repair'] = dict(repair)
    pins['source_run'] = SOURCE_RUN
    pins['source_chunk_jobs'] = dict(SOURCE_CHUNK_JOBS)
    pins['source_stage_job_id'] = JOIN_REPAIR_PINS['source_stage_job_id']
    return pins


def repair_plan(config):
    plan = recovery_plan(config)
    require(isinstance(plan.get('join_repair'), dict),
            'This operation requires the targeted CONFIG.join_repair pins.')
    return plan['join_repair']


def recovery_runtime_preflight(config, plan):
    observed = login_runtime_preflight(config)
    ready = Path(observed['runtime_package']) / 'runtime_env/READY.json'
    require(sha(ready) == plan['expected_runtime_ready_sha256'],
            'Existing corrected runtime READY bytes changed.')
    observed['ready_sha256'] = plan['expected_runtime_ready_sha256']
    observed['runtime_installation_performed'] = False
    return observed


def original_receipt_preflight(plan):
    """Read only original orchestration bytes on login; no data are staged here."""
    old = Path(plan['old_run'])
    require(old.is_dir() and not old.is_symlink(), 'Pinned original run is missing or symlinked.')
    for name, digest in [('submission.json', plan['original_submission_sha256']),
                         ('PACKAGE_SHA256SUMS.txt', plan['original_package_manifest_sha256'])]:
        path = old / name
        require(path.is_file() and not path.is_symlink(), 'Missing original receipt: ' + str(path))
        require(sha(path) == digest, 'Frozen original receipt changed: ' + str(path))
    original = json_file(old / 'submission.json')
    require(original.get('run_directory') == str(old), 'Original receipt run directory mismatch.')
    for shard, job in ORIGINAL_WORKER_JOBS.items():
        require(original.get('jobs', {}).get('worker:' + shard) == job,
                'Original worker scheduler identity changed: ' + shard)
    repair = plan['join_repair']
    source = Path(repair['source_run'])
    require(source.is_dir() and not source.is_symlink(), 'Completed recovery source is missing or symlinked.')
    for name, key in [('PACKAGE_SHA256SUMS.txt', 'source_manifest_sha256'),
                      ('submission.json', 'source_submission_sha256'),
                      ('HPC_STATUS.json', 'source_status_sha256')]:
        path = source / name
        require(path.is_file() and not path.is_symlink(), 'Missing recovery source receipt: ' + str(path))
        require(sha(path) == repair[key], 'Frozen recovery source receipt changed: ' + str(path))
    source_receipt = json_file(source / 'submission.json')
    require(source_receipt.get('run_directory') == str(source), 'Recovery source run identity differs.')
    source_jobs = source_receipt.get('jobs', {})
    for role, key in [('stage', 'source_stage_job_id'), ('join', 'source_join_job_id'),
                      ('combine', 'source_combine_job_id'), ('collector', 'source_collector_job_id')]:
        require(source_jobs.get(role) == repair[key], 'Recovery source scheduler identity differs: ' + role)
    for chunk, job in SOURCE_CHUNK_JOBS.items():
        require(source_jobs.get('chunk:' + chunk) == job, 'Original chunk scheduler identity changed: ' + chunk)
    for shard, job in REUSE_ORIGIN_WORKER_JOBS.items():
        require(source_jobs.get('worker:' + shard) == job, 'Preserved worker scheduler identity changed: ' + shard)
    for shard in FAILED_WORKER_IDS:
        require(source_jobs.get('worker:' + shard) == repair['source_join_job_id'],
                'Source join logical worker identity changed: ' + shard)
    return {'old_run': str(old), 'original_receipts_verified': True,
            'recovery_source_receipts_verified': True, 'source_run': str(source),
            'source_submission_sha256': repair['source_submission_sha256'],
            'source_manifest_sha256': repair['source_manifest_sha256'],
            'source_status_sha256': repair['source_status_sha256'],
            'original_submission_sha256': plan['original_submission_sha256'],
            'original_package_manifest_sha256': plan['original_package_manifest_sha256'],
            'source_data_staged_on_login': False}


def time_seconds(value):
    require(isinstance(value, str), 'Slurm time must be text.')
    match = re.fullmatch(r'(?:(\d+)-)?(\d+):(\d{2}):(\d{2})', value)
    require(match is not None, 'Unsupported Slurm time: ' + repr(value))
    days, hours, minutes, seconds = match.groups()
    require(int(minutes) < 60 and int(seconds) < 60, 'Invalid Slurm time: ' + value)
    return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def resource_plan(config):
    overrides = config.get('resources', {})
    require(isinstance(overrides, dict), 'CONFIG.resources must be an object.')
    result = {}
    for role, defaults in RESOURCE_DEFAULTS.items():
        values = dict(defaults)
        values.update(overrides.get(role, {}))
        require(isinstance(values['cpus'], int) and not isinstance(values['cpus'], bool)
                and values['cpus'] == defaults['cpus'], 'Pinned CPU count differs for ' + role)
        require(values['memory'] == defaults['memory'], 'Pinned memory request differs for ' + role)
        require(0 < time_seconds(values['time']) <= time_seconds(defaults['time']),
                'Time request exceeds authorized recovery limit for ' + role)
        result[role] = values
    return result


def resource_arguments(values):
    # Puma allocates 5 GiB per requested CPU; do not combine --mem and CPU flags.
    # CONFIG memory is the planned allocation derived from that documented rule.
    return ['--cpus-per-task=' + str(values['cpus']), '--time=' + values['time']]


def partition_preflight(partition, resources):
    require(partition == 'standard', 'Pinned recovery requires standard partition.')
    proc = subprocess.run(['scontrol', 'show', 'partition', partition, '-o'],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45)
    require(proc.returncode == 0, 'Partition preflight failed: ' + proc.stderr)
    fields = dict(re.findall(r'(\w+)=([^\s]+)', proc.stdout))
    require(fields.get('PartitionName') == partition, 'Partition identity mismatch.')
    require(fields.get('State') == 'UP', 'Standard partition is not UP.')
    max_time = fields.get('MaxTime', '')
    require(bool(max_time), 'Partition MaxTime was not reported.')
    if max_time.upper() not in {'UNLIMITED', 'INFINITE'}:
        limit = time_seconds(max_time)
        for role, request in resources.items():
            require(time_seconds(request['time']) <= limit,
                    role + ' exceeds observed standard partition MaxTime=' + max_time)
    max_cpus = fields.get('MaxCPUsPerNode', 'UNLIMITED')
    if max_cpus.isdigit():
        require(all(values['cpus'] <= int(max_cpus) for values in resources.values()),
                'Recovery CPU request exceeds standard partition maximum.')
    max_memory = fields.get('MaxMemPerNode', 'UNLIMITED')
    if max_memory.isdigit() and int(max_memory) > 0:
        require(all(int(values['memory'][:-1]) * 1024 <= int(max_memory)
                    for values in resources.values()),
                'Recovery memory request exceeds standard partition maximum.')
    return {'partition': partition, 'scheduler_record': proc.stdout.strip(),
            'max_time': max_time, 'requests_within_observed_limits': True,
            'resource_requests': resources,
            'memory_policy': 'CPU-only submission; configured memory is expected at 5 GiB per CPU.',
            'memory_policy_documentation': 'https://hpcdocs.hpc.arizona.edu/running_jobs/batch_jobs/batch_directives/'}


def role_layout(role):
    if role == 'stage':
        return 'audit/stage', 'audit/stage/STAGE_STATUS.json'
    if role.startswith('chunk:'):
        chunk = role.split(':', 1)[1]
        require(any(item['id'] == chunk for item in RETRY_CHUNKS), 'Unknown retry chunk.')
        return 'audit/chunks/' + chunk, 'workers/chunks/' + chunk + '/WORKER_STATUS.json'
    if role == 'join':
        return 'audit/join', 'audit/join/JOIN_STATUS.json'
    if role == 'combine':
        return 'audit/combine', 'results/COMBINE_STATUS.json'
    if role == 'collector':
        return 'audit/collector', 'HPC_STATUS.json'
    raise RuntimeError('Unknown recovery role: ' + role)
