#!/usr/bin/env python3
"""Puma-only diversity/supplement workflow. Importing this module runs no science."""
from pathlib import Path
import argparse,csv,gzip,hashlib,json,os,re,shutil,sys,traceback,zipfile,datetime,platform,importlib.metadata
from hpc_guard import require_hpc
BASE=Path(__file__).resolve().parent
RANKS=('Kingdom','Phylum','Class','Order','Family')

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def save(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,indent=2)+'\n')
def require(v,msg):
 if not v:raise RuntimeError(msg)
def truth(v):return str(v).strip().lower() in ('true','1','yes')
def verify_package():
 for line in (BASE/'SHA256SUMS.txt').read_text().splitlines():
  digest,rel=line.split('  ',1);require(sha(BASE/rel)==digest,'Package bytes changed: '+rel)
def environment(run):
 execution=require_hpc();verify_package();cfg=json.loads((BASE/'CONFIG.json').read_text());ready=Path(cfg['runtime_ready']);require(sha(ready)==cfg['runtime_ready_sha256'],'Pinned runtime READY bytes changed')
 require(Path(sys.executable).absolute()==Path(cfg['runtime_python']).absolute(),'Wrong Python interpreter')
 required={'numpy':'2.3.5','pandas':'2.2.3','matplotlib':'3.10.8','Pillow':'12.3.0','scipy':'1.17.0'}
 got={n:importlib.metadata.version(n) for n in required};require(got==required,'Pinned numerical/plotting runtime changed')
 save(run/'audit'/('environment_'+os.environ['SLURM_JOB_ID']+'.json'),{'execution':execution,'python':sys.version,'executable':sys.executable,'versions':got,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
 return cfg

def stage(run,cfg):
 # Structural synthetic regression only; actual data are not read by this check.
 from structural_index_self_check import run_check
 run_check(run)
 out=run/'source_snapshot';out.mkdir(exist_ok=False);receipts=[]
 for role,spec in cfg['sources'].items():
  p=Path(spec['path']);require(p.is_absolute() and p.is_file(),'Missing pinned Puma source: '+str(p));require((spec.get('bytes') is None or p.stat().st_size==spec['bytes']) and sha(p)==spec['sha256'],'Pinned source changed: '+role)
  target=out/(role+''.join(p.suffixes));shutil.copyfile(p,target);require(sha(target)==spec['sha256'],'Snapshot mismatch: '+role);target.chmod(0o444);receipts.append({'role':role,'original':str(p),'snapshot':str(target),'sha256':spec['sha256'],'bytes':p.stat().st_size})
 state=json.loads(next(Path(r['snapshot']) for r in receipts if r['role']=='corrected_source_status').read_text());require(str(state.get('analysis_job_id'))=='24067160' and state.get('scheduler_state')=='COMPLETED' and state.get('pipeline_exit_code')==0 and state.get('scheduler_exit_code')=='0:0' and state.get('scientific_scripts_completed') is True,'Corrected source completion receipt invalid')
 save(run/'STAGE_STATUS.json',{'status':'VERIFIED','sources':receipts,'scientific_tests_added':False,'structural_index_self_check':'audit/STRUCTURAL_INDEX_SELF_CHECK.json'})

def tax_value(value,rank):
 value=str(value or '').strip();prefix={'Kingdom':'d__','Phylum':'p__','Class':'c__','Order':'o__','Family':'f__'}[rank]
 if value.lower().startswith(prefix):value=value[len(prefix):]
 if value.lower() in ('','na','n/a','unclassified','unassigned','uncultured','unknown','ambiguous_taxa','metagenome') or re.match(r'^(?:uncultured|uncultivated|unclassified|unidentified|unassigned)(?:[_\s]|$)',value,re.I):return '[unclassified]'
 return value

def worker(run,cfg,domain):
 require_hpc()
 # Numerical and plotting imports occur only after live Puma allocation/environment checks.
 import numpy as np
 import pandas as pd
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.patches import Patch
 stage_status=json.loads((run/'STAGE_STATUS.json').read_text());require(stage_status['status']=='VERIFIED','Stage incomplete');paths={x['role']:Path(x['snapshot']) for x in stage_status['sources']}
 for rec in stage_status['sources']:require(sha(rec['snapshot'])==rec['sha256'],'Frozen staged input changed')
 out=run/'results'/domain;out.mkdir(parents=True,exist_ok=False)
 metadata=pd.read_csv(BASE/'inputs'/f'{domain}_sample_metadata.csv.gz',keep_default_na=False)
 required={'analysis_sample','collection_date','analytic_system','treatment_label','treatment','biological_replicate','target_read_count','primary_included','depth_sensitivity_included','source_plot_group_2025','biological_plot_id','microbial_domain'};require(required.issubset(metadata),'Metadata schema changed');require(not metadata.analysis_sample.duplicated().any(),'Duplicate accepted sample identity');require((metadata.microbial_domain==domain).all(),'Metadata domain mismatch');require(metadata.depth_sensitivity_included.astype(str).str.lower().isin(['true','false','1','0','yes','no']).all(),'Invalid depth-sensitivity boolean');require(metadata.primary_included.map(truth).all(),'Unexpected nonprimary member in fixed display membership');require((pd.to_numeric(metadata.target_read_count)>0).all(),'Nonpositive target member')
 with gzip.open(paths['corrected_counts_'+domain],'rt') as f:header=next(csv.reader(f,delimiter='\t'))
 require(header[0]=='sample_id' and len(header)==len(set(header)),'Count orientation or duplicate header changed')
 counts=pd.read_csv(paths['corrected_counts_'+domain],sep='\t',index_col=0,keep_default_na=False);require(not counts.index.duplicated().any(),'Duplicated source counts');require(set(metadata.analysis_sample).issubset(counts.index),'Accepted sample absent in corrected counts')
 counts=counts.loc[metadata.analysis_sample].copy();values=counts.to_numpy(float);require(np.isfinite(values).all() and (values>=0).all() and np.equal(values,np.floor(values)).all(),'Invalid count matrix')
 expected=pd.to_numeric(metadata.set_index('analysis_sample').loc[counts.index,'target_read_count']).to_numpy(float);require(np.array_equal(values.sum(axis=1),expected),'Target count totals differ from accepted metadata')
 targets=set(counts.columns);taxonomy={}
 with paths['taxonomy_'+domain].open(newline='') as f:
  rd=csv.DictReader(f,delimiter='\t');require(set(('asv_id',)+RANKS).issubset(rd.fieldnames),'Taxonomy schema changed')
  for row in rd:
   key=row['asv_id']
   if key not in targets:continue
   require(key not in taxonomy,'Duplicate target taxonomy ASV');require(tax_value(row['Kingdom'],'Kingdom').lower()==domain,'Target Kingdom mismatch');taxonomy[key]=row
 require(set(taxonomy)==targets,'Taxonomy missing corrected target ASVs')
 keys=[];features={};mapping=[]
 for asv in counts.columns:
  lineage={rank:tax_value(taxonomy[asv][rank],rank) for rank in RANKS};key=json.dumps(list(lineage.items()),ensure_ascii=False,separators=(',',':'));keys.append(key);features[key]={'family':lineage['Family'],'lineage':lineage};mapping.append({'asv_id':asv,'family_lineage_key':key,**lineage})
 families=counts.T.groupby(pd.Index(keys),sort=True).sum().T
 previous=json.loads((BASE/'inputs'/f'{domain}_previous_family_genus_reference.json').read_text())['Family'];active=sorted(k for k in families if float(families[k].sum())>0);require(active==sorted(previous['features']),'Family universe differs from completed correlation source')
 require(np.array_equal(families.sum(axis=1).to_numpy(float),expected),'Family aggregation lost target counts')
 rel=families.div(families.sum(axis=1),axis=0);require(np.allclose(rel.sum(axis=1).to_numpy(float),1,atol=1e-12,rtol=0),'Family closure failed')
 # A fixed display key uses unweighted across-sample mean proportions, not pooled reads.
 named=[k for k in active if features[k]['family']!='[unclassified]'];means=rel[named].mean(axis=0);chosen=sorted(named,key=lambda k:(-float(means[k]),k))[:cfg['display_top_named_lineages']]
 labels={};name_counts={n:sum(features[k]['family']==n for k in chosen) for n in set(features[k]['family'] for k in chosen)}
 for k in chosen:
  n=features[k]['family'];labels[k]=n if name_counts[n]==1 else n+' ['+str(sorted([q for q in chosen if features[q]['family']==n]).index(k)+1)+']'
 display=pd.DataFrame(index=rel.index)
 for k in chosen:display[labels[k]]=rel[k]
 omitted=[k for k in named if k not in chosen];unknown=[k for k in active if features[k]['family']=='[unclassified]']
 if omitted:display['Other named families']=rel[omitted].sum(axis=1)
 if unknown:display['Unclassified family']=rel[unknown].sum(axis=1)
 require(np.allclose(display.sum(axis=1).to_numpy(float),1,atol=1e-12,rtol=0),'Display denominator changed')
 metadata=metadata.set_index('analysis_sample',drop=False).rename_axis(None);order_map={'Control':0,'CC':1,'CCS':2}
 metadata['display_treatment']=metadata.treatment_label
 for sid,row in metadata.iterrows():
  system=str(row.analytic_system);tr=str(row.treatment_label)
  require(tr in order_map,'Unknown Oatman treatment label: '+tr)
  if system=='OFR_2025_Field':
   require((tr=='Control' and row.source_plot_group_2025=='EAST') or (tr=='CC' and row.source_plot_group_2025=='WEST'),'2025 side/treatment identity changed')
   metadata.loc[sid,'display_treatment']='Control/EAST' if tr=='Control' else 'CC/WEST'
 metadata['sort_treatment']=metadata.treatment_label.map(order_map);metadata['sort_replicate']=pd.to_numeric(metadata.biological_replicate,errors='raise');metadata=metadata.sort_values(['analytic_system','collection_date','sort_treatment','sort_replicate','analysis_sample'])
 events=list(metadata[['analytic_system','collection_date']].drop_duplicates().itertuples(index=False,name=None));require(events==[('OFR_2023_2024_Field','2023-11-20'),('OFR_2023_2024_Field','2024-06-06'),('OFR_2023_2024_Field','2024-12-06'),('OFR_2025_Field','2025-05-21')],'Unexpected Oatman field/date set')
 plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.labelsize':8,'axes.titlesize':8.5,'xtick.labelsize':8,'ytick.labelsize':8,'svg.fonttype':'none','pdf.fonttype':42,'ps.fonttype':42})
 fig,axes=plt.subplots(2,2,figsize=(6.5,6.3),sharey=True);fig.subplots_adjust(left=.095,right=.985,top=.90,bottom=.34,wspace=.15,hspace=.62)
 palette=list(plt.get_cmap('tab20').colors);colors={c:palette[i%20] for i,c in enumerate(display.columns)}
 if 'Other named families' in colors:colors['Other named families']='#b8bec4'
 if 'Unclassified family' in colors:colors['Unclassified family']='#565e66'
 membership=[]
 for i,((system,date),ax) in enumerate(zip(events,axes.flat)):
  selected=metadata.loc[(metadata.analytic_system==system)&(metadata.collection_date==date)];ids=list(selected.index);bottom=np.zeros(len(ids));x=np.arange(len(ids))
  for family in display:
   height=display.loc[ids,family].to_numpy(float)*100;ax.bar(x,height,bottom=bottom,width=.82,color=colors[family],edgecolor='white',linewidth=.15,label=family);bottom+=height
  xlabels=[]
  for sid in ids:
   r=metadata.loc[sid];rep=int(r.sort_replicate);treatment=r.display_treatment;short=('C' if r.treatment_label=='Control' else r.treatment_label)+str(rep);mark='*' if not truth(r.depth_sensitivity_included) else '';xlabels.append(short+mark+'\n'+str(sid));membership.append({'panel':chr(65+i),'analysis_sample':sid,'display_label':short+mark,'full_treatment':treatment,'biological_plot_id':r.biological_plot_id,'collection_date':date,'analytic_system':system,'target_read_count':float(r.target_read_count),'depth_sensitivity_included':truth(r.depth_sensitivity_included),'family_sum':float(bottom[len(xlabels)-1])})
  ax.set_xticks(x,xlabels,rotation=90);ax.set_ylim(0,100);ax.set_yticks([0,25,50,75,100]);ax.grid(axis='y',color='#e4e7ea',linewidth=.4);ax.set_axisbelow(True);ax.spines[['top','right']].set_visible(False)
  context='shared field' if system=='OFR_2023_2024_Field' else 'separate 2025 field';ax.set_title(chr(65+i)+'  '+datetime.date.fromisoformat(date).strftime('%d %b %Y')+'\n'+context,loc='left')
  if i%2==0:ax.set_ylabel('Target-domain relative abundance (%)')
 fig.suptitle('Oatman Flats Ranch: '+domain+' at family level',x=.095,ha='left',fontsize=10,fontweight='bold',y=.985)
 legend=[Patch(facecolor=colors[c],label=c) for c in display];fig.legend(handles=legend,loc='lower left',bbox_to_anchor=(.09,.065),ncol=2,frameon=False,fontsize=8,columnspacing=1.0,handlelength=1.1)
 fig.text(.095,.034,'2025 field: Control = EAST; CC = WEST. The two fields remain separate.',fontsize=8)
 fig.text(.095,.015,'C = control; CC = cover crop; CCS = cover crop + sheep. * Source feature-depth flag; see legend.',fontsize=8)
 number='S1' if domain=='bacteria' else 'S2';stem=f'Fig{number}_Oatman_Flats_Ranch_{domain}_family_composition'
 for ext in ['pdf','svg','png','tif','eps']:
  kwargs={'dpi':600 if ext=='tif' else 300}
  if ext=='tif':kwargs['pil_kwargs']={'compression':'tiff_lzw'}
  fig.savefig(out/(stem+'.'+ext),**kwargs)
 plt.close(fig)
 families.to_csv(out/'family_counts_all_samples.tsv.gz',sep='\t');rel.to_csv(out/'family_relative_abundance_all_samples.tsv.gz',sep='\t');display.to_csv(out/'display_relative_abundance_all_samples.tsv.gz',sep='\t');pd.DataFrame([{'display_label':labels[k],'family_lineage_key':k,**features[k]['lineage']} for k in chosen]).to_csv(out/'DISPLAY_FAMILY_KEY.csv',index=False);pd.DataFrame(mapping).to_csv(out/'asv_to_full_family_lineage.tsv.gz',sep='\t',index=False);pd.DataFrame(membership).to_csv(out/'figure_sample_key.csv',index=False);metadata.to_csv(out/'accepted_sample_metadata.csv.gz',index=False);save(out/'FAMILY_LINEAGE_METADATA.json',features)
 caption=(f'Family-level {domain} composition at Oatman Flats Ranch. Each bar represents one accepted sample after technical merging; panels separate the three sampling events in the shared 2023-2024 field from the separate field sampled on 21 May 2025. Proportions use all target-domain ASV reads in that sample, without rarefaction. The highest-mean named full-lineage family bins are displayed individually, with remaining named bins combined as Other named families and unresolved family bins retained as Unclassified family. Selection uses unweighted across-sample means only for the color key; sample bars are not averaged. Colors remain fixed across panels within this domain. C, control; CC, cover crop; CCS, cover crop plus sheep; numerals identify biological replicates and source library IDs are retained below them. In the separate 2025 field, control occupies EAST and CC occupies WEST; field side is confounded with treatment. Asterisks denote samples outside the recorded feature-depth-sensitivity population; this flag is distinct from the saved minimum-100-read Shannon criterion. These samples remain in the accepted primary descriptive population. Relative read representation is not absolute abundance, activity or a test of treatment differences.')
 (out/'FIGURE_LEGEND.txt').write_text(caption+'\n')
 for rec in stage_status['sources']:require(sha(rec['original'])==rec['sha256']==sha(rec['snapshot']),'Pinned source changed during figure job')
 files=[p for p in out.iterdir() if p.is_file()];(out/'SHA256SUMS.txt').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in sorted(files)))
 save(out/'WORKER_STATUS.json',{'status':'FIGURE_CREATED_SCIENTIFIC_AND_VISUAL_REVIEW_PENDING','domain':domain,'sample_count':len(metadata),'family_lineage_count':len(families.columns),'target_count_conservation':True,'closure_passed':True,'new_inferential_tests':False,'figures':[stem+'.'+x for x in ['pdf','svg','png','tif','eps']]})

