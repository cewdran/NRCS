#!/usr/bin/env python3
"""Show receipts, live scheduler state, recorded Whipstone pH diagnostic stages and preserved failure evidence."""
from pathlib import Path
import json
import subprocess
import sys

package = Path(sys.argv[1])
if len(sys.argv) > 2 and sys.argv[2]:
    run = Path(sys.argv[2])
else:
    latest = package / 'LATEST_RUN.txt'
    if not latest.is_file():
        raise SystemExit('No run has been staged or submitted yet.')
    run = Path(latest.read_text().strip())
receipt = json.loads((run / 'submission.json').read_text())
print('Receipt status (not scheduler state): ' + receipt['state'])
print('RESULT_DIRECTORY=' + str(run))
jobs = ','.join(receipt['jobs'].values())
if jobs:
    for args in [
        ['squeue', '-j', jobs],
        ['sacct', '-S', receipt['accounting_since'], '-j', jobs,
         '--format=JobID,State,ExitCode,Elapsed,ReqMem,MaxRSS'],
    ]:
        process = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(process.stdout)
        print(process.stderr)
else:
    print('No submitted job IDs recorded.')
for name in ['HPC_STATUS.json','audit/SOURCE_STAGE_STATUS.json','audit/ph_self_checks.json',
             'audit/PH_ENGINE_STATUS.json','results/REPORT_STATUS.json','audit/software/environment.json',
             'audit/run_end.json','audit/ARCHIVE_FAILURE.json','logs/analysis.log','logs/collector.log']:
    path = run / name
    if path.is_file():
        lines = path.read_text(errors='replace').splitlines()
        if name == 'logs/analysis.log':
            for index, line in enumerate(lines):
                if line.startswith('Traceback (most recent call last):'):
                    print('\nWHIPSTONE PH FAILURE DETAIL\n' + '\n'.join(lines[index:]))
        print('\n' + name + '\n' + '\n'.join(lines[-55:]))
download = run.parent / (run.name + '_DOWNLOAD.txt')
if download.is_file():
    archive = Path(download.read_text().strip())
    print('DOWNLOAD_RESULTS=' + str(archive))
    collection = archive.with_suffix('.zip.collection.json')
    if collection.is_file():
        print('\nPOST_ARCHIVE_COLLECTION_RECEIPT\n' + collection.read_text())
