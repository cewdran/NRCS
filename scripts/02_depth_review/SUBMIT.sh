#!/bin/bash -l
set -euo pipefail
nrcs_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$nrcs_package"
sha256sum -c SHA256SUMS.txt
nrcs_output=$(mktemp -d "$nrcs_package/results_XXXXXXXX")
nrcs_job=$(sbatch --parsable --account=dinusha1 --partition=standard --job-name=nrcs_depth --ntasks=1 --cpus-per-task=1 --mem=5G --time=24:00:00 --output="$nrcs_output/job.log" "$nrcs_package/RUN.sh" "$nrcs_package" "$nrcs_output")
printf 'JOB_ID=%s\nRESULT_DIRECTORY=%s\n' "$nrcs_job" "$nrcs_output" | tee "$nrcs_output/submission.txt"
