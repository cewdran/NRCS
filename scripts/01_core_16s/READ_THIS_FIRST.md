# PacBio 16S processing and original farm community analyses

Scope: All three farms; domain-specific executed versions

Recorded jobs: 23875047_0, 23881745, 23881746_0, 23881746_1, 23881746_2.

Evidence: Completed bacterial/farm logs and outputs; original archaeal output retained.

Original entry point: `package/run_recovery_stage.sh (bacteria or farm)`.

The exact successful original archaeal script, bacterial memory-recovery script and both-domain farm-analysis scripts are included. The archaeal script was recovered by reversing the recorded literal memory patch and matches the original execution-receipt SHA256 exactly. Its memory-heavy bacterial branch must not be used; the successful streaming bacterial version is separate. Recovery launcher restores the original source-run layout; this is not a fresh all-domain launcher.

These are preserved source files, configurations and protocols. Restore the original
input files, source-run snapshots, references, manifests and pinned runtime before
using an original launch entry point. This scripts-only checkout is not the full
original submission distribution. Do not remove checks or change input pins to
make an incomplete checkout execute. See [RUNNING.md](../../RUNNING.md).

Execution evidence:

- [RECOVERY_RECEIPT.json](../../evidence/01_core_16s/RECOVERY_RECEIPT.json)
- [sessionInfo.txt](../../evidence/01_core_16s/dada2/bacteria/sessionInfo.txt)
- [sessionInfo.txt](../../evidence/01_core_16s/dada2/archaea/sessionInfo.txt)
- [ARCHAEA_SOURCE_RECOVERY.json](../../evidence/01_core_16s/ARCHAEA_SOURCE_RECOVERY.json)
- [bacteria_recovery_23881745.log.completion.txt](../../evidence/01_core_16s/bacteria_recovery_23881745.log.completion.txt)
- [farms_23881746_0.log.completion.txt](../../evidence/01_core_16s/farms_23881746_0.log.completion.txt)
- [farms_23881746_1.log.completion.txt](../../evidence/01_core_16s/farms_23881746_1.log.completion.txt)
- [farms_23881746_2.log.completion.txt](../../evidence/01_core_16s/farms_23881746_2.log.completion.txt)
- [OatmanFlatsRanch_COMPLETE.txt](../../evidence/01_core_16s/OatmanFlatsRanch_COMPLETE.txt)
- [PageSpringsCellars_COMPLETE.txt](../../evidence/01_core_16s/PageSpringsCellars_COMPLETE.txt)
- [Whipstone_COMPLETE.txt](../../evidence/01_core_16s/Whipstone_COMPLETE.txt)
