#!/usr/bin/env python3
"""Record the Python/platform and real Puma execution, including failure exits."""
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
import sys, os, json, socket, platform, datetime, getpass

stage = sys.argv[1]
if stage not in ('start', 'end'):
    raise SystemExit('Expected start or end runtime stage')
run = Path(sys.argv[2])
(run / 'audit').mkdir(exist_ok=True)
result = {
    'stage': stage,
    'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'execution': execution,
    'hostname': socket.getfqdn(),
    'user': getpass.getuser(),
    'platform': platform.platform(),
    'interpreter': sys.executable,
    'python_version': sys.version,
    'stdlib_only_audit': True,
    'numerical_models_fitted': False,
    'ten_year_forecast_generated': False,
    'slurm': {key: value for key, value in os.environ.items()
              if key.startswith('SLURM_') or key == 'SLURMD_NODENAME'},
}
if stage == 'end':
    result['pipeline_exit_code'] = int(sys.argv[3])
(run / 'audit' / ('run_' + stage + '.json')).write_text(
    json.dumps(result, indent=2, allow_nan=False) + '\n')
(run / 'audit' / ('scontrol_' + stage + '.txt')).write_text(execution['job_record'] + '\n')
print(json.dumps(result, indent=2, allow_nan=False))
