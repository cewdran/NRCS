#!/bin/bash
set -euo pipefail
nrcs_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$nrcs_package/SUBMIT.sh"
