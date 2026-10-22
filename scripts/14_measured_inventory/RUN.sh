#!/bin/bash -l
set -euo pipefail
nrcs_run=$1
nrcs_python=$2
cd "$nrcs_run"
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE NRCS_RUNTIME_FALLBACK_REASON
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 LC_ALL=C
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export MPLBACKEND=Agg MPLCONFIGDIR="$nrcs_run/audit/matplotlib_cache"
nrcs_runtime_failure_reason='Required Python module setup did not complete; no verified corrected runtime was started'
nrcs_record_exit() {
    local nrcs_exit=$?
    trap - EXIT
    if [[ ! -f audit/run_start.json ]]; then
        NRCS_RUNTIME_FALLBACK_REASON="$nrcs_runtime_failure_reason" python3 record_runtime.py start "$nrcs_run" || true
    fi
    if [[ -x "$nrcs_python" ]]; then
        "$nrcs_python" record_runtime.py end "$nrcs_run" "$nrcs_exit" || \
            NRCS_RUNTIME_FALLBACK_REASON="$nrcs_runtime_failure_reason" python3 record_runtime.py end "$nrcs_run" "$nrcs_exit" || true
    else
        NRCS_RUNTIME_FALLBACK_REASON="$nrcs_runtime_failure_reason" python3 record_runtime.py end "$nrcs_run" "$nrcs_exit" || true
    fi
    exit "$nrcs_exit"
}
trap nrcs_record_exit EXIT
module load python/3.11/3.11.4
module list >audit/modules.txt 2>&1
mkdir -p "$MPLCONFIGDIR"
nrcs_runtime_failure_reason='Pinned corrected runtime interpreter is unavailable or failed on this allocated compute node'
if [[ ! -x "$nrcs_python" ]]; then
    NRCS_RUNTIME_FALLBACK_REASON="$nrcs_runtime_failure_reason" python3 record_runtime.py start "$nrcs_run" || true
    printf 'PINNED_RUNTIME_UNAVAILABLE=%s\nNo scientific stages ran in this attempt. Failure evidence will be collected separately.\n' "$nrcs_python" >&2
    exit 2
fi
"$nrcs_python" record_runtime.py start "$nrcs_run"
sha256sum -c PACKAGE_SHA256SUMS.txt >audit/package_integrity.log 2>&1
"$nrcs_python" environment_check.py "$nrcs_run"
"$nrcs_python" stage_source.py "$nrcs_run"
"$nrcs_python" inventory_self_checks.py "$nrcs_run"
"$nrcs_python" inventory_outcomes.py "$nrcs_run"
"$nrcs_python" render_inventory_report.py "$nrcs_run"
sha256sum -c PACKAGE_SHA256SUMS.txt >audit/package_integrity_after_report.log 2>&1
test -s ANALYSES_COMPLETE.txt
