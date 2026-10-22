#!/bin/bash -l
set -euo pipefail
nrcs_package=$1
nrcs_setup=$2
exec >> "$nrcs_setup/setup.log" 2>&1
cd "$nrcs_package"
python3 -c 'from hpc_guard import require_hpc; print(require_hpc())'
sha256sum -c SHA256SUMS.txt >/dev/null
date -u '+SETUP_START_UTC=%Y-%m-%dT%H:%M:%SZ'
uname -a
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE R_HOME R_LIBS R_LIBS_USER
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export MAMBA_ROOT_PREFIX="$nrcs_package/mamba_root"
mkdir -p "$nrcs_package/bootstrap"
# An isolated bootstrap does not edit shell startup files or another environment.
if [[ ! -x "$nrcs_package/bootstrap/bin/micromamba" ]]; then
  curl -fL --retry 3 --connect-timeout 30 --max-time 600 \
    https://micro.mamba.pm/api/micromamba/linux-64/latest \
    -o "$nrcs_package/bootstrap/micromamba.tar.bz2"
  tar -xjf "$nrcs_package/bootstrap/micromamba.tar.bz2" -C "$nrcs_package/bootstrap" bin/micromamba
fi
nrcs_mamba="$nrcs_package/bootstrap/bin/micromamba"
"$nrcs_mamba" --version
sha256sum "$nrcs_mamba" > "$nrcs_setup/micromamba.sha256.txt"
# Prefix first also ensures package post-link scripts see the new interpreter.
export PATH="$nrcs_package/runtime_env/bin:$PATH"
if [[ -f "$nrcs_package/runtime_env/conda-meta/history" ]]; then
  nrcs_operation=install
else
  nrcs_operation=create
fi
"$nrcs_mamba" "$nrcs_operation" -y -p "$nrcs_package/runtime_env" \
  --override-channels -c conda-forge -c bioconda --strict-channel-priority \
  'picrust2=2.6.3=pyhdfd78af_2' 'python=3.10' 'numpy=1.26.4' 'pandas=2.2.3'
"$nrcs_mamba" list -p "$nrcs_package/runtime_env" --explicit > "$nrcs_setup/conda-explicit.txt"
"$nrcs_mamba" list -p "$nrcs_package/runtime_env" --json > "$nrcs_setup/conda-packages.json"
# Always install the reference directory from the checksummed 2.6.3 source.
# This avoids historical conda post-link problems and mixed reference releases.
curl -fL --retry 3 --connect-timeout 30 --max-time 3600 \
  https://github.com/picrust/picrust2/archive/refs/tags/v2.6.3.tar.gz \
  -o "$nrcs_package/bootstrap/picrust2-v2.6.3.tar.gz"
"$nrcs_package/runtime_env/bin/python" "$nrcs_package/environment_check.py" \
  "$nrcs_package" "$nrcs_setup" --install-references
printf '%s\n' "$nrcs_setup" > "$nrcs_package/runtime_env/SETUP_DIRECTORY.txt"
cp "$nrcs_setup/environment.json" "$nrcs_package/runtime_env/READY.json"
date -u '+SETUP_END_UTC=%Y-%m-%dT%H:%M:%SZ'
