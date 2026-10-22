"""Standard-library verification of an actual running Puma allocation."""
import os
import re
import socket
import subprocess


def command(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT,
                                   timeout=30).strip()


def puma_cluster():
    config = command(['scontrol', 'show', 'config'])
    match = re.search(r'^\s*ClusterName\s*=\s*(\S+)', config, re.M)
    actual = match.group(1).lower() if match else ''
    if actual != 'puma':
        raise RuntimeError('Puma is required; scontrol recorded ' + repr(actual))
    advertised = os.environ.get('SLURM_CLUSTER_NAME', actual).lower()
    if advertised != actual:
        raise RuntimeError('Slurm cluster environment and scheduler disagree.')
    return actual


def require_hpc():
    try:
        job = os.environ.get('SLURM_JOB_ID', '')
        if not job.isdigit():
            raise RuntimeError('No Slurm allocation. Scientific execution is HPC-only.')
        cluster = puma_cluster()
        record = command(['scontrol', 'show', 'job', '-o', job])
        if not re.search(r'\bJobState=RUNNING\b', record):
            raise RuntimeError('Current job is not recorded as RUNNING.')
        match = re.search(r'\bNodeList=(\S+)', record)
        if not match or match.group(1) in ('(null)', 'None'):
            raise RuntimeError('Scheduler did not identify allocated nodes.')
        allocated = set(command(['scontrol', 'show', 'hostnames', match.group(1)]).splitlines())
        node = socket.gethostname().split('.')[0]
        if node not in allocated:
            raise RuntimeError('Current host is not an allocated compute node.')
        return {'job_id': job, 'cluster': cluster, 'node': node, 'job_record': record}
    except Exception as exc:
        raise SystemExit('HPC_ONLY: ' + str(exc))
