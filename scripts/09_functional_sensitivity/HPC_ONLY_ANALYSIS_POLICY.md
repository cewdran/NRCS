# Standing instruction: scientific analyses run on HPC

Recorded from Dinusha's instruction on 29 September 2026 at 13:32 America/Phoenix.

All future scientific analyses must execute on the HPC itself, with an auditable execution record. This includes model fitting, statistical tests, numerical preprocessing and data-derived figures. Local code preparation, packaging, source inspection, syntax checks and review of returned HPC results remain possible; they are not HPC execution.

The NRCS companion analyses reported earlier on 29 September 2026 ran outside HPC. Their results are **provisional historical results**, pending this Puma rerun and review. Keep their original execution history intact. Do not relabel them as HPC results or cite the rerun as completed until actual Slurm evidence and outputs are available.

The scope of this rerun is every new NRCS companion analysis: soil/crop treatment comparisons, within-plot soil changes, Shannon-diversity tests, Oatman and newly verified Whipstone 2023 soil–microbiome models, the Page Springs bacterial phylum follow-up, repeated-plot summaries, and all eight associated figures. Soil means and target-library diversity inputs are recomputed on Puma. Earlier completed 16S denoising and original community/depth-review jobs retain their existing HPC provenance and are inputs to this phase.

Required records: immutable input/code snapshot; input/output SHA-256 checksums; actual job IDs and node/cluster; timestamps; command logs; exact installed packages; random seed; success/failure status; and scheduler accounting. A submission receipt is not a scheduler state or a completion record.

No new HPC job has been submitted by preparing this package. The package requires execution from the user's Puma terminal.
