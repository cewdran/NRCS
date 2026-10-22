#!/bin/bash -l
set -euo pipefail
[[ $# -eq 3 ]] || { echo 'LAUNCH_ERROR: package run runtime arguments required' >&2; exit 2; }
nrcs_package=$1
nrcs_run=$2
nrcs_python=$3
[[ "$nrcs_package" = /* && "$nrcs_run" = /* && "$nrcs_python" = /* ]] || { echo 'LAUNCH_ERROR: absolute paths required' >&2; exit 2; }
[[ -f "$nrcs_package/refresh.py" && -d "$nrcs_run/audit" ]] || { echo 'LAUNCH_ERROR: driver/run missing' >&2; exit 2; }
cd "$nrcs_run"
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE BASH_ENV ENV
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export MPLCONFIGDIR="$nrcs_run/audit/matplotlib_${SLURM_JOB_ID:?Slurm job ID required}"
nrcs_record_exit() {
 local nrcs_exit=$?
 trap - EXIT
 if [[ "$nrcs_exit" -ne 0 ]]; then python3 "$nrcs_package/collect_archive.py" "$nrcs_run" "$nrcs_exit" || true; fi
 exit "$nrcs_exit"
}
trap nrcs_record_exit EXIT
if ! type module >/dev/null 2>&1; then
 if [[ -f /etc/profile.d/modules.sh ]]; then source /etc/profile.d/modules.sh; fi
fi
printf 'LAUNCH_CWD=%s\nLAUNCH_HOST=%s\nLAUNCH_SLURM_JOB_ID=%s\nLAUNCH_PACKAGE=%s\nLAUNCH_RUNTIME=%s\n' "$PWD" "$(hostname)" "$SLURM_JOB_ID" "$nrcs_package" "$nrcs_python"
module load python/3.11/3.11.4
[[ -x "$nrcs_python" ]] || { echo "LAUNCH_ERROR: pinned runtime not executable after module load: $nrcs_python" >&2; exit 2; }
"$nrcs_python" "$nrcs_package/refresh.py" "$nrcs_run"
