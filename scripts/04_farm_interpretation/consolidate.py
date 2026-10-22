#!/usr/bin/env python3
"""Consolidate existing NRCS results and draw descriptive figures. No new treatment tests."""
from pathlib import Path
import json
import hashlib
import re
import sys
import numpy as np
import pandas as pd
import scipy
from scipy.spatial.distance import pdist, squareform
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT=Path(__file__).resolve().parent
FARMS=['OatmanFlatsRanch','PageSpringsCellars','Whipstone']
DOMAINS=['archaea','bacteria']
LABEL={'OatmanFlatsRanch':'Oatman Flats Ranch','PageSpringsCellars':'Page Springs Cellars','Whipstone':'Whipstone'}
DEPTHS=[0,100,500,1000,2000,5000]
for folder in ['tables','figures']:(ROOT/folder).mkdir(exist_ok=True)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                     'figure.facecolor':'white','savefig.facecolor':'white','svg.fonttype':'none'})

def short(cohort):
    return cohort.replace('OFR_2023_2024_Field__','Field 2023–24 · ').replace('OFR_2025_Field__','New field · ').replace('Grape_','').replace('PetiteSirah','Petite Sirah').replace('Blackberry_','').replace('__',' · ')

def bh(p):
    p=np.asarray(p,dtype=float);order=np.argsort(p);ranked=p[order]
    q=np.minimum.accumulate((ranked*len(p)/np.arange(1,len(p)+1))[::-1])[::-1]
    out=np.empty(len(p));out[order]=np.clip(q,0,1);return out

def savefig(fig,stem):
    fig.savefig(ROOT/f'figures/{stem}.png',dpi=230,bbox_inches='tight')
    fig.savefig(ROOT/f'figures/{stem}.svg',bbox_inches='tight')
    plt.close(fig)

def pcoa_lingoes(counts):
    relative=counts/counts.sum(axis=1,keepdims=True)
    d=squareform(pdist(relative,metric='braycurtis'))
    n=len(d);j=np.eye(n)-np.ones((n,n))/n
    raw=-.5*j@(d*d)@j
    correction=max(0.,-float(np.linalg.eigvalsh(raw).min()))
    corrected=d*d+2*correction*(1-np.eye(n))
    b=-.5*j@corrected@j
    eig,vec=np.linalg.eigh(b);order=np.argsort(eig)[::-1];eig=eig[order];vec=vec[:,order]
    positive=eig>max(1,eig.max())*1e-12
    points=vec[:,positive]*np.sqrt(eig[positive])
    assert np.allclose(squareform(pdist(points))**2,corrected,atol=1e-9)
    for k in range(points.shape[1]):
        if points[np.argmax(np.abs(points[:,k])),k]<0:points[:,k]*=-1
    return points[:,:2],eig[positive],correction

