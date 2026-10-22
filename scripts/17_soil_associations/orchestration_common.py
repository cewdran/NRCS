"""Shared orchestration helpers: standard library only; no scientific imports."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import re
import subprocess

TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY',
            'NODE_FAIL', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp.' + str(os.getpid()))
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    os.replace(temporary, path)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def json_file(path):
    return json.loads(Path(path).read_text())


def safe_relative(name):
    rel = Path(name)
    require(not rel.is_absolute() and '..' not in rel.parts and str(rel) != '.',
            'Unsafe relative path: ' + str(name))
    return rel


def verify_manifest(root, name='PACKAGE_SHA256SUMS.txt'):
    root = Path(root)
    entries = []
    for line in (root / name).read_text().splitlines():
        if not line.strip():
            continue
        digest, relative = line.split('  ', 1)
        rel = safe_relative(relative)
        require(re.fullmatch(r'[a-f0-9]{64}', digest) is not None, 'Invalid checksum.')
        path = root / rel
        require(path.is_file() and not path.is_symlink(), 'Missing or symlinked package file: ' + relative)
        require(sha(path) == digest, 'Package checksum mismatch: ' + relative)
        entries.append(rel)
    require(bool(entries), 'Empty package manifest.')
    require(len(entries) == len(set(entries)), 'Duplicate checksum-manifest paths.')
    return entries


def load_shards(root):
    data = json_file(Path(root) / 'SHARDS.json')
    shards = data['shards'] if isinstance(data, dict) else data
    require(isinstance(shards, list) and bool(shards), 'SHARDS.json requires a nonempty list.')
    ids = []
    for shard in shards:
        require(isinstance(shard, dict), 'Every shard must be an object.')
        key = shard.get('id', '')
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', key) is not None,
                'Invalid shard id: ' + repr(key))
        require(key not in ids, 'Duplicate shard id: ' + key)
        ids.append(key)
    return shards


def runtime_config(config):
    package = Path(config.get('runtime_package',
        '/xdisk/dinusha1/dinusha1/NRCS_corrected_rerun_HPC_v3'))
    require(package.is_absolute(), 'runtime_package must be absolute.')
    interpreter = Path(config.get('runtime_python', str(package / 'runtime_env/bin/python')))
    require(interpreter.is_absolute(), 'runtime_python must be absolute.')
    return package, interpreter


def login_runtime_preflight(config):
    package, interpreter = runtime_config(config)
    require((package / 'runtime_env/READY.json').is_file(), 'Existing corrected runtime READY.json is missing.')
    require(os.path.lexists(interpreter), 'Existing runtime interpreter entry is missing.')
    json_file(package / 'runtime_env/READY.json')
    return {'runtime_package': str(package), 'runtime_python': str(interpreter),
            'interpreter_resolves_on_login_node': interpreter.exists(),
            'scientific_compute_runtime_verified': False}


def scheduler_main_rows(job_ids):
    job_ids = [str(value) for value in job_ids]
    require(all(value.isdigit() for value in job_ids), 'Invalid scheduler job ids.')
    proc = subprocess.run(['sacct', '-X', '--parsable2', '--noheader', '--jobs', ','.join(job_ids),
        '--format=JobIDRaw,State,ExitCode,Elapsed,ReqMem,MaxRSS,NodeList'],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
    require(proc.returncode == 0, 'sacct failed: ' + proc.stderr)
    rows = {}
    for line in proc.stdout.splitlines():
        values = line.split('|')
        if len(values) >= 7 and values[0] in job_ids:
            state = values[1].split()[0].rstrip('+')
            rows[values[0]] = {'job_id': values[0], 'state': state, 'exit_code': values[2],
                              'elapsed': values[3], 'requested_memory': values[4],
                              'maximum_rss': values[5], 'node_list': values[6]}
    return rows, proc.stdout


def normalize_status(path):
    try:
        value = json_file(path)
        require(isinstance(value, dict), 'Status is not an object.')
        return value, None
    except Exception as exc:
        return None, str(exc)
