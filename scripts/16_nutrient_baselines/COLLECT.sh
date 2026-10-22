#!/bin/bash -l
set -euo pipefail
nrcs_run=$1
nrcs_main=$2
nrcs_python=$3
cd "$nrcs_run"
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE NRCS_RUNTIME_FALLBACK_REASON
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 LC_ALL=C
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
nrcs_module_ready=false
if module load python/3.11/3.11.4; then
    nrcs_module_ready=true
else
    printf 'Required Python module did not load; using available Python3 for guarded standard-library collection only.\n' >&2
fi
module list >audit/collector_modules.txt 2>&1 || true
if [[ "$nrcs_module_ready" == true && -x "$nrcs_python" ]]; then
    exec "$nrcs_python" collect.py "$nrcs_run" "$nrcs_main"
fi
export NRCS_RUNTIME_FALLBACK_REASON='Pinned corrected runtime/module unavailable to collector; available Python3 used for guarded standard-library failure/evidence collection only'
printf 'COLLECTOR_RUNTIME_FALLBACK=available Python3; no scientific stages will run in the collector.\n' >&2
exec python3 collect.py "$nrcs_run" "$nrcs_main"
