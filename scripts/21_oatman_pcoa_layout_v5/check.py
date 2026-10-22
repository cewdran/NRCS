"""Report absence of a submission separately from a scheduler job state."""
from pathlib import Path
import json,subprocess

def main():
 base=Path(__file__).resolve().parent;latest=base/'LATEST_RUN.txt'
 if not latest.is_file():
  print('SUBMISSION_STATE=NOT_SUBMITTED');print('DOWNLOADS_READY=false')
  preflight=base/'PREFLIGHT_STATUS.json'
  print(preflight.read_text() if preflight.is_file() else 'NO_SUBMISSION_RECEIPT=true')
  return
 run=Path(latest.read_text().strip());receipt=run/'submission.json'
 if not receipt.is_file():
  print('SUBMISSION_STATE=RECEIPT_MISSING');print('RUN_DIRECTORY='+str(run));print('DOWNLOADS_READY=false');return
 r=json.loads(receipt.read_text());print('RUN_DIRECTORY='+str(run))
 print('RECEIPT_STATE_NOT_SCHEDULER_STATE='+r['state'])
 s=subprocess.run(['sacct','-n','-X','-j',r['job_id'],'--format=JobIDRaw,State,ExitCode','--parsable2'],text=True,capture_output=True,check=False)
 print(s.stdout.strip() or 'SCHEDULER_RECORD_NOT_YET_AVAILABLE=true')
 if s.returncode:print('SCHEDULER_QUERY_ERROR='+s.stderr.strip())
 p=run/'FINAL_STATUS.json'
 print(p.read_text() if p.exists() else 'DOWNLOADS_READY=false\nFINAL_COMPLETION_NOT_YET_VERIFIED=true')

if __name__=='__main__':main()
