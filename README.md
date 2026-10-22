# NRCS Arizona Three-Farm Soil Microbiome Analysis Scripts

Curated source archive, updated **22 October 2026**, for Oatman Flats Ranch,
Page Springs Cellars, Whipstone, and the cross-farm synthesis. It includes the
successful figure-refresh package associated with **Puma job 24371235**.

This repository contains the selected successful workflow versions, their
configuration and protocol files, and compact provenance/completion records.
Raw and processed datasets, manuscripts, figure files, databases and installed
runtimes are excluded. The full input packages remain necessary for execution.

## Coverage

| Study component | Included work |
|---|---|
| Oatman Flats Ranch | Archaeal and bacterial processing, community and depth diagnostics, corrected soil companions, functional analyses, associations, treatment/time analyses, and the successful supplementary/layout refreshes through the final S7 v5 renderer |
| Page Springs Cellars | Grenache and Petite Sirah analyses, community/function/soil workflows, supplementary figure production, general plot-layout map and latest figure refresh |
| Whipstone | Annual crops, Chester and Natchez analyses, community/function/soil workflows, supplementary figures, general plot-layout map and latest figure refresh; separate retrospective baseline/readiness workflows are also retained |
| Cross-farm synthesis | Shared source audits, comparable within-system analysis workflows, collection-event context, saved system-specific glycolysis changes and selected soil-association displays |

The synthesis preserves the distinct farm, field, crop and sampling designs.
It does not introduce a pooled cross-farm intervention effect. See
[CROSS_FARM.md](CROSS_FARM.md) for the exact scope and script locations.

## Start here

- [WORKFLOWS.md](WORKFLOWS.md): all 23 workflow folders, purposes and recorded jobs.
- [RUNNING.md](RUNNING.md): execution environment, required external inputs and
  original package restoration.
- [PROVENANCE.md](PROVENANCE.md): evidence levels, source recovery and exclusions.
- [CHANGELOG.md](CHANGELOG.md): what changed from the 20 October repository.
- [GITHUB_SETUP.md](GITHUB_SETUP.md): suggested repository name, title and description.

Each workflow has a `READ_THIS_FIRST.md` note. The source files retain their
original names and contents; successful repair versions replace failed
predecessors. Required earlier computation stages remain when a later script
only redraws their saved outputs. Supporting launchers, guards, tests and
helpers belong to the selected workflow version; inclusion does not imply that
every optional entry point was individually run.

## Latest completion evidence

The user-provided `CHECK.sh` output reports job **24371235** as `COMPLETED`,
exit `0:0`, with `RESULTS_READY=true`. The exact delivered
`NRCS_Figure_Refresh_HPC_v2` source is included in
[`scripts/23_figure_refresh_v2`](scripts/23_figure_refresh_v2/READ_THIS_FIRST.md).
Its returned results ZIP has not yet been received for independent checksum,
scientific or visual review. Successful execution is not a claim of completed
figure review or manuscript revision.

Other workflows retain their available completion evidence. Earlier historical
workflows and collector snapshots have narrower evidence than a final Slurm
receipt; those limits are recorded in their individual notes. Original records
are preserved, including preparation-time or review-pending labels.

## Verify the downloaded source archive

From this repository's root directory:

```bash
python3 verify_repository.py
```

This command only checks file hashes. It does not run analyses or submit jobs.
Local packaging validation is recorded in
[`provenance/CURATION_REVIEW.json`](provenance/CURATION_REVIEW.json).

## Project context

The project combines soil measurements, bacterial and archaeal 16S community
data, and predicted relative functional potential. Predicted functions are not
measurements of gene expression, enzyme activity or biogeochemical flux.
Retrospective baseline and feasibility analyses do not establish independent
prospective validation or ten-year forecasting skill.

Project records identify NRCS BioProject **PRJNA1529295** and reused Oatman NIFA
libraries under **PRJNA1518919**. The NIFA reuse statement applies to Oatman,
not automatically to the other farms. Funding agreement: **NR223A750013G005**.

No software license has been selected in the supplied project records. See
[LICENSE_STATUS.md](LICENSE_STATUS.md). This archive does not assign software
ownership, manuscript authorship, a DOI or a publication citation.