def main():
    provenance=json.loads((ROOT/'INPUT_PROVENANCE.json').read_text())
    for item in provenance:
        assert hashlib.sha256((ROOT/item['package_file']).read_bytes()).hexdigest()==item['sha256']
    results=pd.read_csv(ROOT/'inputs/ALL_ANALYSES_STATUS.tsv',sep='\t')
    assert not results.duplicated(['farm','domain','cohort','minimum_reads']).any()
    for keys,g in results.groupby(['farm','domain','minimum_reads']):
        for p,q in [('treatment_p','treatment_q_BH'),('dispersion_p','dispersion_q_BH')]:
            tested=g[p].notna()
            if tested.any():assert np.allclose(bh(g.loc[tested,p]),g.loc[tested,q],atol=1e-12)
    counts={d:pd.read_csv(ROOT/f'inputs/{d}_counts.tsv',sep='\t',index_col=0) for d in DOMAINS}
    metadata=[];alphas=[];taxa=[];coords={};eigvals={};ordination_validation=[]
    for farm in FARMS:
        for domain in DOMAINS:
            folder=ROOT/'inputs/farms'/farm/domain
            md=pd.read_csv(folder/'farm_read_tracking_all_approved_samples.tsv',sep='\t')
            md['domain']=domain
            metadata.append(md)
            alpha=pd.read_csv(folder/'sample_readdepth_alpha_and_design.tsv',sep='\t')
            alpha['domain']=domain
            alphas.append(alpha)
            positive=md[md.target.gt(0)]
            assert set(alpha.sample_name)==set(positive.sample_name)
            assert np.array_equal(counts[domain].loc[md.sample_name].sum(axis=1).to_numpy(),md.target.to_numpy())
            assert not set(md.sample_name)&{'C108','C111','D108','D111'}
            for rank in ['phylum','genus']:
                t=pd.read_csv(folder/f'{rank}_relative_abundance.tsv',sep='\t')
                assert not t.duplicated(['sample_name','taxon']).any()
                assert set(t.sample_name)==set(positive.sample_name)
                totals=t.groupby('sample_name').read_count.sum()
                assert np.array_equal(totals.loc[positive.sample_name].to_numpy(),positive.target.to_numpy())
                assert np.allclose(t.groupby('sample_name').relative_abundance.sum(),1,atol=1e-12)
                assert np.allclose(t.relative_abundance.to_numpy(),t.read_count.to_numpy()/t.sample_name.map(totals).to_numpy(),atol=1e-12)
                assert t.groupby('taxon').sample_name.nunique().eq(len(positive)).all()
                t['farm']=farm;t['domain']=domain;t['rank']=rank
                t['target_reads']=t.sample_name.map(positive.set_index('sample_name').target)
                taxa.append(t)
            co=pd.read_csv(folder/f'{domain}_ordination_coordinates.tsv',sep='\t')
            coords[(farm,domain)]=co
            e=pd.read_csv(folder/f'{domain}_ordination_eigenvalues.tsv',sep='\t').eigenvalue.to_numpy()
            eigvals[(farm,domain)]=e
            pt,e2,c=pcoa_lingoes(counts[domain].loc[co.sample_name].to_numpy(dtype=float))
            assert np.allclose(e[e>1e-12],e2,atol=1e-9)
            for k in [0,1]:
                assert np.allclose(np.abs(pt[:,k]),np.abs(co[f'PCoA{k+1}'].to_numpy()),atol=1e-8)
            ordination_validation.append({'farm':farm,'domain':domain,'n':len(co),'lingoes_constant':c,'maximum_eigenvalue_error':float(np.max(np.abs(e[e>1e-12]-e2))),'status':'PASS'})
    metadata=pd.concat(metadata,ignore_index=True)
    alpha=pd.concat(alphas,ignore_index=True)
    taxa=pd.concat(taxa,ignore_index=True)
    assert len(metadata)==342 and len(alpha)==341
    base=results[results.minimum_reads.eq(0)].copy().sort_values(['farm','domain','cohort'])
    assert len(base)==38 and base.treatment_status.eq('tested_exploratory').sum()==30
    grid=[]
    for _,b in base.iterrows():
        md=metadata[metadata.farm.eq(b.farm)&metadata.domain.eq(b.domain)&metadata.cohort.eq(b.cohort)]
        for depth in DEPTHS:
            sel=md[md.target.gt(0)&md.target.ge(depth)]
            source=results[results.farm.eq(b.farm)&results.domain.eq(b.domain)&results.cohort.eq(b.cohort)&results.minimum_reads.eq(depth)]
            if len(source):
                row=source.iloc[0].to_dict();assert row['n_samples']==len(sel)
                assert set(str(row['sample_set']).split(';'))==set(sel.sample_name)
            else:
                assert len(sel)==0
                row={k:np.nan for k in results.columns}
                row.update(farm=b.farm,domain=b.domain,cohort=b.cohort,minimum_reads=depth,n_samples=0,
                           treatment_status='not_tested',treatment_reason='No positive-target samples remain at this depth',sample_set='',dispersion_status='not_tested')
            row['samples_removed_from_baseline']=';'.join(sorted(set(str(b.sample_set).split(';'))-set(sel.sample_name)))
            row['same_samples_as_baseline']=set(str(b.sample_set).split(';'))==set(sel.sample_name)
            grid.append(row)
    depth=pd.DataFrame(grid)
    depth.to_csv(ROOT/'tables/all_depth_results.csv',index=False)
    decisions=metadata[['farm','domain','sample_name','sample_id','plot_id','cohort','collection_date','treatment_label','input','filtered','denoised','nonchimera','target']].copy()
    decisions['primary_status']=np.where(decisions.target.gt(0),'included_positive_target','excluded_zero_target')
    decisions['QC_note']=''
    decisions.loc[decisions.target.between(1,99),'QC_note']='Near-empty target library; retained in documented baseline; review with >=100 sensitivity'
    decisions.loc[decisions.sample_name.eq('C012')&decisions.domain.eq('archaea'),'QC_note']='Retain; influential in Petite Sirah 2023; no independent technical exclusion criterion established'
    decisions.loc[decisions.target.eq(0),'QC_note']='Zero target reads; relative abundance and Bray distance undefined'
    decisions.to_csv(ROOT/'tables/sample_decisions.csv',index=False)
    metadata[['farm','treatment_label','treatment']].drop_duplicates().sort_values(['farm','treatment_label']).to_csv(ROOT/'tables/treatment_codebook.csv',index=False)
    core=[]
    for _,row in base.iterrows():
        r=row.to_dict();sub=depth[depth.farm.eq(r['farm'])&depth.domain.eq(r['domain'])&depth.cohort.eq(r['cohort'])]
        md=metadata[metadata.farm.eq(r['farm'])&metadata.domain.eq(r['domain'])&metadata.cohort.eq(r['cohort'])&metadata.target.gt(0)]
        r['collection_dates']=';'.join(sorted(set(md.collection_date)))
        r['near_empty_samples']=';'.join(md.loc[md.target.lt(100),'sample_name'])
        r['BH_family_size']=int(base[base.farm.eq(r['farm'])&base.domain.eq(r['domain'])].treatment_p.notna().sum())
        r['same_sample_thresholds']=';'.join(str(int(v)) for v in sub[sub.same_samples_as_baseline].minimum_reads)
        for depthval in [100,1000,5000]:
            rr=sub[sub.minimum_reads.eq(depthval)].iloc[0]
            for value in ['n_samples','treatment_R2','treatment_q_BH','dispersion_q_BH','treatment_status']:
                r[f'depth_{depthval}_{value}']=rr[value]
        if r['treatment_status']!='tested_exploratory':note=r['treatment_reason']
        elif pd.notna(r['dispersion_q_BH']) and r['dispersion_q_BH']<.05:note='Dispersion also differs; treatment association cannot be read solely as a centroid shift'
        elif r['n_collection_dates']>1:note='Date-adjusted association with within-date permutations; no pooled dispersion test'
        else:note='Single-date exploratory treatment association; no causal claim'
        if r['domain']=='archaea' and r['cohort']=='Grape_PetiteSirah__2023':note+='; threshold 1000 removes influential C012'
        if r['near_empty_samples']:note+='; near-empty target library included in baseline'
        r['interpretation_note']=note;core.append(r)
    primary=pd.DataFrame(core)
    primary.to_csv(ROOT/'tables/primary_results_and_sensitivity.csv',index=False)
    alpha_summary=alpha.groupby(['farm','domain','cohort']).agg(n=('sample_name','size'),
        target_median=('target_read_count','median'),target_min=('target_read_count','min'),target_max=('target_read_count','max'),
        observed_ASVs_median=('observed_ASVs','median'),Shannon_median=('Shannon','median'),Shannon_min=('Shannon','min'),Shannon_max=('Shannon','max')).reset_index()
    alpha_summary['interpretation']='Descriptive unrarefied values; depth-dependent; no alpha-diversity tests'
    alpha_summary.to_csv(ROOT/'tables/descriptive_diversity.csv',index=False)
    groups=['farm','domain','rank','cohort','taxon']
    taxsum=taxa.groupby(groups).agg(n=('sample_name','size'),mean_relative_abundance=('relative_abundance','mean'),
        median_relative_abundance=('relative_abundance','median'),minimum_relative_abundance=('relative_abundance','min'),
        maximum_relative_abundance=('relative_abundance','max'),prevalence=('read_count',lambda x:float(np.mean(x>0)))).reset_index()
    taxsum.to_csv(ROOT/'tables/cohort_taxonomic_profiles.csv',index=False)
    treatment=taxa.groupby(groups+['treatment_label']).agg(n=('sample_name','size'),mean_relative_abundance=('relative_abundance','mean'),
        median_relative_abundance=('relative_abundance','median'),prevalence=('read_count',lambda x:float(np.mean(x>0)))).reset_index()
    treatment.to_csv(ROOT/'tables/treatment_taxonomic_profiles_descriptive.csv',index=False)
    robust=taxa[taxa.target_reads.ge(100)].groupby(groups).relative_abundance.mean().rename('mean_relative_abundance_ge100').reset_index()
    taxcheck=taxsum.merge(robust,on=groups,how='left')
    taxcheck['difference_percentage_points_ge100_minus_baseline']=100*(taxcheck.mean_relative_abundance_ge100-taxcheck.mean_relative_abundance)
    taxcheck.to_csv(ROOT/'tables/composition_near_empty_sensitivity.csv',index=False)
    taxsum.sort_values('mean_relative_abundance',ascending=False).groupby(['farm','domain','rank','cohort']).head(5).sort_values(['farm','domain','rank','cohort','mean_relative_abundance'],ascending=[True,True,True,True,False]).to_csv(ROOT/'tables/top_five_taxa_by_cohort.csv',index=False)
    design=metadata.groupby(['farm','domain','cohort','collection_date','treatment_label']).agg(n_approved=('sample_name','size'),n_positive=('target',lambda x:int((x>0).sum())),n_ge100=('target',lambda x:int((x>=100).sum())),plots=('plot_id',lambda x:';'.join(sorted(set(x))))).reset_index()
    design.to_csv(ROOT/'tables/design_by_date_and_treatment.csv',index=False)
    pd.DataFrame(ordination_validation).to_csv(ROOT/'tables/ordination_validation.csv',index=False)
    plot_effects(primary)
    plot_compositions(taxsum)
    plot_farm_ordinations(coords,eigvals)
    cohort_coordinates=plot_page_springs(counts['bacteria'],alpha,primary)
    cohort_coordinates.to_csv(ROOT/'tables/PageSprings_bacteria_selected_cohort_ordination.csv',index=False)
    validations={'source_checksums':'PASS','positive_sample_identity_and_count_totals':'PASS','relative_abundance_and_sample_denominators':'PASS',
        'BH_adjustment_all_existing_families':'PASS','all_depth_sample_sets_and_sizes':'PASS','new_PCoA_reconstruction':'PASS',
        'PCoA_vs_existing_R_eigenvalues_and_coordinates':'PASS','approved_sample_domain_records':len(metadata),
        'positive_sample_domain_records':len(alpha),'primary_cohort_domain_rows':len(primary),
        'primary_treatment_tests':int(primary.treatment_p.notna().sum()),'depth_grid_rows':len(depth),
        'new_treatment_or_taxon_hypothesis_tests':0}
    (ROOT/'VALIDATION.json').write_text(json.dumps(validations,indent=2)+'\n')
    (ROOT/'environment.json').write_text(json.dumps({'python':sys.version,'numpy':np.__version__,'pandas':pd.__version__,
                                                   'scipy':scipy.__version__,'matplotlib':matplotlib.__version__},indent=2)+'\n')
    print(json.dumps(validations,indent=2))
    print('Largest composition change when the near-empty libraries are omitted:')
    print(taxcheck.loc[taxcheck.difference_percentage_points_ge100_minus_baseline.abs().nlargest(8).index,['farm','domain','rank','cohort','taxon','difference_percentage_points_ge100_minus_baseline']].to_string(index=False))

