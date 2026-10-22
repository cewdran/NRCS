#!/bin/bash -l
set -euo pipefail
nrcs_run=$1
nrcs_main=$2
cd "$nrcs_run"
exec python3 "$nrcs_run/collect.py" "$nrcs_run" "$nrcs_main"
