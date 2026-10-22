#!/usr/bin/env python3
"""Reproduce NRCS companion analyses from the frozen, supplied inputs."""
from hpc_guard import require_hpc
require_hpc()

from pathlib import Path
from collections import defaultdict
from itertools import permutations, product, combinations
import json, hashlib, math, sys, platform, re
import numpy as np
import pandas as pd
import scipy
from scipy.stats import rankdata, false_discovery_control, spearmanr, f_oneway
from scipy.spatial.distance import pdist, squareform

BASE=Path(__file__).resolve().parent; IN=BASE/'inputs'; OUT=BASE/'tables'; OUT.mkdir(exist_ok=True)
SEED=20260929; NPERM=9999; MAX_EXACT=40320
payload=json.loads((IN/'workbook_data.json').read_text())
def table(n):
 t=payload['tables'][n];return pd.DataFrame(t['rows'],columns=t['columns'])
meta=table('16S samples');records=table('Sample records');measurements=table('Measurements');means=table('Oatman soil means')
for col in ['value','reporting_limit']:measurements[col]=pd.to_numeric(measurements[col],errors='coerce')
ref=meta[meta.microbial_domain=='bacteria'].set_index('sample_key')
counts={d:pd.read_csv(IN/f'{d}_counts.tsv.gz',sep='\t',index_col=0)for d in ['bacteria','archaea']}
for domain,c in counts.items():
 m=meta[(meta.microbial_domain==domain)&(meta.target_read_count>0)]
 assert np.allclose(c.loc[m.analysis_sample].sum(axis=1),m.target_read_count.to_numpy()),domain
 assert (c.to_numpy()>=0).all()
PERMS={};checks=[]
def rng(key):return np.random.default_rng(SEED+int(hashlib.sha256(str(key).encode()).hexdigest()[:12],16))
def design(*cols,n=None):
 if n is None:n=len(cols[0])
 a=[np.ones((n,1))]
 for c in cols:
  s=pd.Series(c)
  if pd.api.types.is_numeric_dtype(s):
   v=s.to_numpy(float);sd=np.std(v)
   if sd>1e-12:a.append(((v-v.mean())/sd)[:,None])
  else:
   a.append(pd.get_dummies(s.astype(str),drop_first=True,dtype=float).to_numpy())
 return np.column_stack(a)
def projection(x):return x@np.linalg.pinv(x)
def pset(strata,key,label_groups=None):
 s=np.asarray(strata);groups=[np.flatnonzero(s==v)for v in np.unique(s)];n=len(s)
 signature=(tuple(map(str,s)),str(key),tuple(label_groups)if label_groups is not None else None);cache=PERMS.get(signature)
 if cache is not None:return cache
 space=math.prod(math.factorial(len(g))for g in groups);mode='all row permutations'
 if label_groups is not None:
  labels=np.asarray(label_groups);blocks=[np.flatnonzero(labels==v)for v in np.unique(labels)]
  space=math.factorial(n)//math.prod(math.factorial(len(b))for b in blocks)
 else:blocks=None
 if blocks is not None and space<=MAX_EXACT:
  allocations=[]
  def rec(available,parts):
   k=len(parts)
   if k==len(blocks)-1:
    perm=np.arange(n)
    for block,chosen in zip(blocks,parts+[available]):perm[block]=chosen
    allocations.append(perm);return
   for chosen in combinations(available,len(blocks[k])):rec(tuple(i for i in available if i not in chosen),parts+[chosen])
  rec(tuple(range(n)),[]);a=np.asarray(allocations);assert len(a)==space;exhaustive=True;mode='all unique group allocations'
 elif space<=MAX_EXACT:
  a=np.tile(np.arange(n),(space,1))
  for i,items in enumerate(product(*(list(permutations(g))for g in groups))):
   for g,p in zip(groups,items):a[i,g]=p
  exhaustive=True
 else:
  a=np.tile(np.arange(n),(NPERM,1));rg=rng(key)
  for i in range(NPERM):
   for g in groups:a[i,g]=rg.permutation(g)
  exhaustive=False;mode='Monte Carlo plus-one'
 result=(a,exhaustive,space,mode);PERMS[signature]=result;return result
