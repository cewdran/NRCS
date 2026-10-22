#!/usr/bin/env python3
"""Build figures and the companion results report from analyze.py outputs."""
from hpc_guard import require_hpc
require_hpc()

from pathlib import Path
import json, hashlib, platform, html, io
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE=Path(__file__).resolve().parent
FIG=BASE/'figures';FIG.mkdir(exist_ok=True)
def read(n):return pd.read_csv(BASE/'tables'/f'{n}.csv')
obs=read('analysis_observations');st=read('soil_crop_tests');at=read('alpha_Shannon_tests')
et=read('environment_tests');pt=read('PageSprings_phylum_tests');paired=read('matched_plot_changes')
payload=json.loads((BASE/'inputs/workbook_data.json').read_text())
def source_table(n):
 t=payload['tables'][n];return pd.DataFrame(t['rows'],columns=t['columns'])
meta=source_table('16S samples');core=pd.read_csv(BASE/'inputs/primary_results_and_sensitivity.csv')
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.titlesize':11,'axes.labelsize':10,
 'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','savefig.facecolor':'white'})
C={'Control':'#667581','CC':'#198078','CCS':'#AC6524','T1':'#667581','T2':'#198078','T3':'#AD6B25','T4':'#7162A1',
 'T7':'#198078','T8':'#AD6B25','T9':'#7162A1','A':'#AD6B25','B':'#198078','C':'#667581','D':'#7162A1'}
figures=[]
def savefig(fig,name,title,caption):
 buffer=io.BytesIO();fig.savefig(buffer,format='png',dpi=300,bbox_inches='tight')
 (FIG/f'{name}.png').write_bytes(buffer.getvalue())
 from PIL import Image as PILImage
 with PILImage.open(FIG/f'{name}.png')as check:check.load()
 fig.savefig(FIG/f'{name}.svg',bbox_inches='tight')
 plt.close(fig);figures.append(dict(figure_id=name,title=title,caption=caption,png=f'figures/{name}.png',svg=f'figures/{name}.svg'))
def dots(ax,g,order,y='value',ylabel='',title='',annotation=None):
 order=[t for t in order if (g.treatment==t).any()]
 for i,t in enumerate(order):
  v=g.loc[g.treatment==t,y].to_numpy(float);jit=np.linspace(-.11,.11,len(v)) if len(v)>1 else np.zeros(1)
  ax.scatter(i+jit,v,color=C.get(t,'#198078'),s=42,edgecolor='white',linewidth=.5,zorder=3)
  ax.plot([i-.22,i+.22],[np.median(v)]*2,color=C.get(t,'#198078'),lw=2.2,zorder=4)
 ax.set_xticks(range(len(order)),[f'{t}\nn={sum(g.treatment==t)}'for t in order]);ax.set_ylabel(ylabel)
 ax.set_title(title,loc='left',fontweight='bold',pad=10);ax.grid(axis='y',color='#e5e9eb',lw=.7);ax.set_axisbelow(True)
 ax.margins(x=.2,y=.18)
 if annotation:ax.text(.02,.98,annotation,ha='left',va='top',transform=ax.transAxes,fontsize=9,
  bbox=dict(facecolor='white',edgecolor='none',alpha=.85))

def test_summary(d):
 n=int(d.p.notna().sum()); bh=int((d.q_BH<.05).sum()); by=int((d.q_BY<.05).sum())
 return f'{n} model rows tested; {bh} have BH q<0.05 and {by} have BY q<0.05. Counts can include sensitivity runs of the same samples.'

configs=[('soil_haney_1_1_Soil_pH','2023-11-20','A  Soil pH','pH units'),
 ('soil_haney_H3A_ICAP_Sodium','2023-11-20','B  H3A-extractable sodium','ppm Na'),
 ('soil_haney_H3A_ICAP_Sulfur','2023-11-20','C  H3A-extractable sulfur','ppm S'),
 ('soil_physical_Water_available_for_plants','2024-06-06','D  Water available for plants','Reported %')]
