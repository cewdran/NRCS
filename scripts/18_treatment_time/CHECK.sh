#!/bin/bash -l
set -euo pipefail
nrcs_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE BASH_ENV ENV
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
python3 "$nrcs_package/check.py" --package "$nrcs_package" "$@"
