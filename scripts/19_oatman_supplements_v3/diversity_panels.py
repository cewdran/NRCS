"""Reuse saved Shannon values and model outputs; all rendering executes on Puma."""
from pathlib import Path
import csv,gzip,json,datetime,math
from hpc_guard import require_hpc
DOMAINS=('bacteria','archaea')
SHARED_DATES=('2023-11-20','2024-06-06','2024-12-06')
SYSTEM='OFR_2023_2024_Field'
SEPARATE='OFR_2025_Field'
COLORS={'Control':'#667785','CC':'#16877d','CCS':'#ad6822'}

def require(condition,message):
 if not condition:raise RuntimeError(message)
def truth(value):return str(value).strip().lower() in ('true','1','yes')
def paths_from_stage(run):return {x['role']:Path(x['snapshot']) for x in json.loads((run/'STAGE_STATUS.json').read_text())['sources']}
def save_json(path,data):path.write_text(json.dumps(data,indent=2)+'\n')
def export(fig,path):
 for suffix in ['pdf','svg','png','tif']:
  kwargs={'dpi':600 if suffix=='tif' else 300}
  if suffix=='tif':kwargs['pil_kwargs']={'compression':'tiff_lzw'}
  fig.savefig(path.with_suffix('.'+suffix),**kwargs)

def selected_table(path,pd,domain=None):
 t=pd.read_csv(path,keep_default_na=False)
 t=t.loc[t.farm.eq('Oatman Flats')].copy()
 if domain:t=t.loc[t.domain.eq(domain)].copy()
 t['minimum_reads']=pd.to_numeric(t.minimum_reads,errors='raise').astype(int)
 require(t.minimum_reads.isin([0,100]).all(),'Unexpected saved Shannon thresholds')
 return t

def model_for(tests,domain,system,date,threshold):
 c=system+'__'+date
 rows=tests.loc[tests.domain.eq(domain)&tests.cohort.eq(c)&tests.minimum_reads.eq(threshold)&tests.outcome.eq('Shannon')]
 require(len(rows)==1,'Missing/ambiguous saved Shannon model: '+str((domain,c,threshold)))
 row=rows.iloc[0];members=[x for x in str(row.sample_set).split(';') if x]
 require(len(members)==len(set(members))==int(row.n),'Saved model membership/count mismatch')
 return row,members

