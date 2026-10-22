#!/bin/bash -l
set -euo pipefail
nrcs_run=$1
nrcs_python=$2
cd "$nrcs_run"
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 LC_ALL=C
trap 'nrcs_exit=$?; trap - EXIT; "$nrcs_python" record_runtime.py end "$nrcs_run" "$nrcs_exit" || true; exit "$nrcs_exit"' EXIT
"$nrcs_python" record_runtime.py start "$nrcs_run"
sha256sum -c PACKAGE_SHA256SUMS.txt >/dev/null
"$nrcs_python" audit_feasibility.py "$nrcs_run"