def plot_effects(primary):
    fig,axes=plt.subplots(3,2,figsize=(15,12.4),gridspec_kw={'height_ratios':[1,1.15,2]})
    for i,farm in enumerate(FARMS):
        for j,domain in enumerate(DOMAINS):
            ax=axes[i,j];sub=primary[primary.farm.eq(farm)&primary.domain.eq(domain)].sort_values('cohort')
            for y,(_,r) in enumerate(sub.iterrows()):
                if pd.notna(r.treatment_R2):
                    sig=r.treatment_q_BH<.05;disp=pd.notna(r.dispersion_q_BH) and r.dispersion_q_BH<.05
                    ax.scatter(r.treatment_R2,y,s=62,facecolors='#167D8D' if sig else 'white',edgecolors='#C45632' if disp else '#31566C',marker='D' if disp else 'o',linewidths=1.7,zorder=3)
                    text=f'n={int(r.n_samples)}   q={r.treatment_q_BH:.4f}'
                else:text=f'n={int(r.n_samples)}   not tested'
                ax.text(.765,y,text,fontsize=8.8,va='center')
            ax.set_yticks(range(len(sub)),[short(c) for c in sub.cohort],fontsize=9)
            ax.set_ylim(len(sub)-.5,-.5);ax.set_xlim(-.025,1.2);ax.set_xticks([0,.2,.4,.6]);ax.set_xlabel('Treatment R²')
            ax.set_title(f'{LABEL[farm]} · {domain}',loc='left',fontweight='bold',fontsize=11)
            ax.grid(axis='x',alpha=.16);ax.spines['right'].set_visible(False)
            ax.spines['bottom'].set_bounds(0,.7)
    fig.suptitle('Primary treatment associations across the NRCS cohorts',x=.02,ha='left',fontsize=18)
    fig.text(.02,.944,'Positive-target baseline. Adjusted q values retain the original farm × domain testing families.',fontsize=11)
    handles=[Line2D([0],[0],marker='o',color='none',markerfacecolor='#167D8D',markeredgecolor='#31566C',label='Treatment q < 0.05'),
             Line2D([0],[0],marker='o',color='none',markerfacecolor='white',markeredgecolor='#31566C',label='Treatment q ≥ 0.05'),
             Line2D([0],[0],marker='D',color='none',markerfacecolor='white',markeredgecolor='#C45632',label='Dispersion q < 0.05')]
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.045),ncol=3,frameon=False)
    fig.text(.02,.012,'R² values describe different designs and sample sizes; they are not a ranking of treatment efficacy. Missing tests reflect design limits.',fontsize=10,color='#555555')
    fig.tight_layout(rect=(0,.09,1,.925),h_pad=2,w_pad=2)
    savefig(fig,'01_primary_treatment_results')

