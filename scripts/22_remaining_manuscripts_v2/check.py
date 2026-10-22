#!/usr/bin/env python3
"""Read-only live scheduler and archive-integrity check; no scientific imports."""
from pathlib import Path
import json,subprocess,hashlib
base=Path(__file__).resolve().parent
run=Path((base/'LATEST_RUN.txt').read_text().strip());receipt=json.loads((run/'submission.json').read_text())
print('RUN_DIRECTORY='+str(run));terminal=True
for label,job in receipt['jobs'].items():
 r=subprocess.run(['sacct','-n','-X','-j',job,'--format=JobIDRaw,State,ExitCode','--parsable2'],text=True,capture_output=True,timeout=30)
 rows=[x.split('|') for x in r.stdout.splitlines() if x.split('|')[0].strip()==job]
 valid=r.returncode==0 and len(rows)==1
 state=rows[0][1].strip() if valid else 'ACCOUNTING_UNAVAILABLE';code=rows[0][2].strip() if valid else ''
 print(label+' job='+job+' state='+state+' exit='+code)
 terminal=terminal and state=='COMPLETED' and code=='0:0'
p=run/'FINAL_STATUS.json'
if not p.is_file():print('RESULTS_READY=false; final status not yet available');raise SystemExit(0)
s=json.loads(p.read_text());print('PIPELINE_STATE='+s['state'])
a=Path(s.get('archive_path',''));check=Path(s.get('checksum_path',''));integrity=False
if a.is_file() and check.is_file():
 h=hashlib.sha256()
 with a.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 integrity=h.hexdigest()==check.read_text().split()[0]
ready=terminal and s['state']=='HPC_OUTPUTS_CREATED_REVIEW_PENDING' and integrity
print('RESULTS_READY='+str(ready).lower());print('SCIENTIFIC_AND_VISUAL_REVIEW_PENDING=true')
if integrity:
 print(('RETURN_RESULTS=' if ready else 'RETURN_FOR_DIAGNOSIS=')+str(a));print('RETURN_CHECKSUM='+str(check))
else:print('Archive absent or checksum incomplete; inspect logs if jobs have ended.')
