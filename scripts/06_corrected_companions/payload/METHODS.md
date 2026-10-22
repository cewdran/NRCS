# NRCS companion-data analysis, version 2

Version 2 specification recorded on 29 September 2026, before this HPC rerun. This is an exploratory update to prior companion analyses, not a preregistration. Newly enabled Whipstone 2023 predictors are fixed in this package before their model execution.

The unit for soil-only comparisons is a distinct plot within a laboratory submission or field event. Oatman Haney duplicate laboratory records are averaged once per plot/event using the PDF-preferred values in the compiled workbook. Means and their contributing records are not treated as independent observations. No missing-value imputation, unit conversion or pooled-soil disaggregation is performed.

## Scope and eligibility

- Oatman: soil comparisons use 27 plot/events from the shared 2023–2024 field, including the June sheep-East soil record that has no approved 16S counterpart. Soil–microbiome integration uses only confirmed collection matches, at most 26 events, analyzed separately by collection date. The 2025 field is separate. EAST/WEST composites, preliminary water, biomass with unresolved units/subsampling, and IAS Sample-1/2/3 remain descriptive or on hold.
- Page Springs: separate Petite Sirah/Grenache physical-soil comparisons from the 2024 IAS report, and 2025 Petite Sirah nutrient comparisons from the Ward PDF. T5 physical samples are retained as outside the current defined treatment comparison. T2 is steer manure following researcher confirmation; older conflicting descriptions are superseded. Laboratory submission/year does not establish a matching 16S collection date.
- Whipstone 2023: use 28 verified experimental records from the original Ward report, 12 annual plots and eight each for Natchez and Chester. Collection date is 2023-11-16 by researcher confirmation, matching 16S. Annual row labels and blackberry row 2/B and 2/C follow verified PDF identities, not duplicate PODS labels. PDF methods/units replace misheaded PODS columns; organic carbon is not treated as active carbon, and LOI organic matter is not ACE protein. Active carbon is not reported. STATELAND and ASPARAGUS22 remain descriptive; neither enters replicated treatment models.
- Whipstone 2025: separate annual crops, Natchez and Chester using the established row/cultivar mapping. R0232/lab 95559 retains the verbatim R2DBD label but is analyzed as researcher-confirmed R2DBB (row 2/D, Natchez). This restores eight Natchez records, two per treatment. It does not establish a matching 2025 soil/16S collection date.
- Other, unverified PODS field headings, repeated labels and unresolved dates are retained for review; their entries are not treated as a verified nutrient matrix. Page Springs petiole/juice figures have no located sample-level numerical tables.
- Grain elements: zero/below-MDL results are retained in the source and censoring summaries. Inferential comparisons require every available value for an element/year to be explicitly at or above MDL. Otherwise the entire element/year is descriptive only. No deletion of nondetects to manufacture a detected-only treatment comparison.

## Soil and crop treatment comparisons

Exploratory rank-based omnibus F tests compare groups within each dataset/event. At least two observations per expected treatment and at least three residual degrees of freedom are required. Ranking is performed within the tested complete-case cohort. This tests a distributional association, not specifically equality of means. Raw-unit means, medians, SD, ranges and treatment-minus-control median differences accompany tests. Field randomization/spatial independence is not established, so no causal treatment claim is made.

The Oatman Haney panel comprises 24 named physicochemical/biological fields in the analysis script. All numerical IAS physical fields are included. Ward comparisons use PDF-backed numeric scientific measurements, excluding missing markers, lime ratings, buffer placeholders, CSV-only ambiguous fields and duplicate depth-derived water totals. Whipstone 2023 additionally excludes the depth-derived nitrate pounds/acre duplicate and textual lime rating; all other verified numeric fields are retained with their original methods. Constant outcomes are exported as not tested. Grain elements are screened as above; supplied grain yield is compared in its explicitly reported lb/acre unit, with the original area-unit caveat retained.

