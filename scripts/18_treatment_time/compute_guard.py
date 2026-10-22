"""Allocation check before any scientific entrypoint."""
from hpc_guard import require_hpc
execution=require_hpc()
print('PUMA_COMPUTE_ALLOCATION_VERIFIED='+execution['job_id'],flush=True)
