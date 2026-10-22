#!/usr/bin/env python3
from hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
import sys,os,json,socket,platform,datetime,getpass,subprocess

stage=sys.argv[1];run=Path(sys.argv[2]);(run/'audit').mkdir(exist_ok=True)
result={'stage':stage,'utc':datetime.datetime.utcnow().isoformat()+'Z','execution':execution,
 'hostname':socket.getfqdn(),'user':getpass.getuser(),'platform':platform.platform(),'interpreter':sys.executable,
 'slurm':{k:v for k,v in os.environ.items()if k.startswith('SLURM_')or k=='SLURMD_NODENAME'}}
if stage=='end':result['pipeline_exit_code']=int(sys.argv[3])
(run/'audit'/('run_'+stage+'.json')).write_text(json.dumps(result,indent=2)+'\n')
(run/'audit'/('scontrol_'+stage+'.txt')).write_text(execution['job_record']+'\n')
print(json.dumps(result,indent=2))
