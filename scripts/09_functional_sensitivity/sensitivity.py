#!/usr/bin/env python3
"""HPC-only check of the effect of reference-domain-discordant archaeal ASVs."""
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
import sys, json, os, shutil, subprocess, datetime
import numpy as np
import pandas as pd
from picrust2 import default as defaults
from common import require, sha, save, table, readcsv

run = Path(sys.argv[1]).resolve()
cfg = json.loads((run/'CONFIG.json').read_text())
source = Path(cfg['source_run'])
snap = run/'source_snapshot'; snap.mkdir()
out = run/'results'; out.mkdir()
prepared = run/'sensitivity_inputs'; prepared.mkdir()
verified = []
for name, expected in cfg['sources'].items():
    rel = Path(name)
    require(not rel.is_absolute() and '..' not in rel.parts, 'Unsafe source path')
    dst = snap/rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source/rel,dst)
    actual = sha(dst)
    require(actual == expected, 'Source hash mismatch: '+name)
    verified.append({'source':str(source/rel),'sha256':actual})
save(run/'audit/SOURCE_VERIFICATION.json',verified)
prior = json.loads((snap/'HPC_STATUS.json').read_text())
require(prior['analysis_job_id']==cfg['source_analysis_job_id'] and prior['pipeline_exit_code']==0
        and prior['scheduler_state']=='COMPLETED','Functional source run did not complete successfully')
current_env = json.loads((run/'audit/runtime_environment/environment.json').read_text())
previous_env = json.loads((snap/'audit/runtime_environment/environment.json').read_text())
for key in ['versions','references','pipeline_script_sha256']:
    require(current_env[key] == previous_env[key], 'Environment differs from the original predictions: '+key)

def read_numeric(path):
    frame = pd.read_csv(path,sep='\t',index_col=0)
    require(not frame.index.has_duplicates and not frame.columns.has_duplicates,'Duplicate numeric table labels')
    values=frame.to_numpy(float)
    require(np.isfinite(values).all() and (values>=0).all(),'Invalid numeric table values')
    return frame

count = read_numeric(snap/'validated/archaea/asv_by_sample.tsv')
marker = pd.read_csv(snap/'results/archaea/combined_marker_predicted_and_nsti.tsv.gz',sep='\t',index_col=0)
require(set(marker.index)==set(count.index),'Marker/count ASV sets differ')
require(set(marker.best_domain)<= {'bac','arc'},'Unexpected reference-domain label')
drop = sorted(marker.index[marker.best_domain=='bac'])
require(drop == sorted(cfg['expected_discordant_ASVs']), 'Discordant ASV set changed')
keep = sorted(set(count.index)-set(drop))
filtered = count.loc[keep]
require((filtered.sum(axis=0)>0).all(), 'An entire sample would be removed; stop for review')
count.to_csv(prepared/'all_ASVs.tsv',sep='\t',index_label='ASV_ID')
filtered.to_csv(prepared/'domain_concordant_ASVs.tsv',sep='\t',index_label='ASV_ID')
tax = pd.read_csv(snap/'source_snapshot/candidates/archaea/taxonomy.tsv',sep='\t',index_col=0).fillna('')
references = {d:pd.read_csv(snap/f'predictions/archaea/{d}_marker_predicted_and_nsti.tsv.gz',sep='\t',index_col=0) for d in ['bac','arc']}
flagged=[]
for asv in drop:
    row={'ASV_ID':asv,'original_taxonomy':';'.join(str(tax.loc[asv,k]) for k in ['Kingdom','Phylum','Class','Order','Family','Genus','Species']),
         'best_reference_domain':marker.loc[asv,'best_domain'], 'total_input_reads':int(count.loc[asv].sum()),
         'samples_with_reads':int((count.loc[asv]>0).sum()),
         'interpretation':'Reference-choice discrepancy only; no taxonomic reclassification is made'}
    for domain,ref in references.items():
        row[domain+'_NSTI'] = float(ref.loc[asv,'metadata_NSTI']) if asv in ref.index else None
        row[domain+'_closest_reference_genome'] = str(ref.loc[asv,'closest_reference_genome']) if asv in ref.index and 'closest_reference_genome' in ref else ''
    flagged.append(row)