def model(y,x0,x1,strata,key,label_groups=None):
 y=np.asarray(y,float);y=y[:,None]if y.ndim==1 else y
 n=len(y);r0=np.linalg.matrix_rank(x0);r1=np.linalg.matrix_rank(x1);df=r1-r0;res=n-r1
 base=dict(n=n,effect_df=int(df),residual_df=int(res),F=np.nan,p=np.nan,partial_R2=np.nan,
  permutations_evaluated=0,permutation_space=None,permutation_mode=None,status='not_tested',reason='')
 if df<1 or res<3:base['reason']='No estimable added effect or fewer than three residual degrees of freedom';return base
 if np.linalg.norm(y-y.mean(axis=0))<1e-11:base['reason']='Constant outcome';return base
 h0=projection(x0);h1=projection(x1);a=h1-h0;b=np.eye(n)-h1
 ss0=float(np.sum(y*((np.eye(n)-h0)@y)));sse=max(0,float(np.sum(y*(b@y))));sseff=max(0,ss0-sse)
 if ss0<1e-12:base['reason']='No variation after reduced-model adjustment';return base
 f=(sseff/df)/(sse/res) if sse>1e-12 else np.inf
 indices,exact,space,mode=pset(strata,key,label_groups);fit=h0@y;resid=y-fit;yp=fit[None,:,:]+resid[indices]
 num=np.einsum('bik,ij,bjk->b',yp,a,yp,optimize=True)/df
 den=np.einsum('bik,ij,bjk->b',yp,b,yp,optimize=True)/res
 with np.errstate(divide='ignore',invalid='ignore'):fp=np.divide(np.maximum(num,0),np.maximum(den,0))
 extreme=int(np.sum(fp>=f-max(1e-10,abs(f)*1e-10)))if np.isfinite(f)else int(np.sum(np.isinf(fp)))
 p=extreme/len(indices)if exact else (extreme+1)/(len(indices)+1)
 base.update(F=float(f),p=p,partial_R2=sseff/ss0,permutations_evaluated=len(indices),permutation_space=str(space),
  permutation_mode=mode,status='tested_exploratory')
 return base
def adjust(rows,family):
 d=pd.DataFrame(rows)
 if len(d)==0:return d
 d['q_BH']=np.nan;d['q_BY']=np.nan;d['BH_family_n']=0
 for key,g in d.groupby(family,dropna=False):
  ii=g.index[g.p.notna()&np.isfinite(g.p)]
  if len(ii):
   d.loc[ii,'q_BH']=false_discovery_control(d.loc[ii,'p'].to_numpy(),method='bh')
   d.loc[ii,'q_BY']=false_discovery_control(d.loc[ii,'p'].to_numpy(),method='by');d.loc[g.index,'BH_family_n']=len(ii)
 return d
def save(name,d):
 if not isinstance(d,pd.DataFrame):d=pd.DataFrame(d)
 d.to_csv(OUT/f'{name}.csv',index=False);return d
def eligibility(g,expected):
 c=g.groupby('treatment').size()
 if set(c.index)!=set(expected):return 'An expected treatment is absent'
 if c.min()<2:return 'An expected treatment has fewer than two distinct plots'
 if g['plot'].duplicated().any():return 'Repeated plot within comparison'
 return ''

# Verify nested model mechanics against an independent one-way ANOVA calculation.
y=np.array([1.,2,4,7,8,10,11,13,16]);tr=np.repeat(['a','b','c'],3)
t=model(y,design(n=9),design(tr),np.repeat('all',9),'validation_anova')
assert np.isclose(t['F'],f_oneway(y[:3],y[3:6],y[6:]).statistic)
checks.append('Projection F matches independent SciPy one-way ANOVA.')
yy=np.arange(1.,7.);tt=np.repeat(['a','b'],3)
zz=model(yy,design(n=6),design(tt),np.repeat('all',6),'validation_exact',label_groups=tt)
assert zz['permutations_evaluated']==20 and np.isclose(zz['p'],.1)
checks.append('Exact two-group allocation test agrees with the 2/20 tail probability.')

HAN=[
 '1_1_Soil_pH','1_1_Soluble_Salt','Organic_Matter','CO2_C','H2O_Total_Organic_C','H2O_Total_N','H2O_Organic_N',
 'H3A_Nitrate','H3A_Ammonium','H3A_Inorganic_Nitrogen','H3A_Total_Phosphorus','H3A_Inorganic_Phosphorus','H3A_Organic_Phosphorus',
 'H3A_ICAP_Potassium','H3A_ICAP_Calcium','H3A_ICAP_Magnesium','H3A_ICAP_Sodium','H3A_ICAP_Sulfur','H3A_ICAP_Zinc',
 'H3A_ICAP_Manganese','H3A_ICAP_Iron','H3A_ICAP_Copper','H3A_ICAP_Aluminum','Soil_Health_Calculation']
