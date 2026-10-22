"""Standard-library I/O and integrity helpers; no scientific imports."""
from pathlib import Path
import csv
import datetime
import gzip
import hashlib
import json
import os
import re
import subprocess

TERMINAL = {'COMPLETED','FAILED','CANCELLED','TIMEOUT','OUT_OF_MEMORY','NODE_FAIL',
            'PREEMPTED','BOOT_FAIL','DEADLINE','REVOKED'}

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def json_file(path):
    return json.loads(Path(path).read_text())

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp.' + str(os.getpid()))
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    os.replace(temporary, path)

def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()

def readcsv(path):
    path = Path(path)
    with (gzip.open(path, 'rt', newline='') if path.suffix == '.gz' else path.open(newline='')) as handle:
        return list(csv.DictReader(handle))

def table(path, rows, fieldnames=None):
    path = Path(path)
    rows = list(rows)
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    require(bool(fieldnames), 'CSV output needs field names: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp.' + str(os.getpid()))
    with (gzip.open(temporary, 'wt', newline='') if path.suffix == '.gz' else temporary.open('w', newline='')) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='raise')
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)

def safe_relative(name):
    path = Path(name)
    require(not path.is_absolute() and '..' not in path.parts and str(path) != '.', 'Unsafe relative path: ' + str(name))
    return path

def verify_manifest(root, name='PACKAGE_SHA256SUMS.txt'):
    root = Path(root)
    entries = []
    for line in (root / name).read_text().splitlines():
        if not line.strip():
            continue
        digest, relative = line.split('  ', 1)
        relative = safe_relative(relative)
        path = root / relative
        require(re.fullmatch('[a-f0-9]{64}', digest) is not None, 'Malformed SHA256 manifest')
        require(path.is_file() and not path.is_symlink(), 'Missing or symlinked manifested file: ' + str(path))
        require(sha(path) == digest, 'Checksum mismatch: ' + str(path))
        entries.append(relative)
    require(entries and len(entries) == len(set(entries)), 'Empty or duplicate manifest: ' + name)
    return entries

def write_manifest(root, paths, name='OUTPUT_SHA256SUMS.txt'):
    root = Path(root)
    paths = sorted({safe_relative(str(p)) for p in paths}, key=str)
    require(paths, 'No files to manifest')
    for p in paths:
        require((root/p).is_file() and not (root/p).is_symlink(), 'Unsafe output: ' + str(p))
    target = root/name
    temporary = target.with_name(target.name + '.tmp.' + str(os.getpid()))
    temporary.write_text(''.join(sha(root/p) + '  ' + str(p) + '\n' for p in paths))
    os.replace(temporary, target)
    return sha(target)

def scheduler_rows(jobs):
    jobs = list(dict.fromkeys(str(j) for j in jobs))
    require(jobs and all(j.isdigit() for j in jobs), 'Invalid scheduler job inventory')
    process = subprocess.run(['sacct','-X','--parsable2','--noheader','--jobs',','.join(jobs),
        '--format=JobIDRaw,State,ExitCode,Elapsed,ReqMem,MaxRSS,NodeList'],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
    require(process.returncode == 0, 'sacct failed: ' + process.stderr)
    result = {}
    for line in process.stdout.splitlines():
        values = line.split('|')
        if len(values) >= 7 and values[0] in jobs:
            result[values[0]] = dict(zip(['job_id','state','exit_code','elapsed','requested_memory','max_rss','node_list'], values[:7]))
            result[values[0]]['state'] = values[1].split()[0].rstrip('+')
    return result, process.stdout

def load_status(path):
    try:
        value = json_file(path)
        require(isinstance(value, dict), 'Status must be an object')
        return value, None
    except Exception as exc:
        return {}, str(exc)

def workers(config):
    values = config['workers']
    ids = [x['id'] if isinstance(x,dict) else x for x in values]
    require(ids and all(isinstance(x,str) and re.fullmatch('[a-z][a-z0-9_]{0,79}',x) for x in ids), 'Unsafe worker IDs')
    require(len(set(ids)) == len(ids), 'Duplicate worker IDs')
    return ids

def audit_directory(run, role):
    return Path(run)/'audit'/('workers/'+role.split(':',1)[1] if role.startswith('worker:') else role)

def verify_runtime(run, role, job, row):
    """Fail closed on final scheduler and START/END/ENV job/node disagreement."""
    directory = audit_directory(run,role)
    require(row.get('job_id') == str(job) and row.get('state') == 'COMPLETED' and row.get('exit_code') == '0:0',
            role + ': scheduler did not establish COMPLETED 0:0')
    start=json_file(directory/'EXECUTION_START.json')
    end=json_file(directory/'EXECUTION_END.json')
    environment=json_file(directory/'ENVIRONMENT_STATUS.json')
    node=end.get('execution',{}).get('node')
    require(node and row.get('node_list') == node, role + ': accounting/runtime node differs')
    for label,value in [('START',start),('END',end),('ENV',environment)]:
        identity=value.get('execution',{})
        require(identity.get('job_id') == str(job) and identity.get('cluster') == 'puma' and identity.get('node') == node,
                role + ': ' + label + ' execution identity differs')
    require(start.get('event') == 'start' and end.get('event') == 'end' and end.get('pipeline_exit_code') == 0,
            role + ': runtime start/end does not establish zero exit')
    require(environment.get('status') == 'COMPUTE_RUNTIME_VERIFIED' and environment.get('package_integrity_passed') is True
            and environment.get('ready_sha256') == '6acaa39f5023c9f61a7cbe69d6c5687f634efb2b56e3907158003a41e1f6bf30',
            role + ': corrected runtime not verified')
    return {'verified':True,'job_id':str(job),'node':node,'scheduler':row,
            'start':start,'end':end,'environment':environment}
