#!/bin/bash
set -euo pipefail
TASK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$TASK_DIR"
sha256sum -c SHA256SUMS.txt
# Login-node orchestration uses only the standard library.
exec python3 "$TASK_DIR/submit.py"