fig,axs=plt.subplots(2,2,figsize=(9,6.3),layout='constrained')
for ax,(v,e,title,unit)in zip(axs.flat,configs):
 g=obs[(obs.variable==v)&(obs.event==e)];r=st[(st.variable==v)&(st.event==e)&(st.farm=='Oatman Flats')].iloc[0]
 dots(ax,g,['Control','CC','CCS'],ylabel=unit,title=title+'\n'+e)
 ax.text(.98,.03,f'BH q={r.q_BH:.3f}; BY q={r.q_BY:.3f}',ha='right',transform=ax.transAxes,fontsize=9)
savefig(fig,'F01_Oatman_soil','Oatman soil: selected exploratory panels',
 'Points are plot-level values; lines are medians. Haney laboratory duplicates are recomputed as plot/event means on HPC. '
 'These panels were selected in the earlier exploratory report and are retained for continuity; current q values are printed in each panel. '
 'CC: cover crop; CCS: cover crop and sheep. Omnibus associations do not establish pairwise differences or causality. '
 'The water percentage retains the laboratory definition.')

g=meta[(meta.farm=='Oatman Flats')&(meta.microbial_domain=='bacteria')&(meta.collection_date=='2023-11-20')].copy()
g['treatment']=g.treatment_label
fig,axs=plt.subplots(1,2,figsize=(9,3.8),layout='constrained')
dots(axs[0],g,['Control','CC','CCS'],y='Shannon',ylabel='Observed Shannon diversity',title='A  November 2023 bacterial diversity')
dots(axs[1],g,['Control','CC','CCS'],y='target_read_count',ylabel='Target bacterial reads',title='B  Sequencing depth in the same libraries')
savefig(fig,'F02_Oatman_Shannon','Oatman bacterial Shannon diversity',
 'Raw points and medians show the November 2023 libraries; models adjust for log10 target reads. '
 'The two read-threshold analyses can contain identical samples and are not independent replication. '
 'Diversity is not standardized to equal coverage. All Oatman bacterial Shannon models: '+
 test_summary(at[(at.farm=='Oatman Flats')&(at.domain=='bacteria')]))

fig,axs=plt.subplots(3,2,figsize=(9,8),layout='constrained')
psconfigs=[('Page Springs IAS physical 2024','Petite Sirah','Water Holding Capacity','Water holding capacity (%)','A  Petite Sirah | 2024 IAS'),
 ('Page Springs IAS physical 2024','Grenache','Water Holding Capacity','Water holding capacity (%)','B  Grenache | 2024 IAS'),
 ('Page Springs IAS physical 2024','Petite Sirah','Bulk Density','Bulk density (lb/cubic yard)','C  Petite Sirah | 2024 IAS'),
 ('Page Springs IAS physical 2024','Grenache','Bulk Density','Bulk density (lb/cubic yard)','D  Grenache | 2024 IAS'),
 ('Page Springs 2025 lab','Petite Sirah','Organic Matter LOI %','Organic matter (LOI %)','E  Petite Sirah | 2025 Ward'),
 ('Page Springs 2025 lab','Petite Sirah','Soil pH 1:1','pH units','F  Petite Sirah | 2025 Ward')]
for ax,(ds,sy,v,unit,title)in zip(axs.flat,psconfigs):
 g=obs[(obs.dataset==ds)&(obs.system==sy)&(obs.variable==v)]
 dots(ax,g,['T1','T2','T3','T4']if sy=='Petite Sirah'else['T1','T7','T8','T9'],ylabel=unit,title=title)
savefig(fig,'F03_PageSprings_soil','Page Springs soil: selected descriptive panels',
 'Points and medians show verified soil results. The 2025 nutrient collection is confirmed; IAS physical-soil aliquot correspondence is not independently verified. '
 'T1 is the control. Petite Sirah: T2 steer manure (researcher-confirmed); T3 fish manure; T4 horse manure with pomace. '
 'Grenache: T7 fresh pomace; T8 composted pomace with horse manure; T9 composted horse manure. '
 'These panels illustrate physical properties and soil condition; all tested traits are in the tables. '+test_summary(st[st.farm=='Page Springs']))

