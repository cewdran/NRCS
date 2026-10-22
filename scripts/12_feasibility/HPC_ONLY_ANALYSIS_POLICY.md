# Standing instruction: scientific analyses run on Puma

Recorded from Dinusha's instruction on 29 September 2026 at 13:32 America/Phoenix.
All scientific analyses must execute on HPC compute nodes under Slurm so that
execution is auditable. This includes numerical preprocessing, statistical tests,
models, figures and feasibility calculations derived from scientific data.

This package audits whether available NRCS observations can support prospective
ten-year modeling work. It performs no model fitting and generates no forecast.
Reviewing audit eligibility does not establish forecast validation or authorize
claiming projected treatment benefits from the current short observation series.

Version 2 repeats the descriptive audit to correct source-catalog hold flags and
unknown-method gate labels found after v1 Puma job 24110010. Keep that run and
all source scientific data immutable. Corrected catalog membership comes from
actual held-record IDs; literal method placeholders remain unknown/held. The
correction is not an assay harmonization pass or approval for later models.

Local code editing, immutable packaging, source inspection, syntax checks,
guard-refusal checks and review of returned HPC outputs are permitted. They must
not be relabeled as HPC execution. Existing source results retain their recorded
provenance and remain unchanged.

Required records include source/code/output checksums, actual job IDs and nodes,
Python/platform and Slurm details, commands, timestamps, runtime exit status,
scheduler accounting and success/failure evidence. Preparing this package does
not submit a job or constitute a completed audit. Eligibility review remains
pending after computational completion.
