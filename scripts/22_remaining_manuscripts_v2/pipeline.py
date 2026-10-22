#!/usr/bin/env python3
"""Guarded Puma staging, workers, complete table export, and diagnostic collection."""
from common import *
import argparse,traceback,subprocess,zipfile,importlib

def stage(run,c):
 target=run/'source_snapshot';target.mkdir(exist_ok=False);records=[]
 for role,spec in c['sources'].items():
  p=Path(spec['path']);need(p.is_absolute() and p.is_file(),'Missing pinned source '+role)
  need((spec.get('bytes') is None or p.stat().st_size==spec['bytes']) and sha(p)==spec['sha256'],'Pinned source differs '+role)
  dest=target/(role+''.join(p.suffixes));shutil.copyfile(p,dest);need(sha(dest)==spec['sha256'],'Snapshot differs '+role);dest.chmod(0o444)
  records.append({'role':role,'original':str(p),'snapshot':str(dest),'sha256':spec['sha256'],'bytes':p.stat().st_size})
 state=json.loads(next(Path(r['snapshot']) for r in records if r['role']=='corrected_source_status').read_text())
 need(str(state.get('analysis_job_id'))=='24067160' and state.get('scheduler_state')=='COMPLETED' and state.get('pipeline_exit_code')==0 and state.get('scheduler_exit_code')=='0:0' and state.get('scientific_scripts_completed') is True,'Corrected source completion receipt invalid')
 js(run/'STAGE_STATUS.json',{'status':'VERIFIED','sources':records,'new_inferential_tests':False})
 # Complete source tables are copied unchanged, including skipped rows and all correction families.
 out=run/'results'/'source_tables';out.mkdir(parents=True);inventory=[]
 for role,spec in c['local_sources'].items():
  p=source(role,run,c);q=out/spec['file'];q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
  inventory.append({'role':role,'relative_file':str(q.relative_to(run/'results')),'source_sha256':spec['sha256'],'export_sha256':sha(q),'content':'complete unchanged source file'})
 wb=json.loads(source('corrected_workbook',run,c).read_text())
 for name,table in wb['tables'].items():
  q=out/'corrected_workbook_tables'/(name.replace('/','_')+'.csv');q.parent.mkdir(parents=True,exist_ok=True)
  with q.open('w',newline='') as f:
   writer=csv.writer(f);writer.writerow(table['columns'])
   for row in table['rows']:
    writer.writerow([row.get(k,'') for k in table['columns']] if isinstance(row,dict) else row)
  inventory.append({'role':'corrected workbook / '+name,'relative_file':str(q.relative_to(run/'results')),'source_sha256':c['sources']['corrected_workbook']['sha256'],'export_sha256':sha(q),'rows':len(table['rows']),'content':'complete saved table; no new calculations'})
 js(out/'COMPLETE_SOURCE_INVENTORY.json',inventory)
 (out/'READ_ME.txt').write_text('All bundled CSV source files are copied whole, including nonselected endpoints, skipped tests, original family definitions and source metadata. Workbook exports retain all saved rows. These are source-preserving supplements for editorial assembly, not final journal-numbered tables. Association display_rows is a compact selection, NOT the full association-screen matrix. Frozen full-reference CLR matrices stay on Puma; exact plotted coordinates and their source hashes are returned. Technical/library repeats, pooled contributors, visits and plants are not extra independent plots. No new p values, adjusted probabilities or inferential tests are calculated.\n')
 # Preserve the exact script and editorial source snapshot used by this run.
 frozen=run/'audit'/'package_snapshot';frozen.mkdir(parents=True)
 for p in BASE.iterdir():
  if p.is_file() and p.name!='LATEST_RUN.txt':shutil.copyfile(p,frozen/p.name)
 for name in ('field_plans','editorial_sources'):shutil.copytree(BASE/name,frozen/name)
 js(run/'STAGE_STATUS.json',{'status':'VERIFIED','sources':records,'new_inferential_tests':False,'source_tables_exported':len(inventory),'editorial_snapshot_preserved':True})

def expected_figures(c):
 stems=[]
 for spec in c['systems']:
  folder='communities/'+spec['farm'].replace(' ','')+'/'
  stems.extend(folder+spec['slug']+'_'+suffix for suffix in ('bacteria_families','archaea_families','Shannon','depth','PCoA'))
 for spec in c['paired_panels']:stems.append('extras/'+spec['farm'].replace(' ','')+'/'+spec['slug']+'_paired_functions')
 for farm,n in [('PageSprings',3),('Whipstone',2)]:stems.extend('extras/'+farm+'/'+farm+'_soil_associations_'+str(i) for i in range(1,n+1))
 stems.extend('extras/Synthesis/'+s for s in ('Synthesis_predicted_function_soil_examples','Synthesis_collection_context','Synthesis_glycolysis_saved_changes'))
 stems.extend('maps/'+s for s in ('PageSprings_general_plot_layout','Whipstone_general_plot_layout'))
 return stems

def scheduler_rows(run):
 jobs=json.loads((run/'submission.json').read_text())['jobs'];rows={}
 for label,job in jobs.items():
  if label=='collector':continue
  raw=subprocess.check_output(['sacct','-n','-X','-j',job,'--format=JobIDRaw,State,ExitCode','--parsable2'],text=True,timeout=30)
  matches=[line.split('|') for line in raw.splitlines() if line.split('|')[0].strip()==job]
  need(len(matches)==1,'Scheduler accounting unavailable/ambiguous for '+label)
  row=matches[0];rows[label]={'job_id':job,'state':row[1].strip(),'exit_code':row[2].strip()}
 js(run/'audit'/'worker_scheduler_receipts.json',rows)
 return rows

