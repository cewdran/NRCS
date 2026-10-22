#!/usr/bin/env python3
"""Show scheduler state and recent logs for the NRCS bacteria recovery."""
import collections
import json
from pathlib import Path
import subprocess
import sys
from RECOVER import DEFAULT_SOURCE, source_receipt_path

receipt_path = Path(__file__).resolve().parent / 'RECOVERY_RECEIPT.json'
if not receipt_path.is_file():
    receipt_path = source_receipt_path(DEFAULT_SOURCE)
    if not receipt_path.is_file():
        sys.exit('No recovery receipt yet. Submit RECOVER.py first.')
receipt = json.loads(receipt_path.read_text())
run = Path(receipt['recovery_run'])
jobs = [receipt['archaea_job']] + list(receipt['jobs'].values())
print('Recovery run:', run, flush=True)
print('Submission receipt status (not scheduler state):', receipt['status'], flush=True)
if receipt.get('submission_error'):
    print('Submission error:', receipt['submission_error'], flush=True)
subprocess.run(['squeue', '-r', '-j', ','.join(jobs)])
subprocess.run(['sacct', '-S', '2026-09-14', '-j', ','.join(jobs),
                '--format=JobID%22,JobName%16,State%22,ExitCode,Elapsed,ReqMem,MaxRSS'])
logs = [Path(receipt['source_run']) / 'logs/dada2_23875047_0.log']
logs += sorted((run / 'logs').glob('*.log'))
for path in logs:
    if path.is_file():
        print('\n==> ' + str(path) + ' <==', flush=True)
        with path.open(errors='replace') as handle:
            print(''.join(collections.deque(handle, maxlen=30)), end='', flush=True)
print('\nCompletion markers:', flush=True)
for domain in ('archaea', 'bacteria'):
    print(domain, (run / 'dada2' / domain / 'COMPLETE').is_file())
for farm in ('OatmanFlatsRanch', 'PageSpringsCellars', 'Whipstone'):
    print(farm, (run / 'farm_analysis' / farm / 'FARM_ANALYSIS_COMPLETE.txt').is_file())
