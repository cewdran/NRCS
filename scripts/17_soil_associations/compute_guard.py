#!/usr/bin/env python3
"""Verify the actual allocation before runtime recording or scientific imports."""
from hpc_guard import require_hpc

execution = require_hpc()
print('PUMA_COMPUTE_ALLOCATION_VERIFIED=' + execution['job_id'])
