#!/bin/bash
set -euo pipefail
nrcs_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v srun >/dev/null || { echo 'Run this from your Puma terminal.' >&2; exit 2; }
cd "$nrcs_package"
sha256sum -c SHA256SUMS.txt > /dev/null
# Reuse the proven companion environment only when its interpreter exists.
# Exact package versions are checked again on the allocated compute node.
if [[ ! -e "$nrcs_package/runtime_env" && -x /xdisk/dinusha1/dinusha1/NRCS_companion_HPC_v2/runtime_env/bin/python && -f /xdisk/dinusha1/dinusha1/NRCS_companion_HPC_v2/runtime_env/READY.json ]]; then
  ln -s /xdisk/dinusha1/dinusha1/NRCS_companion_HPC_v2/runtime_env "$nrcs_package/runtime_env"
fi
if [[ ! -f "$nrcs_package/runtime_env/READY.json" ]]; then
  mkdir -p "$nrcs_package/setup_logs"
  nrcs_setup=$(mktemp -d "$nrcs_package/setup_logs/setup_XXXXXXXX")
  printf 'Preparing the isolated environment on a Puma compute node.\nSETUP_LOG=%s/setup.log\n' "$nrcs_setup"
  # The site launcher may select --pty. Let the compute-side script own logging.
  if srun --account=dinusha1 --partition=standard --nodes=1 --ntasks=1 --cpus-per-task=2 \
      --time=01:00:00 --job-name=nrcs_setup \
      /bin/bash -l "$nrcs_package/SETUP_ENV.sh" "$nrcs_package" "$nrcs_setup"; then
    tail -n 12 "$nrcs_setup/setup.log"
  else
    nrcs_exit=$?
    printf 'Setup failed (exit %s). The analysis jobs were not submitted.\n' "$nrcs_exit" >&2
    if [[ -f "$nrcs_setup/setup.log" ]]; then tail -n 80 "$nrcs_setup/setup.log" >&2; fi
    exit "$nrcs_exit"
  fi
fi
exec bash "$nrcs_package/SUBMIT.sh"
