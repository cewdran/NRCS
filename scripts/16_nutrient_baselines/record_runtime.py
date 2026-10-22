#!/usr/bin/env python3
"""Record actual guarded Puma execution and Python/platform, including failure exits."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
import datetime
import getpass
import json
import os
import platform
import socket
import sys
from common import save

stage = sys.argv[1]
if stage not in ('start', 'end'):
    raise SystemExit('Expected start or end runtime stage')
run = Path(sys.argv[2]).resolve()
result = {
    'stage': stage,
    'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'execution': execution,
    'hostname': socket.getfqdn(),
    'user': getpass.getuser(),
    'platform': platform.platform(),
    'interpreter': sys.executable,
    'python_version': sys.version,
    'runtime_interpreter_fallback': bool(os.environ.get('NRCS_RUNTIME_FALLBACK_REASON')),
    'runtime_interpreter_fallback_reason': os.environ.get('NRCS_RUNTIME_FALLBACK_REASON') or None,
    'authorized_scope': 'Whipstone Annual reported LOI organic matter and KCl nitrate trajectories and separate 2024 retrospective simple-baseline point diagnostics',
    'accepted_measured_inventory_source_job_id': '24147988',
    'baseline_execution_status': 'CONSULT_ENGINE_AND_COLLECTOR_VALIDATION_RECEIPTS',
    'baseline_parameters_computed': None,
    'baseline_predictions_computed': None,
    'baseline_models_fitted': None,
    'complex_models_fitted': False,
    'accepted_validation_source_job_id': '24128032',
    'models_fitted': None,
    'statistical_tests_run': False,
    'forecasts_generated': False,
    'numerical_ten_year_forecast': False,
    'ten_year_forecast_generated': False,
    'predictive_validation_established': False,
    'scientific_review_pending': True,
    'slurm': {key: value for key, value in os.environ.items()
              if key.startswith('SLURM_') or key == 'SLURMD_NODENAME'},
}
if stage == 'end':
    result['pipeline_exit_code'] = int(sys.argv[3])
save(run / 'audit' / ('run_' + stage + '.json'), result)
(run / 'audit' / ('scontrol_' + stage + '.txt')).write_text(execution['job_record'] + '\n', encoding='utf-8')
print(json.dumps(result, indent=2, allow_nan=False))
