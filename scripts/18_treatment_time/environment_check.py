"""Verify the existing corrected runtime on the actual allocated Puma node."""
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
import argparse
import importlib
import importlib.metadata
import platform
import subprocess
import sys
from common import require, json_file, save, sha, utc, verify_manifest

READY_SHA256 = '6acaa39f5023c9f61a7cbe69d6c5687f634efb2b56e3907158003a41e1f6bf30'
PINS = {'numpy':'2.3.5','pandas':'2.2.3','scipy':'1.17.0','matplotlib':'3.10.8',
        'reportlab':'4.4.9','Pillow':'12.3.0','pypdf':'6.10.0','openpyxl':'3.1.5'}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-root',required=True)
    parser.add_argument('--audit-dir',required=True)
    args=parser.parse_args()
    run=Path(args.run_root).resolve(); audit=Path(args.audit_dir)
    require(audit.resolve().is_relative_to(run), 'Audit directory outside run')
    verify_manifest(run)
    config=json_file(run/'CONFIG.json')
    receipt=json_file(run/'submission.json')
    require(sha(run/'PACKAGE_SHA256SUMS.txt')==receipt.get('package_manifest_sha256'),
            'Package manifest differs from frozen submission')
    require(sha(run/'SOURCE_MANIFEST.json')==receipt.get('source_manifest_sha256'),
            'Source manifest differs from frozen submission')
    relative=audit.resolve().relative_to(run/'audit')
    role='worker:'+relative.parts[1] if len(relative.parts)==2 and relative.parts[0]=='workers' else str(relative)
    require(receipt.get('run_directory')==str(run) and receipt.get('jobs',{}).get(role)==execution['job_id'],
            'Current allocation differs from submitted role '+role)
    require(config.get('runtime_ready_sha256',READY_SHA256)==READY_SHA256,'Configured runtime READY pin differs')
    package=Path(config.get('runtime_package','/xdisk/dinusha1/dinusha1/NRCS_corrected_rerun_HPC_v3'))
    expected=Path(config.get('runtime_python',str(package/'runtime_env/bin/python')))
    ready_path=package/'runtime_env/READY.json'; ready=json_file(ready_path)
    require(sha(ready_path)==READY_SHA256,'Existing corrected READY bytes changed')
    require(platform.python_version()=='3.11.4','Corrected Python 3.11.4 is required')
    require(Path(sys.executable).absolute()==expected.absolute(),'Unexpected scientific interpreter')
    require(sys.version==ready.get('python',ready.get('python_version')),'Python build differs from READY')
    require(ready.get('versions')==PINS,'READY package versions differ')
    require(ready.get('requirements_sha256')=='af523eeb337a4f73489cd16e7c36b60527e48dd6b429736b63ae5670d66516cf','READY requirements provenance differs')
    versions={}
    for name,wanted in PINS.items():
        got=importlib.metadata.version(name)
        require(got==wanted,name+': pinned runtime version differs')
        importlib.import_module({'Pillow':'PIL'}.get(name,name)); versions[name]=got
    audit.mkdir(parents=True,exist_ok=True)
    save(audit/'ENVIRONMENT_STATUS.json',{'status':'COMPUTE_RUNTIME_VERIFIED','utc':utc(),
        'execution':execution,'python':sys.version,'executable':sys.executable,'versions':versions,
        'package_manifest_sha256':sha(run/'PACKAGE_SHA256SUMS.txt'),
        'source_manifest_sha256':sha(run/'SOURCE_MANIFEST.json'),
        'ready_sha256':READY_SHA256,'package_integrity_passed':True,'runtime_installation_performed':False})
    with (audit/'pip_freeze.txt').open('w') as out:
        subprocess.run([sys.executable,'-m','pip','freeze','--all'],stdout=out,stderr=subprocess.STDOUT,check=True,timeout=120)
    print('COMPUTE_RUNTIME_VERIFICATION=PASSED',flush=True)

if __name__=='__main__': main()