def collect(run,cfg):
 require_hpc()
 states={};okay=True
 for domain in ['bacteria','archaea']:
  out=run/'results'/domain;p=out/'WORKER_STATUS.json';state=json.loads(p.read_text()) if p.exists() else {'status':'WORKER_OUTPUT_MISSING'};states[domain]=state;okay=okay and state['status']=='DIVERSITY_SUPPLEMENTS_DOMAIN_COMPLETED_REVIEW_PENDING'
  for filename,status in [('DIVERSITY_REUSE_STATUS.json','SAVED_SHANNON_AND_MODEL_MEMBERSHIP_VERIFIED'),('PCoA_DISTANCE_AND_GEOMETRY_QC.json','DESCRIPTIVE_PUMA_GEOMETRY_CREATED_REVIEW_PENDING')]:
   q=out/filename;okay=okay and q.exists() and json.loads(q.read_text()).get('status')==status
 extra=run/'results'/'extras'/'EXTRA_STATUS.json';states['extras']=json.loads(extra.read_text()) if extra.exists() else {'status':'EXTRA_OUTPUT_MISSING'};okay=okay and states['extras']['status']=='EXTRA_OUTPUTS_CREATED_SCIENTIFIC_AND_VISUAL_REVIEW_PENDING'
 status={'state':'HPC_WORKERS_COMPLETE_COLLECTING' if okay else 'INCOMPLETE_OR_FAILED','scientific_execution_completed':False,'visual_review_complete':False,'workers':states,'downloads_ready':False};save(run/'FINAL_STATUS.json',status)
 if not okay:raise RuntimeError('A domain or extras worker did not finish successfully; inspect logs')
 from diversity_panels import combined_outputs,combined_pcoa_figure
 combined_outputs(run,cfg);combined_pcoa_figure(run,cfg)
 required_stems=['Fig2_Oatman_Flats_Ranch_Shannon_all_shared_field_events','FigS1_Oatman_Flats_Ranch_bacteria_family_composition','FigS2_Oatman_Flats_Ranch_archaea_family_composition','FigS3_Oatman_Flats_Ranch_Shannon_separate_2025_field','FigS4_Oatman_Flats_Ranch_target_read_depth_and_retention','FigS5_Oatman_Flats_Ranch_individual_paired_function_trajectories','FigS6_Oatman_Flats_Ranch_selected_CLR_soil_associations','FigS7_Oatman_Flats_Ranch_descriptive_Bray_Curtis_PCoA']
 for stem in required_stems:
  for ext in ['pdf','svg','png','tif']:
   found=list((run/'results').rglob(stem+'.'+ext));require(len(found)==1 and found[0].stat().st_size>0,'Missing or ambiguous required figure: '+stem+'.'+ext)
 for filename in ['Table_S2_complete_sample_retention_and_source_eligibility.csv','Table_S3_saved_Shannon_models_all_four_events.csv']:
  q=run/'results'/'combined'/filename;require(q.is_file() and q.stat().st_size>0,'Missing complete supplemental table: '+filename)
 extras=run/'results'/'extras'
 for filename in ['TableS1_cover_crop_commercial_components.csv','TableS2_functional_and_selected_association_membership.csv','Table_S3_environment_tests.csv','Table_S3_confirmed_soil_microbiome_tests.csv','Table_S3_confirmed_soil_microbiome_matches.csv','Table_S3_environment_matched_values.csv','SUPPLEMENTAL_TABLE_INVENTORY.json','TABLE_COMPLETENESS.json']:
  q=extras/filename;require(q.is_file() and q.stat().st_size>0,'Missing supplemental table/audit: '+filename)
 for number in [4,5,6,7]:require(any(q.is_file() and q.stat().st_size>0 for q in extras.glob('TableS'+str(number)+'_*.csv')),'Missing supplemental table slot S'+str(number))
 frozen=run/'audit'/'frozen_package_code';frozen.mkdir(exist_ok=False)
 for source in BASE.iterdir():
  if source.is_file() and (source.suffix in ['.py','.sh','.json','.md','.txt']):shutil.copyfile(source,frozen/source.name)
 backup=run/'results'/'legacy_reference';backup.mkdir(exist_ok=False)
 for source in sorted((BASE/'inputs_legacy').iterdir()):
  if source.is_file():shutil.copyfile(source,backup/source.name)
 # Reverify all immutable source bytes before declaring completed execution.
 staged=json.loads((run/'STAGE_STATUS.json').read_text())
 for rec in staged['sources']:require(sha(rec['original'])==rec['sha256']==sha(rec['snapshot']),'Pinned source changed before collection')
 for domain in ['bacteria','archaea','combined','extras','legacy_reference']:
  folder=run/'results'/domain;files=[p for p in folder.rglob('*') if p.is_file() and p.name!='SHA256SUMS.txt'];(folder/'SHA256SUMS.txt').write_text(''.join(sha(p)+'  '+str(p.relative_to(folder))+'\n' for p in sorted(files)))
 status.update(state='HPC_DIVERSITY_SUPPLEMENTS_CREATED_REVIEW_PENDING',scientific_execution_completed=True,new_descriptive_PCoA=True,new_inferential_tests=False,observed_Shannon_recalculated=False,FDR_recalculated=False,supplements_supported=True)
 package=run.parent.parent;archive=package.parent/(package.name+'_'+run.name+'_results.zip');checksum=archive.with_suffix(archive.suffix+'.sha256.txt');status.update(downloads_ready=True,download_results=str(archive),download_checksum=str(checksum));save(run/'FINAL_STATUS.json',status)
 try:
  with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
   for p in sorted(run.rglob('*')):
    if p.is_file() and 'source_snapshot' not in p.parts:z.write(p,p.relative_to(run.parent))
  checksum.write_text(sha(archive)+'  '+archive.name+'\n')
 except BaseException:
  status.update(downloads_ready=False,state='PACKAGING_FAILED');save(run/'FINAL_STATUS.json',status);raise
 print(json.dumps(status,indent=2))