Oatman soil temporal comparisons use within-plot differences between consecutive 2023–2024 events; treatment groups are compared on these differences. A plot contributes one change per interval. This does not test an overall treatment-by-time interaction or infer a 2025 continuation of the old field.

## Diversity follow-up

The estimand is Shannon diversity of the observed target-filtered library. It is not rarefied richness or coverage-standardized diversity. Within each existing farm/crop/year or event cohort, compare treatment after adjusting for log10 target reads and collection date where needed. Use the positive-target baseline and a separate >=100-read sensitivity; retain C012. C094 remains excluded. The stored `depth_sensitivity_included` flag is not reused because it represents a different threshold. Observed ASV richness remains descriptive because of depth dependence.

Freedman–Lane residual permutations use the reduced model (depth/date), add its fitted values back, and evaluate the nested-model F statistic. Permutations stay within collection date. Groups with fewer than two members, missing expected treatments, rank-deficient added effects or fewer than three residual degrees of freedom are not tested. Complete enumeration of a residual-permutation set does not make nuisance-adjusted inference mathematically exact.

## Oatman environmental integration

Six predictors are fixed in advance: pH, soluble salts, organic matter, CO2-C, available N and available P. Analyze one predictor at a time within event/domain and baseline or >=100-read population. Rank each environmental predictor. The reduced model contains treatment and log10 target reads. Residual permutations are restricted within treatment, assessing within-treatment associations and preserving the treatment structure. This is a small-sample exploratory screen, not a multivariable predictive model.

Outcomes are (1) Shannon diversity and (2) complete positive-axis coordinates of Bray–Curtis distances between target-ASV relative-abundance profiles, with Lingoes correction when needed. The latter is a partial distance-based RDA implemented by nested projection matrices. Report conditional/partial R², F, and the finite permutation resolution. An additional raw Spearman coefficient is descriptive and is not the adjusted model effect. No Page Springs or Whipstone 2025 environment–16S pairing is forced from plot/year alone.

## Newly enabled Whipstone 2023 environmental integration

Use only the 28 verified experimental plot keys and the confirmed 2023-11-16 collection. Keep annual crops, Natchez and Chester separate. Six predictors are fixed before this rerun: 1:1 pH, 1:1 soluble salts, LOI organic matter, organic carbon, KCl nitrate-N and Olsen P. Reuse the reduced treatment/log10-depth model, ranked single predictor, Shannon and Lingoes Bray–Curtis outcomes, within-treatment residual permutations, and >0 / >=100-read populations described above. Every expected A/B/C/D treatment needs two plots and each full model needs at least three residual degrees of freedom; small adjusted blackberry models may therefore be untested. Record reasons rather than dropping covariates to force a test. This is a newly enabled exploratory screen, not a replicated confirmation.

The Ward report's unusually large printed Olsen P values are preserved, not decimal-corrected or removed. Rank-based models do not establish laboratory validity. Laboratory confirmation remains necessary before agronomic interpretation of these concentrations. No 2023–2025 soil temporal test is added because 2025 collection dates are not yet confirmed and laboratory fields/methods differ.

## Focused Page Springs bacterial phylum follow-up

The three cohorts highlighted by the completed community-level analysis are screened: Petite Sirah 2023, Petite Sirah 2024 and Grenache 2023. This is explicitly a post hoc exploratory follow-up. Within a cohort, phyla must have mean relative abundance >=1% and occur in >=50% of samples. Unclassified categories remain in the CLR denominator but are not tested as named phyla. CLR uses all cohort-present phyla after adding 0.5 to counts; repeat with 1 as a sensitivity. Test treatment after date/log-depth adjustment with residual permutations within date. Changes are relative to the phylum geometric mean, not absolute abundance. This does not resolve the previously detected Grenache dispersion difference.

## Repeated-plot 16S review

Pair consecutive observed visits of the same biological plot within farm/system/domain. Record actual dates, elapsed days, Shannon/richness changes and Bray–Curtis turnover. Summaries are descriptive; differing visit intervals, sequencing depth, seasonal changes and missing visits prevent a single pooled temporal effect. The distinct Oatman 2025 plot IDs prevent old/new field pairing.

