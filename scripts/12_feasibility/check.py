#!/usr/bin/env python3
"""Show receipts, current scheduler state, audit status and failure details."""
from pathlib import Path
import sys, json, subprocess

package = Path(sys.argv[1])
run = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else Path((package / 'LATEST_RUN.txt').read_text().strip())
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
        process = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        print(process.stdout)
        print(process.stderr)
else:
    print('No submitted job IDs recorded.')
for name in ['HPC_STATUS.json', 'results/FEASIBILITY_STATUS.json',
             'audit/SOURCE_VERIFICATION.json', 'audit/run_end.json',
             'logs/analysis.log', 'logs/collector.log']:
    path = run / name
    if path.exists():
        lines = path.read_text(errors='replace').splitlines()
        if name == 'logs/analysis.log':
            for index, line in enumerate(lines):
                if line.startswith('Traceback (most recent call last):'):
                    print('\nAUDIT FAILURE DETAIL\n' + '\n'.join(lines[index:index + 18]))
        print('\n' + name + '\n' + '\n'.join(lines[-45:]))
download = run.parent / (run.name + '_DOWNLOAD.txt')
if download.exists():
    print('DOWNLOAD_RESULTS=' + download.read_text().strip())
