#!/bin/bash
# Sourced only by guarded Slurm stage wrappers.
set -euo pipefail
cd "$nrcs_run"
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE BASH_ENV ENV
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=20261009 LC_ALL=C
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export MPLBACKEND=Agg MPLCONFIGDIR="$nrcs_audit/matplotlib_cache"
python3 "$nrcs_run/compute_guard.py"
python3 "$nrcs_run/record_runtime.py" start --audit-dir "$nrcs_audit"
trap 'nrcs_exit=$?; trap - EXIT; python3 "$nrcs_run/record_runtime.py" end --audit-dir "$nrcs_audit" --exit-code "$nrcs_exit" || true; exit "$nrcs_exit"' EXIT
module load python/3.11/3.11.4
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE
module list > "$nrcs_audit/modules.txt" 2>&1
"$nrcs_python" "$nrcs_run/environment_check.py" --run-root "$nrcs_run" --audit-dir "$nrcs_audit"