def domain_outputs(run,cfg,domain):
 require_hpc()
 import numpy as np
 import pandas as pd
 base=Path(__file__).resolve().parent;out=run/'results'/domain;paths=paths_from_stage(run)
 metadata=pd.read_csv(base/'inputs'/f'{domain}_sample_metadata.csv.gz',keep_default_na=False)
 require(not metadata.analysis_sample.duplicated().any(),'Duplicate accepted per-sample Shannon identity')
 require({'Shannon','observed_ASVs','target_read_count','primary_included','analytic_system','collection_date','biological_plot_id','treatment_label'}.issubset(metadata),'Shannon metadata schema changed')
 metadata=metadata.set_index('analysis_sample',drop=False)
 require(metadata.primary_included.map(truth).all(),'Unapproved primary Shannon member')
 require((metadata.microbial_domain==domain).all(),'Observed Shannon domain mismatch')
 require((pd.to_numeric(metadata.target_read_count,errors='raise')>0).all(),'Nonpositive observed Shannon library')
 for field in ['Shannon','target_read_count','observed_ASVs']:metadata[field]=pd.to_numeric(metadata[field],errors='raise');require(np.isfinite(metadata[field]).all(),'Nonfinite saved observed '+field)
 tests=selected_table(paths['corrected_alpha_tests'],pd,domain);descriptives=selected_table(paths['corrected_alpha_descriptive'],pd,domain)
 model_rows=[];median_rows=[]
 for system,date in [(SYSTEM,d) for d in SHARED_DATES]+[(SEPARATE,'2025-05-21')]:
  actual=metadata.loc[metadata.analytic_system.eq(system)&metadata.collection_date.eq(date)]
  require(not actual.biological_plot_id.duplicated().any(),'Repeated physical plot within domain/event Shannon population')
  for threshold in [0,100]:
   model,members=model_for(tests,domain,system,date,threshold)
   expected=set(actual.index if threshold==0 else actual.index[actual.target_read_count.ge(100)])
   require(set(members)==expected,'Saved model sample_set differs from exact accepted metadata/threshold: '+str((domain,system,date,threshold)))
   require(int(model.BH_family_n)==4,'Original complete four-event alpha correction family changed')
   metadata.loc[list(actual.index),'saved_Shannon_min'+str(threshold)+'_included']=metadata.loc[list(actual.index)].index.isin(members)
   model_rows.append(model.to_dict())
   sub=descriptives.loc[descriptives.cohort.eq(system+'__'+date)&descriptives.minimum_reads.eq(threshold)&descriptives.outcome.eq('Shannon')]
   for treatment in sorted(actual.treatment_label.unique()):
    rows=sub.loc[sub.treatment.eq(treatment)];require(len(rows)==1,'Saved treatment median missing/ambiguous')
    row=rows.iloc[0];require(set(str(row.sample_set).split(';'))==set(members),'Saved median membership differs from model')
    require(int(row.n)==sum(metadata.loc[members,'treatment_label'].eq(treatment)),'Saved descriptive treatment count differs')
    median_rows.append(row.to_dict())
 pd.DataFrame(model_rows).to_csv(out/'saved_Shannon_model_rows.csv',index=False);pd.DataFrame(median_rows).to_csv(out/'saved_Shannon_descriptive_rows.csv',index=False);metadata.to_csv(out/'observed_Shannon_values_and_exact_eligibility.csv',index=False)
 # Complete corrected Oatman library inventory, including records excluded from display.
 workbook=json.loads(paths['corrected_workbook'].read_text());table=workbook['tables']['16S samples'];allmeta=pd.DataFrame(table['rows'],columns=table['columns'])
 require({'farm','microbial_domain','analysis_sample','target_read_count','primary_included','depth_sensitivity_included'}.issubset(allmeta),'Corrected inventory schema changed')
 allmeta=allmeta.loc[allmeta.farm.eq('Oatman Flats')&allmeta.microbial_domain.eq(domain)].copy()
 require(len(allmeta)>0 and not allmeta.analysis_sample.duplicated().any(),'Empty/duplicate corrected Oatman inventory')
 source_rows=allmeta.set_index('analysis_sample',drop=False)
 require(set(metadata.index).issubset(source_rows.index),'Corrected workbook lacks accepted Shannon identities')
 for field in ['Shannon','target_read_count','observed_ASVs']:
  original=pd.to_numeric(source_rows.loc[metadata.index,field],errors='raise').to_numpy(float)
  require(np.isfinite(original).all() and np.allclose(metadata[field].to_numpy(float),original,atol=1e-12,rtol=0),'Saved per-sample values differ from corrected workbook: '+field)
 for field in ['sample_key','biological_plot_id','analytic_system','collection_date']:
  require(list(metadata[field].astype(str))==list(source_rows.loc[metadata.index,field].astype(str)),'Corrected workbook sample identity differs: '+field)
 allmeta['family_display_included']=allmeta.analysis_sample.isin(set(metadata.index));allmeta['saved_Shannon_min0_included']=allmeta.analysis_sample.isin(set(metadata.index));min100=set(metadata.index[metadata.saved_Shannon_min100_included.map(truth)]);allmeta['saved_Shannon_min100_included']=allmeta.analysis_sample.isin(min100)
 require(set(allmeta.loc[allmeta.saved_Shannon_min0_included,'analysis_sample'])==set(metadata.index),'Corrected inventory omitted displayed samples')
 allmeta['recorded_feature_depth_sensitivity_included']=allmeta.depth_sensitivity_included.map(truth)
 allmeta.to_csv(out/'complete_corrected_sample_retention_inventory.csv',index=False)
 retention=[]
 for system,date in [(SYSTEM,d) for d in SHARED_DATES]+[(SEPARATE,'2025-05-21')]:
  event=allmeta.loc[allmeta.analytic_system.eq(system)&allmeta.collection_date.eq(date)].copy();reads=pd.to_numeric(event.target_read_count,errors='raise')
  retention.append({'domain':domain,'analytic_system':system,'collection_date':date,'corrected_library_records':len(event),'saved_Shannon_min0_n':int(event.saved_Shannon_min0_included.sum()),'saved_Shannon_min100_n':int(event.saved_Shannon_min100_included.sum()),'zero_target_records':int(reads.eq(0).sum()),'excluded_from_Shannon_min0':int((~event.saved_Shannon_min0_included).sum()),'recorded_feature_depth_sensitivity_n':int(event.recorded_feature_depth_sensitivity_included.sum()),'feature_flag_is_min100_alpha_flag':False})
 pd.DataFrame(retention).to_csv(out/'sample_retention_by_event.csv',index=False)
 save_json(out/'DIVERSITY_REUSE_STATUS.json',{'status':'SAVED_SHANNON_AND_MODEL_MEMBERSHIP_VERIFIED','domain':domain,'observed_alpha_recomputed':False,'inferential_tests_recomputed':False,'FDR_recomputed':False,'original_correction_event_count':4,'exact_saved_model_rows':len(model_rows),'generic_feature_depth_flag_relabelled_as_min100':False})

