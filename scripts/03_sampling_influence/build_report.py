#!/usr/bin/env python3
"""Create readable HTML and Markdown reports from the completed diagnostic tables."""
from pathlib import Path
from html import escape
import pandas as pd

ROOT=Path(__file__).resolve().parent
md=[]
html=[]

def heading(text,level=2):
    md.append('#'*level+' '+text+'\n')
    html.append(f'<h{level}>{escape(text)}</h{level}>')

def para(text):
    md.append(text+'\n')
    html.append('<p>'+escape(text)+'</p>')

def table(headers,rows):
    md.append('| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+
              '\n'.join('| '+' | '.join(map(str,row))+' |' for row in rows)+'\n')
    html.append('<div class="tablewrap"><table><thead><tr>'+''.join('<th>'+escape(str(h))+'</th>' for h in headers)+
                '</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+
                '</tbody></table></div>')

def figure(filename,caption):
    md.append(f'![{caption}](figures/{filename})\n')
    html.append(f'<figure><img src="figures/{filename}" alt="{escape(caption)}"><figcaption>{escape(caption)}</figcaption></figure>')

samples=pd.read_csv(ROOT/'tables/sample_diagnostics.csv')
summary=pd.read_csv(ROOT/'tables/sampling_summary.csv').query("farm=='ALL'").set_index('domain')
loo=pd.read_csv(ROOT/'tables/C012_cohort_leave_one_out.csv')
cohort=pd.read_csv(ROOT/'tables/C012_cohort_sample_diagnostics.csv').set_index('sample_name')
old=pd.read_csv(ROOT/'reference/ALL_ANALYSES_STATUS.tsv',sep='\t')
baseline=loo.set_index('omitted_sample').loc['none']
c012=cohort.loc['C012']

heading('NRCS sampling adequacy and sample influence',1)
para('24 September 2026 · Diagnostic follow-up to the completed NRCS depth review · Version 1')
heading('Decision supported by these diagnostics')
para('Retain C012 in the primary exploratory Petite Sirah 2023 archaeal analysis. The 1,000-target-read cutoff removes one influential plot at 998 reads; these results provide no independent quality-failure criterion for that exclusion. Treat the stronger result after omission as sensitivity to cohort membership. A separate high-depth sample, C014, also substantially influences the result.')
para('The full-cohort treatment association remains inconclusive: the prior R analysis reported p=0.0600 and BH q=0.2400. The new exhaustive test gives unadjusted p=0.05987. Neither influence diagnostics nor flatter rarefaction curves establish a treatment effect or validate an optimal depth cutoff.')

heading('Sampling diagnostics across the project')
rows=[]
for domain in ['archaea','bacteria']:
    s=summary.loc[domain]
    rows.append([domain.capitalize(),int(s.n_positive),f'{s.median_target_reads_positive:,.1f}',
                 f'{s.median_observed_ASVs_positive:.0f}',f'{100*s.median_fraction_observed_at_half_depth:.2f}%',
                 f'{int(s.n_positive_zero_singletons)}/{int(s.n_positive)}'])
table(['Domain','Positive libraries','Median target reads','Median observed ASVs','Median ASVs retained at half depth','Libraries with no singletons'],rows)
para('All 171 approved sample records per domain were checked; the zero-target archaeal library is retained in diagnostic tables but has no curve. Half depth means floor(N/2) reads sampled without replacement. Its percentage uses each library’s observed ASV richness as the denominator, not the number of taxa truly present in soil. Domain assays are evaluated separately.')
para('The curves mostly approach their observed ASV totals. However, 111 of 170 positive archaeal libraries contain no sample-level singleton ASVs. Denoising and target filtering change the rare-count distribution, so high singleton-based coverage summaries and flat curves cannot establish ecological completeness. The official vegan rarefaction documentation explicitly cautions that removing rare taxa can bias rarefaction [1].')
figure('01_all_sample_rarefaction.png','Analytic rarefaction within each observed library. Every curve stops at its available target-read count.')
figure('02_half_depth_richness.png','Expected fraction of observed ASVs retained at half depth. Extremely shallow libraries can also appear fully sampled.')