def plot_compositions(taxsum):
    # Fixed labels and colors across farms within each domain/rank.
    archaeal=['Candidatus Nitrososphaera','Candidatus Nitrosocosmicus','Unclassified_Genus','Other taxa']
    acols=['#4576A3','#E7AA45','#9D9DA8','#CAB9D5']
    bacteria=['Pseudomonadota','Bacillota','Actinomycetota','Acidobacteriota','Bacteroidota','Gemmatimonadota','Planctomycetota','Cyanobacteriota','Other taxa']
    bcols=['#355F8D','#3FA796','#E8A44C','#A56BA8','#CB667A','#92B353','#7F8D9A','#6EB5D3','#D6D6D6']
    for idx,farm in enumerate(FARMS,2):
        n=taxsum[taxsum.farm.eq(farm)].cohort.nunique()
        fig,axes=plt.subplots(1,2,figsize=(15,max(6.2,.55*n+2.5)))
        for ax,domain,rank,names,colors in [(axes[0],'archaea','genus',archaeal,acols),(axes[1],'bacteria','phylum',bacteria,bcols)]:
            sub=taxsum[taxsum.farm.eq(farm)&taxsum.domain.eq(domain)&taxsum['rank'].eq(rank)]
            pivot=sub.pivot(index='cohort',columns='taxon',values='mean_relative_abundance').fillna(0).sort_index()
            table=pivot.reindex(columns=names[:-1],fill_value=0)
            table['Other taxa']=1-table.sum(axis=1)
            left=np.zeros(len(table));sizes=sub.groupby('cohort').n.first()
            for name,color in zip(names,colors):
                v=100*table[name].to_numpy();ax.barh(range(len(table)),v,left=left,color=color,label=name.replace('Unclassified_Genus','Unclassified genus'),height=.67);left+=v
            labels=[]
            for c in table.index:
                tag='†' if (farm=='OatmanFlatsRanch' and domain=='bacteria' and c.endswith('2024-12-06')) or (farm=='Whipstone' and domain=='archaea' and c=='Blackberry_Natchez__2025') else ''
                labels.append(f'{short(c)}  (n={sizes[c]}){tag}')
            ax.set_yticks(range(len(table)),labels,fontsize=9);ax.invert_yaxis();ax.set_xlim(0,100)
            ax.set_xlabel('Mean sample relative abundance (%)');ax.set_title(f'{domain.capitalize()} · {rank}',loc='left',fontweight='bold')
            ax.legend(loc='upper center',bbox_to_anchor=(.5,-.16),ncol=2,frameon=False,fontsize=8.5)
        fig.suptitle(f'{LABEL[farm]}: descriptive community composition',x=.02,ha='left',fontsize=17)
        fig.text(.02,.91,'Cohort means give each positive-target sample equal weight. Treatments are combined for this overview.',fontsize=10.5)
        extra={'OatmanFlatsRanch':'† December 2024 includes D094 (3 target reads). ',
               'PageSpringsCellars':'',
               'Whipstone':'† Natchez 2025 includes C115 (4 target reads). Unplanted soil is one descriptive reference. '}[farm]
        fig.text(.02,.014,extra+'\nThese proportions describe sequencing libraries; they do not measure absolute abundance or treatment effects.',fontsize=9.5,color='#555555')
        fig.tight_layout(rect=(0,.08,1,.89),w_pad=3)
        savefig(fig,f'{idx:02d}_{farm}_composition')