def style(plt):
 plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.labelsize':8,'axes.titlesize':8.2,'xtick.labelsize':8,'ytick.labelsize':8,'legend.fontsize':8,'svg.fonttype':'none','pdf.fonttype':42,'ps.fonttype':42})
def pretty_q(value):
 try:
  v=float(value)
  return format(v,'.3g') if math.isfinite(v) else 'NA'
 except (TypeError,ValueError):return 'NA'
def title_date(date):return datetime.date.fromisoformat(date).strftime('%d %b %Y')

def draw_shannon(ax,metadata,tests,descs,domain,system,date,np,y_limits=None):
 model,members=model_for(tests,domain,system,date,0);sensitivity,sens_members=model_for(tests,domain,system,date,100)
 data=metadata.set_index('analysis_sample',drop=False).rename_axis(None).loc[members].copy();groups=[x for x in ['Control','CC','CCS'] if x in set(data.treatment_label)]
 nlabels=[]
 for x,treatment in enumerate(groups):
  sub=data.loc[data.treatment_label.eq(treatment)].sort_values(['biological_replicate','analysis_sample']);n=len(sub);jitter=np.linspace(-.11,.11,n) if n>1 else np.array([0.])
  for offset,(_,row) in zip(jitter,sub.iterrows()):
   retained=str(row.analysis_sample) in sens_members
   ax.scatter(x+offset,row.Shannon,s=20,c=COLORS[treatment] if retained else 'white',edgecolors=COLORS[treatment],linewidths=.9,zorder=3,marker='o')
   if not retained:ax.annotate(str(row.analysis_sample)+'*',(x+offset,row.Shannon),xytext=(-6,7),textcoords='offset points',ha='right',va='bottom',fontsize=8)
  d=descs.loc[descs.domain.eq(domain)&descs.cohort.eq(system+'__'+date)&descs.minimum_reads.eq(0)&descs.treatment.eq(treatment)]
  require(len(d)==1,'Saved median disappeared during plotting');med=float(d.iloc[0]['median']);ax.hlines(med,x-.20,x+.20,color=COLORS[treatment],linewidth=1.4,zorder=2)
  label=treatment
  if system==SEPARATE:label='Control/EAST' if treatment=='Control' else 'CC/WEST'
  nlabels.append(label+'\n(n='+str(n)+')')
 ax.set_xticks(range(len(groups)),nlabels);ax.set_xlim(-.45,len(groups)-.55)
 if y_limits:ax.set_ylim(*y_limits)
 ax.grid(axis='y',color='#e0e4e8',linewidth=.5);ax.set_axisbelow(True);ax.spines[['top','right']].set_visible(False)
 ax.set_title(title_date(date)+'\nBH q='+pretty_q(model.q_BH)+'; BY q='+pretty_q(model.q_BY),fontsize=8.1,pad=7)
 return {'domain':domain,'cohort':system+'__'+date,'minimum_reads':0,'sample_set':model.sample_set,'saved_p':model.p,'saved_q_BH':model.q_BH,'saved_q_BY':model.q_BY,'BH_family_n':int(model.BH_family_n)}

