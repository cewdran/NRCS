"""Descriptive community figures on Puma; existing alpha inference is copied unchanged."""
from pathlib import Path
import json,re,csv
from common import *

def run(run,c):
 require_hpc()
 import numpy as np
 import pandas as pd
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.patches import Patch
 from matplotlib.lines import Line2D
 style(plt);run=Path(run);root=run/'results'/'communities';root.mkdir(parents=True)
 full=workbook(run,c,pd,'16S samples');full['analysis_sample']=full.analysis_sample.astype(str)
 need(not full.duplicated(['microbial_domain','analysis_sample']).any(),'Duplicate corrected sample identity')
 tests=frame('corrected_alpha_tests',run,c,pd);tests.minimum_reads=pd.to_numeric(tests.minimum_reads)
 cohorts=[];counts_by={};family_by={};keys_by={};retentions=[];all_eigen=[];all_geom=[]
 for domain in ('bacteria','archaea'):
  counts=pd.read_csv(source('corrected_counts_'+domain,run,c),sep='\t',index_col=0,keep_default_na=False)
  need(not counts.index.duplicated().any() and not counts.columns.duplicated().any(),'Duplicate ASV matrix IDs')
  tax=pd.read_csv(source('taxonomy_'+domain,run,c),sep='\t',index_col=0,keep_default_na=False)
  need(not tax.index.duplicated().any() and set(counts.columns).issubset(tax.index),'Missing or duplicate ASV taxonomy')
  tax=tax.loc[counts.columns];ranks=['Kingdom','Phylum','Class','Order','Family']
  def normalize(v,rank):
   v=str(v).strip();prefix=dict(Kingdom='d__',Phylum='p__',Class='c__',Order='o__',Family='f__')[rank]
   if v.lower().startswith(prefix):v=v[len(prefix):]
   if v.lower() in ('','na','n/a','unclassified','unassigned','uncultured','unknown','ambiguous_taxa','metagenome') or re.match(r'^(?:uncultured|uncultivated|unclassified|unidentified|unassigned)(?:[_\s]|$)',v,re.I):return '[unclassified]'
   return v
  lineage=[];keymeta={};asvkey=[]
  for sid,row in tax.iterrows():
   record={r:normalize(row[r],r) for r in ranks};need(record['Kingdom'].lower()==domain,'Wrong taxonomy target domain')
   key=json.dumps(list(record.items()),ensure_ascii=False,separators=(',',':'));lineage.append(key);keymeta[key]=record;asvkey.append({'asv_id':sid,'lineage_key':key,**record})
  family=counts.T.groupby(pd.Index(lineage),sort=True).sum().T
  counts_by[domain]=counts;family_by[domain]=family;keys_by[domain]=keymeta
  export_table(pd.DataFrame(asvkey),root,domain+'_ASV_to_full_family_lineage.csv')
  for farm in ('Page Springs','Whipstone'):
   inv=full.loc[full.farm.eq(farm)&full.microbial_domain.eq(domain)].copy()
   base=inv.loc[inv.primary_included.map(truth)&~inv.descriptive_reference.map(truth)].copy()
   need((pd.to_numeric(base.target_read_count)>0).all(),'Nonpositive accepted primary member')
   need(set(base.analysis_sample).issubset(counts.index),'Accepted sample missing from counts')
   x=counts.loc[base.analysis_sample].to_numpy(float)
   need(np.isfinite(x).all() and (x>=0).all() and np.equal(x,np.floor(x)).all(),'Invalid ASV counts')
   need(np.array_equal(x.sum(1),pd.to_numeric(base.target_read_count).to_numpy(float)),'Target-read totals disagree with saved metadata')
   inv['figure_primary']=inv.analysis_sample.isin(base.analysis_sample);inv['saved_alpha_min100']=False
   ft=tests.loc[tests.farm.eq(farm)&tests.domain.eq(domain)].copy()
   for _,t in ft.iterrows():
    ids=[v for v in t.sample_set.split(';') if v];need(len(ids)==len(set(ids))==int(t.n),'Saved alpha membership/n mismatch')
    # The source reference-only cohort is preserved in tables, not plotted as a treatment replicate.
    group=inv.loc[inv.cohort.eq(t.cohort)&inv.primary_included.map(truth)]
    if int(t.minimum_reads)==100:group=group.loc[pd.to_numeric(group.target_read_count)>=100]
    need(set(ids)==set(group.analysis_sample),'Saved alpha membership differs from corrected cohort '+t.cohort)
    if int(t.minimum_reads)==100:inv.loc[inv.analysis_sample.isin(ids),'saved_alpha_min100']=True
   folder=root/farm.replace(' ','');folder.mkdir(exist_ok=True)
   export_table(inv,folder,domain+'_complete_sample_inventory.csv')
   export_table(ft,folder,domain+'_saved_alpha_tests_all_rows.csv')
   export_table(frame('corrected__alpha_descriptive',run,c,pd).loc[lambda x:x.farm.eq(farm)&x.domain.eq(domain)],folder,domain+'_saved_alpha_descriptives_all_rows.csv')
   fam=family.loc[base.analysis_sample];need(np.array_equal(fam.sum(1).to_numpy(float),x.sum(1)),'Family count loss')
   rel=fam.div(fam.sum(1),axis=0);need(np.allclose(rel.sum(1),1,atol=1e-12,rtol=0),'Family closure failed')
   active=[k for k in rel if (rel[k]>0).any()];named=[k for k in active if keymeta[k]['Family']!='[unclassified]'];means=rel[named].mean(0)
   top=sorted(named,key=lambda k:(-float(means[k]),k))[:c['top_families']]
   labels={k:keymeta[k]['Family'] for k in top}
   for k in top:
    same=[j for j in top if labels[j]==labels[k]]
    if len(same)>1:labels[k]=keymeta[k]['Family']+' ['+str(sorted(same).index(k)+1)+']'
   # Detect duplicated display names independently of iteration order.
   for k in top:
    same=sorted(j for j in top if keymeta[j]['Family']==keymeta[k]['Family'])
    labels[k]=keymeta[k]['Family']+(' ['+str(same.index(k)+1)+']' if len(same)>1 else '')
   display=pd.DataFrame({labels[k]:rel[k] for k in top},index=rel.index)
   omitted=[k for k in named if k not in top];unknown=[k for k in active if keymeta[k]['Family']=='[unclassified]']
   if omitted:display['Other named families']=rel[omitted].sum(1)
   if unknown:display['Unclassified family']=rel[unknown].sum(1)
   need(np.allclose(display.sum(1),1,atol=1e-12,rtol=0),'Display denominator loss')
   rel.to_csv(folder/(domain+'_all_family_relative_abundances.csv.gz'));display.to_csv(folder/(domain+'_display_family_proportions.csv.gz'))
   export_table(pd.DataFrame([{'display_label':labels[k],'lineage_key':k,**keymeta[k]} for k in top]),folder,domain+'_family_display_key.csv')
   palette=list(plt.get_cmap('tab20').colors);col={v:palette[i%20] for i,v in enumerate(display)}
   col.update({'Other named families':'#BBC1C7','Unclassified family':'#555D65'})
   for spec in [s for s in c['systems'] if s['farm']==farm]:
    system=spec['system'];m=inv.loc[inv.analytic_system.eq(system)].copy();m['display_code']=[code_for(row,c) for _,row in m.iterrows()]
    m=m.sort_values(['collection_date','display_code','biological_replicate','analysis_sample']);primary=m.loc[m.figure_primary].copy()
    need(sorted(primary.collection_date.unique())==spec['dates'],'Unexpected collection-event set '+system)
    need(not primary.duplicated(['collection_date','biological_plot_id']).any(),'Repeated physical plot in event')
    for d in spec['dates']:
     pp=primary.loc[primary.collection_date.eq(d)]
     retentions.append({'farm':farm,'domain':domain,'system':system,'date':d,'primary_n':len(pp),'minimum100_n':int(pp.saved_alpha_min100.sum()),'recorded_feature_depth_n':int(pp.depth_sensitivity_included.map(truth).sum()),'corrected_records':int(m.collection_date.eq(d).sum())})
    fig,axs=plt.subplots(2,2,figsize=(7.1,7.5));fig.subplots_adjust(left=.09,right=.98,top=.90,bottom=.31,hspace=.78,wspace=.25)
    members=[]
    for idx,ax in enumerate(axs.flat):
     if idx>=len(spec['dates']):ax.set_visible(False);continue
     d=spec['dates'][idx];sub=primary.loc[primary.collection_date.eq(d)];ids=list(sub.analysis_sample);bottom=np.zeros(len(ids));xx=np.arange(len(ids))
     for label in display:
      height=display.loc[ids,label].to_numpy(float)*100;ax.bar(xx,height,bottom=bottom,color=col[label],width=.84,edgecolor='white',linewidth=.2);bottom+=height
     labs=[str(r.display_code)+'-'+str(int(float(r.biological_replicate)))+(' *' if not truth(r.depth_sensitivity_included) else '')+'\n'+r.analysis_sample for _,r in sub.iterrows()]
     ax.set_xticks(xx,labs,rotation=90);ax.set_ylim(0,100);ax.set_ylabel('Relative reads (%)');ax.set_title(chr(65+idx)+'  '+date(d)+'; n = '+str(len(ids)),loc='left');ax.spines[['top','right']].set_visible(False)
     members.extend(sub.to_dict('records'))
    fig.suptitle(farm+' | '+spec['label']+' | '+domain+' families',x=.09,ha='left',y=.975,fontsize=10,fontweight='bold')
    fig.legend(handles=[Patch(facecolor=col[k],label=wrap(k,37)) for k in display],loc='lower left',bbox_to_anchor=(.08,.075),ncol=2,frameon=False,fontsize=7.5)
    fig.text(.09,.025,'* Outside source feature-depth population. Individual libraries; complete target-read denominator.',fontsize=7.3)
    stem=spec['slug']+'_'+domain+'_families';finish(fig,folder,stem,'Individual-sample '+domain+' family composition in '+spec['label']+'. Panels retain actual collection events. Proportions use target-filtered unrarefied ASV counts and complete lineage keys. The twelve highest-mean named lineages across accepted '+farm+' '+domain+' samples define the shared display palette; remaining named and unresolved families retain all read mass. This is a display selection, not a significance screen. Asterisks use the saved feature-depth flag, which is distinct from minimum-100-read Shannon eligibility. Codes match the manuscript; source sample and biological-plot IDs are supplied separately. Relative reads do not measure absolute abundance or activity.',plt)
    export_table(pd.DataFrame(members),folder,stem+'_sample_key.csv')
 # Both domains share one figure per system. Date panels never inherit a year-cohort p value.
 for spec in c['systems']:
  farm=spec['farm'];system=spec['system'];folder=root/farm.replace(' ','');dates=spec['dates'];nc=len(dates)
  for kind in ('Shannon','depth','PCoA'):
   fig,axs=plt.subplots(2,nc,figsize=(7.2,6.5),squeeze=False);fig.subplots_adjust(left=.095,right=.975,top=.87,bottom=.22,wspace=.75,hspace=.75)
   data=[];qc=[]
   for di,domain in enumerate(('bacteria','archaea')):
    inv=pd.read_csv(folder/(domain+'_complete_sample_inventory.csv'),keep_default_na=False);inv['figure_primary']=inv.figure_primary.map(truth);inv['saved_alpha_min100']=inv.saved_alpha_min100.map(truth)
    for j,d in enumerate(dates):
     ax=axs[di,j];allrows=inv.loc[inv.analytic_system.eq(system)&inv.collection_date.eq(d)].copy();sub=allrows.loc[allrows.figure_primary].copy();sub['display_code']=[code_for(r,c) for _,r in sub.iterrows()];sub=sub.sort_values(['display_code','biological_replicate','analysis_sample']);codes=list(spec['treatments'].values())
     ax.set_title(chr(65+di*nc+j)+'  '+date(d)+'\n'+domain+'; n = '+str(len(sub)),fontsize=8,loc='left')
     if kind in ('Shannon','depth'):
      for k,code in enumerate(codes):
       g=sub.loc[sub.display_code.eq(code)];xx=k+np.linspace(-.12,.12,len(g)) if len(g)>1 else np.array([k]) if len(g) else np.array([])
       yy=pd.to_numeric(g.Shannon if kind=='Shannon' else g.target_read_count).to_numpy(float)
       need(np.isfinite(yy).all(),'Nonfinite plotted saved value')
       for x,y,(_,r) in zip(xx,yy,g.iterrows()):
        ax.scatter(x,y,s=20,facecolor=COLORS[code] if r.saved_alpha_min100 else 'white',edgecolor=COLORS[code],linewidth=.8,zorder=3)
       if kind=='Shannon' and len(yy):ax.plot([k-.22,k+.22],[np.median(yy)]*2,color=COLORS[code],lw=1.6)
      ax.set_xticks(range(len(codes)),codes,rotation=45);ax.set_xlim(-.5,len(codes)-.5)
      if kind=='Shannon':ax.set_ylabel('Observed ASV Shannon');ax.set_ylim(bottom=0)
      else:
       ax.set_yscale('symlog',linthresh=1);ax.set_ylabel('Target reads');ax.axhline(100,color='#777777',ls=':',lw=.7)
       excluded=allrows.loc[~allrows.figure_primary]
       for _,r in excluded.iterrows():
        code=code_for(r,c);ax.scatter(codes.index(code),float(r.target_read_count),marker='x',color=COLORS[code],s=25)
       ax.text(.03,.03,'Retained ≥100: '+str(int(sub.saved_alpha_min100.sum())),transform=ax.transAxes,fontsize=7)
     else:
      ids=list(sub.analysis_sample);X=counts_by[domain].loc[ids].to_numpy(float);n=len(ids);need(n>0,'Empty PCoA cohort');P=X/X.sum(1)[:,None];D=np.zeros((n,n))
      for a in range(n):
       for b in range(a):D[a,b]=D[b,a]=np.abs(P[a]-P[b]).sum()/(P[a].sum()+P[b].sum())
      H=np.eye(n)-np.ones((n,n))/n;B=-.5*H@(D*D)@H;B=(B+B.T)/2;before=np.linalg.eigvalsh(B);tol=c['pcoa_eigen_relative_tolerance']*max(1.,float(np.abs(before).max()));constant=max(0.,-float(before.min())) if float(before.min()) < -tol else 0.;D2=D*D+2*constant;np.fill_diagonal(D2,0)
      G=-.5*H@D2@H;ev,V=np.linalg.eigh((G+G.T)/2);order=np.argsort(ev)[::-1];ev=ev[order];V=V[:,order];ptol=c['pcoa_eigen_relative_tolerance']*max(1.,float(np.abs(ev).max()));need(float(ev.min())>=-max(ptol*10,1e-10),'PCoA correction failed');pos=ev>ptol;vals=ev[pos];V=V[:,pos]
      for k in range(V.shape[1]):
       if V[int(np.argmax(np.abs(V[:,k]))),k]<0:V[:,k]*=-1
      co=V*np.sqrt(vals)[None,:];rec=((co[:,None,:]-co[None,:,:])**2).sum(2);err=float(np.abs(rec-D2).max());need(err<=c['pcoa_reconstruction_squared_tolerance'],'PCoA reconstruction failed')
      frac=vals/vals.sum() if len(vals) else np.zeros(0);xy=np.zeros((n,2));xy[:,:min(2,len(vals))]=co[:,:2]
      for a,(_,r) in enumerate(sub.iterrows()):ax.scatter(*xy[a],s=22,facecolor=COLORS[r.display_code] if r.saved_alpha_min100 else 'white',edgecolor=COLORS[r.display_code],lw=.8)
      ax.set_aspect('equal',adjustable='datalim');ax.set_xlabel('PCoA1'+(' (%.1f%%)'%(100*frac[0]) if len(frac) else ' unavailable'));ax.set_ylabel('PCoA2'+(' (%.1f%%)'%(100*frac[1]) if len(frac)>1 else ' unavailable'))
      if len(vals)<2:ax.text(.03,.03,'One axis' if len(vals)==1 else 'Zero positive inertia',transform=ax.transAxes,fontsize=7)
      sub['PCoA1']=xy[:,0];sub['PCoA2']=xy[:,1];sub['Lingoes_constant']=constant
      for k in range(co.shape[1]):sub['Coordinate_'+str(k+1)]=co[:,k]
      prefix=spec['slug']+'_'+domain+'_'+d
      pd.DataFrame(D,index=ids,columns=ids).to_csv(folder/(prefix+'_Bray_Curtis.csv'));pd.DataFrame(np.sqrt(D2),index=ids,columns=ids).to_csv(folder/(prefix+'_corrected_distances.csv'))
      export_table(pd.DataFrame({'axis':list(range(1,n+1)),'before':before[::-1],'after':ev}),folder,prefix+'_eigenvalues.csv')
      qc.append({'domain':domain,'date':d,'n':n,'sample_ids':ids,'Lingoes_constant':constant,'positive_axes':int(len(vals)),'max_squared_distance_error':err})
     ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',color='#E3E6E8',lw=.4);ax.set_axisbelow(True);data.append(sub)
   title={'Shannon':'Observed diversity at each collection event','depth':'Target-read depth and retention','PCoA':'Descriptive community similarity within each event'}[kind]
   fig.suptitle(farm+' | '+spec['label']+'\n'+title,x=.095,ha='left',y=.98,fontsize=10,fontweight='bold')
   codes=list(spec['treatments'].values());handles=[Line2D([],[],marker='o',color=COLORS[k],ls='',label=k) for k in codes]+[Line2D([],[],marker='o',color='#555555',mfc='white',ls='',label='Below saved 100-read threshold')]
   if kind=='depth':handles.append(Line2D([],[],marker='x',color='#555555',ls='',label='Excluded primary record'))
   fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.085,.075),ncol=3,frameon=False,fontsize=7.5)
   note='Saved tests use year cohorts; see complete table. Date panels show observed values.' if kind=='Shannon' else 'Separate event/domain geometry; panel separation is not a treatment test.' if kind=='PCoA' else 'Depth flags retain separate definitions; zero-read records remain in the inventory.'
   fig.text(.095,.025,note,fontsize=7.3)
   stem=spec['slug']+'_'+kind
   caption=farm+' '+spec['label']+'. '+title+'. Bacterial and archaeal libraries remain separate and actual dates are not collapsed. Open points indicate samples below the saved minimum-100-read Shannon threshold. Treatment codes use the authoritative manuscript definitions. '
   caption+=('Shannon is copied from saved target-filtered unrarefied natural-log scores; short lines are newly displayed event-specific medians, computed on Puma for description only. Saved inferential models may combine dates within a year and are exported with their exact sample sets, status, reasons and original correction families. No per-date p value is inferred from those year models. No isolated Petite Sirah T1 figure is created.' if kind=='Shannon' else 'Bray-Curtis distances use unit-sum target-ASV profiles separately within each domain/date, followed by classical PCoA and Lingoes correction only for material negative eigenvalues. Full distances, all eigenvalues, positive coordinates and reconstruction checks are supplied. Panel axes are independently fitted with equal coordinate-unit aspect. No pooled ordination, new hypothesis test or confidence ellipse is fitted.' if kind=='PCoA' else 'The dotted line marks 100 reads. Crosses identify excluded primary source records; the complete inventory retains their reasons and the independent source feature-depth flag. Depth differences may influence observed community summaries.')
   finish(fig,folder,stem,caption,plt);export_table(pd.concat(data,ignore_index=True),folder,stem+'_source_values.csv')
   if qc:js(folder/(stem+'_geometry_QC.json'),qc)
 export_table(pd.DataFrame(retentions),root,'all_systems_event_retention.csv')
 js(root/'STATUS.json',{'state':'COMPLETE_REVIEW_PENDING','new_inferential_tests':False,'observed_Shannon_recomputed':False,'figures_expected':25,'manual_visual_review_complete':False})