## Permutations, multiplicity and reproducibility

Seed: 20260929, with stable per-comparison hashes. For the rank-based soil/crop comparisons with only an intercept in the reduced model, enumerate distinct group allocations when there are <=40,320 allocations. For nuisance-adjusted models, enumerate the full allowed row-permutation set when it has <=40,320 members, including identity. Otherwise use 9,999 random permutations and the plus-one p correction. Floating-point comparisons use a small explicit tolerance. Report both the permutation space and the number evaluated.

BH families: soil/crop traits within a dataset/event/system; Oatman soil-change traits within interval; Shannon cohorts within farm/domain/threshold; Oatman environment predictors/events within domain/outcome/threshold; Whipstone 2023 predictors within crop system/domain/outcome/threshold; and the selected phylum/cohort tests across all three Page Springs cohorts separately for each pseudocount. Also provide BY-adjusted q values as a more conservative dependence sensitivity. Skipped tests do not become p=1 or zero. All p/q values and exclusions are exported, not only favorable results.

2025 Oatman CC plots belong to the latest WEST group and NCC/Control plots to EAST. Both had a pre-establishment burn and 4-inch tillage. Treatment and field side therefore cannot be separated by these group comparisons; shared preparation does not provide an unburned/untilled contrast. Numerical summaries are not updated evidence of causality.

## Method references

- Winkler et al. (2014), *Permutation inference for the general linear model*, NeuroImage 92:381–397. https://doi.org/10.1016/j.neuroimage.2014.01.060
- SciPy permutation-test documentation, including exact/random permutation conventions: https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html
- SciPy multiple-testing documentation: https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.false_discovery_control.html
- vegan weighted classical scaling / Lingoes documentation: https://vegandevs.github.io/vegan/reference/wcmdscale.html
- vegan constrained-ordination permutation models: https://vegandevs.github.io/vegan/reference/anova.cca.html

## Source and reporting audit

The byte-identical corrected workbook (version 2) and original Whipstone 2023 PDF are bundled. `prepare_inputs.py` reads the workbook on HPC, validates corrected identities and source-page lab IDs, saves the supplied snapshot, and recomputes all Haney means and count-derived diversity. `record_disposition.csv` covers every source record, distinguishing raw Haney inputs used via aggregation from true holds. Retained measurements remain available.

Historical non-HPC CSVs are comparison references only. Whipstone-related soil output differences and revised holds are expected; unchanged analysis scopes are checked for numerical concordance. No particular p/q result is a verification target. All findings and quantitative summaries are read from current HPC output tables. Figure panels retained from the prior exploratory report are labeled as such; they are not newly prespecified findings.


## Corrected rerun v3, 30 September 2026
Apply source PDFs and decisions D1–D18 on the Slurm compute node after the original source extraction. Original frozen workbook and superseded extracted values remain as provenance; revised JSON is authoritative for this run. Do not merge different carbon assays, impute active carbon/ACE protein, average S1/S2, or count technical or spatial subsamples as independent plots.

Regenerate every companion analysis and ten figures. Preserve established ASV count tables and original 16S core results as prior HPC inputs; no DADA2 rerun is needed for these metadata/chemistry changes. Additional confirmed-pair environmental screens use seven fixed assay-specific predictors, treatment and log10 target reads adjustment, ranks of predictors, within-treatment permutations, and existing minimum-plot/residual-df gates. BH and BY families are farm/system/event/domain/read threshold/outcome. Threshold sensitivity is not independent confirmation.

Biomass: fresh shoot grams, two 8 ft² sampling footprints ten feet apart per plot, averaged within plot on HPC. Control Bermuda grass is distinguished from the cover-crop mixture. Porewater from 3 August 2023 is not joined to November 2023 soil/16S. CC porewater lacks a verified report and remains held. Baseline IAS is excluded; nonbaseline physical data remain soil-only unless independently paired. Forecasts and functional predictions require readiness assessment and are not fitted in this rerun.
