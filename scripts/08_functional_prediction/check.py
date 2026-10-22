#!/usr/bin/env python3
from pathlib import Path
import sys,json,subprocess
package=Path(sys.argv[1]);run=Path(sys.argv[2])if len(sys.argv)>2 and sys.argv[2]else Path((package/'LATEST_RUN.txt').read_text().strip())
r=json.loads((run/'submission.json').read_text());print('Receipt status (not scheduler state): '+r['state']);print('RESULT_DIRECTORY='+str(run))
jobs=','.join(r['jobs'].values());date=r['accounting_since']
for args in [['squeue','-j',jobs],['sacct','-S',date,'-j',jobs,'--format=JobID,State,ExitCode,Elapsed,ReqMem,MaxRSS']]:
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True);print(p.stdout);print(p.stderr)
for name in ['HPC_STATUS.json','results/PREDICTION_STATUS.json','logs/analysis.log','logs/bacteria_picrust2.log','logs/archaea_picrust2.log','logs/collector.log']:
 p=run/name
 if p.exists():
  lines=p.read_text(errors='replace').splitlines()
  if name=='logs/analysis.log':
   for i,line in enumerate(lines):
    if line.startswith('Traceback (most recent call last):'):
     print('\nANALYSIS FAILURE DETAIL\n'+'\n'.join(lines[i:i+18]))
  limit=12 if name.endswith('_picrust2.log') else 35
  print('\n'+name+'\n'+'\n'.join(lines[-limit:]))
download=run.parent/(run.name+'_DOWNLOAD.txt')
if download.exists():print('DOWNLOAD_RESULTS='+download.read_text().strip())
