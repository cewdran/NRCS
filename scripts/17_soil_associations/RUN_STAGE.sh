#!/bin/bash -l
set -euo pipefail
nrcs_run=${1:?run root required}
nrcs_python=${2:?corrected runtime python required}
nrcs_audit="$nrcs_run/audit/stage"
source "$nrcs_run/RUN_COMMON.sh"
set -x
"$nrcs_python" "$nrcs_run/stage_source.py" --run-root "$nrcs_run"
set +x
