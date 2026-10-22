#!/bin/bash -l
set -euo pipefail
nrcs_run=${1:?run root required}
nrcs_python=${2:?corrected Python required}
nrcs_worker=${3:?worker ID required}
[[ "$nrcs_worker" =~ ^[a-z][a-z0-9_]{0,79}$ ]] || exit 2
nrcs_audit="$nrcs_run/audit/workers/$nrcs_worker"
source "$nrcs_run/RUN_COMMON.sh"
"$nrcs_python" "$nrcs_run/worker.py" --run-root "$nrcs_run" --worker-id "$nrcs_worker"
