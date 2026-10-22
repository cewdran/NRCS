# Source selection and evidence

This collection selects the successful versions from the 20 October repository
and the later Oatman and remaining-manuscript workflows. Code and configuration
bytes are preserved. New root documentation and per-workflow notes describe
their role without modifying the analysis methods.

## Evidence levels

| Evidence | Interpretation |
|---|---|
| Returned frozen source and completed worker receipts | Supports the recorded source and successful stage execution. A collector's still-running snapshot is not relabeled as a terminal success. |
| Historical completed outputs and validation records | Supports completion of the named workflow. Some early packages do not have an independent frozen runtime code hash or final Slurm receipt. |
| User-provided terminal output for job 24371235 | Supports the user's reported `COMPLETED 0:0` and `RESULTS_READY=true`. The exact delivered v2 source is included; the returned result bytes have not been independently inspected. |

Execution success and scientific/visual review are separate. Original status
records may retain an earlier preparation state, a submission state or a
review-pending flag. Later evidence is supplied alongside them rather than
silently rewriting historical records.

## Exact source recovery and dependencies

The prepared original DADA2 script in the older repository did not match the
original runtime hash in `RECOVERY_RECEIPT.json`. The recorded memory-recovery
launcher defines literal old/new text blocks. Reversing those edits in the
successful returned bacterial script recovers the original script with SHA256:

```text
dfb2e3df01344d8dcc6f855536f03c2d404150d9cf495e8a27f312e4efaab0f9
```

That exactly matches the pre-existing original-script receipt. The recovered
file is preserved for the successful **archaeal** branch only. The successful
bacterial streaming version remains separate. No scientific program was
executed to recover the source text. The recovery method is recorded in
`evidence/01_core_16s/ARCHAEA_SOURCE_RECOVERY.json` and the source origins list.

The depth-review source matches the original delivered package's checksum
manifest. The newly recovered `results_VNJJgJBd.zip` contains the submission
record for job 24003880, completion marker, runtime session and passing
permutation/reuse checks. It does not contain a frozen execution-time source
snapshot, so provenance is by the named delivered package and completed result.

Stage 13's successful backtest dynamically imported the earlier `pilot.py`
verifier. That executed dependency is retained; the failed earlier reporting
driver is not. Stage 17 preserves the repaired join code and reuses completed
workers/chunks. Earlier source-producing stages remain when later successful
versions only change figure layout.

## Exclusions

Failed original bacterial launch configuration, failed remaining-manuscript v1,
failed figure-refresh v1 launcher, stalled `join_chunks.original.py`, unused
duplicates and superseded repair snapshots are excluded from the selected
execution paths. The older prepared DADA2 source with the mismatched hash is
excluded in favor of the exact recovered source.

Raw/processed data, plots, manuscripts, databases and installed environments
are excluded by the scripts-only scope. Some provenance records contain input
filenames, hashes, job metadata and aggregate check results; these document
the executed workflow rather than replace its underlying datasets.

## Machine-readable records

- `provenance/workflows.json`: scope, entry point, evidence and limitations.
- `provenance/source_archives.json`: hashes of the source distributions used.
- `provenance/source_file_origins.json`: original source member, destination,
  size, hash and role for each copied or exact-hash-recovered file.
- `provenance/excluded_sources.json`: selection/exclusion audit. Unselected
  does not necessarily mean failed; some files are duplicates or outside scope.
- `provenance/CURATION_REVIEW.json`: local integrity and static checks.
- Root `SHA256SUMS.txt`: integrity of this final repository snapshot.

These records support code provenance. They do not reintroduce the removed
machine-readable companion-inventory section into manuscript supplements.
