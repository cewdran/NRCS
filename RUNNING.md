# Running the preserved workflows

This is a source-only repository for review, version control and preservation.
It is not a complete replacement for the original HPC submission packages.

Scientific computation and actual-data figure generation belong on **Puma**.
Preserve all HPC guards, source pins, cohort checks and multiplicity families.
Use login-node entry points only for the staging/submission/checking operations
they were designed to perform. Do not execute analysis workers on a login node
or in a local notebook.

## Restore the appropriate original package

1. Select the workflow in [WORKFLOWS.md](WORKFLOWS.md) and read its note.
2. Restore the full corresponding original package and source-run directory on
   Puma. Preserve its original directory structure, input manifests, source
   archives, metadata and runtime. Original package names and paths are recorded
   in `CONFIG.json`, receipts and source provenance.
3. Verify the original package and source hashes using its original manifests.
   The root `SHA256SUMS.txt` in this repository verifies this curated archive;
   it does not replace a workflow's full input/source manifest. Do not regenerate
   an input manifest simply to accept missing or changed inputs.
4. Use the original submission entry point documented for that workflow, then
   its checker. Directly launching a worker can bypass required staging,
   scheduler dependencies or environment initialization.
5. Retain the new job receipts, runtime records, returned ZIP and checksum.
   Review numerical outputs and figure layout after completion.

The repository directory names are an organizational index. They are not a new
universal package layout, and the workflows have not been refactored into one
fresh end-to-end launcher. Original absolute Puma paths remain intentionally
unchanged. Porting to a different account or machine requires a separately
reviewed configuration and environment change.

## Important dependencies

| Workflow | Required context |
|---|---|
| 01 core 16S | Original validated FASTQ inputs, metadata, reference databases and original source-run structure. The original archaea script is in `archaea_original/`; bacteria use the successful streaming memory-recovery script in `package/scripts/`. The recovery launcher is specifically for the recorded recovery, not a generic new study. |
| 02 depth review | Original depth-review input count/metadata tables and package manifest. The returned run selected no new primary threshold. |
| 03–06 community and companion interpretation | Completed core/depth outputs and the corresponding corrected companion inputs. These inputs are not shipped here. |
| 07–11 functional workflows | Validated project sources, PICRUSt2 runtime/reference resources and the preceding prediction/sensitivity/comparison outputs. |
| 12–16 readiness and baselines | Exact measured-outcome and earlier analysis snapshots. Stage 13 reuses the original `pilot.py` verifier, `baseline_core.py` and `common.py`; its pinned engine-source tree must be restored in full. |
| 17 soil associations | Successful original workers/chunks and source snapshots. The included repaired join/combine entry point reuses those completed chunks. It is not a replacement launcher for all original workers. |
| 18 treatment/time | Pinned source manifests, corrected companions, functional comparisons and their eligibility records. |
| 19–21 Oatman figures | Successful supplemental source archive, then layout v3, then final S7 layout v5. Keep each source archive available to the following stage. |
| 22 remaining manuscripts | Pinned analysis inputs for Page Springs, Whipstone and synthesis figures/maps. It produces the source ZIP consumed by stage 23. |
| 23 latest figure refresh | Exact completed stage-22 ZIP with SHA256 `6cf1ea04c863156a210db3c666dc232a431dcce7915b9c1fb304c7c160b7b692`, plus the configured plotting runtime on Puma. |

## Latest figure run

Job **24371235 has already completed**. A further submission is not necessary
just to use its outputs. On the original account, its checker is:

```bash
bash /xdisk/dinusha1/dinusha1/NRCS_Figure_Refresh_HPC_v2/CHECK.sh
```

The reported output files are:

```text
/xdisk/dinusha1/dinusha1/NRCS_Figure_Refresh_HPC_v2_run_20261022T212520Z_e89d54e9_results.zip
/xdisk/dinusha1/dinusha1/NRCS_Figure_Refresh_HPC_v2_run_20261022T212520Z_e89d54e9_results.zip.sha256.txt
```

Those returned bytes still need independent verification and figure review.
The source package is designed to redraw 23 figures and reuse 17, producing
40 figure candidates; that is a source-defined expectation until the returned
archive is inspected.

## Environments

Use each workflow's saved environment record and original checks. There is no
single interchangeable environment for every stage. Historical core/depth
receipts record R 4.5.2; the functional prediction workflow uses PICRUSt2 2.6.3.
The latest plotting workflow preserves its Python 3.11.4 runtime configuration.
Do not infer package compatibility from these top-level version examples alone.

Local validation of this curated archive is limited to source integrity,
Python parsing, shell syntax and documentation checks. No scientific scripts,
actual-data plots or scientific self-tests were run locally during curation.
