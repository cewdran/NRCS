#!/bin/bash -l
set -euo pipefail
nrcs_run=$1
cd "$nrcs_run"
exec python3 collect.py "$nrcs_run" "$2"
