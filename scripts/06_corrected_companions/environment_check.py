#!/usr/bin/env python3
from payload.hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
import sys,json,datetime,subprocess,importlib,importlib.metadata,hashlib

package=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(exist_ok=True,parents=True)
versions={}
for line in (package/'payload/requirements.txt').read_text().splitlines():
 if not line.strip()or line.startswith('#'):continue
 name,want=line.split('==');got=importlib.metadata.version(name)
 if got!=want:raise SystemExit('VERSION_MISMATCH '+name+': expected '+want+', got '+got)
 importlib.import_module({'Pillow':'PIL'}.get(name,name));versions[name]=got
result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'execution':execution,'python':sys.version,
 'executable':sys.executable,'versions':versions,'requirements_sha256':hashlib.sha256((package/'payload/requirements.txt').read_bytes()).hexdigest()}
(out/'environment.json').write_text(json.dumps(result,indent=2)+'\n')
with (out/'pip_freeze.txt').open('w')as f:subprocess.run([sys.executable,'-m','pip','freeze','--all'],stdout=f,check=True)
with (out/'numpy_build.txt').open('w')as f:subprocess.run([sys.executable,'-c','import numpy; numpy.show_config()'],stdout=f,stderr=subprocess.STDOUT,check=True)
print(json.dumps(result,indent=2))
