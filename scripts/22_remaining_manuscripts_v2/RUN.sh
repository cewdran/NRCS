#!/bin/bash -l
set -euo pipefail
# Slurm copies this script to its spool. All original paths arrive as arguments.
[[ $# -eq 5 ]] || { echo 'LAUNCH_ERROR: package run mode domain runtime-python arguments required' >&2; exit 2; }
nrcs_package=$1
nrcs_run=$2
nrcs_mode=$3
nrcs_domain=$4
nrcs_python=$5
[[ "$nrcs_package" = /* && "$nrcs_run" = /* && "$nrcs_python" = /* ]] || { echo 'LAUNCH_ERROR: package, run and runtime paths must be absolute' >&2; exit 2; }
[[ -d "$nrcs_package" && -f "$nrcs_package/pipeline.py" && -f "$nrcs_package/CONFIG.json" ]] || { echo 'LAUNCH_ERROR: expected package driver/config missing' >&2; exit 2; }
[[ -d "$nrcs_run" && -d "$nrcs_run/audit" && -x "$nrcs_python" ]] || { echo 'LAUNCH_ERROR: run/audit directory or runtime executable missing' >&2; exit 2; }
case "$nrcs_mode" in stage|community|extras|maps|collect) ;; *) echo 'LAUNCH_ERROR: invalid mode' >&2; exit 2 ;; esac
[[ "$nrcs_domain" = none ]] || { echo 'LAUNCH_ERROR: reserved argument must be none' >&2; exit 2; }
cd "$nrcs_run"
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE BASH_ENV ENV
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export MPLCONFIGDIR="$nrcs_run/audit/matplotlib_${SLURM_JOB_ID:?Slurm job ID required}"
if ! type module >/dev/null 2>&1; then
  if [[ -f /etc/profile.d/modules.sh ]]; then source /etc/profile.d/modules.sh; fi
fi
printf 'LAUNCH_CWD=%s\nLAUNCH_HOST=%s\nLAUNCH_SLURM_JOB_ID=%s\nLAUNCH_PACKAGE=%s\nLAUNCH_RUN=%s\nLAUNCH_MODE=%s\nLAUNCH_DOMAIN=%s\nLAUNCH_RUNTIME_PYTHON=%s\n' "$PWD" "$(hostname)" "$SLURM_JOB_ID" "$nrcs_package" "$nrcs_run" "$nrcs_mode" "$nrcs_domain" "$nrcs_python"
module load python/3.11/3.11.4
# Import scientific code only through the original package's pinned Python driver.
exec "$nrcs_python" "$nrcs_package/pipeline.py" "$nrcs_mode" "$nrcs_run"