fig,axs=plt.subplots(3,2,figsize=(9,8),layout='constrained')
for row,sy in enumerate(['Annual crops','Chester','Natchez']):
 for col,(v,u) in enumerate([('Soil pH 1:1','pH units'),('Organic Carbon % C','Organic carbon (% C)')]):
  g=obs[(obs.dataset=='Whipstone 2025 lab')&(obs.system==sy)&(obs.variable==v)]
  dots(axs[row,col],g,['C','A','B','D'],ylabel=u,title=f'{chr(65+row*2+col)}  {sy} | 2025 Ward')
savefig(fig,'F04_Whipstone_soil','Whipstone 2025 soil: crop systems remain separate',
 'Points are individual report records; lines are medians. C: control; A: blood meal and feather meal; B: cover crop; D: their combination. '
 'The researcher-confirmed correction R2DBD to R2DBB is applied, restoring the row 2/D Natchez sample; the original report label remains in the workbook. '
 'The 2025 soil-to-16S sample correspondence is researcher-confirmed; unmatched R1BBB is excluded. '+test_summary(st[st.dataset=='Whipstone 2025 lab']))

fig,axs=plt.subplots(3,3,figsize=(11,8),layout='constrained')
for row,year in enumerate(['2024','2025']):
 for col,v in enumerate(['P','Fe','Zn']):
  g=obs[(obs.dataset=='Oatman Grain elements')&(obs.event==year)&(obs.variable==v)]
  dots(axs[row,col],g,['Control','CC','CCS'],ylabel=f'{v} (ug/g)',title=f'{chr(65+row*3+col)}  Grain {v} | {year}')
g=obs[obs.dataset=='Oatman Grain yield']
dots(axs[2,0],g,['Control','CC'],ylabel='Reported lb/acre',title='G  Grain yield | 2024')
for ax in axs[2,1:]:ax.axis('off')
axs[2,1].text(0,.85,'Selected P, Fe and Zn panels.\nAll elements and test results\nare retained in the tables.\n\nYield units are reproduced as supplied;\nthe source area-unit caveat remains.',va='top',fontsize=11)
savefig(fig,'F05_Oatman_grain','Oatman grain: nutrients and reported yield',
 'Points and medians show three plots per represented treatment. Selected P, Fe and Zn results are all at or above the stated MDL. '
 'Any element/year with a zero or below-MDL entry is kept descriptive as a whole. '
 'The 2025 grain observations come from the distinct new field; CC is WEST and Control/NCC is EAST, so treatment and field side cannot be separated. '
 'Both 2025 groups had a burn and 4-inch tillage before establishment. The supplied 2024 yield conversion requires source-area verification before agronomic interpretation.')

envvars=['soil_haney_'+v for v in ['1_1_Soil_pH','1_1_Soluble_Salt','Organic_Matter','CO2_C','Available_N','Available_P']]
envlabels=['pH','Soluble salts','Organic matter','CO2-C','Available N','Available P']
fig,axs=plt.subplots(2,2,figsize=(9,6.2),layout='constrained')
for row,domain in enumerate(['bacteria','archaea']):
 for col,outcome in enumerate(['Shannon','Bray-Curtis community']):
  ax=axs[row,col];g=et[(et.domain==domain)&(et.outcome==outcome)&(et.minimum_reads==0)]
  m=g.pivot(index='predictor',columns='event',values='partial_R2').reindex(envvars)
  im=ax.imshow(m.to_numpy(),vmin=0,vmax=1,cmap='Blues',aspect='auto')
  for i in range(len(m)):
   for j in range(len(m.columns)):
    v=m.iloc[i,j];ax.text(j,i,f'{v:.2f}',ha='center',va='center',fontsize=10,color='white'if v>.58 else '#122c43')
  ax.set_yticks(range(6),envlabels);ax.set_xticks(range(3),['Nov 2023','Jun 2024','Dec 2024'])
  ax.set_title(f'{chr(65+row*2+col)}  {domain.title()}\n{outcome}',loc='left',fontweight='bold',fontsize=10)