HAN=['soil_haney_'+s for s in HAN];assert len(HAN)==24
ENV=['soil_haney_'+s for s in ['1_1_Soil_pH','1_1_Soluble_Salt','Organic_Matter','CO2_C','Available_N','Available_P']]
obs=[];holds=[];dispositions=[]
# Two within-plot spatial subsamples are averaged on HPC; plot remains n=1.
biomass=records[records.record_id.isin([f'R{i:04}' for i in range(278,290)])]
for plot, rr in biomass.groupby('original_sample_label'):
 values=measurements[measurements.record_id.isin(rr.record_id)].value.dropna().to_numpy(float)
 assert len(rr)==2 and len(values)==2,(plot,len(values))
 treatment='Control' if plot.startswith('Control') else 'CC'
 obs.append(dict(farm='Oatman Flats',system='Cover crop and control shoots',dataset='Fresh shoot biomass',
  event='2023-11-20',plot=plot,treatment=treatment,variable='Fresh shoot biomass per 8 square feet',
  value=float(values.mean()),unit='g fresh weight / 8 ft2',sample_key=None,
  source_id='S20;researcher D15/D17',record_id=';'.join(rr.record_id),censored=False))
 for rid in rr.record_id:
  dispositions.append(dict(record_id=rid,dataset='Fresh shoot biomass',status='used_via_recomputed_plot_means',reason='Two fresh-weight spatial subsamples per plot; 8 ft2 each; 10 ft apart'))
for _,r in means[means.variable.isin(HAN)].iterrows():
 obs.append(dict(farm='Oatman Flats',system='Shared 2023–2024 field',dataset='Haney soil',event=r.collection_date,
  plot=r.sample_key.split('|')[0],treatment=r.treatment,variable=r.variable,value=r.mean_value,unit=r.unit,
  sample_key=r.sample_key,source_id=r.source_file,record_id=';'.join(str(r.lab_ids).split(';')),censored=False))
