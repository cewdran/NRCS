#!/usr/bin/env python3
"""Build the NRCS interpretation report and the remaining-analysis plan."""
from pathlib import Path
from html import escape
from math import comb
import json
import pandas as pd
from consolidate import FARMS, DOMAINS, LABEL, short

ROOT=Path(__file__).resolve().parent
primary=pd.read_csv(ROOT/'tables/primary_results_and_sensitivity.csv')
taxa=pd.read_csv(ROOT/'tables/cohort_taxonomic_profiles.csv')
depth=pd.read_csv(ROOT/'tables/all_depth_results.csv')
decisions=pd.read_csv(ROOT/'tables/sample_decisions.csv')
md=[];html=[]

def h(text,level=2):
    md.append('#'*level+' '+text+'\n');html.append(f'<h{level}>{escape(text)}</h{level}>')
def p(text):
    md.append(text+'\n');html.append('<p>'+escape(text)+'</p>')
def table(headers,rows):
    rows=[[str(v) for v in row] for row in rows]
    md.append('| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(row)+' |' for row in rows)+'\n')
    html.append('<div class="tablewrap"><table><thead><tr>'+''.join('<th>'+escape(x)+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>')
def fig(name,caption):
    assert (ROOT/'figures'/name).is_file()
    md.append(f'![{caption}](figures/{name})\n')
    html.append(f'<figure><a href="figures/{name}"><img src="figures/{name}" alt="{escape(caption)}"></a><figcaption>{escape(caption)}</figcaption></figure>')
def links(path,label):
    md.append(f'[{label}]({path})\n');html.append(f'<p><a href="{path}">{escape(label)}</a></p>')
def num(v):return '—' if pd.isna(v) else f'{v:.4f}'
def farm_stats(farm):
    data=primary[primary.farm.eq(farm)]
    rows=[]
    for _,r in data.iterrows():
        disp=num(r.dispersion_q_BH) if pd.notna(r.dispersion_q_BH) else r.dispersion_status.replace('_',' ')
        rows.append([r.domain,short(r.cohort),int(r.n_samples),num(r.treatment_R2),num(r.treatment_q_BH),disp])
    table(['Domain','Cohort','n','Treatment R²','Treatment q','Dispersion q / status'],rows)

h('NRCS farm-by-farm results and interpretation',1)
p('24 September 2026 · Consolidated exploratory 16S results · Version 1')
p('The primary treatment evidence is concentrated in Page Springs Cellars bacteria. Three cohort-level associations meet q < 0.05 within the existing farm/domain correction families. Oatman Flats Ranch and Whipstone have no primary treatment association below that threshold. Those results do not establish absence of biological effects. Replication, dispersion, collection dates, and sample depth limit the conclusions.')
p('This report combines 38 primary cohort/domain records, 30 completed primary treatment tests, and 228 cohort/domain/depth scenarios. It reviews existing taxonomic and diversity outputs and adds descriptive figures. The original treatment p values and correction families are preserved; no new treatment or taxon-level hypothesis tests were run.')
table(['Farm','Main result','Interpretive limit'],[
    ['Page Springs Cellars','Bacterial treatment associations in Grenache 2023 and Petite Sirah 2023/2024','Grenache 2023 also differs in dispersion; 2024 tests require collection-date adjustment'],
    ['Oatman Flats Ranch','No primary treatment q < 0.05; bacterial November 2023 and December 2024 each have q = 0.0522','Small cohorts; archaeal December 2024 differs in dispersion; one nearly empty bacterial library'],
    ['Whipstone','No primary treatment q < 0.05; crop/year composition patterns are visible','Repeated plots, sparse blackberry replication, mixed dates, and one descriptive unplanted sample']])
fig('01_primary_treatment_results.png','Figure 1. All primary cohorts, including cohorts that could not be tested. Filled points mark treatment q < 0.05 within the original correction family. Orange diamonds flag dispersion q < 0.05.')

h('Sample handling and the working analysis population')
p('The approved metadata contain 171 records for each domain. The documented positive-target baseline contains 170 archaeal and 171 bacterial libraries. Zero-target C094 stays excluded from relative-abundance and distance analyses. C012 remains included. The two nearly empty positive libraries remain explicitly flagged in the baseline and are omitted by the existing 100-read sensitivity. This report does not turn those flags into an undocumented replacement analysis.')
table(['Sample','Input reads','Target reads','Handling'],[
    ['C094, Oatman archaea','198','0','Already excluded: no target reads'],
    ['D094, Oatman bacteria','176','3','Flagged; compare baseline with ≥100-read sensitivity'],
    ['C115, Whipstone archaea','257','4','Flagged; compare baseline with ≥100-read sensitivity'],
    ['C012, Page Springs archaea','20,370','998','Retained; leave-one-out influence documented']])
p('C094 and D094 correspond to the same Oatman collection, OFR_CC_sheep_1_East on 6 December 2024. Their small input libraries provide a technical reason to investigate that collection independently of significance. C115 is the Blackberry_2A collection on 17 August 2025. The read counts do not identify the underlying extraction, amplification, or sample-handling cause.')
p('The nearly empty libraries affect descriptive means as well as plots: omitting D094 lowers the December 2024 Oatman mean Pseudomonadota proportion by 7.72 percentage points, from 38.21% to 30.48%. Omitting C115 lowers the Natchez 2025 mean Candidatus Nitrosocosmicus proportion by 6.40 points, from 61.59% to 55.18%. These are within-cohort sensitivity calculations with equal weight per sample.')
links('tables/sample_decisions.csv','Complete sample-handling ledger')

h('Page Springs Cellars')
farm_stats('PageSpringsCellars')
p('Petite Sirah 2023 bacteria provides the clearest starting point for further treatment interpretation: all four treatments have three distinct plots on one collection date. Treatment R² is 0.3438 and q is 0.0053. Its dispersion q of 0.4683 provides no detected dispersion difference, although a nonsignificant dispersion test does not establish equality.')
p('Grenache 2023 bacteria has treatment R² = 0.4184 and q = 0.0012, accompanied by dispersion q = 0.0272. The association therefore needs to be reported with the possibility that within-treatment spread contributes to it. This result alone cannot identify a specific amendment as producing a consistent centroid shift [1].')
p('Petite Sirah 2024 bacteria has date-adjusted treatment R² = 0.3475 and q = 0.0020. Each treatment has two samples in August and one in November, so date is represented in every treatment. The test adjusts for date and restricts permutations within date; a pooled dispersion test was deliberately omitted. The ordination below shows raw distances and is not a visualization of the date-adjusted residuals.')
p('The samples in these three bacterial cohorts are identical from the positive-target baseline through the 2,000-read threshold. That is stable membership, not independent evidence of robustness. At 5,000 reads, Grenache 2023 and Petite Sirah 2023 each lose three samples and become untestable because a treatment is left with one plot. Petite Sirah 2024 retains all 12 samples. Petite Sirah 2025 has only one control and remains unsuitable for the specified omnibus comparison.')
fig('06_PageSprings_bacterial_cohort_ordinations.png','Figure 2. The three bacterial cohorts meeting the primary treatment q threshold. Cohort-specific axes are independent. The 2024 plot displays both dates, while the statistical test adjusts for date.')
p('The descriptive profiles suggest concrete questions for taxon-level follow-up. In Grenache 2023, mean Bacillota proportions are 48.3% for composted grape pomace plus horse manure, 39.2% for composted horse manure, 8.4% for the fertigation control, and 4.2% for fresh grape pomace. Each mean represents three plots. In Petite Sirah, the horse-manure-plus-pomace means are 38.5% in 2023 and 33.5% in 2024, versus control means of 15.9% and 18.1%. These percentages are descriptive; the 2024 means pool collection dates, and no taxon-specific significance has been established.')
p('The archaeal profiles are dominated by the assigned genera Candidatus Nitrososphaera and Candidatus Nitrosocosmicus plus reads without a genus assignment. No primary archaeal treatment test meets q < 0.05. For Petite Sirah 2023, q changes from 0.2400 to 0.0204 at the 1,000-read sensitivity because C012 alone is removed. The completed influence review supports retaining C012 and reporting this dependence on sample membership.')
fig('03_PageSpringsCellars_composition.png','Figure 3. Page Springs cohort mean profiles. These combine treatments and are descriptive. Archaeal genus and bacterial phylum panels use separate target-domain denominators.')

h('Oatman Flats Ranch')
farm_stats('OatmanFlatsRanch')
p('The November 2023 bacterial effect estimate is relatively large (R² = 0.5492), but its q = 0.0522 does not meet the stated 0.05 threshold. December 2024 bacteria also has q = 0.0522, with R² = 0.3115. Excluding D094 through the 100-read sensitivity changes the December estimate to R² = 0.3880 and q = 0.0542; it does not establish a treatment association. No tested Oatman cohort in either domain crosses q < 0.05 in the reviewed depth scenarios.')
p('Archaeal December 2024 has R² = 0.6822 and q = 0.0832, together with strong dispersion evidence (q = 0.0004). The large R² should not be promoted into a treatment-effect conclusion, especially given the dispersion difference and the loss of evaluable treatment replication at higher depth cutoffs.')
p('Candidatus Nitrososphaera comprises about 62–74% of the mean archaeal profiles across the four cohorts. Pseudomonadota, Acidobacteriota, Actinomycetota, and Bacillota dominate the bacterial phylum overview. Approximately 46–56% of bacterial reads, on a cohort mean basis, lack a genus assignment; any genus-level interpretation must retain this unresolved fraction.')
p('The bacterial ordination shows a pronounced separation of the 2023 collections from the later collections. This is a descriptive observation, not an isolated year effect. The 2025 field has different plot identities and a different treatment set, so it cannot be appended as a third year of the same field experiment. Follow-up should first examine the shared 2023–2024 plots, technical batch information, and collection conditions.')
fig('02_OatmanFlatsRanch_composition.png','Figure 4. Oatman cohort mean profiles. The December 2024 bacterial mean includes D094 in the documented baseline; its influence is quantified separately.')

h('Whipstone')
farm_stats('Whipstone')
p('None of the primary treatment comparisons meets q < 0.05 in either domain. Blackberry Chester 2024 and Natchez 2025 fail the specified minimum replication rule because one treatment has only one plot. The unplanted 2023 sample is a descriptive reference, not a replicated treatment. Several remaining blackberry comparisons have only two plots per treatment, with corresponding limits on dispersion assessment and power.')
p('Composition and ordination show patterns worth following within matched physical plots. The mean Bacillota proportion in annual-crop samples is 33.3% in 2023, 34.6% in 2024, and 58.4% in 2025. The mean Candidatus Nitrosocosmicus proportion in Chester archaeal profiles is 57.1%, 62.4%, and 76.9%, respectively. These are observed changes in cohort averages, not tests of temporal change or treatment responses. Collection dates, changing cohort membership, technical processing, and compositional denominators must be considered.')
p('Annual-crop and blackberry samples also occupy different regions of the within-farm ordinations. Crop identity is tied to field context here, so the layouts do not establish an independent crop effect. The 2025 Natchez archaeal mean needs the explicit C115 sensitivity noted above.')
fig('04_Whipstone_composition.png','Figure 5. Whipstone cohort mean profiles. The single unplanted reference and the C115-containing Natchez cohort remain visibly identified.')

h('Ordination and diversity context')
fig('05_farm_community_ordinations.png','Figure 6. Existing within-farm R ordinations, redrawn from their saved coordinates. Lines connect repeated plot IDs in date order; they do not add independent biological replication.')
p('Axis percentages refer to positive eigenvalues after Lingoes correction [2]. Each panel has its own distance geometry and axes; its position cannot be compared with a different farm/domain panel. The Page Springs bacterial overview is especially compressed into two axes (9.1% and 6.3%), so apparent overlap in that projection cannot negate a full-distance treatment association.')
p('Observed richness and Shannon values are supplied in a cohort summary table as descriptive unrarefied measures. The depth review and rarefaction diagnostics do not establish a single optimal depth standardization. Formal alpha-diversity comparisons remain an explicit follow-up task. Separately amplified bacterial and archaeal libraries cannot be compared as absolute organism abundances, and taxonomy alone does not measure nitrogen cycling or other biochemical activity.')
links('tables/descriptive_diversity.csv','Descriptive richness, Shannon diversity, and sequencing-depth table')
fig('07_C012_influence.png','Figure 7. Previously completed C012 influence analysis. The exact p values in this diagnostic are unadjusted and do not replace the BH-adjusted cohort results.')

h('Focused remaining-analysis plan')
plan=[
    {'priority':1,'task':'Resolve technical QC and design records','scope':'D094/C094 and C115; date-separated patterns at all farms','next_action':'Check sample/extraction/PCR/library records and field randomization or spatial layout. Preserve baseline and >=100-read sensitivity until final QC handling is documented.','dependency':'Technical records and field design details are not present in the reviewed metadata.','status':'Requires additional records for a causal/technical interpretation'},
    {'priority':2,'task':'Focused bacterial taxon-level follow-up','scope':'Page Springs Petite Sirah 2023 first; Petite Sirah 2024 with date adjustment; Grenache 2023 with dispersion qualification','next_action':'Specify omnibus questions and treatment-versus-control effect summaries. Declare taxon filtering, contrasts, and correction families before fitting. Retain unclassified abundance in the descriptive account.','dependency':'Count, taxonomy, and cohort inputs are available; choose and validate a model suitable for compositional data and small n.','status':'Ready for a defined exploratory follow-up'},
    {'priority':3,'task':'Matched-plot temporal review','scope':'Whipstone separately by annual crop/Chester/Natchez; Oatman shared 2023–2024 field; Page Springs by cultivar','next_action':'Use physical plot IDs and actual collection dates; review missing visits and technical batches. Keep the Oatman 2025 field separate.','dependency':'Plot/date mappings are available; technical batch metadata require confirmation.','status':'Design audit can proceed with current inputs'},
    {'priority':4,'task':'Formal alpha-diversity analysis','scope':'Adequately replicated cohorts within farm and crop','next_action':'Define the diversity estimand and depth handling before testing. Preserve repeated-plot structure and retain C012 unless independent QC justifies otherwise.','dependency':'Unrarefied richness/Shannon and count tables are available; no formal alpha tests have been run.','status':'Method and estimand need to be fixed'},
    {'priority':5,'task':'Environmental integration','scope':'Sample-matched soil properties and community outcomes','next_action':'Join measurements by collection identity and date, check units and missingness, then limit predictors to the available independent sample size.','dependency':'The reviewed inputs contain no integrated soil chemistry or environmental measurement table.','status':'Requires the measurement data'},
]
pd.DataFrame(plan).to_csv(ROOT/'tables/follow_up_plan.csv',index=False)
table(['Priority','Task','Next action / dependency'],[[r['priority'],r['task'],r['next_action']+' '+r['dependency']] for r in plan])
assert comb(6,3)==20
p('A useful constraint for the next stage is the small number of plots. For a single-date, unrestricted three-versus-three pairwise PERMANOVA, there are only 20 distinct labelled allocations. Because swapping both group labels leaves the F statistic unchanged, the smallest exact p is 2/20 = 0.10, before multiple-testing adjustment. This is a design calculation, not a test of these samples [3]. Additional Monte Carlo permutations cannot create more independent allocations. Pairwise community tests should therefore not be promised as a way to obtain significant amendment rankings from these cohorts.')
p('The immediate analytical priority is a carefully defined exploratory taxon-level review of the balanced Page Springs bacterial cohorts, accompanied by effect summaries and uncertainty. Temporal patterns provide a separate question for matched-plot analysis. Neither path should reclassify the current sensitivity results as confirmatory evidence.')

h('Methods, evidence, and scope')
p('Treatment and dispersion statistics come from the completed depth-review R run. The report verifies BH adjustment separately for each farm, domain, and depth threshold, with treatment and dispersion as separate families. The positive-target families contain four Oatman tests, four Page Springs tests, and seven Whipstone tests per domain. These are six primary treatment families, not a single correction across all 30 tests. Depth analyses are sensitivity families. Mixed-date models adjust for collection date with within-date permutations; plot repetition across cohorts is not pooled as extra replication.')
p('Taxonomic means are arithmetic means of per-sample target-relative abundances, including zeros for absent taxa; sequencing libraries are not pooled by read count. Genus labels and the unclassified category are preserved from the existing outputs. Composition figures combine treatments within each cohort and are intended as descriptive context. The complete treatment-stratified tables remain available for review. Their 2024 group means are descriptive pooled-date summaries, not date-adjusted abundance estimates.')
p('Validation covered source hashes, all approved sample IDs and target totals, taxonomic count denominators and proportions summing to one, BH correction, and all depth-specific sample sets. The three new Page Springs cohort PCoAs were verified by reconstructing their Lingoes-corrected distances. The implementation also reproduced the saved R eigenvalues and absolute coordinate values for all six farm/domain ordinations; eigenvector sign changes do not alter the geometry. This report adds zero treatment or taxon-level hypothesis tests.')
p('Sources are the completed NRCS count and farm-analysis outputs, the depth-review result folder results_VNJJgJBd (Puma job 24003880), and the completed sampling/influence follow-up. The original archaeal source run is run_20260914T201432Z_3761495; the bacterial recovery/farm-analysis run is run_20260915T193437Z_bacteria_memory_recovery_9633c9d3. The reviewed metadata do not contain extraction/PCR/library batch fields. Randomization, technical causation, environmental mechanisms, absolute abundance, and microbial functions remain outside what these results establish.')
for title,url in [('[1] vegan: adonis2 and dispersion interpretation','https://vegandevs.github.io/vegan/reference/adonis.html'),('[2] vegan: weighted classical scaling and Lingoes correction','https://vegandevs.github.io/vegan/reference/wcmdscale.html'),('[3] permute: complete enumeration of permutation designs','https://gavinsimpson.github.io/permute/reference/allPerms.html')]:
    links(url,title)

h('Tables and reproduction')
for path,label in [('tables/primary_results_and_sensitivity.csv','Primary results with selected depth sensitivities and interpretation notes'),('tables/all_depth_results.csv','Complete 228-row depth grid, including empty-cohort cases'),('tables/design_by_date_and_treatment.csv','Sample sizes by date and treatment'),('tables/top_five_taxa_by_cohort.csv','Top five taxa at each rank within each cohort'),('tables/cohort_taxonomic_profiles.csv','Complete descriptive cohort taxonomic profiles'),('tables/treatment_taxonomic_profiles_descriptive.csv','Complete descriptive treatment profiles'),('tables/composition_near_empty_sensitivity.csv','Composition sensitivity to the near-empty target libraries'),('tables/follow_up_plan.csv','Remaining-analysis plan'),('RESULTS_DRAFT.md','Concise results wording for adaptation')]:links(path,label)
p('The included scripts regenerate the tables, figures, and report from copied source inputs. README.md gives the commands. All PNG figures also have editable SVG counterparts. The detailed source file inventory and validation records accompany the package.')

(ROOT/'REPORT.md').write_text('\n'.join(md))
css='''body{font:17px/1.65 system-ui,-apple-system,Segoe UI,sans-serif;color:#283743;background:#f0f3f5;margin:0}main{max-width:1190px;margin:30px auto;padding:40px 46px;background:#fff;border-radius:10px}h1{font-size:34px;line-height:1.2;color:#173e50}h2{margin-top:42px;font-size:25px;color:#173e50;border-top:1px solid #dce4e8;padding-top:24px}p{max-width:105ch}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.45}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #dbe3e8;vertical-align:top}th{background:#e8f0f3;color:#173e50}tbody tr:nth-child(even){background:#f8fafb}.tablewrap{overflow:auto}figure{margin:32px 0}img{width:100%;height:auto}figcaption{font-size:14px;color:#596973}a{color:#176b8a}a:hover{text-decoration:none}@media(max-width:700px){main{margin:8px;padding:20px}h1{font-size:28px}}@media print{body{background:white}main{margin:0;padding:0}figure{break-inside:avoid}h2{break-after:avoid}}'''
(ROOT/'REPORT.html').write_text('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NRCS farm interpretation</title><style>'+css+'</style></head><body><main>'+''.join(html)+'</main></body></html>')

draft='''# NRCS exploratory results — wording for adaptation

Analyses retained 170 archaeal and 171 bacterial libraries with positive target-domain counts. Community-level treatment associations were evaluated within farm-specific crop/field and collection cohorts. Where cohorts spanned collection dates, models adjusted for date and restricted permutations within date. Benjamini–Hochberg correction was applied across tested cohorts within each farm, domain, and analysis variant, separately for treatment and dispersion tests.

At Page Springs Cellars, bacterial composition was associated with treatment in Grenache 2023 (R² = 0.4184, q = 0.0012), Petite Sirah 2023 (R² = 0.3438, q = 0.0053), and date-adjusted Petite Sirah 2024 (R² = 0.3475, q = 0.0020). Grenache 2023 also showed evidence of unequal multivariate dispersion (q = 0.0272), limiting interpretation as a difference in community location alone. Each of these cohorts contained three plots per treatment. Their membership was unchanged through the 2,000-target-read threshold, so agreement across those thresholds did not provide independent replication. Petite Sirah 2025 was not tested because only one control plot was represented.

No primary archaeal treatment association at Page Springs Cellars met q < 0.05. The Petite Sirah 2023 archaeal result was sensitive to excluding C012, whose 998 target reads placed it immediately below the 1,000-read cutoff. The full-cohort q was 0.2400; the cutoff-based sensitivity q was 0.0204. Leave-one-out diagnostics identified C012 as influential, but did not establish an independent technical reason to exclude it. C012 was therefore retained in the primary exploratory cohort.

At Oatman Flats Ranch, no primary treatment comparison met q < 0.05. Bacterial November 2023 and December 2024 comparisons each had q = 0.0522, with R² values of 0.5492 and 0.3115. Archaeal December 2024 had a large effect estimate (R² = 0.6822) but q = 0.0832 and evidence of unequal dispersion (q = 0.0004). The nearly empty D094 bacterial library was explicitly flagged and examined through depth sensitivity. The 2025 field was treated as a separate field context.

At Whipstone, no primary treatment comparison met q < 0.05. Several blackberry comparisons were limited by only two plots per treatment, and Chester 2024 and Natchez 2025 lacked the required replication in one treatment. Descriptive profiles and ordinations suggested crop- and year-associated structure: the annual-crop mean Bacillota proportion was 33.3%, 34.6%, and 58.4% in 2023, 2024, and 2025, while the Chester mean Candidatus Nitrosocosmicus proportion was 57.1%, 62.4%, and 76.9%. These descriptive changes were not evaluated as independent temporal or crop effects. The single unplanted sample was retained only as a descriptive reference.

Taxonomic profiles summarize relative abundances within separately amplified target domains. They do not establish absolute microbial abundance, biochemical activity, or specific taxon-level treatment effects. Richness and Shannon diversity were reported descriptively from unrarefied counts. Technical batch information, field design, and matched environmental measurements remain relevant to subsequent interpretation.
'''
(ROOT/'RESULTS_DRAFT.md').write_text(draft)
print('Report, results wording, and follow-up plan written.')
