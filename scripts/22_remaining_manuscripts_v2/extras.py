"""Saved functional trajectories, exact CLR/soil pairs, and bounded synthesis displays."""
from common import *

def run(run,c):
 require_hpc()
 import numpy as np
 import pandas as pd
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 import matplotlib.dates as mdates
 from matplotlib.lines import Line2D
 style(plt);run=Path(run);root=run/'results'/'extras';root.mkdir(parents=True)
 obs=frame('followup__all_observations',run,c,pd)
 follow=frame('followup__tests_with_multiplicity',run,c,pd)
 target=frame('functions__targeted_tests',run,c,pd)
 robust=frame('functions__robustness',run,c,pd)
 values=frame('functions__feature_sample_values',run,c,pd)
 allpairs=[];allstats=[];pairqc=[];figure_count=0
 for spec in c['paired_panels']:
  farm,system=spec['farm'],spec['system'];nr=len(spec['features'])
  fig,axs=plt.subplots(nr,2,figsize=(7.2,4.7 if nr==1 else 3.0*nr+1.35),squeeze=False)
  fig.subplots_adjust(left=.11,right=.97,top=.80 if nr==1 else .90,bottom=.40 if nr==1 else .13,hspace=1.2,wspace=.40)
  for i,(family,feature,label) in enumerate(spec['features']):
   for j,scope in enumerate(('primary','depth_sensitivity')):
    ax=axs[i,j]
    test=one(follow,follow.analysis_class.eq('scalar')&follow.farm.eq(farm)&follow.system.eq(system)&follow.domain.eq('bacteria')&follow.variant.eq('original')&follow.scope.eq(scope)&follow.family.eq(family)&follow.endpoint.eq(feature),'between-treatment paired-change row')
    sub=obs.loc[obs.hypothesis_id.eq(test.hypothesis_id)&obs.analysis_class.eq('scalar')].copy()
    pairs=json.loads(test.ordered_pairs_json);keys=['biological_plot_id','early_sample','late_sample','treatment']
    need(not sub.biological_plot_id.duplicated().any() and len(sub)==int(test.n_independent_pairs),'Paired physical-plot membership invalid')
    need(sorted(tuple(r[k] for k in keys) for r in pairs)==sorted(tuple(r[k] for k in keys) for _,r in sub.iterrows()),'Saved ordered-pair manifest differs')
    need(sub.early_date.eq(test.early_date).all() and sub.late_date.eq(test.late_date).all(),'Functional paired dates differ')
    contrast=test.early_date+' to '+test.late_date
    pooled=one(target,target.domain.eq('bacteria')&target.variant.eq('original')&target.scope.eq(scope)&target.farm.eq(farm)&target.analytic_system.eq(system)&target.family.eq(family)&target.function.eq(feature)&target.test_type.eq('paired_shared_date_change')&target.contrast.eq(contrast),'pooled within-plot change')
    adjustment=one(robust,robust.hypothesis_id.eq(pooled.hypothesis_id),'saved pooled-change multiplicity row')
    need(int(pooled.n_independent_pairs)==len(sub),'Pooled-change and follow-up pair counts differ')
    reference=values.loc[values.domain.eq('bacteria')&values.variant.eq('original')&values.scope.eq(scope)&values.farm.eq(farm)&values.analytic_system.eq(system)&values.family.eq(family)&values.function.eq(feature)]
    for _,r in sub.iterrows():
     cc=code_for(r,c)
     for side in ('early','late'):
      src=one(reference,reference.analysis_sample.eq(r[side+'_sample'])&reference.collection_date.eq(r[side+'_date']),'paired source endpoint')
      need(src.biological_plot_id==r.biological_plot_id and src.treatment==r.treatment,'Paired endpoint identity mismatch')
      need(np.isclose(float(src.relative_abundance),float(r[side+'_value']),rtol=1e-10,atol=1e-12),'Paired value disagrees with normalized source')
     a,b=float(r.early_value),float(r.late_value)
     need(np.isfinite([a,b]).all() and min(a,b)>=0 and max(a,b)<=1,'Invalid relative-abundance endpoint')
     need(np.isclose(b-a,float(r.change),rtol=1e-10,atol=1e-12),'Saved paired change differs')
     ax.plot([0,1],[100*a,100*b],color=COLORS[cc],marker='o',markersize=3.2,lw=.8)
    need(np.isclose(pd.to_numeric(sub.change).mean(),float(pooled.mean_change),rtol=1e-10,atol=1e-12),'Displayed pairs do not reproduce saved mean change')
    ax.set_xlim(-.15,1.15);ax.set_xticks([0,1],[date(test.early_date),date(test.late_date)],fontsize=7.5)
    ax.set_ylabel('Predicted relative proportion (%)');ax.ticklabel_format(axis='y',style='plain',useOffset=False)
    ax.set_title(chr(65+2*i+j)+'  '+label+'\n'+('Primary' if scope=='primary' else 'Saved depth sensitivity')+'; n = '+str(len(sub)),loc='left',fontsize=8)
    between='Holm p = '+format(float(test.holm_p_value),'.3g') if test.status=='tested' and test.holm_p_value else 'unestimated'
    pooled_p='Holm p = '+format(float(adjustment.holm_p_conservative),'.3g') if pooled.status=='TESTED' and adjustment.holm_p_conservative else 'unestimated'
    ax.text(0,-.32,'Pooled change: '+pooled_p+'\nBetween-treatment change: '+between,transform=ax.transAxes,fontsize=7.3,va='top')
    ax.spines[['top','right']].set_visible(False)
    sub['display_code']=[code_for(r,c) for _,r in sub.iterrows()];allpairs.append(sub)
    allstats.append({'panel_system':system,'panel_scope':scope,'panel_feature':feature,'followup_hypothesis':test.hypothesis_id,'pooled_hypothesis':pooled.hypothesis_id,'followup_status':test.status,'followup_reason':test.reason,'followup_Holm_p':test.holm_p_value,'pooled_status':pooled.status,'pooled_conservative_Holm_p':adjustment.holm_p_conservative,'pooled_correction_family':adjustment.multiplicity_family_id,'followup_correction_family':test.multiplicity_family})
    pairqc.append({'system':system,'scope':scope,'feature':feature,'n':len(sub),'pair_manifest_verified':True,'normalized_endpoints_verified':True,'new_tests':False})
  syspec=next(s for s in c['systems'] if s['system']==system)
  fig.suptitle(farm+' | '+syspec['label']+'\nFixed-date predicted functional trajectories',x=.11,y=.98,ha='left',fontsize=10,fontweight='bold')
  fig.legend(handles=[Line2D([],[],color=COLORS[k],marker='o',label=k) for k in syspec['treatments'].values()],loc='lower center',bbox_to_anchor=(.5,.025),ncol=4,frameon=False)
  folder=root/farm.replace(' ','');stem=spec['slug']+'_paired_functions'
  finish(fig,folder,stem,'Saved bacterial predicted functional profiles in '+syspec['label']+'. Each line joins the same physical plot at the two printed dates. Values are relative proportions of the complete saved feature profile, multiplied by 100 for display; they are predicted potential, not measured expression, activity, flux or absolute gene abundance. Primary and saved depth-sensitivity populations remain separate. Pooled temporal change uses the original fixed-date paired test and its complete conservative Holm family. Between-treatment change uses the subsequent whole-plot change test and its own complete planned Holm family. An unestimated test is not a nonsignificant test. In particular, the Petite Sirah follow-up cannot estimate the planned contrast with one surviving control pair. Neither omnibus result establishes pairwise treatment superiority. Saved inference remains conditional on the documented exchangeability assumptions; the field plans do not establish random assignment. Exact endpoints, pair manifests, complete tests and correction-family tables are supplied.',plt);figure_count+=1
 export_table(pd.concat(allpairs,ignore_index=True),root,'selected_paired_endpoints.csv')
 export_table(pd.DataFrame(allstats),root,'selected_paired_test_index.csv');js(root/'paired_coordinate_QC.json',pairqc)
 # Exact soil/CLR examples use complete frozen caches. No selected-feature CLR is fitted.
 display=frame('association__association_display_rows',run,c,pd)
 frozen=json.loads((BASE/'SELECTED_ASSOCIATIONS.json').read_text());need([r['test_id'] for r in frozen]==c['selected_association_ids'],'Selected association contract differs')
 caches={};scatter={};qc=[]
 for saved in frozen:
  actual=one(display,display.test_id.eq(saved['test_id']),'frozen association')
  need(all(str(actual[k])==str(v) for k,v in saved.items()),'Saved association row changed')
  need(saved['scenario']=='PRIMARY__PRIMARY__PRIMARY' and float(saved['q_by_global'])==1,'Selected association scenario/correction differs')
  shard,klass=saved['shard_id'],saved['feature_class'];prefix=shard+'__'
  cohort_table=frame(prefix+'cohorts',run,c,pd);scenario=frame(prefix+'scenario_cohorts',run,c,pd)
  cohort=one(cohort_table,cohort_table.cohort_id.eq(saved['cohort_id']),'endpoint cohort')
  member=one(scenario,scenario.cohort_id.eq(saved['cohort_id'])&scenario.feature_class.eq(klass)&scenario.scenario.eq(saved['scenario']),'feature-class cohort')
  ids=json.loads(member.sample_ids_json)
  need(len(ids)==len(set(ids))==int(saved['n']) and ids==json.loads(cohort.primary_sample_ids_json),'Association membership invalid')
  selected=frame(prefix+'matched_endpoints',run,c,pd)
  for key,value in json.loads(cohort.traits_json).items():
   field='collection_date' if key=='event' else key;need(field in selected,'Missing endpoint trait '+field);selected=selected.loc[selected[field].eq(str(value))]
  selected=selected.loc[selected.analysis_sample.isin(ids)].copy()
  need(len(selected)==len(ids) and not selected.analysis_sample.duplicated().any(),'Ambiguous matched endpoint')
  selected=selected.set_index('analysis_sample').loc[ids]
  meta=frame(prefix+'sample_metadata',run,c,pd);need(not meta.analysis_sample.duplicated().any(),'Duplicate source sample metadata');meta=meta.set_index('analysis_sample').loc[ids]
  need(len(set(selected.biological_plot_id))==len(ids),'Duplicated physical plot in association')
  for field in ('biological_plot_id','treatment','collection_date','analytic_system'):need(selected[field].tolist()==meta[field].tolist(),'Association identity differs: '+field)
  need(selected.collection_date.eq(saved['event']).all() and selected.analytic_system.eq(saved['analytic_system']).all(),'Association event/system differs')
  need(sorted(selected.biological_plot_id)==sorted(json.loads(member.plot_ids_json)),'Association physical-plot manifest differs')
  role='CLR__'+shard+'__'+klass
  if role not in caches:caches[role]=pd.read_csv(source(role,run,c),index_col=0,dtype=str,keep_default_na=False)
  clr=caches[role];need(not clr.index.duplicated().any() and not clr.columns.duplicated().any() and saved['feature_id'] in clr and set(ids).issubset(clr.index),'Frozen CLR reference incomplete')
  x=pd.to_numeric(selected.value).to_numpy(float);y=pd.to_numeric(clr.loc[ids,saved['feature_id']]).to_numpy(float)
  need(np.isfinite(x).all() and np.isfinite(y).all(),'Nonfinite association coordinates')
  rho=float(np.corrcoef(pd.Series(x).rank(method='average'),pd.Series(y).rank(method='average'))[0,1]);need(np.isclose(rho,float(saved['rho']),atol=1e-10,rtol=1e-10),'Coordinates do not reproduce saved rho')
  selected=selected.reset_index();selected['farm']=saved['farm'];selected['display_code']=[code_for(r,c) for _,r in selected.iterrows()]
  selected['selected_test_id']=saved['test_id'];selected['frozen_full_reference_CLR']=y
  for k in ('feature_class','feature_id','feature_label','rho','q_by_global','cohort_id'):selected['saved_'+k]=saved[k]
  scatter[saved['test_id']]=selected;qc.append({'test_id':saved['test_id'],'n':len(ids),'rho_coordinate_check':rho,'saved_global_BY_q':saved['q_by_global'],'CLR_recomputed':False,'p_or_q_recomputed':False})
 export_table(pd.concat(scatter.values(),ignore_index=True),root,'all_selected_exact_CLR_soil_pairs.csv');export_table(pd.DataFrame(frozen),root,'all_selected_association_saved_rows.csv');js(root/'association_coordinate_QC.json',qc)
 groups=[]
 for farm in ('Page Springs','Whipstone'):
  group=[s for s in frozen if s['farm']==farm]
  for i in range(0,len(group),4):groups.append((farm,group[i:i+4],farm.replace(' ','')+'_soil_associations_'+str(i//4+1)))
 groups.append(('Synthesis',[s for s in frozen if s['test_id'] in ('2589580486ce284278bc7716','18ec6fb6ac0eb34ca401c8ee','14f70d1002747fd6ae04a4e7')],'Synthesis_predicted_function_soil_examples'))
 for farm,group,stem in groups:
  fig,axs=plt.subplots(2,2,figsize=(7.2,8.0));fig.subplots_adjust(left=.11,right=.97,top=.88,bottom=.23,hspace=1.2,wspace=.45)
  codes=[]
  for i,ax in enumerate(axs.flat):
   if i>=len(group):ax.set_visible(False);continue
   saved=group[i];ss=scatter[saved['test_id']]
   for code,g in ss.groupby('display_code',sort=False):
    ax.scatter(pd.to_numeric(g.value),pd.to_numeric(g.frozen_full_reference_CLR),s=25,color=COLORS[code],edgecolor='white',linewidth=.4);codes.append(code)
   label=saved['feature_label'].replace('Candidatus ','Ca. ')
   ax.set_title(chr(65+i)+'  '+wrap(label,37)+'\n'+saved['analytic_system'].replace('Grape_','').replace('Blackberry_','')+' | '+date(saved['event']),loc='left',fontsize=8)
   # The source variable already carries assay/unit wording; do not relabel assays as interchangeable.
   ax.set_xlabel(wrap(saved['variable'],35),fontsize=7.5);ax.set_ylabel('Full-reference '+saved['feature_class']+' CLR');ax.spines[['top','right']].set_visible(False)
   ax.text(0,-.40,'n = '+saved['n']+'; rho = '+format(float(saved['rho']),'.3f')+'\nGlobal BY q = 1.0',transform=ax.transAxes,va='top',fontsize=7.5)
  fig.suptitle(farm+' | Selected descriptive soil associations',x=.11,y=.975,ha='left',fontsize=10,fontweight='bold')
  fig.legend(handles=[Line2D([],[],marker='o',ls='',color=COLORS[k],label=k) for k in dict.fromkeys(codes)],loc='lower left',bbox_to_anchor=(.10,.085),ncol=4,frameon=False)
  fig.text(.11,.035,'Marginal across-treatment examples; none survives the saved global BY correction.',fontsize=7.5)
  finish(fig,root/farm.replace(' ',''),stem,'Selected source-matched descriptive associations. Exact farm, system, collection date, measured assay, units, biological plots and frozen feature-class reference populations are retained in the accompanying tables. CLR coordinates are read from complete saved matrices; the selected subset is not renormalized. Saved Spearman rho is reproduced only as a coordinate integrity check. Every displayed original primary global BY q equals 1.0. These marginal across-treatment relationships are not treatment-adjusted estimates, within-treatment effects or evidence of organism–function coupling. No new p value, selected-subset correction, regression line or confidence band is fitted. Functional features describe predicted relative potential; taxonomic features describe relative composition. Treatment codes identify context and are not harmonized into a common intervention across farms.',plt);figure_count+=1
 # Cross-system context: physical systems stay distinct and use actual events.
 md=workbook(run,c,pd,'16S samples');md=md.loc[md.primary_included.map(truth)&~md.descriptive_reference.map(truth)].copy()
 systems=[('Oatman Flats','OFR_2023_2024_Field','Oatman 2023–2024 field'),('Oatman Flats','OFR_2025_Field','Oatman separate 2025 field')]+[(s['farm'],s['system'],s['farm']+' — '+s['label']) for s in c['systems']]
 fig,ax=plt.subplots(figsize=(7.2,4.7));fig.subplots_adjust(left=.38,right=.97,top=.83,bottom=.24);events=[]
 for i,(farm,system,label) in enumerate(systems):
  group=md.loc[md.farm.eq(farm)&md.analytic_system.eq(system)];need(len(group)>0,'Missing synthesis system')
  dates=sorted(group.collection_date.unique());ax.scatter([datetime.date.fromisoformat(d) for d in dates],[i]*len(dates),s=30,color='#247A88')
  for d in dates:
   for domain in ('bacteria','archaea'):
    sub=group.loc[group.collection_date.eq(d)&group.microbial_domain.eq(domain)]
    events.append({'farm':farm,'system':system,'date':d,'domain':domain,'primary_n':len(sub),'sample_ids_json':json.dumps(sub.analysis_sample.tolist()),'biological_plot_ids_json':json.dumps(sub.biological_plot_id.tolist())})
 ax.set_yticks(range(len(systems)),[x[2] for x in systems],fontsize=8);ax.invert_yaxis();ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6));ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'));ax.grid(axis='x',color='#E1E5E7',lw=.6);ax.spines[['top','right','left']].set_visible(False)
 fig.suptitle('Cross-farm synthesis | Actual microbiome collection events',x=.07,y=.97,ha='left',fontsize=10,fontweight='bold');fig.text(.07,.07,'Each row is a separate physical system; no cross-farm or cross-field trajectory is fitted.\nWhipstone annual succession: lettuce (2023), onion (2024), marigolds (2025).',fontsize=8)
 finish(fig,root/'Synthesis','Synthesis_collection_context','Actual collection events from the corrected primary 16S metadata. Separate rows preserve the seven physical systems, including the two distinct Oatman fields and both blackberry cultivars. Dots mark observed events, not evenly spaced experimental visits or a prospective monitoring design. Counts and exact domain-specific sample/plot membership are exported separately. Reference-only unplanted samples are retained in the complete inventory but are not displayed as experimental replicates. Annual crop succession follows the author-confirmed study crop sequence, not prospective field-plan crop notes.',plt);figure_count+=1
 export_table(pd.DataFrame(events),root/'Synthesis','collection_event_membership.csv')
 # Compare already-saved system-specific temporal estimates without fitting a pooled model.
 syn=[]
 for farm,system,label in [x for x in systems if x[1]!='OFR_2025_Field']:
  available=follow.loc[follow.analysis_class.eq('scalar')&follow.farm.eq(farm)&follow.system.eq(system)&follow.domain.eq('bacteria')&follow.variant.eq('original')&follow.scope.eq('primary')&follow.family.eq('METACYC')&follow.endpoint.eq('GLYCOLYSIS')]
  need(len(available)<=1,'Ambiguous synthesis follow-up row')
  t=one(target,target.domain.eq('bacteria')&target.variant.eq('original')&target.scope.eq('primary')&target.farm.eq(farm)&target.analytic_system.eq(system)&target.family.eq('METACYC')&target.function.eq('GLYCOLYSIS')&target.test_type.eq('paired_shared_date_change'),'synthesis saved glycolysis estimate')
  if len(available):
   f=available.iloc[0];need(t.contrast==f.early_date+' to '+f.late_date,'Synthesis fixed-date contrasts differ');follow_status=f.status;follow_p=f.holm_p_value;follow_reason=f.reason
  else:follow_status='not_in_saved_followup_scope';follow_p='';follow_reason='No between-treatment scalar follow-up row in the supplied analysis scope'
  adj=one(robust,robust.hypothesis_id.eq(t.hypothesis_id),'synthesis original adjustment')
  record=t.to_dict();record.update(display_label=label,saved_conservative_Holm_p=adj.holm_p_conservative,saved_correction_family=adj.multiplicity_family_id,followup_between_treatment_status=follow_status,followup_between_treatment_Holm_p=follow_p,followup_reason=follow_reason);syn.append(record)
 fig,ax=plt.subplots(figsize=(7.2,5.8));fig.subplots_adjust(left=.34,right=.76,top=.86,bottom=.22)
 for i,r in enumerate(syn):
  need(r['status']=='TESTED','Expected saved synthesis estimate is unestimated')
  mean,lo,hi=[100*float(r[k]) for k in ('mean_change','ci_low','ci_high')];need(np.isfinite([mean,lo,hi]).all() and lo<=mean<=hi,'Invalid saved interval')
  ax.errorbar(mean,i,xerr=[[mean-lo],[hi-mean]],fmt='o',color='#247A88',capsize=3,ms=4)
  ax.text(1.04,i,'n = '+r['n_independent_pairs']+'\nHolm p = '+format(float(r['saved_conservative_Holm_p']),'.3g'),transform=ax.get_yaxis_transform(),fontsize=7.5,va='center')
 labels=[r['display_label']+'\n'+r['contrast'] for r in syn];ax.set_yticks(range(len(syn)),labels,fontsize=7.5);ax.invert_yaxis();ax.axvline(0,color='#777777',ls=':',lw=.8);ax.set_xlabel('Saved mean late − early change (percentage points)');ax.spines[['top','right','left']].set_visible(False)
 fig.suptitle('Cross-farm synthesis | Predicted glycolysis I',x=.07,y=.98,ha='left',fontsize=10,fontweight='bold');fig.text(.07,.07,'System-specific paired changes and approximate 95% intervals; no pooled effect.\nIntervals are unadjusted; printed Holm p values retain the complete original test family.\nThe separate Oatman 2025 field has no comparable paired temporal contrast.',fontsize=8)
 finish(fig,root/'Synthesis','Synthesis_glycolysis_saved_changes','Six already-saved bacterial primary-population glycolysis I temporal estimates, each using its own fixed early/late date pair and matched physical plots. Points and approximate paired-t 95% intervals are copied from saved source tables and converted from relative proportions to percentage points. Intervals are not multiplicity-adjusted and are fragile under small-sample distributional assumptions. Printed conservative Holm p values retain the complete original family; no six-panel adjustment or meta-analysis is computed. Different systems, treatments, time intervals and environmental histories prevent a common causal treatment estimate. Temporal change across treatments does not establish between-treatment superiority. Predicted relative pathway representation does not measure pathway activity or flux. The separate Oatman 2025 field has no corresponding paired series.',plt);figure_count+=1
 export_table(pd.DataFrame(syn),root/'Synthesis','glycolysis_complete_saved_rows.csv')
 js(root/'STATUS.json',{'state':'COMPLETE_REVIEW_PENDING','figures_expected':figure_count,'new_inferential_tests':False,'manual_visual_review_complete':False})