def combined_outputs(run,cfg):
 require_hpc()
 import numpy as np
 import pandas as pd
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.lines import Line2D
 style(plt);out=run/'results'/'combined';out.mkdir(parents=True,exist_ok=False)
 metadata={d:pd.read_csv(run/'results'/d/'observed_Shannon_values_and_exact_eligibility.csv',keep_default_na=False) for d in DOMAINS}
 tests=pd.concat([pd.read_csv(run/'results'/d/'saved_Shannon_model_rows.csv',keep_default_na=False) for d in DOMAINS],ignore_index=True);descs=pd.concat([pd.read_csv(run/'results'/d/'saved_Shannon_descriptive_rows.csv',keep_default_na=False) for d in DOMAINS],ignore_index=True)
 tests.minimum_reads=pd.to_numeric(tests.minimum_reads).astype(int);descs.minimum_reads=pd.to_numeric(descs.minimum_reads).astype(int)
 annotations=[]
 fig,axes=plt.subplots(2,3,figsize=(6.5,4.7),sharey='row');fig.subplots_adjust(left=.09,right=.99,top=.85,bottom=.13,wspace=.24,hspace=.76)
 for row,domain in enumerate(DOMAINS):
  full=metadata[domain].loc[metadata[domain].analytic_system.eq(SYSTEM)];values=pd.to_numeric(full.Shannon).to_numpy(float);span=max(float(values.max()-values.min()),.25);limits=(max(0,float(values.min())-span*.10),float(values.max())+span*.16)
  for col,date in enumerate(SHARED_DATES):
   ax=axes[row,col];a=draw_shannon(ax,metadata[domain],tests,descs,domain,SYSTEM,date,np,limits);a['panel']=chr(65+row*3+col);annotations.append(a);ax.text(-.14,1.30,a['panel'],transform=ax.transAxes,fontweight='bold',fontsize=8.5)
   if col==0:ax.set_ylabel(domain.capitalize()+'\nObserved Shannon diversity')
 fig.suptitle('Oatman Flats Ranch: observed Shannon diversity across shared-field sampling events',x=.09,y=.985,ha='left',fontsize=9,fontweight='bold')
 fig.text(.09,.02,'Saved observed values and treatment medians; * outside the saved 100-read Shannon sensitivity.',fontsize=8)
 stem='Fig2_Oatman_Flats_Ranch_Shannon_all_shared_field_events';export(fig,out/stem);plt.close(fig)
 (out/(stem+'_legend.txt')).write_text('Observed bacterial and archaeal Shannon diversity across all three sampling events in the shared 2023-2024 Oatman Flats Ranch field. Panels A-C show bacteria and D-F archaea. Each point is a saved observed target-domain 16S rDNA Shannon value (natural logarithms) for one physical plot at that sampling event; lines are saved treatment medians. Treatment-group counts are printed under each panel. Vertical scales are shared across the three dates within each domain, with domain scales interpreted separately. The primary positive-target sample sets and their exact saved read-depth-adjusted treatment models are reused; plotted scores are not covariate-adjusted values. Printed BH and BY q values retain their original complete four-event correction families, including the separate 2025 event, although only the three shared-field events are displayed here. No alpha value, model or adjusted probability was recalculated. The open, labelled D094 point is retained in the primary population and absent from the saved minimum-100-read Shannon sensitivity. CC, cover crop; CCS, cover crop plus sheep.\n')
 # Separate field: never join this event to original-field trajectories.
 fig,axes=plt.subplots(1,2,figsize=(6.5,3.2));fig.subplots_adjust(left=.09,right=.985,top=.75,bottom=.22,wspace=.23)
 for i,domain in enumerate(DOMAINS):
  a=draw_shannon(axes[i],metadata[domain],tests,descs,domain,SEPARATE,'2025-05-21',np);a['panel']='2025_'+domain;annotations.append(a);axes[i].set_ylabel('Observed Shannon diversity');axes[i].text(.02,1.32,domain.capitalize(),transform=axes[i].transAxes,fontweight='bold',fontsize=8.5)
 fig.suptitle('Separate 2025 field: treatment is confounded with EAST/WEST field side',x=.09,y=.985,ha='left',fontsize=9,fontweight='bold');fig.text(.09,.035,'21 May 2025; exact saved six-sample models. This field is independent of the 2023-2024 field.',fontsize=8)
 stem='FigS3_Oatman_Flats_Ranch_Shannon_separate_2025_field';export(fig,out/stem);plt.close(fig)
 (out/(stem+'_legend.txt')).write_text('Observed bacterial and archaeal Shannon diversity in the separate Oatman Flats Ranch field sampled on 21 May 2025. Each point is one saved observed 16S rDNA target-domain Shannon value and each horizontal line is the saved treatment median; the two domains retain their own vertical scales. Control samples occupied EAST and cover-crop samples WEST, so treatment is inseparable from field side. Exact saved primary six-sample treatment-model annotations are displayed with their original BH/BY correction families. These are within-field associations, not estimates of a causal management effect or continuations of the 2023-2024 field. The complete minimum-100-read model rows remain in Supplemental Table S3, with their own adjusted probabilities even where sample membership is unchanged. No alpha scores, statistical tests or adjusted values were recalculated.\n')
 # Diagnostics reuse source read counts and complete saved threshold-specific q-values.
 fig,axes=plt.subplots(2,2,figsize=(6.5,5.8));fig.subplots_adjust(left=.095,right=.985,top=.89,bottom=.18,wspace=.30,hspace=.83)
 all_events=[(SYSTEM,d) for d in SHARED_DATES]+[(SEPARATE,'2025-05-21')];short_dates=['20 Nov 2023','06 Jun 2024','06 Dec 2024','21 May 2025*']
 for col,domain in enumerate(DOMAINS):
  ax=axes[0,col];md=metadata[domain].copy()
  for e,(system,date) in enumerate(all_events):
   part=md.loc[md.analytic_system.eq(system)&md.collection_date.eq(date)].sort_values(['treatment_label','biological_replicate','analysis_sample']);offsets=np.linspace(-.20,.20,len(part))
   for xx,(_,record) in zip(offsets,part.iterrows()):
    _,s100=model_for(tests,domain,system,date,100);member=str(record.analysis_sample) in s100;ax.scatter(e+xx,float(record.target_read_count),s=20,c=COLORS[record.treatment_label] if member else 'white',edgecolors=COLORS[record.treatment_label],linewidths=.8,zorder=3)
    if not member:ax.annotate(str(record.analysis_sample),(e+xx,float(record.target_read_count)),xytext=(-4,6),textcoords='offset points',ha='right',fontsize=8)
  inventory=pd.read_csv(run/'results'/domain/'sample_retention_by_event.csv');zero=int(pd.to_numeric(inventory.zero_target_records).sum());ax.set_yscale('log');ax.axhline(100,color='#a4aab0',linewidth=.8,linestyle='--');ax.set_xticks(range(4),short_dates,rotation=35,ha='right');ax.set_ylabel('Target-domain 16S rDNA reads');ax.set_title(chr(65+col)+'  '+domain.capitalize()+': accepted library depths',loc='left');ax.text(.02,.02,'Zero-target records excluded from Shannon: '+str(zero),transform=ax.transAxes,fontsize=8);ax.grid(axis='y',linewidth=.4,color='#e4e7e9');ax.spines[['top','right']].set_visible(False)
  ax=axes[1,col]
  for threshold,dy,marker in [(0,-.17,'o'),(100,.17,'s')]:
   xx=[];yy=[]
   for e,(system,date) in enumerate(all_events):
    m,_=model_for(tests,domain,system,date,threshold);q=float(m.q_BY);xx.append(q);yy.append(e+dy);ax.annotate('n='+str(int(m.n)),(q,e+dy),xytext=(-3 if q>.8 else 3,-2),textcoords='offset points',ha='right' if q>.8 else 'left',fontsize=8)
   ax.scatter(xx,yy,s=24,marker=marker,facecolors='#315b76' if threshold==0 else 'white',edgecolors='#315b76',linewidths=.8,label='Positive target' if threshold==0 else 'Minimum 100 reads',zorder=3)
  ax.axvline(.05,color='#aa6633',linestyle='--',linewidth=.8);ax.set_xlim(-.04,1.08);ax.set_yticks(range(4),['Nov 2023','Jun 2024','Dec 2024','May 2025*']);ax.invert_yaxis();ax.set_xlabel('Saved BY-adjusted q');ax.set_title(chr(67+col)+'  '+domain.capitalize()+': saved Shannon sensitivity',loc='left');ax.grid(axis='x',linewidth=.4,color='#e4e7e9');ax.spines[['top','right']].set_visible(False)
 treatment_handles=[Line2D([],[],color=COLORS[t],marker='o',linestyle='',label=t) for t in ['Control','CC','CCS']];fig.legend(handles=treatment_handles,loc='lower left',bbox_to_anchor=(.09,.075),ncol=3,frameon=False)
 handles=[Line2D([],[],color='#315b76',marker='o',linestyle='',label='Positive target'),Line2D([],[],color='#315b76',marker='s',markerfacecolor='white',linestyle='',label='Minimum 100 reads')];fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.09,.04),ncol=2,frameon=False)
 fig.suptitle('Oatman Flats Ranch: read-depth, retention and saved-model diagnostics',x=.095,y=.985,ha='left',fontsize=9,fontweight='bold');fig.text(.095,.017,'* Separate 2025 field. Generic feature-depth flags are distinct from the 100-read Shannon criterion.',fontsize=8)
 stem='FigS4_Oatman_Flats_Ranch_target_read_depth_and_retention';export(fig,out/stem);plt.close(fig)
 (out/(stem+'_legend.txt')).write_text('Target-read depth, sample retention and saved Shannon sensitivity diagnostics at Oatman Flats Ranch. Panels A-B display the saved target-domain read counts for every accepted positive-target Shannon sample, using logarithmic vertical axes; the dashed horizontal line marks 100 reads. Open points identify samples omitted from the exact saved minimum-100-read Shannon population. Zero-target excluded records are reported rather than placed at an artificial positive read count. Panels C-D compare saved BY-adjusted values and cohort sample sizes for the positive-target and minimum-100-read Shannon analyses at each of the four field-specific events. The dashed vertical line is q=0.05. Values retain the original four-event correction families separately within domain and threshold; they were not recalculated for the displayed subset. The 2025 field remains independent and treatment is confounded with EAST/WEST side there. The source feature-depth-sensitivity flag is exported separately in the sample inventory and is not relabelled as the 100-read Shannon criterion. D094 is retained in the primary bacterial population; excluded archaeal C094 is not reinstated.\n')
 pd.DataFrame(annotations).to_csv(out/'PANEL_SAVED_MODEL_ANNOTATIONS.csv',index=False)
 inventory=pd.concat([pd.read_csv(run/'results'/d/'complete_corrected_sample_retention_inventory.csv',keep_default_na=False) for d in DOMAINS],ignore_index=True);inventory.to_csv(out/'Table_S2_complete_sample_retention_and_source_eligibility.csv',index=False)
 tests.sort_values(['domain','cohort','minimum_reads']).to_csv(out/'Table_S3_saved_Shannon_models_all_four_events.csv',index=False)
 descs.sort_values(['domain','cohort','minimum_reads','treatment']).to_csv(out/'saved_Shannon_descriptives_all_four_events.csv',index=False)
 save_json(out/'COMBINED_STATUS.json',{'status':'OBSERVED_DIVERSITY_AND_DIAGNOSTIC_FIGURES_CREATED_REVIEW_PENDING','observed_Shannon_recalculated':False,'statistical_models_recalculated':False,'FDR_recalculated':False,'main_figure':'Fig2_Oatman_Flats_Ranch_Shannon_all_shared_field_events','supplementary_figures':['FigS3_Oatman_Flats_Ranch_Shannon_separate_2025_field','FigS4_Oatman_Flats_Ranch_target_read_depth_and_retention'],'full_original_four_event_families_preserved':True,'visual_review_complete':False})