for _,r in records.iterrows():
 ds=r.dataset;subset=measurements[measurements.record_id==r.record_id];tr=None;system=r.system;plot=r.original_sample_label
 if r.intentional_exclusion:
  holds.append(dict(record_id=r.record_id,dataset=ds,reason=r.intentional_exclusion));continue
 if r.record_id in set(biomass.record_id):continue
 # Newly verified Ward records use the confirmed original field sample key.
 if r.preferred_pdf_source_id in ['S35','S36','S37']:
  assert r.confirmed_sample_key in ref.index,(r.record_id,r.confirmed_sample_key)
  key=r.confirmed_sample_key;plot=key.split('|')[0];event=r.collection_date
  if r.farm=='Page Springs':
   tr=re.search(r'(?:^|_)T(\d+)',plot)[0].lstrip('_');system=r.system
   assert tr in ['T1','T2','T3','T4','T7','T8','T9'],tr
  else:
   mat=re.fullmatch(r'(Blackberry|Annual_plot)_(\d+)([ABCD])',plot);assert mat,plot
   tr=mat[3];system=('Natchez'if int(mat[2])<=2 else 'Chester')if mat[1]=='Blackberry'else 'Annual crops'
  ds=r.farm+' '+str(int(r.year))+' verified Ward soil'
  for _,m in subset.iterrows():
   if pd.isna(m.value) or 'lbs' in m.variable.lower() or 'lb/' in m.variable.lower():continue
   obs.append(dict(farm=r.farm,system=system,dataset=ds,event=event,plot=plot,treatment=tr,
    variable=m.variable,value=m.value,unit=m.unit,sample_key=key,record_id=r.record_id,
    source_id=r.preferred_pdf_source_id,censored=False))
  continue
 event=str(int(r.year)) if pd.notna(r.year) else 'Undated'
 use=ds in ['Oatman Soil physical','Oatman Grain elements','Oatman Grain yield','Page Springs IAS physical 2024','Page Springs 2025 lab','Whipstone 2025 lab']
 ward2023=(ds=='Whipstone PODS soil' and r.preferred_pdf_source_id=='S33' and r.confirmed_sample_key in ref.index and r.collection_date=='2023-11-16')
 use=use or ward2023
 if ds=='Oatman Soil lab records':
  dispositions.append(dict(record_id=r.record_id,dataset=ds,status='used_via_recomputed_plot_means',reason='Source laboratory measurements feed plot/event Haney means; not additional biological replicates'))
  continue
 if not use:
  reason='Unverified plot identity, measurement headings/units, or comparison structure; see workbook Issues and Sample records'
  if r.record_id in ['R0073','R0074']:reason='One six-member 2025 soil composite per field side; no independent plot-level soil values'
  if r.record_id=='R0302':reason='ASPARAGUS22: no established experimental plot or 16S match; retained descriptively'
  if r.record_id=='R0303':reason='STATELAND: confirmed reference collection, not a replicated treatment group'
  holds.append(dict(record_id=r.record_id,dataset=ds,reason=reason));continue
 if r.farm=='Oatman Flats':
  if r.confirmed_sample_key in ref.index:tr=ref.loc[r.confirmed_sample_key,'treatment_label']
  elif 'sheep'in r.original_sample_label.lower():tr='CCS'
  if ds=='Oatman Soil physical':event='2024-06-06';system='Shared 2023–2024 field'
  else:system='Wheat harvest '+event
 elif r.farm=='Page Springs':
  mat=re.search(r'(?:^|[-_])T(\d+)',plot);tr='T'+mat[1]if mat else None
  if tr=='T5':holds.append(dict(record_id=r.record_id,dataset=ds,reason='T5 outside the current defined comparison; no treatment-definition inference'));continue
  event=event+' laboratory submission'
 else:
  if ward2023:
   plot=r.confirmed_sample_key.split('|')[0]
   mat=re.fullmatch(r'(Blackberry|Annual_plot)_(\d+)([ABCD])',plot)
   assert mat,plot
   tr=mat[3];system=('Natchez' if int(mat[2])<=2 else 'Chester') if mat[1]=='Blackberry' else 'Annual crops'
   event='2023-11-16';ds='Whipstone 2023 Ward soil'
  else:
   # The researcher corrected the verbatim PDF label R2DBD to R2DBB.
   if r.record_id=='R0232':plot='R2DBB'
   mat=re.fullmatch(r'R(\d+)([ABCD])(BB)?',plot)
   if mat:tr=mat[2];system=('Natchez'if int(mat[1])<=2 else 'Chester')if mat[3]else 'Annual crops'
   event='2025 laboratory submission' 
 if ds.endswith('2025 lab') and r.confirmed_sample_key in ref.index:
  event=r.collection_date;plot=r.confirmed_sample_key.split('|')[0]
 if tr is None:holds.append(dict(record_id=r.record_id,dataset=ds,reason='Unresolved plot/treatment label'));continue
 for _,m in subset.iterrows():
  if pd.isna(m.value):continue
  variable=m.variable.strip()
  if ward2023 and (m.preferred_pdf_source_id!='S33' or not str(m.value_basis).startswith('PDF') or variable in ['Nitrate-N, lbs N / Acre','Excess Lime Rating']):continue
  if ds=='Oatman Grain yield'and variable!='Reported grain yield':continue
  if ds.endswith('2025 lab'):
   if m.value_basis not in ['PDF preferred','PDF only']:continue
   if variable in ['BpH Modified WDRF','Excess Lime Rating','Nitrate Lbs/Acre','Total Available Water inches / sample','Available Water g H2O / g soil']:continue
  censored=ds=='Oatman Grain elements'and not str(m.qualifier).startswith('At or above MDL')
  obs.append(dict(farm=r.farm,system=system,dataset=ds,event=event,plot=plot,treatment=tr,variable=variable,
   value=m.value,unit=m.unit,sample_key=r.confirmed_sample_key,record_id=r.record_id,source_id=m.preferred_pdf_source_id or m.source_id,censored=censored))
accounted={r['record_id'] for r in obs}|{r['record_id'] for r in holds}|{r['record_id'] for r in dispositions}
for _,r in records[~records.record_id.isin(accounted)].iterrows():
 holds.append(dict(record_id=r.record_id,dataset=r.dataset,reason='No numeric measurement remained after the declared selection rules'))
data=save('analysis_observations',obs);save('held_records',holds)
for rid,g in data.groupby('record_id'):
 if rid in set(records.record_id):dispositions.append(dict(record_id=rid,dataset=g.dataset.iloc[0],status='selected_plot_measurements',reason='Trait-specific eligibility, censoring and residual-df gates still apply'))
for item in holds:dispositions.append(dict(**item,status='retained_outside_inferential_tests'))
save('record_disposition',dispositions)
held_ids={h['record_id'] for h in holds}
save('retained_measurements',measurements[measurements.record_id.isin(held_ids)])

