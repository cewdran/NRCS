#!/usr/bin/env python3
from pathlib import Path
import json,subprocess
base=Path(__file__).resolve().parent
run=Path((base/'LATEST_RUN.txt').read_text().strip());receipt=json.loads((run/'submission.json').read_text());print('RUN_DIRECTORY='+str(run));print('RECEIPT_STATE_NOT_SCHEDULER_STATE='+receipt['state'])
for label,job in receipt['jobs'].items():
 result=subprocess.run(['sacct','-n','-X','-j',job,'--format=JobIDRaw,State,ExitCode','--parsable2'],text=True,capture_output=True,check=False);print(label+' job='+job+' '+result.stdout.strip())
p=run/'FINAL_STATUS.json'
if p.exists():print(p.read_text())
else:print('DOWNLOADS_READY=false\nFINAL_COMPLETION_NOT_YET_VERIFIED=true')
