#!/bin/bash
set -euo pipefail
nrcs_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
python3 "$nrcs_package/check.py"
