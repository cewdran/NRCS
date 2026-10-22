# Farm coverage and cross-farm synthesis

All three farms are represented. Shared workflows preserve farm and system
boundaries when defining cohorts, treatment contrasts, dates and eligible plots.

| Farm | Physical systems used in the synthesis |
|---|---|
| Oatman Flats Ranch | Shared 2023–2024 field and separate 2025 field |
| Page Springs Cellars | Grenache and Petite Sirah |
| Whipstone | Annual crops, Chester and Natchez |

The Whipstone annual system has crop succession across collection years. The
separate Oatman 2025 field is not a temporal continuation of the shared field.
Treatments with similar code labels are not automatically the same intervention
across farms.

## Included synthesis work

- **Shared analysis foundations:** core processing and farm analyses (01),
  depth sensitivity (02), sampling/interpretation (03–04), corrected companions
  (06), functional workflows (07–11), measured-outcome inventory (14),
  soil associations (17) and treatment/time analyses (18).
- **Collection-event context:** `scripts/22_remaining_manuscripts_v2/extras.py`
  displays the actual microbiome collection events by physical system.
- **Predicted glycolysis comparison:** the same script displays saved
  system-specific paired changes with their original adjustment context.
  Approximate intervals are unadjusted; printed Holm-adjusted p values retain
  the complete original test family. It does not estimate a pooled effect.
- **Selected soil-association examples:** stage 22 extracts exact saved CLR/soil
  pairs and original inference; stage 23 redraws the selected displays while
  preserving the underlying sources and scientific meaning.
- **Current layout code:** `scripts/23_figure_refresh_v2/worker.py` and
  `plot_layouts.py` include the consolidated synthesis presentation alongside
  the Page Springs and Whipstone figures.

Cross-farm synthesis is a comparison of context and system-specific findings.
It is not a fourth independent experiment, a randomized comparison of farms,
a pooled treatment test or a meta-analysis establishing a universal effect.
Differences in direction across systems remain descriptive across those
distinct settings. Selected marginal soil relationships are not
treatment-adjusted estimates or evidence of organism–function coupling.

## Maps and field labels

`scripts/22_remaining_manuscripts_v2/maps.py` produces the Page Springs and
Whipstone general plot-layout maps. User-confirmed manuscript codes remain
authoritative; the supplied plans establish the general arrangement only.
These layouts do not supply invented geographic coordinates or surveyed
distances. They are reused in the latest refresh.

## Figure source chain

Oatman's accepted figure source chain is stages **19 → 20 → 21**. Stage 20's S7
was superseded by stage 21; stage 20 remains needed for its other successful
Shannon and depth displays.

Page Springs, Whipstone and synthesis use stages **22 → 23**. Stage 22 supplies
the data/figure source archive and retained displays; stage 23 supplies the
latest refreshed layouts. Job 24371235 completion is supported by the user's
checker output; its actual returned figures remain to be reviewed.