def plot_farm_ordinations(coords,eigvals):
    fig,axes=plt.subplots(3,2,figsize=(14,13.7))
    markers={2023:'o',2024:'s',2025:'^'}
    palette=['#355F8D','#CE8C35','#289889','#9467AD']
    system_labels={'OFR_2023_2024_Field':'Field 2023–24','OFR_2025_Field':'Field 2025','Grape_Grenache':'Grenache',
                   'Grape_PetiteSirah':'Petite Sirah','Annual':'Annual','Blackberry_Chester':'Chester','Blackberry_Natchez':'Natchez','Unplanted':'Unplanted'}
    for i,farm in enumerate(FARMS):
        systems=sorted(set(coords[(farm,'bacteria')].analytic_system));colors=dict(zip(systems,palette))
        for j,domain in enumerate(DOMAINS):
            ax=axes[i,j];co=coords[(farm,domain)];e=eigvals[(farm,domain)];pct=100*e[:2]/e[e>0].sum()
            for _,g in co.groupby('plot_id'):
                g=g.sort_values('collection_date')
                if len(g)>1:ax.plot(g.PCoA1,g.PCoA2,color='#B6B6B6',lw=.7,alpha=.55,zorder=1)
            for (system,year),g in co.groupby(['analytic_system','year']):
                ax.scatter(g.PCoA1,g.PCoA2,c=colors[system],marker=markers[int(year)],s=38,alpha=.8,zorder=2,edgecolors='white',linewidths=.35)
            for _,r in co[co.target_read_count.lt(100)].iterrows():
                ax.scatter(r.PCoA1,r.PCoA2,s=95,facecolors='none',edgecolors='#C4473D',linewidths=1.6,zorder=3)
                ax.annotate(r.sample_name,(r.PCoA1,r.PCoA2),xytext=(5,5),textcoords='offset points',fontsize=9,color='#B42F2D')
            ax.set_title(f'{LABEL[farm]} · {domain}',loc='left',fontweight='bold',fontsize=11)
            ax.set_xlabel(f'PCoA1 ({pct[0]:.1f}%)');ax.set_ylabel(f'PCoA2 ({pct[1]:.1f}%)');ax.grid(alpha=.13)
            if j==0:
                handles=[Patch(color=colors[s],label=system_labels.get(s,s)) for s in systems]
                ax.legend(handles=handles,loc='upper center',bbox_to_anchor=(.5,-.19),ncol=2,frameon=False,fontsize=9)
    fig.suptitle('Within-farm community ordinations',x=.04,ha='left',fontsize=18)
    fig.text(.04,.945,'Bray–Curtis on relative abundance; existing R coordinates with Lingoes correction.',fontsize=11)
    fig.legend(handles=[Line2D([0],[0],marker=m,color='none',markerfacecolor='#566A75',markeredgecolor='none',label=str(y)) for y,m in markers.items()],loc='lower center',bbox_to_anchor=(.5,.042),ncol=3,frameon=False)
    fig.text(.04,.009,'Lines connect the same plot across collection dates. Panels have independent axes. Crop, field, date, and year can be confounded.\nThese are descriptive layouts, not tests of temporal change or cross-farm differences. Red rings identify the near-empty libraries.',fontsize=9.5,color='#555555')
    fig.tight_layout(rect=(0,.085,1,.929),h_pad=3,w_pad=3)
    savefig(fig,'05_farm_community_ordinations')

