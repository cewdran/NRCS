NRCS figure refresh after review of the successful v2 run
22 October 2026

V2 launch repair
The first refresh stopped on the login node before submitting any job.
START_HERE.sh and CHECK.sh now use the login node's python3 for standard-library
orchestration only. The batch runner restores module load python/3.11/3.11.4
before executing the same pinned runtime used by the successful manuscript v2
run. No runtime is rebuilt, installed, replaced or silently substituted.
The plotting worker, layouts, HPC guard, source hash and scientific versions
are unchanged. No layout tests or scientific computations are repeated locally
for this launcher repair. Current launch checks are in LAUNCH_REPAIR_REVIEW.json;
STATIC_AND_SYNTHETIC_REVIEW.json records the earlier unchanged-layout review.

If the original pinned runtime or READY file was deleted, preflight stops
without submitting a job and identifies the missing path. Return that output.
If module setup fails inside a job, CHECK.sh identifies the three small log/
receipt files to return. Do not create a replacement environment blindly.

The v2 return ZIP passed its external SHA-256 checksum and ZIP CRC, and all
594 entries in its internal return manifest verified. All 38 new candidate
figures and the ten older publication figures were visually inspected.
V2 completed its scientific workers, but several displays need corrections
before manuscript insertion. No additional inferential analysis is required.

This is one Puma plotting job. It reads the existing v2 results ZIP at:
/xdisk/dinusha1/dinusha1/NRCS_Remaining_Manuscripts_HPC_v2_run_20261022T193217Z_1a7828f4_results.zip

Do not delete or rename that v2 ZIP. It is pinned to SHA-256:
6cf1ea04c863156a210db3c666dc232a431dcce7915b9c1fb304c7c160b7b692

Upload NRCS_FigRefresh_v2.zip to /xdisk/dinusha1/dinusha1 and run:

cd /xdisk/dinusha1/dinusha1
unzip -n NRCS_FigRefresh_v2.zip &&
bash NRCS_Figure_Refresh_HPC_v2/START_HERE.sh

Check later:
bash /xdisk/dinusha1/dinusha1/NRCS_Figure_Refresh_HPC_v2/CHECK.sh

Run START_HERE.sh once; use CHECK.sh to monitor the submitted job. The refresh
uses account dinusha1, partition standard, two CPUs, 8 GB RAM and a two-hour
wall-time limit. It uses the same pinned Python 3.11.4 and scientific-library
environment as v2, and verifies the actual running Puma allocation.

When RESULTS_READY=true, upload the ZIP identified by RETURN_ZIP and its
RETURN_CHECKSUM file. If the worker fails, return the diagnostic ZIP/checksum.
If no completion record exists after a scheduler failure, return stdout.log,
stderr.log and submission.json from the printed run directory.

What the job does
- Redraws five Shannon, five depth and five PCoA displays with dates as rows
  and domains as columns. Shannon markers receive explicit margin. Depth
  annotations are outside the data axes; log scales avoid the former large
  negative regions. Zero values, if present, remain explicit.
- Uses the exact saved PCoA coordinates and eigenvalues; no distances or
  ordination coordinates are fitted again.
- Redraws six association displays with separate panel legends and clear
  statistics. The Oatman Haney organic-carbon axis uses the saved ppm C unit.
- Adds two measured-soil displays: Grenache fixed-panel trajectories with
  the existing Holm-adjusted tests, and the confirmed 2023 Whipstone soil
  context. The environmental-table source confirms the 2023 exact collection
  date; this does not resolve all later month/year-only environmental labels.
- Reuses the other 17 v2 figures byte for byte, including both layout maps,
  ten family-composition figures, three paired-function figures and two
  synthesis figures. The consolidated result contains 40 candidate figures.
- Keeps every pre-existing scientific CSV/CSV.GZ unchanged and checks its
  hash after plotting. It retains all original correction families and tests.

No ASV processing, functional prediction, CLR estimation, hypothesis testing,
multiple-testing correction or new forecasting model is run. Descriptive
display medians are computed from the unchanged saved values on Puma. No
new analytical treatment, sample or plot-position assignment is introduced.

Layout validation now checks actual drawn text, protected text/legend
intersections, and complete scatter-marker footprints. Eleven invented-data
layout scenarios were tested and visually inspected locally with Matplotlib
3.10.8; tests also verify that overlaps and clipped markers are rejected.
The local synthetic Python was 3.12.14, whereas production remains pinned to
Python 3.11.4 on Puma. No real NRCS figure was regenerated locally.

Actual refreshed outputs still require scientific and visual review after
return. The three manuscripts have not yet been changed. Their subsequent
revision must preserve manuscript treatment codes, use field plans only for
general layout, and omit machine-readable companion inventory sections and
their callouts while retaining scientific figures and editable data tables.

The v2 run and original archives remain untouched. A fresh directory and
submission receipt are made for every launch. The CHECK script verifies the
terminal Slurm state, exit code and returned ZIP hash before declaring ready.