def diagnostic_archive(run,error):
 """Package incomplete outputs plus logs/audits; never assert scientific success."""
 package=run.parent.parent;archive=package.parent/(package.name+'_'+run.name+'_DIAGNOSTIC_INCOMPLETE.zip');checksum=archive.with_suffix(archive.suffix+'.sha256.txt')
 statefile=run/'FINAL_STATUS.json';status=json.loads(statefile.read_text()) if statefile.exists() else {}
 status.update(state='INCOMPLETE_OR_FAILED_DIAGNOSTIC_READY',scientific_execution_completed=False,downloads_ready=False,visual_review_complete=False,diagnostic_only=True,diagnostic_downloads_ready=True,diagnostic_results=str(archive),diagnostic_checksum=str(checksum),collector_error=str(error))
 save(statefile,status)
 try:
  with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
   for path in sorted(run.rglob('*')):
    if path.is_file() and 'source_snapshot' not in path.parts:z.write(path,path.relative_to(run.parent))
  checksum.write_text(sha(archive)+'  '+archive.name+'\n')
 except BaseException:
  status.update(state='INCOMPLETE_DIAGNOSTIC_PACKAGING_FAILED',diagnostic_downloads_ready=False);save(statefile,status);raise
 print(json.dumps(status,indent=2))

