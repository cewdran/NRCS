#!/bin/bash
set -euo pipefail
nrcs_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$nrcs_package"
sha256sum -c SHA256SUMS.txt >/dev/null
exec python3 submit.py "$nrcs_package"
