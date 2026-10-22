#!/bin/bash -l
set -euo pipefail
nrcs_run=${1:?run root required}
nrcs_python=${2:?corrected runtime python required}
nrcs_audit="$nrcs_run/audit/join"
source "$nrcs_run/RUN_COMMON.sh"
set -x
"$nrcs_python" "$nrcs_run/join_chunks.py" --run-root "$nrcs_run"
set +x