def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['stage','worker','extras','collect']);p.add_argument('run');p.add_argument('--domain',choices=['bacteria','archaea']);args=p.parse_args();run=Path(args.run).resolve()
 try:
  cfg=environment(run)
  if args.mode=='stage':stage(run,cfg)
  elif args.mode=='worker':
   require(args.domain,'Domain missing');worker(run,cfg,args.domain)
   from diversity_panels import domain_outputs,pcoa_domain_outputs
   domain_outputs(run,cfg,args.domain);pcoa_domain_outputs(run,cfg,args.domain)
   target=run/'results'/args.domain/'WORKER_STATUS.json';state=json.loads(target.read_text());state.update(status='DIVERSITY_SUPPLEMENTS_DOMAIN_COMPLETED_REVIEW_PENDING',observed_Shannon_recalculated=False,FDR_recalculated=False,new_inferential_tests=False,new_descriptive_PCoA=True);save(target,state)
  elif args.mode=='extras':
   import supplement_extras
   supplement_extras.run(run,cfg)
  else:collect(run,cfg)
 except BaseException as exc:
  save(run/'audit'/('FAILED_'+args.mode+'_'+(args.domain or '')+'.json'),{'type':type(exc).__name__,'error':str(exc),'traceback':traceback.format_exc()})
  if args.mode=='collect':diagnostic_archive(run,exc)
  raise
if __name__=='__main__':main()
