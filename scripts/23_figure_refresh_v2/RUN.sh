#!/bin/bash -l
set -euo pipefail
# Slurm spools this file; original absolute paths arrive as arguments.
[[ $# -eq 3 ]] || { echo 'LAUNCH_ERROR: package, run and runtime-python arguments required' >&2; exit 2; }
nrcs_package=$1
nrcs_run=$2
nrcs_python=$3
[[ "$nrcs_package" = /* && "$nrcs_run" = /* && "$nrcs_python" = /* ]] || { echo 'LAUNCH_ERROR: absolute paths required' >&2; exit 2; }
[[ -f "$nrcs_package/worker.py" && -f "$nrcs_package/CONFIG.json" && -d "$nrcs_run" ]] || { echo 'LAUNCH_ERROR: package or run directory missing' >&2; exit 2; }
cd "$nrcs_run"
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE BASH_ENV ENV
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 NUMEXPR_NUM_THREADS=2
export MPLCONFIGDIR="$nrcs_run/mplconfig"
if ! type module >/dev/null 2>&1; then
    if [[ -f /etc/profile.d/modules.sh ]]; then source /etc/profile.d/modules.sh; fi
fi
printf 'LAUNCH_HOST=%s
LAUNCH_SLURM_JOB_ID=%s
LAUNCH_PACKAGE=%s
LAUNCH_RUN=%s
LAUNCH_RUNTIME_PYTHON=%s
' "$(hostname)" "${SLURM_JOB_ID:?Slurm job ID required}" "$nrcs_package" "$nrcs_run" "$nrcs_python"
# Restore the exact module setup recorded in the successful v2 run.
if ! type module >/dev/null 2>&1; then
    echo 'LAUNCH_ERROR: module initialization is unavailable; return stdout.log and stderr.log.' >&2
    exit 2
fi
module load python/3.11/3.11.4
if [[ ! -x "$nrcs_python" ]]; then
    printf 'LAUNCH_ERROR: pinned runtime unavailable after module load: %s
' "$nrcs_python" >&2
    ls -ld -- "$nrcs_python" "$(dirname -- "$nrcs_python")" >&2 || true
    readlink -- "$nrcs_python" >&2 || true
    exit 2
fi
# worker.py still verifies the actual Puma allocation, Python and package versions.
exec "$nrcs_python" "$nrcs_package/worker.py" "$nrcs_run"