def compare_traits(data,prefix):
 tests=[];desc=[];contrasts=[]
 for (farm,system,ds,event),gg in data.groupby(['farm','system','dataset','event'],sort=True):
  expected=sorted(gg.treatment.unique())
  if farm=='Whipstone':expected=['A','B','C','D']
  control='Control'if farm=='Oatman Flats'else 'T1'if farm=='Page Springs'else 'C'
  for (variable,unit),g in gg.groupby(['variable','unit'],dropna=False,sort=True):
   for tr,h in g.groupby('treatment'):
    v=h.value.to_numpy(float)
    desc.append(dict(farm=farm,system=system,dataset=ds,event=event,variable=variable,unit=unit,treatment=tr,
     n=len(v),mean=v.mean(),median=np.median(v),sd=np.std(v,ddof=1)if len(v)>1 else np.nan,
     minimum=v.min(),maximum=v.max(),censored_n=int(h.censored.sum())))
   base=dict(farm=farm,system=system,dataset=ds,event=event,variable=variable,unit=unit,
    treatments=';'.join(expected),sample_set=';'.join(g.record_id.astype(str)),treatment_counts=';'.join(f'{t}:{len(h)}'for t,h in g.groupby('treatment')))
   reason=eligibility(g,expected)
   if g.censored.any():reason='Contains zero/below-MDL grain result; entire element/event descriptive only'
   if reason:res=dict(status='not_tested',reason=reason,p=np.nan,n=len(g))
   else:res=model(rankdata(g.value),design(n=len(g)),design(g.treatment.to_numpy()),np.repeat('all',len(g)),(prefix,ds,event,system,tuple(g['plot'])),label_groups=g.treatment.to_numpy())
   tests.append(dict(**base,**res))
   if control in set(g.treatment):
    v0=g[g.treatment==control].value
    for tr,h in g.groupby('treatment'):
     if tr!=control:contrasts.append(dict(**base,treatment=tr,control=control,n=len(h),control_n=len(v0),
      median_difference=h.value.median()-v0.median(),mean_difference=h.value.mean()-v0.mean(),
      interpretation='Descriptive contrast; no pairwise p value',contains_censored=bool(g.censored.any())))
 tests=adjust(tests,['farm','system','dataset','event'])
 save(prefix+'_tests',tests);save(prefix+'_descriptive',desc);save(prefix+'_control_differences',contrasts);return tests

soiltests=compare_traits(data,'soil_crop')
print('Soil/crop comparisons completed',flush=True)

# Treatment differences in within-plot changes in the shared Oatman field.
changes=[]
hm=data[data.dataset=='Haney soil']
for (plot,var),g in hm.groupby(['plot','variable']):
 g=g.sort_values('event')
 for i in range(1,len(g)):
  a,b=g.iloc[i-1],g.iloc[i];changes.append(dict(farm=a.farm,system=a.system,dataset='Haney within-plot change',
   event=a.event+' to '+b.event,plot=plot,treatment=a.treatment,variable=var,value=b.value-a.value,unit=a.unit,
   sample_key=a.sample_key+' -> '+b.sample_key,record_id=a.record_id+' -> '+b.record_id,source_id=a.source_id+';'+b.source_id,censored=False))
change=save('soil_temporal_changes',changes);changetests=compare_traits(change,'soil_temporal')

# Shannon treatment follow-up with declared baseline and >=100-read populations.
alpha=[];alpha_desc=[]
for (farm,domain,cohort),g0 in meta.groupby(['farm','microbial_domain','cohort'],sort=True):
 expected=sorted(g0.treatment_label.dropna().unique())
 for threshold in [0,100]:
  g=g0[(g0.target_read_count>0)&(g0.target_read_count>=threshold)&g0.Shannon.notna()].copy()
  g['treatment']=g['treatment_label'];g['plot']=g['biological_plot_id']
  base=dict(farm=farm,domain=domain,cohort=cohort,minimum_reads=threshold,outcome='Shannon',
   sample_set=';'.join(g.analysis_sample),n_dates=g.collection_date.nunique(),
   formula='Shannon ~ collection date + log10(target reads) + treatment')
  for tr,h in g.groupby('treatment'):
   alpha_desc.append(dict(**base,treatment=tr,n=len(h),mean=h.Shannon.mean(),median=h.Shannon.median(),sd=h.Shannon.std(),
    mean_observed_ASVs=h.observed_ASVs.mean(),min_reads=h.target_read_count.min(),max_reads=h.target_read_count.max()))
  reason=eligibility(g,expected)
  if len(expected)<2:reason='Descriptive reference or fewer than two treatments'
  if reason:res=dict(status='not_tested',reason=reason,p=np.nan,n=len(g))
  else:
   x0=design(g.collection_date.to_numpy(),np.log10(g.target_read_count.to_numpy(float)))
   x1=design(g.collection_date.to_numpy(),np.log10(g.target_read_count.to_numpy(float)),g.treatment.to_numpy())
   res=model(g.Shannon.to_numpy(float),x0,x1,g.collection_date.to_numpy(),('alpha',tuple(g.analysis_sample)))
  alpha.append(dict(**base,**res))