def pcoa_domain_outputs(run,cfg,domain):
 """New descriptive geometry on Puma only; no hypothesis test is performed."""
 require_hpc()
 import numpy as np
 import pandas as pd
 out=run/'results'/domain;paths=paths_from_stage(run);md=pd.read_csv(out/'observed_Shannon_values_and_exact_eligibility.csv',keep_default_na=False);tests=pd.read_csv(out/'saved_Shannon_model_rows.csv',keep_default_na=False);tests.minimum_reads=pd.to_numeric(tests.minimum_reads).astype(int)
 counts=pd.read_csv(paths['corrected_counts_'+domain],sep='\t',index_col=0,keep_default_na=False);require(not counts.index.duplicated().any(),'PCoA counts duplicate samples')
 coords_rows=[];eigen_rows=[];audit=[];events=[(SYSTEM,d) for d in SHARED_DATES]+[(SEPARATE,'2025-05-21')]
 for system,date in events:
  model,ids=model_for(tests,domain,system,date,0);sub=md.set_index('analysis_sample',drop=False).loc[ids].copy();require(not sub.biological_plot_id.duplicated().any(),'Repeated physical plot within domain/event PCoA');X=counts.loc[ids].to_numpy(float);n=len(ids);require(np.isfinite(X).all() and (X>=0).all() and np.equal(X,np.floor(X)).all(),'Invalid PCoA target ASV counts');totals=X.sum(axis=1);require(np.array_equal(totals,pd.to_numeric(sub.target_read_count).to_numpy(float)) and (totals>0).all(),'PCoA source count total differs')
  P=X/totals[:,None];require(np.allclose(P.sum(axis=1),1,atol=1e-12,rtol=0),'Unit-sum PCoA profile failed');D=np.zeros((n,n),dtype=float)
  for i in range(n):
   for j in range(i):
    value=float(np.abs(P[i]-P[j]).sum()/(P[i].sum()+P[j].sum()));D[i,j]=D[j,i]=value
  require(np.isfinite(D).all() and np.allclose(D,D.T,atol=1e-14) and np.array_equal(np.diag(D),np.zeros(n)) and float(D.min())>=0 and float(D.max())<=1+1e-12,'Bray-Curtis distance QC failed')
  H=np.eye(n)-np.ones((n,n))/n;B=-.5*H@(D*D)@H;B=(B+B.T)/2;before=np.linalg.eigvalsh(B);tol=float(cfg['pcoa_eigen_relative_tolerance'])*max(1.,float(np.abs(before).max()));c=max(0.,-float(before.min())) if float(before.min()) < -tol else 0.
  if c>0:
   D2=D*D+2*c;np.fill_diagonal(D2,0.);Dc=np.sqrt(D2)
  else:Dc=D.copy()
  G=-.5*H@(Dc*Dc)@H;G=(G+G.T)/2;values,vectors=np.linalg.eigh(G);order=np.argsort(values)[::-1];values=values[order];vectors=vectors[:,order];posttol=float(cfg['pcoa_eigen_relative_tolerance'])*max(1.,float(np.abs(values).max()));require(float(values.min())>=-max(posttol*10,1e-10),'Lingoes-corrected eigenvalues remain materially negative')
  positive=values>posttol;ev=values[positive];V=vectors[:,positive]
  for k in range(V.shape[1]):
   anchor=int(np.argmax(np.abs(V[:,k])))
   if V[anchor,k]<0:V[:,k]*=-1
  coordinates=V*np.sqrt(ev)[None,:] if len(ev) else np.zeros((n,0));reconstructed=np.zeros_like(Dc)
  for i in range(n):
   for j in range(i):reconstructed[i,j]=reconstructed[j,i]=float(((coordinates[i]-coordinates[j])**2).sum())
  error=float(np.abs(reconstructed-Dc*Dc).max());require(error<=float(cfg['pcoa_reconstruction_squared_tolerance']),'PCoA full-coordinate squared-distance reconstruction failed')
  total=float(ev.sum());fractions=ev/total if total>0 else np.zeros_like(ev)
  for idx,sid in enumerate(ids):
   record=sub.loc[sid].to_dict();record.update({'domain':domain,'cohort':system+'__'+date,'lingoes_constant':c,'positive_axis_count':len(ev),'PCoA1':float(coordinates[idx,0]) if len(ev)>0 else 0.,'PCoA2':float(coordinates[idx,1]) if len(ev)>1 else 0.,'PCoA1_fraction':float(fractions[0]) if len(ev)>0 else 0.,'PCoA2_fraction':float(fractions[1]) if len(ev)>1 else 0.})
   for k in range(coordinates.shape[1]):record['Coordinate_'+str(k+1)]=float(coordinates[idx,k])
   coords_rows.append(record)
  for k,value in enumerate(before[::-1]):eigen_rows.append({'domain':domain,'cohort':system+'__'+date,'matrix':'uncorrected_Bray_Curtis','axis_order_descending':k+1,'eigenvalue':float(value),'lingoes_constant':c})
  for k,value in enumerate(values):eigen_rows.append({'domain':domain,'cohort':system+'__'+date,'matrix':'Lingoes_corrected' if c>0 else 'uncorrected_no_material_negative_eigenvalue','axis_order_descending':k+1,'eigenvalue':float(value),'lingoes_constant':c})
  safe=date+'_'+system;pd.DataFrame(D,index=ids,columns=ids).to_csv(out/('PCoA_Bray_Curtis_'+safe+'.csv'));pd.DataFrame(Dc,index=ids,columns=ids).to_csv(out/('PCoA_corrected_distances_'+safe+'.csv'))
  audit.append({'domain':domain,'cohort':system+'__'+date,'n':n,'sample_set':model.sample_set,'profile':'unit_sum_target_ASV_counts','distance':'Bray_Curtis','unrarefied':True,'initial_minimum_eigenvalue':float(before.min()),'negative_eigen_tolerance':tol,'lingoes_applied':c>0,'lingoes_constant':c,'final_minimum_eigenvalue':float(values.min()),'positive_axis_count':len(ev),'max_squared_distance_reconstruction_error':error,'original_distance_min':float(D.min()),'original_distance_max':float(D.max()),'no_new_statistical_test':True})
 pd.DataFrame(coords_rows).to_csv(out/'PCoA_all_positive_coordinates_and_sample_metadata.csv',index=False);pd.DataFrame(eigen_rows).to_csv(out/'PCoA_all_eigenvalues_before_and_after.csv',index=False);save_json(out/'PCoA_DISTANCE_AND_GEOMETRY_QC.json',{'status':'DESCRIPTIVE_PUMA_GEOMETRY_CREATED_REVIEW_PENDING','hypothesis_tests_added':False,'p_values_added':False,'q_values_added':False,'cohorts':audit})

