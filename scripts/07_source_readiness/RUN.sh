#!/bin/bash
set -euo pipefail
nrcs_run=$1
nrcs_python=$2
cd "$nrcs_run"
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE
export PYTHONNOUSERSITE=1 PYTHONHASHSEED=20260930
trap 'nrcs_code=$?; trap - EXIT; printf "%s\n" "$nrcs_code" > "$nrcs_run/audit/pipeline_exit_code.txt"; exit "$nrcs_code"' EXIT
sha256sum -c PACKAGE_SHA256SUMS.txt > audit/package_integrity.log
set -x
"$nrcs_python" "$nrcs_run/audit.py" "$nrcs_run"
