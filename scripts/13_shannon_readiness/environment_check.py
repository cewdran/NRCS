#!/usr/bin/env python3
"""Verify the existing corrected-analysis environment on Puma; install nothing."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
import datetime
import importlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from common import require, save, sha

EXPECTED = {
    'numpy': '2.3.5', 'pandas': '2.2.3', 'scipy': '1.17.0',
    'matplotlib': '3.10.8', 'reportlab': '4.4.9', 'Pillow': '12.3.0',
    'pypdf': '6.10.0', 'openpyxl': '3.1.5',
}
REQUIREMENTS_SHA256 = 'af523eeb337a4f73489cd16e7c36b60527e48dd6b429736b63ae5670d66516cf'
run = Path(sys.argv[1]).resolve()
config = json.loads((run / 'CONFIG.json').read_text())
require(config.get('runtime_expected_versions') == EXPECTED
        and config.get('runtime_requirements_sha256') == REQUIREMENTS_SHA256,
        'Configured runtime pins differ from the reviewed corrected environment.')
runtime = Path(config['runtime_package']).resolve(strict=True)
require(runtime == Path('/xdisk/dinusha1/dinusha1/NRCS_corrected_rerun_HPC_v3'),
        'This validation/readiness stage must use the existing corrected-analysis runtime.')
python = runtime / 'runtime_env/bin/python'
require(Path(sys.executable).resolve() == python.resolve(strict=True), 'Unexpected validation/readiness interpreter.')
ready_path = runtime / 'runtime_env/READY.json'
ready = json.loads(ready_path.read_text())
require(ready.get('python') == sys.version
        and ready.get('executable') == str(python),
        'Actual Python build or interpreter differs from the corrected runtime READY record.')
require(ready.get('versions') == EXPECTED, 'Corrected runtime READY versions differ from pinned validation/readiness dependencies.')
require(ready.get('requirements_sha256') == REQUIREMENTS_SHA256,
        'Corrected runtime READY requirement checksum differs from the reviewed environment.')
requirements = runtime / 'payload/requirements.txt'
require(sha(requirements) == REQUIREMENTS_SHA256, 'Corrected runtime requirements changed.')
versions = {}
for name, expected in EXPECTED.items():
    actual = importlib.metadata.version(name)
    require(actual == expected, 'VERSION_MISMATCH ' + name + ': expected ' + expected + ', got ' + actual)
    importlib.import_module({'Pillow': 'PIL'}.get(name, name))
    versions[name] = actual
out = run / 'audit/software'
out.mkdir(parents=True, exist_ok=True)
shutil.copyfile(ready_path, out / 'original_runtime_READY.json')
require(sha(out / 'original_runtime_READY.json') == sha(ready_path), 'Runtime READY snapshot checksum mismatch.')
result = {
    'status': 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED',
    'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'execution': execution,
    'python': sys.version,
    'executable': sys.executable,
    'runtime_package': str(runtime),
    'runtime_ready_sha256': sha(ready_path),
    'versions': versions,
    'requirements_sha256': REQUIREMENTS_SHA256,
    'software_installed': False,
}
with (out / 'pip_freeze.txt').open('w', encoding='utf-8') as stream:
    subprocess.run([sys.executable, '-m', 'pip', 'freeze', '--all'], stdout=stream,
                   stderr=subprocess.STDOUT, text=True, check=True)
with (out / 'numpy_build.txt').open('w', encoding='utf-8') as stream:
    subprocess.run([sys.executable, '-c', 'import numpy; numpy.show_config()'], stdout=stream,
                   stderr=subprocess.STDOUT, text=True, check=True)
save(out / 'environment.json', result)
print(json.dumps(result, indent=2, allow_nan=False))