table(out/'flagged_ASV_details.csv',flagged)
commands=[]
def command(args,name):
    args=list(map(str,args)); commands.append({'argv':args,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    save(run/'audit/commands.json',commands)
    print('COMMAND '+repr(args),flush=True)
    with (run/'logs'/name).open('w') as log:
        subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,check=True)

profiles={}
for scope,input_name in [('baseline_reproduced','all_ASVs.tsv'),('domain_concordant','domain_concordant_ASVs.tsv')]:
    dest=run/'predictions'/scope
    dest.mkdir(parents=True)
    profiles[scope]={}
    for family in ['EC','KO']:
        command(['metagenome_pipeline.py','--input',prepared/input_name,
            '--function',snap/f'predictions/archaea/combined_{family}_predicted.tsv.gz',
            '--marker',snap/'results/archaea/combined_marker_predicted_and_nsti.tsv.gz',
            '--max_nsti',str(cfg['max_nsti']),'--min_reads','1','--min_samples','1',
            '--out_dir',dest/f'{family}_metagenome_out'],scope+'_'+family+'.log')
        profiles[scope][family]=read_numeric(dest/f'{family}_metagenome_out/pred_metagenome_unstrat.tsv.gz')
    command(['pathway_pipeline.py','--input',dest/'EC_metagenome_out/pred_metagenome_unstrat.tsv.gz',
        '--out_dir',dest/'pathways_out','--map',defaults.default_pathway_map,
        '--regroup_map',defaults.default_regroup_map,'--intermediate',dest/'pathways_intermediate',
        '--proc',str(int(os.environ.get('SLURM_CPUS_PER_TASK','1'))),'--verbose'],scope+'_METACYC.log')
    profiles[scope]['METACYC']=read_numeric(dest/'pathways_out/path_abun_unstrat.tsv.gz')
    for family,frame in profiles[scope].items():
        require(set(frame.columns)==set(count.columns),'Profile sample set changed: '+scope+'/'+family)
        frame.to_csv(out/f'{scope}_{family}.tsv.gz',sep='\t',index_label='function')

reproduction=[]
for family,baseline in profiles['baseline_reproduced'].items():
    original=read_numeric(snap/f'results/archaea/{family}_predicted_abundance.tsv.gz')
    require(set(original.index)==set(baseline.index) and set(original.columns)==set(baseline.columns),'Baseline label set mismatch: '+family)
    baseline=baseline.reindex(index=original.index,columns=original.columns)
    difference=float(np.abs(original.to_numpy()-baseline.to_numpy()).max())
    ok=np.allclose(original,baseline,rtol=1e-10,atol=1e-8)
    reproduction.append({'family':family,'same_labels':True,'max_absolute_difference':difference,'passed':bool(ok)})
save(out/'BASELINE_REPRODUCTION.json',reproduction)
require(all(r['passed'] for r in reproduction),'Baseline could not be reproduced; stop interpretation and review')

metadata={r['analysis_sample']:r for r in readcsv(snap/'validated/archaea/sample_metadata.csv')}
sample_rows=[]; function_rows=[]; pathway_changes=[]
for family,baseline in profiles['baseline_reproduced'].items():
    comparison=profiles['domain_concordant'][family]
    function_ids=sorted(set(baseline.index)|set(comparison.index))
    baseline=baseline.reindex(index=function_ids,columns=count.columns,fill_value=0)
    comparison=comparison.reindex(index=function_ids,columns=count.columns,fill_value=0)
    before_total=baseline.sum(axis=0);after_total=comparison.sum(axis=0)
    # Missing or all-zero profiles never silently become a zero distance.
    before_rel=baseline.divide(before_total.replace(0,np.nan),axis=1)
    after_rel=comparison.divide(after_total.replace(0,np.nan),axis=1)
    rel_delta=(after_rel-before_rel).abs()
    for sid in count.columns:
        original_reads=float(count[sid].sum());removed_reads=float(count.loc[drop,sid].sum())
        row={k:metadata[sid].get(k,'') for k in ['farm','analytic_system','biological_plot_id','collection_date','sample_key','treatment']}
        row.update(family=family,analysis_sample=sid,input_reads=int(original_reads),excluded_ASV_reads=int(removed_reads),
            excluded_read_fraction=removed_reads/original_reads,functions_before=int((baseline[sid]>0).sum()),
            functions_after=int((comparison[sid]>0).sum()),functions_lost=int(((baseline[sid]>0)&(comparison[sid]==0)).sum()),
            functions_gained=int(((baseline[sid]==0)&(comparison[sid]>0)).sum()),
            relative_composition_Bray_Curtis=float(0.5*rel_delta[sid].sum()) if before_total[sid]>0 and after_total[sid]>0 else None,
            total_predicted_abundance_ratio=float(after_total[sid]/before_total[sid]) if before_total[sid]>0 else None,
            profile_status='NONZERO_BOTH' if before_total[sid]>0 and after_total[sid]>0 else 'ZERO_PROFILE_REVIEW_REQUIRED')
        sample_rows.append(row)
    for fun in function_ids:
        changes=rel_delta.loc[fun].dropna()
        function_rows.append({'family':family,'function':fun,'samples_present_before':int((baseline.loc[fun]>0).sum()),
            'samples_present_after':int((comparison.loc[fun]>0).sum()),
            'samples_losing_function':int(((baseline.loc[fun]>0)&(comparison.loc[fun]==0)).sum()),
            'samples_gaining_function':int(((baseline.loc[fun]==0)&(comparison.loc[fun]>0)).sum()),
            'max_absolute_relative_abundance_change':float(changes.max()) if len(changes) else None})
        if family=='METACYC':
            for sid in count.columns:
                if not np.isclose(baseline.loc[fun,sid],comparison.loc[fun,sid],rtol=1e-10,atol=1e-8):
                    pathway_changes.append({'analysis_sample':sid,'pathway':fun,'before':float(baseline.loc[fun,sid]),'after':float(comparison.loc[fun,sid])})
table(out/'sample_sensitivity.csv',sample_rows)
table(out/'function_sensitivity.csv',function_rows)
table(out/'pathway_changes.csv',pathway_changes,fields=['analysis_sample','pathway','before','after'])
save(out/'SENSITIVITY_STATUS.json',{'execution':execution,'source_job':cfg['source_analysis_job_id'],
    'status':'SENSITIVITY_COMPLETED_REVIEW_REQUIRED','excluded_ASVs_in_sensitivity_only':drop,
    'original_ASVs':len(count),'sensitivity_ASVs':len(filtered),'samples':len(count.columns),
    'baseline_reproduced':True,'original_scientific_data_changed':False,
    'limitations':'Diagnostic perturbation only; no taxonomic reclassification, treatment tests, gene measurements or forecasting.',
    'seed':'Deterministic count filtering and comparisons; existing HSP seed-100 predictions reused'})
(run/'ANALYSES_COMPLETE.txt').write_text('Sensitivity completed on Puma; review baseline and functional changes.\n')
