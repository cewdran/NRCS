# Shannon retrospective baselines and forecast readiness

Scope: Whipstone annual crops; project readiness context

Recorded jobs: 24128032.

Evidence: Scientific/main stage completion recorded; any collector snapshot limits are retained.

Original entry point: `RUN.sh -> backtest.py`.

Original guards, numerical code, source pins and methods are unchanged. The successful job dynamically imported the original pilot.py verifier. That dependency is retained because this import ran successfully; its old failed reporting driver and failed launch are excluded. This is retrospective known-plot evaluation, not independent prospective validation.

These are preserved source files, configurations and protocols. Restore the original
input files, source-run snapshots, references, manifests and pinned runtime before
using an original launch entry point. This scripts-only checkout is not the full
original submission distribution. Do not remove checks or change input pins to
make an incomplete checkout execute. See [RUNNING.md](../../RUNNING.md).

Execution evidence:

- [HPC_STATUS.json](../../evidence/13_shannon_readiness/HPC_STATUS.json)
- [BACKTEST_STATUS.json](../../evidence/13_shannon_readiness/results/BACKTEST_STATUS.json)
- [REPORT_STATUS.json](../../evidence/13_shannon_readiness/results/REPORT_STATUS.json)