heading('C012: sampling depth and composition')
para(f'C012 contains {int(c012.target_reads):,} target reads and {int(c012.observed_ASVs)} observed archaeal ASVs, including {int(c012.singleton_ASVs)} singletons and {int(c012.doubleton_ASVs)} doubletons. At 499 reads, expected richness is {c012.expected_ASVs_half:.2f} ASVs ({100*c012.fraction_observed_ASVs_half:.2f}% of its observed richness). At 898 reads, it is {c012.expected_ASVs_ninety_percent:.2f} ASVs ({100*c012.fraction_observed_ASVs_ninety_percent:.2f}%).')
para('Its half-depth fraction is the lowest among the 12 cohort samples, so its smaller library deserves attention. This difference does not establish that the sample is invalid. Its 33 observed ASVs are similar in number to the other horse-manure-plus-pomace plots (32 in C006 and 30 in C008), although taxonomic composition differs. Mean Bray–Curtis distance to its two same-treatment companions is 0.550, the largest such mean in this cohort.')
para('Source tracking records 20,370 input reads, 15,762 filtered reads, 8,169 denoised reads, 7,978 nonchimeric reads, and 998 target reads for C012. Thus low target depth is not equivalent to having sequenced only 998 raw reads. These totals alone cannot determine whether extraction, amplification, contamination, or biological variation explains its composition.')

heading('Every sample omitted in turn')
para('The cohort comprises 12 distinct plot IDs, with three plots per treatment and one collection date (20 October 2023). Each diagnostic omits exactly one sample and retains all four treatments. Bray–Curtis distances use the original relative-abundance transformation; no random rarefaction is used for PERMANOVA.')
rowlist=[]
for _,r in pd.concat([loo[loo.omitted_sample.eq('none')],loo[loo.omitted_sample.ne('none')].sort_values('delta_R2_vs_full',ascending=False)]).iterrows():
    rowlist.append(['None (all 12)' if r.omitted_sample=='none' else r.omitted_sample,
                    '—' if pd.isna(r.omitted_reads) else f'{int(r.omitted_reads):,}',int(r.n_samples),
                    f'{r.R2:.4f}',f'{r.delta_R2_vs_full:+.4f}',f'{r.exact_p_unadjusted:.5f}'])
table(['Omitted sample','Omitted target reads','Remaining n','Treatment R²','Change in R²','Exact unadjusted p'],rowlist)
para('C012 has the largest positive influence on R² when omitted (+0.1414). Omitting C014, which has 7,358 reads, produces the second-largest increase (+0.0885). This shows that the sensitivity cannot be attributed to a low-depth filter alone. With only three plots per treatment, individual compositions materially affect the result.')
para('These exact p values are diagnostic and unadjusted. They are not the previous BH-adjusted q values, and the 12 overlapping omissions are not independent replications. No omission was selected as the primary result. The original 1,000-read sensitivity reported Monte Carlo p=0.0051 and BH q=0.0204; its effect size is identical to dropping C012 here. Small p-value differences arise because the new diagnostic enumerates every allocation.')
figure('03_C012_influence.png','Rarefaction and all 12 leave-one-out diagnostics for Page Springs Cellars Petite Sirah 2023 archaea.')

heading('Separate flags for nearly empty target libraries')
table(['Domain','Sample','Target reads','Interpretation'],[
    ['Archaea','C094','0','Already absent from positive-target treatment analyses'],
    ['Bacteria','D094','3','Only three reads; retain as an explicit QC flag'],
    ['Archaea','C115','4','One observed ASV; a flat curve is not evidence of adequate sampling']])
para('Use the already completed 100-read sensitivity to show the effect of removing the two positive near-empty libraries. The review flag below 100 reads is not a newly validated biological cutoff. Preserve the documented positive-target baseline and report its limitations; any final QC exclusions should be recorded with a technical rationale independent of treatment significance. C012 is retained at both the 100- and 500-read sensitivities.')

heading('Depth thresholds also change the available design')
retention=[]
for depth in [0,100,500,1000,2000,5000]:
    values=[]
    for domain in ['archaea','bacteria']:
        sub=samples[samples.domain.eq(domain)]
        n=int(((sub.target_reads>0)&(sub.target_reads>=depth)).sum())
        tests=old[old.domain.eq(domain)&old.minimum_reads.eq(depth)].treatment_status.eq('tested_exploratory').sum()
        values.extend([n,int(tests)])
    retention.append(['Positive only' if depth==0 else f'≥{depth:,}']+values)
table(['Minimum target reads','Archaeal samples','Archaeal tested cohorts','Bacterial samples','Bacterial tested cohorts'],retention)
para('For the Petite Sirah 2023 archaeal cohort, 2,000 reads leaves seven samples and only one horse-manure-plus-pomace plot, so the previous pipeline correctly declines a treatment test. These losses are changes to the biological comparison, not simply improved sequencing precision. Identical sample membership across lower thresholds is not independent replication of a result.')