def archive_run(run,diagnostic=False):
 tag='_DIAGNOSTIC_INCOMPLETE.zip' if diagnostic else '_results.zip'
 archive=BASE.parent/(BASE.name+'_'+run.name+tag);checksum=archive.with_suffix(archive.suffix+'.sha256.txt')
 state=json.loads((run/'FINAL_STATUS.json').read_text())
 state.update(archive_path=str(archive),checksum_path=str(checksum),archive_kind='diagnostic' if diagnostic else 'results',archive_prepared=True)
 js(run/'FINAL_STATUS.json',state)
 files=[p for p in run.rglob('*') if p.is_file() and 'source_snapshot' not in p.relative_to(run).parts and p.name!='RETURN_SHA256SUMS.txt']
 # Batch stdout/stderr may still grow during collection; they are diagnostic, not immutable scientific assets.
 (run/'RETURN_SHA256SUMS.txt').write_text(''.join(sha(p)+'  '+str(p.relative_to(run))+'\n' for p in sorted(files) if 'logs' not in p.relative_to(run).parts))
 files.append(run/'RETURN_SHA256SUMS.txt')
 with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
  for p in sorted(files):z.write(p,Path(run.name)/p.relative_to(run))
 checksum.write_text(sha(archive)+'  '+archive.name+'\n')
 print('ARCHIVE='+str(archive));print('CHECKSUM='+str(checksum));print('Run CHECK.sh after the collector exits; visual/scientific review remains pending.')

def collect(run,c):
 states={}
 stage_state=json.loads((run/'STAGE_STATUS.json').read_text());need(stage_state['status']=='VERIFIED' and stage_state.get('editorial_snapshot_preserved'),'Staging incomplete')
 for name,n in [('communities',25),('extras',11),('maps',2)]:
  p=run/'results'/name/'STATUS.json';need(p.is_file(),'Worker output missing: '+name);states[name]=json.loads(p.read_text())
  need(states[name]['state']=='COMPLETE_REVIEW_PENDING' and states[name]['figures_expected']==n,'Worker did not finish: '+name)
 jobs=scheduler_rows(run)
 need(all(r['state']=='COMPLETED' and r['exit_code']=='0:0' for r in jobs.values()),'One or more workers did not exit successfully')
 for record in stage_state['sources']:need(sha(record['snapshot'])==record['sha256'],'Staged input changed')
 registry=[]
 for stem in expected_figures(c):
  for ext in ('pdf','svg','eps','png','tif'):
   p=run/'results'/(stem+'.'+ext);need(p.is_file() and p.stat().st_size>0,'Required figure missing: '+stem+'.'+ext)
  q=run/'results'/(stem+'_LAYOUT_QC.json');need(q.is_file() and not json.loads(q.read_text())['text_outside_canvas'],'Figure canvas check failed: '+stem)
  cap=run/'results'/(stem+'_caption.txt');need(cap.is_file() and cap.stat().st_size>0,'Figure caption missing: '+stem)
  registry.append({'stem':stem,'formats':['pdf','svg','eps','png','tif'],'manual_visual_review_complete':False})
 need(len(registry)==38,'Unexpected figure registry size')
 required=['source_tables/COMPLETE_SOURCE_INVENTORY.json','communities/all_systems_event_retention.csv','extras/selected_paired_endpoints.csv','extras/selected_paired_test_index.csv','extras/paired_coordinate_QC.json','extras/all_selected_exact_CLR_soil_pairs.csv','extras/all_selected_association_saved_rows.csv','extras/association_coordinate_QC.json','extras/Synthesis/collection_event_membership.csv','extras/Synthesis/glycolysis_complete_saved_rows.csv']
 for rel in required:need((run/'results'/rel).is_file(),'Required table/QC missing: '+rel)
 js(run/'results'/'FIGURE_REGISTRY.json',registry)
 js(run/'FINAL_STATUS.json',{'state':'HPC_OUTPUTS_CREATED_REVIEW_PENDING','scientific_execution_completed':True,'new_inferential_tests':False,'observed_Shannon_recomputed':False,'FDR_recalculated':False,'manuscripts_edited':False,'visual_review_complete':False,'scheduler_workers_verified':True,'collector_terminal_state_verified':False,'figure_count':38,'workers':states,'archive_prepared':False})
 archive_run(run)

def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['stage','community','extras','maps','collect']);p.add_argument('run');args=p.parse_args();run=Path(args.run).resolve()
 try:
  c=environment(run)
  if args.mode=='stage':stage(run,c)
  elif args.mode=='collect':collect(run,c)
  else:
   stage_state=json.loads((run/'STAGE_STATUS.json').read_text());need(stage_state['status']=='VERIFIED','Staging not verified')
   importlib.import_module(args.mode).run(run,c)
 except BaseException as exc:
  js(run/'audit'/('FAILED_'+args.mode+'.json'),{'error':str(exc),'type':type(exc).__name__,'traceback':traceback.format_exc()})
  if args.mode=='collect':
   js(run/'FINAL_STATUS.json',{'state':'INCOMPLETE_OR_FAILED','scientific_execution_completed':False,'visual_review_complete':False,'manuscripts_edited':False,'diagnostic_only':True,'collector_error':str(exc)})
   archive_run(run,diagnostic=True)
  raise
if __name__=='__main__':main()
