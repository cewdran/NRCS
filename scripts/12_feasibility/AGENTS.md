# Scientific feasibility audit execution requirement

Dinusha's standing instruction: scientific computations, numerical preprocessing,
model fitting, statistical tests and data-derived figures run on Puma compute nodes
under Slurm, with execution recorded. This includes eligibility calculations.

- This package performs a ten-year modeling feasibility audit only. Do not fit
  numerical models, generate forecasts or change original scientific data.
- The audit engine is `audit_feasibility.py`; call the real Puma guard before
  computing any data-derived eligibility counts or decisions.
- Local work may edit/package code, inspect source files or returned HPC outputs,
  compile syntax, or verify that guarded entry points refuse non-HPC execution.
  Do not run scientific workflows or synthetic statistical analyses locally.
- Preserve pinned source/code checksums, Slurm job IDs and node/cluster records,
  Python/platform versions, commands, timestamps, exit status, scheduler accounting
  and success/failure artifacts. A submission receipt is not completion evidence.
- Keep immutable prior runs and original source files. Eligibility review remains
  pending until returned audit outputs have been reviewed.
- This is the v2 correction rerun for output-label defects in v1 job 24110010.
  Preserve v1 unchanged. Catalog hold flags must use actual corrected held IDs;
  absent-reason lookups must not manufacture membership. Preserve literal unknown
  method text but keep its assay compatibility unresolved/held. These fixes do
  not authorize model fitting, response harmonization or forecasting.
- No HPC job is submitted merely by preparing this package.
