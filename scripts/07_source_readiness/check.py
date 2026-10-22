from pathlib import Path
import json,subprocess
pkg=Path(__file__).resolve().parent
if not (pkg/'LATEST_RUN.txt').is_file():raise SystemExit('No submitted readiness run recorded.')
run=Path((pkg/'LATEST_RUN.txt').read_text().strip());r=json.loads((run/'submission.json').read_text())
print('Receipt status (not scheduler state): '+r['state']);print('RESULT_DIRECTORY='+str(run),flush=True)
ids=','.join(r['jobs'].values())
if ids:
 subprocess.run(['squeue','-j',ids])
 subprocess.run(['sacct','-j',ids,'--starttime='+r['accounting_since'],'--format=JobID,State,ExitCode,Elapsed,ReqMem,MaxRSS'])
for p in [run/'HPC_STATUS.json',run/'logs/audit.log']:
 if p.is_file():print('\n'+p.name+'\n'+'\n'.join(p.read_text(errors='replace').splitlines()[-45:]))
d=run/'DOWNLOAD.txt'
if d.exists():print('DOWNLOAD_RESULTS='+d.read_text().strip())
