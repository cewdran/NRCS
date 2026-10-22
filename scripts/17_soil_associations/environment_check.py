#!/usr/bin/env python3
"""Verify the existing corrected runtime on a compute node; never install."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
import argparse
import importlib
import importlib.metadata
import platform
import subprocess
import sys
from orchestration_common import json_file, require, runtime_config, save, sha, utc, verify_manifest
from recovery_common import recovery_plan

PINS = {'numpy': '2.3.5', 'pandas': '2.2.3', 'scipy': '1.17.0',
        'matplotlib': '3.10.8', 'reportlab': '4.4.9', 'Pillow': '12.3.0',
        'pypdf': '6.10.0', 'openpyxl': '3.1.5'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-root', required=True)
    parser.add_argument('--audit-dir', required=True)
    args = parser.parse_args()
    run, out = Path(args.run_root), Path(args.audit_dir)
    out.mkdir(parents=True, exist_ok=True)
    verify_manifest(run)
    config = json_file(run / 'CONFIG.json')
    package, expected_python = runtime_config(config)
    ready_path = package / 'runtime_env/READY.json'
    ready = json_file(ready_path)
    require(sha(ready_path) == recovery_plan(config)['expected_runtime_ready_sha256'],
            'Existing corrected runtime READY bytes changed.')
    require(platform.python_version() == '3.11.4', 'Existing corrected Python 3.11.4 is required.')
    require(Path(sys.executable).absolute() == expected_python.absolute(),
            'Unexpected runtime interpreter: ' + sys.executable)
    require(sys.version == ready.get('python', ready.get('python_version')),
            'Python build differs from the existing READY record.')
    require(ready.get('versions') == PINS, 'Existing READY versions differ from pinned corrected runtime.')
    require(ready.get('requirements_sha256') ==
            'af523eeb337a4f73489cd16e7c36b60527e48dd6b429736b63ae5670d66516cf',
            'Existing READY requirements provenance differs.')
    versions = {}
    for name, desired in PINS.items():
        got = importlib.metadata.version(name)
        require(got == desired, name + ': expected ' + desired + ', observed ' + got)
        importlib.import_module({'Pillow': 'PIL'}.get(name, name))
        versions[name] = got
    result = {'status': 'COMPUTE_RUNTIME_VERIFIED', 'utc': utc(), 'execution': execution,
              'python': sys.version, 'executable': sys.executable, 'versions': versions,
              'ready_sha256': sha(ready_path), 'runtime_installation_performed': False,
              'package_integrity_passed': True}
    save(out / 'ENVIRONMENT_STATUS.json', result)
    with (out / 'pip_freeze.txt').open('w') as handle:
        subprocess.run([sys.executable, '-m', 'pip', 'freeze', '--all'], stdout=handle,
                       stderr=subprocess.STDOUT, check=True)
    print('COMPUTE_RUNTIME_VERIFICATION=PASSED')


if __name__ == '__main__':
    main()
