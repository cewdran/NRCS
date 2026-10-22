# Read-depth grid and cohort retention

Scope: All three farms, bacteria and archaea

Recorded jobs: 24003880.

Evidence: Returned completion marker, runtime session, permutation self-test and identical-cohort reuse check passed. Submission receipt identifies the original package and job; no frozen execution-time code digest is present.

Original entry point: `SUBMIT.sh -> RUN.sh -> run_review.R`.

The original delivered source matches its package checksum manifest. The later returned results replace the prepared-package PENDING_ON_PUMA state. No primary depth threshold was selected. Historical provenance is by named package and completed output; exact runtime source bytes were not independently frozen.

These are preserved source files, configurations and protocols. Restore the original
input files, source-run snapshots, references, manifests and pinned runtime before
using an original launch entry point. This scripts-only checkout is not the full
original submission distribution. Do not remove checks or change input pins to
make an incomplete checkout execute. See [RUNNING.md](../../RUNNING.md).

Execution evidence:

- [submission.txt](../../evidence/02_depth_review/submission.txt)
- [COMPLETE.txt](../../evidence/02_depth_review/COMPLETE.txt)
- [PERMUTATION_SELFTEST_PASS.txt](../../evidence/02_depth_review/PERMUTATION_SELFTEST_PASS.txt)
- [REUSE_VALIDATION_PASS.txt](../../evidence/02_depth_review/REUSE_VALIDATION_PASS.txt)
- [sessionInfo.txt](../../evidence/02_depth_review/sessionInfo.txt)
