#!/bin/bash
set -euo pipefail
TASK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Monitoring does not need the scientific runtime.
exec python3 "$TASK_DIR/check.py"
