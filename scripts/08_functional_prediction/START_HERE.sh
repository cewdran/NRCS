#!/bin/bash
set -euo pipefail
nrcs_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$nrcs_package"
command -v srun >/dev/null || { echo 'Run from your Puma terminal.' >&2; exit 2; }
sha256sum -c SHA256SUMS.txt >/dev/null
python3 -c 'from hpc_guard import puma_cluster; print("CLUSTER="+puma_cluster())'
if [[ ! -f "$nrcs_package/runtime_env/READY.json" ]]; then
  mkdir -p "$nrcs_package/setup_logs"
  nrcs_setup=$(mktemp -d "$nrcs_package/setup_logs/setup_XXXXXXXX")
  printf 'Installing PICRUSt2 and checking reference data on Puma.\nSETUP_LOG=%s/setup.log\n' "$nrcs_setup"
  if srun --account=dinusha1 --partition=standard --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=24G \
      --time=03:00:00 --job-name=nrcs_fn_setup \
      /bin/bash -l "$nrcs_package/SETUP_ENV.sh" "$nrcs_package" "$nrcs_setup"; then
    tail -n 15 "$nrcs_setup/setup.log"
  else
    nrcs_exit=$?
    printf 'Setup failed (exit %s); analysis was not submitted.\n' "$nrcs_exit" >&2
    tail -n 70 "$nrcs_setup/setup.log" >&2
    exit "$nrcs_exit"
  fi
fi
exec python3 "$nrcs_package/submit.py" "$nrcs_package"
