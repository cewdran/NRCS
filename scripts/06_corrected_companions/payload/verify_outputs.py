#!/usr/bin/env python3
"""Check scientific output integrity on HPC without requiring particular findings."""
from hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
import json,hashlib
import pandas as pd
from PIL import Image
from pypdf import PdfReader
BASE=Path(__file__).resolve().parent
summary=json.loads((BASE/'VALIDATION.json').read_text());checks=[]
for name,expected in summary['inputs'].items():
 assert hashlib.sha256((BASE/'inputs'/name).read_bytes()).hexdigest()==expected,name
checks.append('All frozen inputs match analysis-time SHA-256 hashes.')
for name in ['soil_crop','soil_temporal','alpha_Shannon','environment','Whipstone_environment','PageSprings_phylum','confirmed_soil_microbiome']:
 d=pd.read_csv(BASE/'tables'/f'{name}_tests.csv');tested=d[d.p.notna()]
 assert (tested.status=='tested_exploratory').all()
 assert tested[['p','q_BH','q_BY','partial_R2']].apply(lambda s:s.between(-1e-10,1+1e-10).all()).all(),name
 assert (tested.q_BY+1e-10>=tested.q_BH).all(),name
 assert (tested.residual_df>=3).all(),name
 assert (tested.permutations_evaluated>=19).all(),name
 assert (d.loc[d.p.isna(),'status']=='not_tested').all(),name
 assert summary[name]['tested']==len(tested),name
checks.append('All tested models satisfy p/q/effect-size, residual-df and permutation checks. No particular significance outcome is required.')
obs=pd.read_csv(BASE/'tables/analysis_observations.csv')
assert not obs.duplicated(['farm','system','dataset','event','plot','variable']).any()
assert not obs.record_id.isin(['R0073','R0074','R0302','R0303']).any()
wh=obs[obs.dataset=='Whipstone 2023 Ward soil']
assert wh.record_id.nunique()==28 and (wh.source_id=='S33').all() and (wh.event=='2023-11-16').all()
assert not wh.variable.str.contains('Active Carbon',case=False).any()
assert set(wh[wh.record_id=='R0155'].treatment)=={'B'} and set(wh[wh.record_id=='R0156'].treatment)=={'C'}
assert set(obs[obs.record_id=='R0232'].treatment)=={'D'} and set(obs[obs.record_id=='R0232'].system)=={'Natchez'}
nat=obs[(obs.dataset=='Whipstone 2025 lab')&(obs.system=='Natchez')][['record_id','treatment']].drop_duplicates()
assert nat.groupby('treatment').size().to_dict()=={'A':2,'B':1,'C':2,'D':2}
checks.append('Verified Whipstone 2023 plots and corrected 2025 Natchez D record are selected; composites and single reference records are excluded from trait tests.')
disposition=pd.read_csv(BASE/'tables/record_disposition.csv')
assert disposition.record_id.nunique()==303 and not disposition.record_id.duplicated().any()
assert (disposition[disposition.dataset=='Oatman Soil lab records'].status=='used_via_recomputed_plot_means').all()
checks.append('All 303 source records have one explicit disposition; Haney laboratory inputs are identified as used via plot means.')
alpha=pd.read_csv(BASE/'tables/alpha_Shannon_tests.csv')
for _,g in alpha.groupby(['farm','domain','cohort']):
 sets={int(r.minimum_reads):set(str(r.sample_set).split(';')) for r in g.itertuples()}
 assert sets[100]<=sets[0]
assert alpha.sample_set.fillna('').str.contains(r'(?:^|;)C012(?:;|$)',regex=True).any()
checks.append('Read-filter populations are nested and C012 is retained.')
paired=pd.read_csv(BASE/'tables/matched_plot_changes.csv')
assert (paired.days>0).all() and paired.Bray_Curtis.between(0,1).all()
assert not ((paired.farm=='Oatman Flats')&paired.to_date.str.startswith('2025')).any()
checks.append('Consecutive visits have positive intervals and valid dissimilarities; none crosses Oatman field identity.')
wm=pd.read_csv(BASE/'tables/Whipstone_environment_matched_values.csv')
assert set(wm.event)=={'2023-11-16'} and wm.sample_key.nunique()<=28
assert not wm.sample_key.str.contains('Stateland',case=False).any()
checks.append('New soil–16S environmental matches use only confirmed Whipstone 2023 experimental collections.')
index=pd.read_csv(BASE/'FIGURE_INDEX.csv');assert len(index)==10
for row in index.itertuples():
 with Image.open(BASE/row.png) as im:im.load();assert min(im.size)>1000
 assert (BASE/row.svg).is_file()
pdf=PdfReader(BASE/'NRCS_Companion_Results_2026-09-29.pdf')
assert len(pdf.pages)>=8 and all(len((p.extract_text()or'').strip())>60 for p in pdf.pages)
checks.append('Ten indexed PNG/SVG figure pairs open; the regenerated PDF has populated pages.')
ledger=pd.read_csv(BASE/'FINDINGS_LEDGER.csv')
for name in ['soil_crop','soil_temporal','alpha_Shannon','environment','Whipstone_environment','PageSprings_phylum','confirmed_soil_microbiome']:
 assert (ledger.finding_id=='SUMMARY_'+name).sum()==1
revision=json.loads((BASE.parent/'audit/revision_decisions.json').read_text())
assert not obs.record_id.isin(revision['excluded_records']).any()
assert obs[obs.dataset=='Page Springs 2023 verified Ward soil'].record_id.nunique()==24
assert not obs.variable.str.contains('ACE|Active Carbon',case=False).any()
bio=obs[obs.dataset=='Fresh shoot biomass']
assert len(bio)==6 and not bio['plot'].duplicated().any()
new=pd.read_csv(BASE/'tables/confirmed_soil_microbiome_matches.csv')
assert not new.record_id.isin(revision['excluded_records']).any()
source=json.loads((BASE/'inputs/workbook_data.json').read_text())['tables']['16S samples']
keys={dict(zip(source['columns'],r))['sample_key'] for r in source['rows']}
assert set(new.sample_key)<=keys
checks.append('All intentional exclusions enforced; 24 S1 retained; six plot-level fresh-biomass means; new paired values have established 16S keys.')
out={'execution':execution,'status':'passed','checks':checks,'pdf_pages':len(pdf.pages),'figures':len(index),
 'visual_review':'Pending inspection of the newly generated HPC output; no local statistical or figure execution was performed.'}
(BASE/'OUTPUT_CHECKS.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