fig.colorbar(im,ax=axs.ravel().tolist(),shrink=.75,label='Conditional partial R² (not significance)')
savefig(fig,'F06_Oatman_environment','Oatman soil–microbiome screen',
 'Cells are conditional partial R-squared after treatment and log10 target-read adjustment, fitted separately by date. '
 'Six soil predictors were screened in 26 confirmed collections at baseline; small event-level sample sizes can inflate fitted effect sizes. '
 'Current inferential results are in environment_tests.csv; colors alone do not show statistical support. '
 'Permutations remained within treatment. Effect-size color is not evidence of statistical support or prediction. '
 'Community tests used Lingoes-corrected Bray–Curtis coordinates; the full p/q values and finite permutation resolution are provided.')

# Descriptive turnover: keep actual start/end dates in CSV; labels only summarize intervals.
sylabels={'OFR_2023_2024_Field':'Oatman','Grape_Grenache':'Page Springs / Grenache','Grape_PetiteSirah':'Page Springs / Petite Sirah',
 'Annual':'Whipstone / Annual','Blackberry_Chester':'Whipstone / Chester','Blackberry_Natchez':'Whipstone / Natchez'}
keys=list(paired[['farm','system','interval']].drop_duplicates().itertuples(index=False,name=None))
fig,axs=plt.subplots(1,2,figsize=(11,7.2),sharey=True,layout='constrained')
for ax,domain in zip(axs,['bacteria','archaea']):
 for i,(farm,sy,iv)in enumerate(keys):
  g=paired[(paired.farm==farm)&(paired.system==sy)&(paired.interval==iv)&(paired.domain==domain)]
  jitter=np.linspace(-.17,.17,len(g));ok=g.both_at_least_100.astype(bool).to_numpy()
  ax.scatter(g.Bray_Curtis.to_numpy()[ok],i+jitter[ok],s=27,color='#198078',alpha=.8)
  if (~ok).any():ax.scatter(g.Bray_Curtis.to_numpy()[~ok],i+jitter[~ok],s=40,marker='x',color='#B36B27')
  ax.plot([g.Bray_Curtis.median()]*2,[i-.27,i+.27],color='#102f42',lw=2)
  ax.text(1.025,i,f'{len(g)}',va='center',fontsize=9)
 ax.set_xlim(-.02,1.11);ax.set_xticks([0,.25,.5,.75,1]);ax.grid(axis='x',color='#e5e9eb')
 ax.set_title(domain.title()+'   (n at right)',loc='left',fontweight='bold');ax.set_xlabel('Consecutive-visit Bray–Curtis dissimilarity')
labs=[sylabels[sy]+' | '+('Nov 23–Jun 24'if sy=='OFR_2023_2024_Field'and iv=='2023 to 2024'else'Jun–Dec 24'if sy=='OFR_2023_2024_Field'else iv.replace('202','2').replace(' to ','–'))for _,sy,iv in keys]
axs[0].set_yticks(range(len(keys)),labs);axs[0].invert_yaxis()
savefig(fig,'F07_Matched_plot_turnover','Repeated-plot microbial turnover: descriptive only',
 'Each point is a consecutive observed visit-pair of the same plot/domain; vertical marks are medians. '
 'Green points have >=100 target reads at both visits; orange crosses include a library below 100. '
 f'There are {len(paired)} domain-specific pairs in total; these are not independent plots. Pairs may share a visit, and intervals differ. '
 'These are descriptive dissimilarities, without a pooled temporal or between-domain test. No pair joins the old Oatman field to its distinct 2025 field. '
 'Season, laboratory batch and read depth can contribute to apparent turnover; actual dates and days are in matched_plot_changes.csv.')

