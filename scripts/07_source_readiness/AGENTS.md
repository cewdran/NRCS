# Scientific analysis execution requirement

User instruction, 29 September 2026, 13:32 America/Phoenix: all future scientific analyses must run on the HPC itself so that execution is recorded. The newly produced NRCS companion analyses must be rerun on Puma before their results are treated as authoritative.

- Run scientific computations, model fitting, statistical testing, data-derived figure generation and numerical preprocessing on the user's HPC compute nodes under Slurm.
- Local work may prepare/edit code, package inputs, inspect source files and returned HPC outputs, review figures, or perform syntax/packaging checks. Do not execute scientific workflows or synthetic statistical analyses locally as a workaround.
- Preserve job IDs, input and code checksums, commands, software versions, seeds, scheduler accounting and logs.
- Do not claim a prepared package has been submitted or completed. If no direct HPC access exists, provide the runnable package and exact terminal commands and wait for the user's HPC output.
- Preserve earlier non-HPC output as historical/provisional, clearly distinguished from later HPC output. Do not label copied local results as HPC-derived.

This requirement applies to scientific research analyses, not unrelated writing or publishing tasks.
