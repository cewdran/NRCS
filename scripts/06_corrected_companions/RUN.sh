#!/bin/bash -l
set -euo pipefail
nrcs_run=$1
nrcs_python=$2
cd "$nrcs_run"
python3 "$nrcs_run/record_runtime.py" start "$nrcs_run"
trap 'nrcs_exit=$?; trap - EXIT; python3 "$nrcs_run/record_runtime.py" end "$nrcs_run" "$nrcs_exit"; exit "$nrcs_exit"' EXIT
sha256sum -c PACKAGE_SHA256SUMS.txt > "$nrcs_run/audit/package_integrity.log"
module load python/3.11/3.11.4
unset PYTHONPATH PYTHONHOME PYTHONOPTIMIZE
export PYTHONNOUSERSITE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export MPLBACKEND=Agg MPLCONFIGDIR="$nrcs_run/audit/matplotlib_cache" PYTHONHASHSEED=20260929
module list > "$nrcs_run/audit/modules.txt" 2>&1
set -x
"$nrcs_python" "$nrcs_run/environment_check.py" "$nrcs_run" "$nrcs_run/audit/software"
"$nrcs_python" "$nrcs_run/payload/prepare_inputs.py"
"$nrcs_python" "$nrcs_run/payload/apply_corrections.py"
"$nrcs_python" "$nrcs_run/payload/extended_analysis.py"
"$nrcs_python" "$nrcs_run/payload/compare_prior.py"
"$nrcs_python" "$nrcs_run/payload/build_report.py"
"$nrcs_python" "$nrcs_run/payload/verify_outputs.py"
set +x
printf 'Corrected companion analyses and confirmed-pair integration and automated checks completed on Puma.\nVisual review of regenerated figures remains pending.\n' > "$nrcs_run/ANALYSES_COMPLETE.txt"