heading('Methods and verification')
para('Expected richness was calculated analytically as E[S(m)] = Σᵢ{1 − choose(N − nᵢ, m)/choose(N, m)} for integer 0 ≤ m ≤ N. Each nᵢ is an observed target-ASV read count. Values beyond the observed library size are missing, not extrapolated. The accompanying descriptive singleton summary is 1 − f₁/N, where f₁ is the number of within-sample ASVs with one read; its assumptions are not validated for these processed amplicon counts.')
para('For the one-way distance model, total SS = Σᵢ<ⱼ dᵢⱼ²/N and within-group SS = Σg(Σᵢ<ⱼ in g dᵢⱼ²/ng). R² = 1 − SSwithin/SStotal, and pseudo-F uses the usual k−1 and N−k degrees of freedom. The full cohort has 369,600 distinct assignments of four labelled groups of size three. Each omission has 92,400 assignments of sizes two, three, three, and three. The exact p value is the proportion at least as extreme as observed, including the observed allocation; no Monte Carlo +1 correction is needed. Ties use a documented 10⁻¹² scale tolerance.')
para('Unrestricted treatment-label exchangeability matches the earlier one-date R analysis. Distinct plot IDs do not prove random treatment assignment or eliminate spatial dependence. Interpretation remains exploratory. PERMANOVA can reflect differences in within-group dispersion as well as location [2]. The earlier full-cohort dispersion p=0.5319 and q=0.7805 do not establish equal dispersion; this follow-up does not refit dispersion tests for each omission.')
para('Count totals matched metadata exactly, names were unique and aligned, and counts were nonnegative integers. Rarefaction matched direct integer-combination calculations on a small example, and all curves passed endpoint and monotonicity checks. Exact enumeration matched brute-force label permutations on a small example; Euclidean distance sums of squares matched direct multivariate ANOVA calculations. Full-cohort and C012-omitted R², pseudo-F, and treatment sums of squares matched the prior vegan output with maximum absolute error below 9×10⁻¹⁵. All expected allocation counts were verified. The calculations ran in Python; the reference R outputs came from the completed Puma job.')

heading('Files and reproducibility')
para('Open REPORT.html after extracting the archive. analyze.py regenerates numeric tables, validation records, and the three PNG/SVG figure pairs from the included count and metadata inputs. build_report.py regenerates this report. README.md gives commands, environment.json records versions, and SHA256SUMS.txt covers the delivered files. Farm-wide treatment findings remain those of the completed R depth review included under reference/.')

heading('Method references')
for title,url in [('[1] vegan: rarefaction and limitations','https://vegandevs.github.io/vegan/reference/rarefy.html'),
                  ('[2] vegan: adonis2 and dispersion caveats','https://vegandevs.github.io/vegan/reference/adonis.html'),
                  ('Good (1953): population frequency estimation','https://doi.org/10.1093/biomet/40.3-4.237')]:
    md.append(f'- [{title}]({url})\n')
    html.append(f'<p><a href="{escape(url)}">{escape(title)}</a></p>')

(ROOT/'REPORT.md').write_text('\n'.join(md))
css='''body{font:17px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;color:#263441;background:#f3f5f6;margin:0}main{max-width:1150px;margin:32px auto;padding:40px 48px;background:white;border-radius:12px}h1{font-size:32px;line-height:1.2;color:#173b4c}h2{font-size:23px;margin-top:36px;color:#173b4c}p{max-width:100ch}table{border-collapse:collapse;width:100%;font-size:15px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #dce3e6}th{background:#eaf1f3;color:#173b4c}tbody tr:nth-child(even){background:#f7f9fa}.tablewrap{overflow:auto}img{width:100%;height:auto}figure{margin:30px 0}figcaption{font-size:14px;color:#5a6770}a{color:#176185}@media(max-width:700px){main{padding:22px;margin:8px}h1{font-size:26px}}@media print{body{background:white}main{padding:0;margin:0}figure,table{break-inside:avoid}}'''
(ROOT/'REPORT.html').write_text('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>NRCS sampling adequacy and sample influence</title><style>'+css+'</style></head><body><main>'+''.join(html)+'</main></body></html>')
print('REPORT.md and REPORT.html generated.')
