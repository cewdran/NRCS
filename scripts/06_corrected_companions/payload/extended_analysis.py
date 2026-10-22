#!/usr/bin/env python3
"""Regenerate companion outputs and add prespecified confirmed-pair screens."""
from hpc_guard import require_hpc
execution=require_hpc()
import analyze as a  # Executes the corrected companion suite once, on Puma.
import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr
import re,json

# Fixed predictors, distinct assays kept separate. No predictor selection by p value.
def predictor(v):
    v=v.lower()
    if 'ph' in v and ('1:1' in v or '1_1' in v) and not 'phosph' in v:return '1:1 pH'
    if 'soluble salt' in v:return '1:1 soluble salts'
    if 'organic matter' in v and 'loi' in v:return 'LOI organic matter'
    if 'organic carbon' in v:return 'Organic carbon'
    if 'total carbon' in v:return 'Total carbon'
    if 'nitrate' in v and ('kcl' in v or 'ppm' in v) and not ('lbs' in v or 'lb/' in v):return 'KCl nitrate-N'
    if 'phosphorus' in v and 'olsen' in v:return 'Olsen phosphorus'
    return None
obs=a.data.copy();obs['predictor']=obs.variable.map(predictor)
obs=obs[obs.predictor.notna() & obs.sample_key.notna() & obs.farm.isin(['Page Springs','Whipstone'])]
assert not obs.duplicated(['sample_key','predictor']).any()
tests=[];joined=[]
for (farm,system,event),ss in obs.groupby(['farm','system','event']):
    expected=['T1','T2','T3','T4'] if farm=='Page Springs' and ss.treatment.isin(['T2','T3','T4']).any() else ['T1','T7','T8','T9'] if farm=='Page Springs' else ['A','B','C','D']
    for (pred,unit),sg in ss.groupby(['predictor','unit']):
        soil=sg.set_index('sample_key')
        for domain in ['bacteria','archaea']:
            for threshold in [0,100]:
                g=a.meta[(a.meta.farm==farm)&(a.meta.microbial_domain==domain)&a.meta.sample_key.isin(soil.index)&
                         (a.meta.target_read_count>0)&(a.meta.target_read_count>=threshold)].sort_values('analysis_sample').copy()
                assert not g.sample_key.duplicated().any()
                g['treatment']=g.sample_key.map(soil.treatment);g['plot']=g.sample_key.str.split('|',regex=False).str[0]
                g['environment_value']=g.sample_key.map(soil.value)
                base=dict(farm=farm,system=system,event=event,domain=domain,minimum_reads=threshold,
                    predictor=pred,unit=unit,sample_set=';'.join(g.analysis_sample),
                    formula='Outcome ~ treatment + log10(target reads) + rank(environment)',
                    permutation_restriction='within treatment',interpretation='Exploratory association; not causal or functional measurement')
                for r in g.itertuples():
                    joined.append(dict(**base,sample_key=r.sample_key,analysis_sample=r.analysis_sample,
                        treatment=r.treatment,value=r.environment_value,record_id=soil.loc[r.sample_key,'record_id'],
                        source_id=soil.loc[r.sample_key,'source_id'],Shannon=r.Shannon,target_reads=r.target_read_count))
                reason=a.eligibility(g,expected)
                if len(g):
                    x=rankdata(g.environment_value.to_numpy(float));x=(x-x.mean())/(x.std() or 1)
                    x0=a.design(g.treatment.to_numpy(),np.log10(g.target_read_count.to_numpy(float)))
                    x1=np.column_stack([x0,x])
                    if len(g)-np.linalg.matrix_rank(x1)<3:reason='Fewer than three residual degrees of freedom'
                else:reason='No eligible confirmed pairs'
                if reason:
                    for outcome in ['Shannon','Bray-Curtis community']:
                        tests.append(dict(**base,outcome=outcome,status='not_tested',reason=reason,n=len(g),p=np.nan))
                    continue
                coords,constant=a.bray_coordinates(a.counts[domain].loc[g.analysis_sample].to_numpy(float))
                for outcome,y in [('Shannon',g.Shannon.to_numpy(float)),('Bray-Curtis community',coords)]:
                    result=a.model(y,x0,x1,g.treatment.to_numpy(),('confirmed_pair_v3',farm,system,event,domain,pred,threshold,outcome))
                    tests.append(dict(**base,outcome=outcome,**result,Lingoes_constant=constant))
result=a.adjust(tests,['farm','system','event','domain','minimum_reads','outcome'])
a.save('confirmed_soil_microbiome_tests',result);a.save('confirmed_soil_microbiome_matches',joined)
(a.BASE.parent/'audit/confirmed_pair_analysis.json').write_text(json.dumps(dict(execution=execution,
    predictors=['1:1 pH','1:1 soluble salts','LOI organic matter','Organic carbon','Total carbon','KCl nitrate-N','Olsen phosphorus'],
    multiple_testing_family='farm/system/event/domain/read threshold/outcome; BH and BY',
    date_source='Researcher-confirmed same-sample matches; exact dates from established 16S metadata',
    not_included='Unverified physical-soil aliquot links; August porewater/November 16S; CC porewater without original source'),indent=2)+'\n')
print('Confirmed-pair screens completed; no forecasts fitted.')

validation=json.loads((a.BASE/'VALIDATION.json').read_text())
validation['confirmed_soil_microbiome']={'rows':len(result),'tested':int(result.p.notna().sum()),'q_BH_lt_05':int((result.q_BH<.05).sum()),'q_BY_lt_05':int((result.q_BY<.05).sum())}
(a.BASE/'VALIDATION.json').write_text(json.dumps(validation,indent=2)+'\n')
