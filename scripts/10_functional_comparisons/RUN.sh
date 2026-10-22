#!/bin/bash -l
set -euo pipefail
nrcs_run=$1
nrcs_python=$2
nrcs_runtime=$3
cd "$nrcs_run"
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE R_HOME R_LIBS R_LIBS_USER
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 LC_ALL=C
export OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export PATH="$(dirname -- "$nrcs_python"):$PATH"
python3 record_runtime.py start "$nrcs_run"
trap 'nrcs_exit=$?; trap - EXIT; python3 record_runtime.py end "$nrcs_run" "$nrcs_exit" || true; exit "$nrcs_exit"' EXIT
sha256sum -c PACKAGE_SHA256SUMS.txt >/dev/null
nrcs_mamba="$nrcs_runtime/bootstrap/bin/micromamba"
"$nrcs_mamba" run -p "$nrcs_runtime/runtime_env" "$nrcs_python" environment_check.py "$nrcs_runtime" "$nrcs_run/audit/runtime_environment"
"$nrcs_mamba" run -p "$nrcs_runtime/runtime_env" "$nrcs_python" self_checks.py "$nrcs_run"
"$nrcs_mamba" run -p "$nrcs_runtime/runtime_env" "$nrcs_python" compare_functions.py "$nrcs_run"