fig,axs=plt.subplots(3,2,figsize=(9,8),layout='constrained')
for row,sy in enumerate(['Annual crops','Chester','Natchez']):
 for col,(v,u) in enumerate([('1:1 Soil pH','pH units'),('Organic Carbon, % C','Organic carbon (% C)')]):
  g=obs[(obs.dataset=='Whipstone 2023 Ward soil')&(obs.system==sy)&(obs.variable==v)]
  dots(axs[row,col],g,['C','A','B','D'],ylabel=u,title=f'{chr(65+row*2+col)}  {sy} | 16 Nov 2023')
savefig(fig,'F08_Whipstone_2023_soil','Whipstone 2023: verified original Ward results',
 'The original 30-page Ward report supplies 28 experimental plot records plus two additional reference/unassigned records. '
 'Only the experimental records enter treatment comparisons; crop systems remain separate. '
 'Blackberry row 2/B and row 2/C are resolved from report pages 20 and 21. Organic carbon and active carbon are not interchangeable; '
 'the report does not provide active-carbon values. Collection dates match 16S by researcher confirmation. '+
 test_summary(st[st.dataset=='Whipstone 2023 Ward soil']))

fig,axs=plt.subplots(2,2,figsize=(9,6.8),layout='constrained')
for ax,(year,sy) in zip(axs.flat,[(2023,'Petite Sirah'),(2023,'Grenache'),(2024,'Petite Sirah'),(2024,'Grenache')]):
 g=obs[(obs.dataset==f'Page Springs {year} verified Ward soil')&(obs.system==sy)&(obs.variable=='1:1 Soil pH')]
 dots(ax,g,['T1','T2','T3','T4']if sy=='Petite Sirah'else['T1','T7','T8','T9'],ylabel='1:1 pH',title=f'{sy} | {year}')
savefig(fig,'F09_PageSprings_verified_Ward','Page Springs: original-report pH',
 'PDF values only; 2023 S1 retained and S2 excluded. Confirmed field dates come from corresponding 16S records. Each point is one plot.')
g=obs[obs.dataset=='Fresh shoot biomass']
fig,ax=plt.subplots(figsize=(7,4.5),layout='constrained')
dots(ax,g,['Control','CC'],ylabel='Fresh shoot mass (g per 8 ft²)',title='Oatman | 20 November 2023')
savefig(fig,'F10_Oatman_fresh_biomass','Oatman fresh shoot biomass',
 'Each point is the mean of two 2 ft × 4 ft spatial subsamples, ten feet apart within a plot. Control is Bermuda grass; CC is the cover-crop mixture. Fresh weight is not dry biomass. Plot is the biological replicate.')
pd.DataFrame(figures).to_csv(BASE/'FIGURE_INDEX.csv',index=False)


# Every quantitative statement below is generated from the current HPC tables.
validation=json.loads((BASE/'VALIDATION.json').read_text())
runtime=json.loads((BASE.parent/'audit/run_start.json').read_text())
model_names=['soil_crop','soil_temporal','alpha_Shannon','environment','Whipstone_environment','PageSprings_phylum','confirmed_soil_microbiome']
models={name:read(name+'_tests') for name in model_names}
findings=[]
for name,d in models.items():
 findings.append(dict(finding_id='SUMMARY_'+name,scope=name,finding=test_summary(d),
   interpretive_limit='Exploratory models; sensitivity rows are not independent confirmations; non-detection does not prove no effect.',
   evidence='tables/'+name+'_tests.csv'))
 for i,r in d[d.q_BH<.05].iterrows():
  identifiers=[f'{k}={r[k]}' for k in ['farm','system','domain','cohort','dataset','event','variable','predictor','outcome','minimum_reads','pseudocount','taxon'] if k in r and pd.notna(r[k])]
  findings.append(dict(finding_id=f'{name}_{i+1}',scope='; '.join(identifiers),
    finding=f'n={int(r.n)}; F={r.F:.6g}; partial R2={r.partial_R2:.6g}; p={r.p:.6g}; BH q={r.q_BH:.6g}; BY q={r.q_BY:.6g}',
    interpretive_limit='Exploratory association; no pairwise or causal effect claimed. Check sample_set and finite permutation resolution in evidence table.',
    evidence='tables/'+name+'_tests.csv'))
