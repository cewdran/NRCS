# Project-wide soil associations with joined recovery

Scope: All three farms / shared project workflow

Recorded jobs: 24253500, 24253501.

Evidence: Scientific/main stage completion recorded; any collector snapshot limits are retained.

Original entry point: `RUN_STAGE.sh -> RUN_JOIN.sh -> RUN_COMBINE.sh`.

Original guards, numerical code, source pins and methods are unchanged. Exact successful join_chunks.py replaces the excluded join_chunks.original.py. Original completed workers/chunks are retained on Puma; this recovery entry point reuses them.

These are preserved source files, configurations and protocols. Restore the original
input files, source-run snapshots, references, manifests and pinned runtime before
using an original launch entry point. This scripts-only checkout is not the full
original submission distribution. Do not remove checks or change input pins to
make an incomplete checkout execute. See [RUNNING.md](../../RUNNING.md).

Execution evidence:

- [HPC_STATUS.json](../../evidence/17_soil_associations/HPC_STATUS.json)
- [JOIN_STATUS.json](../../evidence/17_soil_associations/audit/join/JOIN_STATUS.json)
- [COMBINE_STATUS.json](../../evidence/17_soil_associations/results/COMBINE_STATUS.json)