def combined_pcoa_figure(run,cfg):
 require_hpc()
 import numpy as np
 import pandas as pd
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.lines import Line2D
 style(plt);out=run/'results'/'combined';fig,axes=plt.subplots(2,4,figsize=(6.5,4.9));fig.subplots_adjust(left=.09,right=.99,top=.83,bottom=.20,wspace=.57,hspace=.95);audit=[]
 for row,domain in enumerate(DOMAINS):
  coords=pd.read_csv(run/'results'/domain/'PCoA_all_positive_coordinates_and_sample_metadata.csv',keep_default_na=False)
  for col,(system,date) in enumerate([(SYSTEM,d) for d in SHARED_DATES]+[(SEPARATE,'2025-05-21')]):
   sub=coords.loc[coords.analytic_system.eq(system)&coords.collection_date.eq(date)];require(len(sub)>0,'PCoA rendering cohort absent');ax=axes[row,col]
   for _,r in sub.iterrows():
    included=truth(r.saved_Shannon_min100_included);ax.scatter(float(r.PCoA1),float(r.PCoA2),s=21,c=COLORS[r.treatment_label] if included else 'white',edgecolors=COLORS[r.treatment_label],linewidths=.8,zorder=3)
    if not included:ax.annotate(str(r.analysis_sample)+'*',(float(r.PCoA1),float(r.PCoA2)),xytext=(-2,7),textcoords='offset points',ha='right',fontsize=8)
   rank=int(sub.iloc[0].positive_axis_count);require((pd.to_numeric(sub.positive_axis_count)==rank).all(),'Mixed positive-rank metadata');ax.set_aspect('equal',adjustable='datalim');ax.set_xlabel(('PCoA1 ('+format(float(sub.iloc[0].PCoA1_fraction)*100,'.1f')+'%)') if rank>0 else 'No positive coordinate axis');ax.set_ylabel(('PCoA2 ('+format(float(sub.iloc[0].PCoA2_fraction)*100,'.1f')+'%)') if rank>1 else 'No second positive axis');
   if rank<2:ax.set_yticks([0]);ax.text(.02,.02,'One-dimensional geometry' if rank==1 else 'Zero positive inertia',transform=ax.transAxes,fontsize=8)
   ax.set_title(title_date(date)+('\nseparate field' if system==SEPARATE else ''),fontsize=8.2,pad=8);ax.text(-.33,1.30,chr(65+row*4+col),transform=ax.transAxes,fontweight='bold',fontsize=8.5);ax.grid(linewidth=.4,color='#e4e7e9');ax.spines[['top','right']].set_visible(False)
   if system==SEPARATE:ax.set_facecolor('#f1f3f5')
   audit.append({'domain':domain,'cohort':system+'__'+date,'sample_set':';'.join(sub.analysis_sample),'lingoes_constant':float(sub.iloc[0].lingoes_constant),'PCoA1_fraction':float(sub.iloc[0].PCoA1_fraction),'PCoA2_fraction':float(sub.iloc[0].PCoA2_fraction)})
 handles=[Line2D([],[],color=COLORS[t],marker='o',linestyle='',label=t) for t in ['Control','CC','CCS']]+[Line2D([],[],color='#444444',marker='o',markerfacecolor='white',linestyle='',label='Outside saved minimum-100 Shannon population')]
 fig.text(.09,.93,'Bacteria',fontweight='bold',fontsize=8.5);fig.text(.09,.485,'Archaea',fontweight='bold',fontsize=8.5)
 fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.08,.065),ncol=2,frameon=False,fontsize=8);fig.suptitle('Oatman Flats Ranch: descriptive ASV community similarity within each sampling event',x=.09,y=.985,ha='left',fontsize=9,fontweight='bold');fig.text(.09,.025,'Each domain/event has an independent ordination. Shaded panels: separate 2025 field (Control/EAST; CC/WEST).',fontsize=8)
 stem='FigS7_Oatman_Flats_Ranch_descriptive_Bray_Curtis_PCoA';export(fig,out/stem);plt.close(fig);pd.DataFrame(audit).to_csv(out/'PCoA_PANEL_AND_CORRECTION_AUDIT.csv',index=False)
 (out/(stem+'_legend.txt')).write_text('Descriptive Bray-Curtis principal-coordinate analyses (PCoA) of accepted unrarefied target-domain ASV profiles at Oatman Flats Ranch. Each panel is calculated independently for one domain and exact field-specific sampling event, using unit-sum relative ASV profiles. Squared Bray-Curtis dissimilarities are double-centered; the Lingoes adjustment adds twice the magnitude of the most negative eigenvalue to off-diagonal squared distances only when a negative eigenvalue exceeds the frozen numerical tolerance. Final positive axes define coordinates, and percentages use the sum of positive eigenvalues for that panel. The complete distances, before/after eigenvalues, correction constants and reconstruction checks accompany the figure. Points are accepted samples with treatment colors; open labelled points are outside the saved minimum-100-read Shannon population, which is a display flag and does not change the plotted primary PCoA cohort. Generic feature-depth flags remain separately recorded. Archaeal C094 is not reinstated; bacterial D094 remains in its accepted cohort. Shaded 2025 panels represent the separate field with Control/EAST and CC/WEST confounding. Axes have equal coordinate-unit aspect within panels and are not linked across dates or domains. Any rank-deficient geometry is explicitly labelled without assigning a second positive axis. Observed community similarity can be influenced by read depth and composition; visible clustering is not a treatment test. No PERMANOVA, dispersion test, new treatment p-value or q-value was calculated.\n')
 save_json(out/'PCoA_COMBINED_STATUS.json',{'status':'DESCRIPTIVE_ORDINATION_FIGURE_CREATED_REVIEW_PENDING','hypothesis_tests_added':False,'p_values_added':False,'q_values_added':False,'visual_review_complete':False})