alphatests=adjust(alpha,['farm','domain','minimum_reads']);save('alpha_Shannon_tests',alphatests);save('alpha_descriptive',alpha_desc)
print('Shannon follow-up completed',flush=True)

def bray_coordinates(c):
 prop=c/c.sum(axis=1,keepdims=True);d=squareform(pdist(prop,metric='braycurtis'));n=len(c);j=np.eye(n)-np.ones((n,n))/n
 gram=-.5*j@(d*d)@j;ev=np.linalg.eigvalsh(gram);add=max(0.,-ev[0]);dc=np.sqrt(np.maximum(0,d*d+2*add));np.fill_diagonal(dc,0)
 gram=-.5*j@(dc*dc)@j;val,vec=np.linalg.eigh(gram);sel=val>max(1e-12,val.max()*1e-10);coords=vec[:,sel]*np.sqrt(val[sel])
 assert np.allclose(squareform(pdist(coords)),dc,atol=1e-7)
 return coords,add

env=[];joins=[]
wide=means.pivot(index='sample_key',columns='variable',values='mean_value')
for domain in ['bacteria','archaea']:
 for event,g0 in meta[(meta.farm=='Oatman Flats')&meta.sample_key.isin(wide.index)&(meta.microbial_domain==domain)].groupby('collection_date'):
  for threshold in [0,100]:
   g0s=g0[(g0.target_read_count>0)&(g0.target_read_count>=threshold)].sort_values('analysis_sample')
   for var in ENV:
    g=g0s[g0s.sample_key.map(wide[var]).notna()].copy();n=len(g)
    x=rankdata(g.sample_key.map(wide[var]).to_numpy(float));logr=np.log10(g.target_read_count.to_numpy(float))
    x0=design(g.treatment_label.to_numpy(),logr);x1=np.column_stack([x0,(x-x.mean())/(x.std()or 1)])
    coords,ac=bray_coordinates(counts[domain].loc[g.analysis_sample].to_numpy(float))
    for outcome,y in [('Shannon',g.Shannon.to_numpy(float)),('Bray-Curtis community',coords)]:
     res=model(y,x0,x1,g.treatment_label.to_numpy(),('environment',tuple(g.analysis_sample)))
     env.append(dict(farm='Oatman Flats',domain=domain,event=event,minimum_reads=threshold,predictor=var,outcome=outcome,
      sample_set=';'.join(g.analysis_sample),formula='Outcome ~ treatment + log10(target reads) + rank(environment)',
      permutation_restriction='within treatment',Lingoes_constant=ac if outcome!='Shannon'else np.nan,
      raw_Spearman_rho=spearmanr(x,y).statistic if outcome=='Shannon'and np.std(y)>0 and np.std(x)>0 else np.nan,**res))
    if threshold==0:
     for _,r in g.iterrows():joins.append(dict(domain=domain,event=event,analysis_sample=r.analysis_sample,sample_key=r.sample_key,
       treatment=r.treatment_label,predictor=var,value=wide.loc[r.sample_key,var],target_reads=r.target_read_count,Shannon=r.Shannon))
envtests=adjust(env,['domain','outcome','minimum_reads']);save('environment_tests',envtests);save('environment_matched_values',joins)
print('Oatman environmental integration completed',flush=True)

# Newly enabled by the verified 2023 Ward report and researcher-confirmed date.
# The six predictors are fixed in advance; no selection based on association p values.
WHIP_ENV=['1:1 Soil pH','Soluble Salts 1:1, mmho/cm','Organic Matter LOI, %',
          'Organic Carbon, % C','Nitrate-N KCl, ppm N','Phosphorus Olsen P, ppm P']