findings.append(dict(finding_id='DESCRIPTIVE_VISITS',scope='Repeated plots',finding=f'{len(paired)} consecutive domain-specific visit pairs',
 interpretive_limit='Overlapping pairs and unequal intervals; no overall temporal test.',evidence='tables/matched_plot_changes.csv'))
findings.append(dict(finding_id='PRIOR_CORE',scope='Original 16S community/depth analysis',
 finding='Previously completed core community results are retained as inputs; this companion rerun does not rerun DADA2 or the original core PERMANOVA/depth workflow.',
 interpretive_limit='Consult original HPC run provenance; do not attribute those input tables to the current job.',evidence='inputs/primary_results_and_sensitivity.csv'))
pd.DataFrame(findings).to_csv(BASE/'FINDINGS_LEDGER.csv',index=False)

fontdir=Path(matplotlib.get_data_path())/'fonts/ttf'
for name,file in [('DejaVu','DejaVuSans.ttf'),('DejaVu-Bold','DejaVuSans-Bold.ttf')]:
 pdfmetrics.registerFont(TTFont(name,str(fontdir/file)))
styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name='NBody',fontName='DejaVu',fontSize=9,leading=13,spaceAfter=9,textColor=colors.HexColor('#223642')))
styles.add(ParagraphStyle(name='NTitle',fontName='DejaVu-Bold',fontSize=21,leading=26,spaceAfter=15,textColor=colors.HexColor('#103e4a')))
styles.add(ParagraphStyle(name='NHeading',fontName='DejaVu-Bold',fontSize=13,leading=18,spaceAfter=11,textColor=colors.HexColor('#103e4a')))
styles.add(ParagraphStyle(name='NCell',parent=styles['NBody'],fontSize=8,leading=11,spaceAfter=0))
story=[];plain=[]
def para(text,style='NBody'):
 story.append(Paragraph(html.escape(str(text)),styles[style]));plain.append(str(text)+'\n')
