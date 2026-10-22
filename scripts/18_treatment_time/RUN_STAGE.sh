#!/bin/bash -l
set -euo pipefail
nrcs_run=${1:?run root required}
nrcs_python=${2:?corrected Python required}
nrcs_audit="$nrcs_run/audit/stage"
source "$nrcs_run/RUN_COMMON.sh"
"$nrcs_python" "$nrcs_run/stage_source.py" --run-root "$nrcs_run"
"$nrcs_python" "$nrcs_run/numeric_self_checks.py" --run-root "$nrcs_run"
