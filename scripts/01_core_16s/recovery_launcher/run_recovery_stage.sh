#!/bin/bash -l
set -euo pipefail
[[ $# -eq 2 ]] || { echo 'Expected STAGE RUN_DIRECTORY' >&2; exit 2; }
nrcs_stage=$1
nrcs_run=$2
source "$nrcs_run/package/config.sh"
if [[ -n "$R_MODULE" ]]; then
  type module >/dev/null 2>&1 || { echo 'The R module environment is unavailable.' >&2; exit 1; }
  module load "$R_MODULE"
fi
command -v Rscript >/dev/null
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export NRCS_SENSITIVITY_MIN_READS="$SENSITIVITY_MIN_READS"
case "$nrcs_stage" in
  bacteria)
    Rscript - "$nrcs_run/package/scripts/02_dada2.R" <<'RS'
args <- commandArgs(trailingOnly = TRUE)
stopifnot(utils::packageVersion("dada2") >= "1.12.0")
invisible(parse(file = args[1]))
cat("RECOVERY_DADA2_VERSION:", as.character(utils::packageVersion("dada2")), "\n")
cat("RECOVERY_SCRIPT_PARSE_OK\n")
RS
    IFS= read -r nrcs_source < "$nrcs_run/SOURCE_RUN.txt"
    Rscript "$nrcs_run/package/scripts/check_r_packages.R" "$nrcs_run/package"
    Rscript "$nrcs_run/package/validate_recovery_runtime.R" "$nrcs_run" "$nrcs_source"
    Rscript "$nrcs_run/package/scripts/02_dada2.R" bacteria "$nrcs_run"
    ;;
  farm)
    [[ -f "$nrcs_run/dada2/archaea/COMPLETE" && -f "$nrcs_run/dada2/bacteria/COMPLETE" ]] || {
      echo 'Both domains must have COMPLETE markers before farm analyses.' >&2; exit 1;
    }
    nrcs_farms=(OatmanFlatsRanch PageSpringsCellars Whipstone)
    [[ ${SLURM_ARRAY_TASK_ID:-} =~ ^[012]$ ]] || exit 2
    Rscript "$nrcs_run/package/scripts/03_farm_analysis.R" "${nrcs_farms[$SLURM_ARRAY_TASK_ID]}" "$nrcs_run"
    ;;
  *) exit 2 ;;
esac