we=[];wj=[]
wh=data[data.dataset=='Whipstone 2023 Ward soil']
for system,sg in wh.groupby('system'):
 ww=sg.pivot(index='sample_key',columns='variable',values='value')
 for domain in ['bacteria','archaea']:
  g0=meta[(meta.farm=='Whipstone') & (meta.microbial_domain==domain) & meta.sample_key.isin(ww.index)]
  assert not g0.sample_key.duplicated().any()
  for threshold in [0,100]:
   gs=g0[(g0.target_read_count>0)&(g0.target_read_count>=threshold)].sort_values('analysis_sample')
   for var in WHIP_ENV:
    g=gs[gs.sample_key.map(ww[var]).notna()].copy()
    g['treatment']=g.treatment_code;g['plot']=g.biological_plot_id
    # Letters follow the report's A/B/C/D comparison; metadata treatment_code
    # is not assumed to be the same coding convention.
    g['treatment']=g.sample_key.map(sg.drop_duplicates('sample_key').set_index('sample_key').treatment)
    base=dict(farm='Whipstone',system=system,domain=domain,event='2023-11-16',minimum_reads=threshold,
      predictor=var,sample_set=';'.join(g.analysis_sample),formula='Outcome ~ treatment + log10(target reads) + rank(environment)',
      permutation_restriction='within treatment',source_id='S33',
      interpretation='Exploratory; report values retained verbatim, including unusually large Olsen P; accepted as printed per researcher; prior horse-manure inputs are a possible explanation')
    reason=eligibility(g,['A','B','C','D'])
    for _,r in g.iterrows():
     wj.append(dict(**{k:base[k] for k in ['farm','system','domain','event','minimum_reads','predictor']},
        analysis_sample=r.analysis_sample,sample_key=r.sample_key,treatment=r.treatment,
        value=ww.loc[r.sample_key,var],target_reads=r.target_read_count,Shannon=r.Shannon))
    if len(g):
     x=rankdata(g.sample_key.map(ww[var]).to_numpy(float));logr=np.log10(g.target_read_count.to_numpy(float))
     x0=design(g.treatment.to_numpy(),logr);x1=np.column_stack([x0,(x-x.mean())/(x.std()or 1)])
     if len(g)-np.linalg.matrix_rank(x1)<3:reason='Fewer than three residual degrees of freedom after treatment, depth and predictor adjustment'
    else:reason='No eligible matched libraries'
    if reason:
     for outcome in ['Shannon','Bray-Curtis community']:
      we.append(dict(**base,outcome=outcome,status='not_tested',reason=reason,n=len(g),p=np.nan,
        Lingoes_constant=np.nan,raw_Spearman_rho=np.nan))
     continue
    coords,ac=bray_coordinates(counts[domain].loc[g.analysis_sample].to_numpy(float))
    for outcome,y in [('Shannon',g.Shannon.to_numpy(float)),('Bray-Curtis community',coords)]:
     res=model(y,x0,x1,g.treatment.to_numpy(),('Whipstone_environment',tuple(g.analysis_sample)))
     we.append(dict(**base,outcome=outcome,Lingoes_constant=ac if outcome!='Shannon' else np.nan,
       raw_Spearman_rho=spearmanr(x,y).statistic if outcome=='Shannon' and np.std(y)>0 and np.std(x)>0 else np.nan,**res))
whipenv=adjust(we,['system','domain','outcome','minimum_reads'])
save('Whipstone_environment_tests',whipenv);save('Whipstone_environment_matched_values',wj)
print('Confirmed Whipstone 2023 environmental integration completed; small cohorts can remain untested.',flush=True)

# Descriptive consecutive-visit microbiome changes; plot identity carries field identity.
paired=[]
for (farm,domain,system,plot),g in meta[meta.target_read_count>0].groupby(['farm','microbial_domain','analytic_system','biological_plot_id']):
 g=g.sort_values('collection_date')
 for i in range(1,len(g)):
  a,b=g.iloc[i-1],g.iloc[i];ca=counts[domain].loc[a.analysis_sample].to_numpy(float);cb=counts[domain].loc[b.analysis_sample].to_numpy(float)
  ca/=ca.sum();cb/=cb.sum()
  paired.append(dict(farm=farm,domain=domain,system=system,plot=plot,treatment=a.treatment_label,
   from_date=a.collection_date,to_date=b.collection_date,from_sample=a.analysis_sample,to_sample=b.analysis_sample,
   interval=str(int(a.year))+' to '+str(int(b.year)),days=(pd.Timestamp(b.collection_date)-pd.Timestamp(a.collection_date)).days,
   Shannon_change=b.Shannon-a.Shannon,observed_ASV_change=b.observed_ASVs-a.observed_ASVs,
   Bray_Curtis=float(np.abs(ca-cb).sum()/2),from_reads=a.target_read_count,to_reads=b.target_read_count,
   both_at_least_100=bool(min(a.target_read_count,b.target_read_count)>=100)))
