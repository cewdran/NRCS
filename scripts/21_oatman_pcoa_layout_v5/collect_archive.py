"""Standard-library archive handoff only; never imports scientific packages."""
from pathlib import Path
import hashlib,json,sys,zipfile
BASE=Path(__file__).resolve().parent

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,indent=2)+'\n')

def archive(run,status,diagnostic=False):
 run=Path(run);name=BASE.name+'_'+run.name+('_DIAGNOSTIC_INCOMPLETE.zip' if diagnostic else '_results.zip');target=BASE.parent/name;check=target.with_suffix('.zip.sha256.txt')
 if diagnostic:
  if target.exists():return
  status.update(state='FAILED_DIAGNOSTIC_READY',scientific_values_recomputed=False,rendering_completed=False,downloads_ready=False,diagnostic_only=True,diagnostic_downloads_ready=True,diagnostic_results=str(target),diagnostic_checksum=str(check))
 else:status.update(state='LAYOUT_REFRESH_CREATED_VISUAL_REVIEW_PENDING',rendering_completed=True,downloads_ready=True,diagnostic_only=False,download_results=str(target),download_checksum=str(check))
 save(run/'FINAL_STATUS.json',status)
 try:
  with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as z:
   for p in sorted(run.rglob('*')):
    if p.is_file() and 'source_snapshot' not in p.parts:z.write(p,p.relative_to(run.parent))
  check.write_text(sha(target)+'  '+target.name+'\n')
 except BaseException:
  status.update(downloads_ready=False,diagnostic_downloads_ready=False,state='ARCHIVE_FAILED');save(run/'FINAL_STATUS.json',status);raise
 print(json.dumps(status,indent=2))

if __name__=='__main__':
 run=Path(sys.argv[1]);path=run/'FINAL_STATUS.json';status=json.loads(path.read_text()) if path.exists() else {}
 status.update(wrapper_exit_code=sys.argv[2] if len(sys.argv)>2 else 'unknown',science_executed_by_fallback=False)
 archive(run,status,diagnostic=True)