def plot_page_springs(counts,alpha,primary):
    cohorts=['Grape_Grenache__2023','Grape_PetiteSirah__2023','Grape_PetiteSirah__2024']
    colors=['#355F8D','#239489','#D28D35','#9B66A8']
    labels={'CompostedGrapePomaceHorseManure':'Composted pomace + horse manure','CompostedHorseManure':'Composted horse manure','ControlFertigationOnly':'Control (fertigation)','FreshGrapePomace':'Fresh grape pomace','FishManure':'Fish manure','HorseManurePomace':'Horse manure + pomace','SteerManure':'Steer manure'}
    fig,axes=plt.subplots(1,3,figsize=(16,6.3));out=[]
    for ax,cohort in zip(axes,cohorts):
        md=alpha[alpha.farm.eq('PageSpringsCellars')&alpha.domain.eq('bacteria')&alpha.cohort.eq(cohort)].copy().sort_values('sample_name')
        pt,e,c=pcoa_lingoes(counts.loc[md.sample_name].to_numpy(dtype=float))
        md['PCoA1']=pt[:,0];md['PCoA2']=pt[:,1];md['lingoes_constant']=c
        md['PCoA1_percent']=100*e[0]/e.sum();md['PCoA2_percent']=100*e[1]/e.sum();out.append(md)
        treatments=sorted(set(md.treatment_label))
        mapping={'ControlFertigationOnly':'#355F8D','FishManure':'#239489','HorseManurePomace':'#D28D35','SteerManure':'#9B66A8',
                 'CompostedGrapePomaceHorseManure':'#239489','CompostedHorseManure':'#D28D35','FreshGrapePomace':'#9B66A8'}
        dates=sorted(set(md.collection_date));marker=dict(zip(dates,['o','s']))
        for (tr,date),g in md.groupby(['treatment_label','collection_date']):
            ax.scatter(g.PCoA1,g.PCoA2,color=mapping[tr],marker=marker[date],s=70,edgecolors='white',linewidths=.5)
        ax.margins(.18)
        r=primary[primary.farm.eq('PageSpringsCellars')&primary.domain.eq('bacteria')&primary.cohort.eq(cohort)].iloc[0]
        qualifier='Dispersion q = 0.0272' if cohort.endswith('Grenache__2023') else ('Dispersion q = 0.4683' if cohort.endswith('2023') else 'Treatment test adjusted for date')
        ax.set_title(f'{short(cohort)}\nR² = {r.treatment_R2:.3f}; q = {r.treatment_q_BH:.4f}\n{qualifier}',loc='left',fontsize=11,fontweight='bold')
        ax.set_xlabel(f'PCoA1 ({100*e[0]/e.sum():.1f}%)');ax.set_ylabel(f'PCoA2 ({100*e[1]/e.sum():.1f}%)');ax.grid(alpha=.13)
        ax.legend(handles=[Patch(color=mapping[t],label=labels[t]) for t in treatments],loc='upper center',bbox_to_anchor=(.5,-.22),frameon=False,fontsize=8.5,ncol=1)
    fig.suptitle('Page Springs Cellars: bacterial cohorts with primary treatment q < 0.05',x=.02,ha='left',fontsize=16)
    fig.text(.02,.014,'Each panel is a separate descriptive PCoA. The 2024 panel shows raw community distances: circles = August; squares = November.\nIts plotted coordinates are not residualized for date. All associations are exploratory; a two-axis projection does not replace the full-distance test.',fontsize=9.5,color='#555555')
    fig.tight_layout(rect=(0,.09,1,.91),w_pad=3)
    savefig(fig,'06_PageSprings_bacterial_cohort_ordinations')
    return pd.concat(out,ignore_index=True)

if __name__=='__main__':main()
