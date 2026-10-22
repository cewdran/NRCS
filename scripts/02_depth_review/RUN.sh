#!/bin/bash -l
set -euo pipefail
nrcs_package=$1
nrcs_output=$2
module load R/4.5
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$nrcs_package"
sha256sum -c SHA256SUMS.txt
Rscript -e 'invisible(parse(file="run_review.R")); invisible(parse(file="cohort_test.R"))'
Rscript run_review.R "$nrcs_package" "$nrcs_output"
python3 - "$nrcs_output" <<'PY'
import sys,zipfile
from pathlib import Path
p=Path(sys.argv[1])
assert (p/'COMPLETE.txt').is_file()
zpath=p.with_suffix('.zip')
with zipfile.ZipFile(zpath,'x',zipfile.ZIP_DEFLATED) as z:
 for f in sorted(p.rglob('*')):
  if f.is_file() and f.name != 'job.log':z.write(f,f.relative_to(p.parent))
print('DOWNLOAD_RESULTS='+str(zpath))
PY