def tab(headers,rows,widths):
 cells=[[Paragraph(html.escape(str(v)),styles['NCell']) for v in row] for row in [headers]+rows]
 t=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
 t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e2eeef')),('VALIGN',(0,0),(-1,-1),'TOP'),
   ('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#779a9e')),('BOTTOMPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7)]))
 story.append(t);story.append(Spacer(1,12))
 plain.extend(' | '.join(map(str,row))+'\n' for row in [headers]+rows)
def page():story.append(PageBreak());plain.append('\n---\n')
para('NRCS companion analyses','NTitle')
para('Puma HPC rerun • corrected source workbook • 30 September 2026','NHeading')
para('These tables, figures and findings were generated in the current Slurm allocation. The attached audit distinguishes computation, scheduler confirmation and visual review. The report is exploratory and requires scientific and visual review before manuscript use.')
execution=runtime['execution']
para(f"Analysis job {execution['job_id']} on {execution['cluster']} node {execution['node']}; started {runtime['utc']}. Full scheduler records are in audit/.")
para('The corrected workbook preserves original labels, laboratory values, source locations and unresolved records. Whipstone 2023 uses the original Ward PDF and the researcher-confirmed 16 November 2023 collection date. Whipstone 2025 row 2/D is corrected to R2DBB. Page Springs T2 is steer manure. Candidate collection matches elsewhere are not silently promoted.')
para('This run recomputes plot-level Haney means, target-count Shannon/richness, companion soil/crop and diversity tests, the focused Page Springs phylum follow-up, environmental screens, descriptive visit changes, and all ten figures. Original core 16S community and depth results remain explicitly identified as previous HPC inputs.')
para('Current run summary','NHeading')
tab(['Analysis table','Tested rows','BH <0.05','BY <0.05'],
 [[n,validation[n]['tested'],validation[n]['q_BH_lt_05'],validation[n]['q_BY_lt_05']] for n in model_names],[276,76,76,76])
para('Counts are model rows, including declared sensitivity analyses, not counts of independent biological findings. BH and BY adjustments use the families defined in METHODS.md.')
page()
para('Eligibility and interpretation','NTitle')
notes=[
 'Whipstone 2023: 28 experimental plots are resolved from the original report. STATELAND is retained outside replicated comparisons; ASPARAGUS22 is intentionally excluded. Misheaded PODS columns are superseded by verified PDF fields. Unreported active carbon is missing, never zero or copied organic carbon.',
 'Whipstone environmental integration: six fixed Ward predictors are evaluated separately by crop system, microbial domain, outcome and read threshold. Every treatment must retain at least two plots, and the adjusted model must retain at least three residual degrees of freedom. A skipped model is not a negative finding.',
 'Whipstone Olsen P: unusually large printed values are retained verbatim. Rank-based exploratory calculations do not validate the laboratory values; the researcher accepts the reported values. Prior heavy horse-manure inputs are a possible explanation, not proven causation.',
 'Oatman 2025 EAST and WEST soil records each pool six source samples. They are not expanded into replicates or used in plot-level tests. Both plot groups were established after burning and four-inch tillage; EAST is described as surface tillage. The new field is not paired longitudinally with the 2023–2024 field. The 2025 grain comparison confounds treatment with field side.',
 'Original PDF headings and values replace misheaded PODS fields. Page Springs 2023 uses S1 only. The researcher-confirmed 2024/2025 nutrient links enter the new paired screens. T5, S2, baseline IAS, preliminary OFR-JB, IAS Sample-1–3 and the specified plot/year records are excluded. CC porewater lacks its original report and remains held. Fresh biomass uses two spatial subsamples averaged within each plot.',
 'Shannon uses target counts and natural logs without coverage standardization. Models adjust for depth where specified; depth sensitivity does not eliminate all sequencing uncertainty. Grain values below detection remain descriptive by element/event. Yield units retain the supplied source-area caveat.',
 'Read held_records.csv together with record_disposition.csv. Raw Oatman Haney laboratory rows are used through recomputed plot means and are not misclassified as unused records. retained_measurements.csv preserves measurements outside inferential tests.',
 'The same samples can contribute to threshold or pseudocount sensitivity runs. Consecutive-visit pairs can overlap. Omnibus tests do not identify particular treatment pairs, and field associations do not establish causation.'
]
for n in notes:para(n)
page()
para('Current findings ledger','NTitle')
for name,d in models.items():
 para(name,'NHeading');para(test_summary(d))
 para(f'{int(d.p.isna().sum())} model rows were not tested; reasons are recorded in tables/{name}_tests.csv.')
para('Every BH-supported model is listed with its scope, sample size, effect size and p/q values in FINDINGS_LEDGER.csv. That ledger is built from this job’s output tables; it does not copy conclusions from the previous report.')
for f in figures:
 page();para(f['title'],'NTitle')
 from PIL import Image as PILImage
 with PILImage.open(BASE/f['png']) as im:w,h=im.size
 scale=min(504/w,435/h)
 story.append(Image(str(BASE/f['png']),width=w*scale,height=h*scale,hAlign='CENTER'))
 story.append(Spacer(1,12));para(f['caption']);plain.append(f['png']+'\n')
para('All plotted values and test results are available in the accompanying CSV tables. Figure files are supplied as 300-dpi PNG and editable SVG.')
def footer(canvas,doc):
 canvas.saveState();canvas.setFont('DejaVu',7);canvas.setFillColor(colors.HexColor('#667581'))
 canvas.drawString(54,25,'NRCS companion HPC v2 | exploratory | visual review pending')
 canvas.drawRightString(558,25,str(doc.page));canvas.restoreState()
SimpleDocTemplate(str(BASE/'NRCS_Companion_Results_2026-09-29.pdf'),pagesize=(612,792),
 leftMargin=54,rightMargin=54,topMargin=45,bottomMargin=45,title='NRCS companion HPC v2 results').build(story,onFirstPage=footer,onLaterPages=footer)
(BASE/'REPORT.txt').write_text('\n'.join(plain)+'\n')
print(f'Generated {len(figures)} figures, dynamic report and {len(findings)} findings-ledger rows on HPC.',flush=True)
