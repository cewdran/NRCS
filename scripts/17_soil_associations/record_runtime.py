#!/usr/bin/env python3
"""Capture actual per-stage Slurm execution using the standard library only."""
from hpc_guard import require_hpc
execution = require_hpc()

import argparse
import os
import platform
import socket
import sys
from pathlib import Path
from orchestration_common import save, utc

parser = argparse.ArgumentParser()
parser.add_argument('event', choices=['start', 'end'])
parser.add_argument('--audit-dir', required=True)
parser.add_argument('--exit-code', type=int)
args = parser.parse_args()
result = {'event': args.event, 'utc': utc(), 'execution': execution,
          'interpreter': sys.executable, 'python': sys.version,
          'hostname': socket.gethostname(), 'platform': platform.platform(),
          'pipeline_exit_code': args.exit_code,
          'slurm': {key: value for key, value in os.environ.items() if key.startswith('SLURM_')},
          'scientific_review_pending': True, 'visual_review_pending': True,
          'ten_year_forecast_generated': False, 'predictive_validation_established': False}
save(Path(args.audit_dir) / ('EXECUTION_' + args.event.upper() + '.json'), result)
print('EXECUTION_' + args.event.upper() + '_RECORDED=' + execution['job_id'])
