#!/bin/bash
set -euo pipefail
nrcs_run=$1
nrcs_python=$2
nrcs_job=$3
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE
export PYTHONNOUSERSITE=1
exec "$nrcs_python" "$nrcs_run/collect.py" "$nrcs_run" "$nrcs_job"
