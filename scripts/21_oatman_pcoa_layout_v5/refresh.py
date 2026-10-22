"""One guarded Puma job renders only FigS7 from saved coordinates, then collects."""
from pathlib import Path
import argparse,datetime,importlib.metadata,json,os,shutil,sys,traceback
from hpc_guard import require_hpc
from collect_archive import sha,save,archive
BASE=Path(__file__).resolve().parent

def require(value,message):
 if not value:raise RuntimeError(message)

def execute(run):
 execution=require_hpc()
 for line in (BASE/'SHA256SUMS.txt').read_text().splitlines():
  digest,relative=line.split('  ',1);require(sha(BASE/relative)==digest,'Package bytes changed: '+relative)
 cfg=json.loads((BASE/'CONFIG.json').read_text());require(Path(sys.executable).absolute()==Path(cfg['runtime_python']).absolute(),'Wrong pinned Python')
 require(sha(cfg['runtime_ready'])==cfg['runtime_ready_sha256'],'Pinned runtime READY changed')
 versions={name:importlib.metadata.version(name) for name in cfg['versions']};require(versions==cfg['versions'],'Pinned plotting/runtime versions changed')
 save(run/'audit'/'ENVIRONMENT.json',{'execution':execution,'python':sys.version,'executable':sys.executable,'versions':versions})
 receipts=[]
 for spec in cfg['source_files']:
  p=Path(spec['path']);require(p.is_absolute() and p.is_file() and p.stat().st_size==spec['bytes'] and sha(p)==spec['sha256'],'Missing/changed saved v3 source: '+spec['relative'])
  target=run/spec['relative'] if spec['kind']=='saved_render_input' else run/'source_snapshot'/spec['relative']
  target.parent.mkdir(parents=True,exist_ok=True);require(not target.exists(),'Fresh run output already exists');shutil.copyfile(p,target);require(sha(target)==spec['sha256'],'Saved snapshot mismatch');target.chmod(0o444);receipts.append(dict(spec,snapshot=str(target)))
 original=json.loads((run/'source_snapshot'/'FINAL_STATUS.json').read_text());require(original.get('scientific_execution_completed') is True and original.get('state')=='HPC_DIVERSITY_SUPPLEMENTS_CREATED_REVIEW_PENDING','Accepted source run completion receipt invalid')
 staged=json.loads((run/'source_snapshot'/'STAGE_STATUS.json').read_text());require(staged['sources']==cfg['original_stage_source_pins'] and staged['status']=='VERIFIED','Original scientific source pins changed')
 save(run/'INPUT_PROVENANCE.json',{'accepted_v3_source':cfg['source_run'],'accepted_archive_sha256':cfg['source_archive_sha256'],'frozen_saved_input_receipts':receipts,'scientific_values_recomputed':False})
 frozen=run/'audit'/'code';frozen.mkdir(exist_ok=False)
 for p in BASE.iterdir():
  if p.is_file():shutil.copyfile(p,frozen/p.name)
 # Only saved PCoA coordinates are drawn. No domain aggregation, distance/PCoA
 # fitting, CLR/alpha calculation, inference or FDR operation is called.
 from render_saved_figures import combined_pcoa_figure
 (run/'results'/'combined').mkdir(parents=True,exist_ok=False)
 combined_pcoa_figure(run,cfg)
 checks=[]
 for name in cfg['unchanged_output_baselines']:
  old=run/'source_snapshot'/'results'/'combined'/name;new=run/'results'/'combined'/name
  require(new.is_file() and sha(new)==sha(old),'Saved plot/model/coordinate audit differs byte-for-byte: '+name)
  checks.append({'file':name,'sha256':sha(new),'exact_original_bytes':True})
 for spec in receipts:require(sha(spec['path'])==spec['sha256']==sha(spec['snapshot']),'Saved source changed during rendering')
 for stem in cfg['expected_figures']:
  for ext in ['pdf','svg','png','tif']:
   p=run/'results'/'combined'/(stem+'.'+ext);require(p.is_file() and p.stat().st_size>0,'Required refreshed figure missing')
  qc=json.loads((run/'results'/'combined'/(stem+'_LAYOUT_QC.json')).read_text());require(qc['status']=='PASS','Rendered canvas/legend QC failed')
  neighbor=qc.get('neighbor_axis_text_clearance',{});minimum=neighbor.get('minimum_clearance_points')
  require(neighbor.get('status')=='PASS' and neighbor.get('violations')==[] and isinstance(minimum,(int,float)) and 2<=minimum<float('inf'),'Rendered neighboring-axis text clearance failed or required less than two points')
  marker=qc.get('own_axes_circle_marker_clearance',{});gap=marker.get('minimum_marker_ink_gap_points');center=marker.get('minimum_center_padding_points')
  require(marker.get('status')=='PASS' and marker.get('violations')==[] and isinstance(gap,(int,float)) and 2<=gap<float('inf') and isinstance(center,(int,float)) and 5<=center<float('inf'),'Rendered full-marker own-axes clearance failed or required physical margins were reduced')
  require(marker.get('point_count')==63 and len(marker.get('panels',[]))==8 and marker.get('skipped_axes')==[],'Marker guard must check all eight frozen panels and all 63 saved points (32 bacterial, 31 archaeal)')
 save(run/'UNCHANGED_PLOT_DATA_AUDIT.json',{'status':'PASS_BYTE_IDENTICAL_SAVED_PLOT_MODEL_COORDINATE_AUDITS','comparisons':checks,'saved_input_hashes_before_after_match':True,'models_recomputed':False,'PCoA_recomputed':False,'CLR_recomputed':False,'Shannon_recomputed':False,'FDR_recomputed':False,'family_composition_recomputed':False})
 files=[p for p in (run/'results').rglob('*') if p.is_file()];(run/'RESULT_SHA256SUMS.txt').write_text(''.join(sha(p)+'  '+str(p.relative_to(run))+'\n' for p in sorted(files)))
 status={'execution':execution,'scientific_values_recomputed':False,'visual_review_complete':False,'output_figures':cfg['expected_figures'],'exact_saved_plot_data_audits_passed':True,'canvas_and_legend_gutter_checks_passed':True,'neighbor_axis_text_clearance_checks_passed':True,'own_axes_marker_ink_clearance_checks_passed':True,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 archive(run,status)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('run');args=parser.parse_args();run=Path(args.run).resolve()
 try:execute(run)
 except BaseException as exc:
  failure={'error':str(exc),'type':type(exc).__name__,'traceback':traceback.format_exc(),'scientific_values_recomputed':False};save(run/'audit'/'FAILED.json',failure);archive(run,failure,diagnostic=True);raise
if __name__=='__main__':main()
