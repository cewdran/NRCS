# Scientific execution boundary

All scientific numerical preprocessing, transformations, statistical tests,
numerical self-checks, models and data-derived figures/reports execute only
under verified Puma Slurm allocations. Scientific modules verify a live job,
cluster, allocated hostname and corrected runtime before numerical execution.

Login-node tools only submit, inspect scheduler state or perform standard-
library integrity/collection operations. Local preparation is limited to
source inspection, code edits, AST/shell syntax checks, byte checksums,
packaging and non-data workflow diagrams. No local numerical fixtures run.

The existing corrected Python 3.11.4 environment is reused. The login-node
interpreter symlink may be unresolved; the compute-node READY/build/version
gate is mandatory. Do not install or silently substitute an interpreter.

Preserve every frozen source and historical status. Keep job IDs, runtime
START/END records, source and code hashes, seeds, output manifests, logs and
fresh scheduler accounting. Prepared, submitted, completed and scientifically
reviewed remain distinct. This release is prepared-only until executed on Puma.
