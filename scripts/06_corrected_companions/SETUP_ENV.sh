#!/bin/bash -l
set -euo pipefail
nrcs_package=$1
nrcs_setup=$2
# Shell redirection on the compute node is compatible with srun's PTY mode.
exec >> "$nrcs_setup/setup.log" 2>&1
cd "$nrcs_package"
python3 -c 'from payload.hpc_guard import require_hpc; print(require_hpc())'
module load python/3.11/3.11.4
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE
export PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
date -u '+SETUP_START_UTC=%Y-%m-%dT%H:%M:%SZ'
hostname -f
module list 2>&1
python3 --version
python3 -c 'import sys; assert sys.version_info[:2]==(3,11),sys.version'
python3 -m venv "$nrcs_package/runtime_env"
nrcs_python="$nrcs_package/runtime_env/bin/python"
"$nrcs_python" -m pip install --only-binary=:all: --report "$nrcs_setup/install_report.json" -r "$nrcs_package/payload/requirements.txt"
"$nrcs_python" -m pip check
"$nrcs_python" "$nrcs_package/environment_check.py" "$nrcs_package" "$nrcs_setup"
date -u '+SETUP_END_UTC=%Y-%m-%dT%H:%M:%SZ'
cp "$nrcs_setup/environment.json" "$nrcs_package/runtime_env/READY.json"
printf '%s\n' "$nrcs_setup" > "$nrcs_package/runtime_env/SETUP_DIRECTORY.txt"