paired=save('matched_plot_changes',paired)
assert not ((paired.farm=='Oatman Flats')&paired.to_date.str.startswith('2025')).any()
save('matched_plot_summary',paired.groupby(['farm','domain','system','interval','treatment']).agg(n_pairs=('plot','size'),
 median_Shannon_change=('Shannon_change','median'),median_Bray_Curtis=('Bray_Curtis','median'),min_days=('days','min'),max_days=('days','max')).reset_index())

# Prespecified exploratory phylum follow-up of the three established bacterial cohorts.
phy=pd.read_csv(IN/'PageSprings_bacteria_phylum.tsv',sep='\t');tax=[];tax_values=[]
cohorts=['Grape_PetiteSirah__2023','Grape_PetiteSirah__2024','Grape_Grenache__2023']
for cohort in cohorts:
 g=meta[(meta.farm=='Page Springs')&(meta.microbial_domain=='bacteria')&(meta.cohort==cohort)&(meta.target_read_count>0)].sort_values('analysis_sample')
 c=phy[phy.cohort==cohort].pivot_table(index='sample_name',columns='taxon',values='read_count',fill_value=0).reindex(g.analysis_sample)
 c=c.loc[:,c.sum()>0];prop=c.div(c.sum(axis=1),axis=0);selected=[t for t in c if prop[t].mean()>=.01 and (c[t]>0).mean()>=.5 and 'unclassified'not in t.lower()]
 assert np.allclose(c.sum(axis=1),g.target_read_count.to_numpy())
 for pc in [.5,1.]:
  logs=np.log(c+pc);clr=logs.sub(logs.mean(axis=1),axis=0)
  x0=design(g.collection_date.to_numpy(),np.log10(g.target_read_count.to_numpy(float)))
  x1=design(g.collection_date.to_numpy(),np.log10(g.target_read_count.to_numpy(float)),g.treatment_label.to_numpy())
  for t in selected:
   res=model(clr[t].to_numpy(),x0,x1,g.collection_date.to_numpy(),('phylum',tuple(g.analysis_sample)))
   tax.append(dict(farm='Page Springs',domain='bacteria',cohort=cohort,taxon=t,pseudocount=pc,
    mean_relative_abundance=prop[t].mean(),prevalence=(c[t]>0).mean(),CLR_denominator_phyla=len(c.columns),
    sample_set=';'.join(g.analysis_sample),**res))
   if pc==.5:
    for sid in c.index:tax_values.append(dict(cohort=cohort,taxon=t,sample=sid,
     treatment=g.set_index('analysis_sample').loc[sid,'treatment_label'],relative_abundance=prop.loc[sid,t],CLR=clr.loc[sid,t]))
taxtests=adjust(tax,['pseudocount']);save('PageSprings_phylum_tests',taxtests);save('PageSprings_phylum_values',tax_values)
print('Focused phylum and matched-plot reviews completed',flush=True)

summary={}
for name,d in [('soil_crop',soiltests),('soil_temporal',changetests),('alpha_Shannon',alphatests),('environment',envtests),('PageSprings_phylum',taxtests),('Whipstone_environment',whipenv)]:
 summary[name]={'rows':len(d),'tested':int(d.p.notna().sum()),'q_BH_lt_05':int((d.q_BH<.05).sum()),'q_BY_lt_05':int((d.q_BY<.05).sum())}
summary['Whipstone_2023_soil_records']=int(wh.record_id.nunique())
summary['Whipstone_2023_environment_collections']=len({r['sample_key'] for r in wj})
summary['distinct_environment_collections']=len({d['sample_key']for d in joins});summary['paired_domain_visits']=len(paired)
summary['inputs']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in IN.iterdir()if p.is_file()}
summary['software']={'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,'scipy':scipy.__version__,'seed':SEED}
summary['validation']=checks+['All count-row totals match current target-read metadata.','Lingoes coordinates reproduce corrected distances.','No temporal pair crosses into the Oatman 2025 field.']
(BASE/'VALIDATION.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items()if k not in ['inputs','software']},indent=2),flush=True)
