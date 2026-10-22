#!/usr/bin/env python3
"""Frozen functional comparison protocol; numerical work runs only on Puma."""
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
from collections import Counter, defaultdict
import sys, json, shutil, subprocess, datetime, math
import numpy as np
import pandas as pd
from common import require, sha, save, readcsv, table, truth
from statistics_core import (stable_seed, hellinger_frame, treatment_test, paired_change_test,
    feature_treatment_contrast, paired_feature_contrast, holm_adjust, hellinger_pca)

run = Path(sys.argv[1]).resolve()
cfg = json.loads((run/'CONFIG.json').read_text())
out = run/'results'; out.mkdir()
snapshot = run/'source_snapshot'; snapshot.mkdir()
panel = readcsv(run/'FEATURE_PANEL.csv')
verified = []
for source_name, entries in cfg['sources'].items():
    source = Path(cfg['source_runs'][source_name]['path'])
    for name, expected in entries.items():
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe source path')
        destination = snapshot/source_name/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source/relative, destination)
        require(sha(destination)==expected, 'Source checksum mismatch: '+source_name+'/'+name)
        verified.append({'source':str(source/relative),'sha256':expected})
    state = json.loads((snapshot/source_name/'HPC_STATUS.json').read_text())
    require(state.get('analysis_job_id')==cfg['source_runs'][source_name]['job_id']
            and state.get('scheduler_state')=='COMPLETED' and state.get('pipeline_exit_code')==0
            and state.get('scheduler_exit_code')=='0:0', 'Source job is not completed: '+source_name)
save(run/'audit/SOURCE_VERIFICATION.json', verified)
old_environment = json.loads((snapshot/'functional/audit/runtime_environment/environment.json').read_text())
new_environment = json.loads((run/'audit/runtime_environment/environment.json').read_text())
for key in ['versions','references','pipeline_script_sha256']:
    require(old_environment[key]==new_environment[key], 'Changed runtime: '+key)
reproduced = json.loads((snapshot/'sensitivity/results/BASELINE_REPRODUCTION.json').read_text())
require({row['family'] for row in reproduced}==set(cfg['families']) and all(row['passed'] for row in reproduced), 'Sensitivity baseline reproduction failed')
sensitivity_status = json.loads((snapshot/'sensitivity/results/SENSITIVITY_STATUS.json').read_text())
require(sensitivity_status['baseline_reproduced'] and not sensitivity_status['original_scientific_data_changed'], 'Invalid sensitivity status')
require(sorted(sensitivity_status['excluded_ASVs_in_sensitivity_only'])==sorted(cfg['expected_filtered_ASVs']), 'Sensitivity exclusion set changed')

def numeric(path):
    frame = pd.read_csv(path, sep='\t', index_col=0)
    require(not frame.index.has_duplicates and not frame.columns.has_duplicates, 'Duplicate profile labels')
    a = frame.to_numpy(float)
    require(np.isfinite(a).all() and (a>=0).all(), 'Invalid functional abundances')
    return frame

def clean(value):
    if isinstance(value, dict): return {k:clean(v) for k,v in value.items()}
    if isinstance(value, (tuple,list,np.ndarray)): return [clean(v) for v in value]
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (float,np.floating)): return float(value) if math.isfinite(value) else None
    if isinstance(value,np.bool_): return bool(value)
    return value

def csv_output(name, rows, default_fields=()):
    fields = list(default_fields)
    for row in rows:
        for key in row:
            if key not in fields: fields.append(key)
    flat=[]
    for row in rows:
        row=clean(row)
        flat.append({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    table(out/name, flat, fields=fields or ['status'])

def context(domain, variant, scope, cohort, family):
    return {'domain':domain,'variant':variant,'scope':scope,'farm':cohort['farm'],
            'analytic_system':cohort['system'],'family':family}

def skipped(reason): return {'status':'SKIPPED','reason':reason,'p_value':None}

profiles = {}; metadata = {}; qc = {}; eligibility = []
for domain in ['bacteria','archaea']:
    rows = readcsv(snapshot/f'functional/validated/{domain}/sample_metadata.csv')
    require(len({r['analysis_sample'] for r in rows})==len(rows), 'Duplicate sample metadata')
    require(len({(r['farm'],r['analytic_system'],r['biological_plot_id'],r['collection_date']) for r in rows})==len(rows), 'Duplicate physical plot/event identity')
    metadata[domain] = rows
    qc[domain] = {r['analysis_sample']:r for r in readcsv(snapshot/f'functional/results/{domain}/sample_prediction_QC.csv')}
    expected = {r['analysis_sample'] for r in rows}
    require(set(qc[domain])==expected, 'QC and metadata sample sets differ')
    profiles[domain] = {'original':{family:numeric(snapshot/f'functional/results/{domain}/{family}_predicted_abundance.tsv.gz') for family in cfg['families']}}
    if domain=='archaea':
        profiles[domain]['filtered'] = {family:numeric(snapshot/f'sensitivity/results/domain_concordant_{family}.tsv.gz') for family in cfg['families']}
        for family in cfg['families']:
            baseline = numeric(snapshot/f'sensitivity/results/baseline_reproduced_{family}.tsv.gz')
            original = profiles[domain]['original'][family]
            require(set(baseline.index)==set(original.index) and set(baseline.columns)==expected, 'Baseline label sets changed')
            require(np.allclose(original,baseline.reindex(index=original.index,columns=original.columns),rtol=1e-10,atol=1e-8), 'Baseline reproduction data mismatch')
            filtered = profiles[domain]['filtered'][family].reindex(columns=original.columns)
            require(np.array_equal(original.sum(axis=0).to_numpy()>0,filtered.sum(axis=0).to_numpy()>0),
                    'Original/filtered zero-profile eligibility differs; stop rather than compare different populations')
    for row in rows:
        require(not truth(row.get('descriptive_reference')), 'Descriptive reference entered prediction samples')
        require(float(row['target_read_count'])>0, 'Zero target count in prediction sample')
        declared = next((c for c in cfg['cohorts'] if c['farm']==row['farm'] and c['system']==row['analytic_system']), None)
        require(declared is not None and row['collection_date'] in declared['dates'] and row['treatment'] in declared['treatments'], 'Unfrozen cohort, date or treatment')
    for variant, families in profiles[domain].items():
        for family,frame in families.items():
            require(set(frame.columns)==expected, 'Profile sample set changed')
            for row in rows:
                sid = row['analysis_sample']; nonzero = float(frame[sid].sum())>0
                q = qc[domain][sid]
                eligibility.append(dict(domain=domain,variant=variant,family=family,analysis_sample=sid,
                    farm=row['farm'],analytic_system=row['analytic_system'],collection_date=row['collection_date'],
                    biological_plot_id=row['biological_plot_id'],treatment=row['treatment'],
                    primary_included=truth(row['primary_included']),depth_sensitivity_included=truth(row['depth_sensitivity_included']),
                    nonzero_profile=nonzero,represented_read_fraction=float(q['represented_read_fraction']),
                    original_raw_read_weighted_NSTI=float(q['raw_read_weighted_NSTI_retained']),
                    original_discordant_read_fraction=float(q['discordant_reference_domain_read_fraction']),
                    hold_reason='' if nonzero else 'ZERO_FUNCTIONAL_PROFILE'))
csv_output('sample_eligibility.csv',eligibility)

all_global=[]; all_targeted=[]; summaries=[]; sample_values=[]; event_audit=[]; paired_rows=[]; dispersion=[]; ordinations=[]
for domain, variants in profiles.items():
    for variant, families in variants.items():
        for family, frame in families.items():
            totals = frame.sum(axis=0)
            relative = frame.divide(totals.replace(0,np.nan),axis=1)
            selected_panel = [p for p in panel if p['family']==family]
            for scope in cfg['scopes']:
                for cohort in cfg['cohorts']:
                    ctx=context(domain,variant,scope,cohort,family)
                    candidates=[r for r in metadata[domain] if r['farm']==cohort['farm'] and r['analytic_system']==cohort['system']
                        and truth(r['primary_included']) and (scope=='primary' or truth(r['depth_sensitivity_included']))]
                    rows=[r for r in candidates if totals[r['analysis_sample']]>0]
                    ids=[r['analysis_sample'] for r in rows]
                    by_event={date:[r for r in rows if r['collection_date']==date] for date in cohort['dates']}
                    valid_dates=[]
                    for date,events in by_event.items():
                        counts={t:len({r['biological_plot_id'] for r in events if r['treatment']==t}) for t in cohort['treatments']}
                        enough=all(n>=cfg['min_plots_per_arm'] for n in counts.values())
                        permitted=enough and cohort['treatment_inference']
                        if permitted: valid_dates.append(date)
                        event_audit.append(dict(ctx,collection_date=date,expected_treatments=';'.join(cohort['treatments']),
                            independent_plots_by_arm=json.dumps(counts,sort_keys=True),treatment_test_eligible=permitted,
                            reason='' if permitted else (cohort.get('hold_reason','') if not cohort['treatment_inference'] else 'Fewer than two independent plots in one or more declared treatment arms')))
                    if ids:
                        Y=hellinger_frame(relative[ids].T.to_numpy())
                        if scope=='primary':
                            pca=hellinger_pca(Y)
                            coords=pca['coordinates'];fractions=pca['variance_fractions']
                            for i,row in enumerate(rows):
                                ordinations.append(dict(ctx,analysis_sample=row['analysis_sample'],biological_plot_id=row['biological_plot_id'],
                                    collection_date=row['collection_date'],treatment=row['treatment'],axis1=float(coords[i][0]),axis2=float(coords[i][1]),
                                    axis1_variance_fraction=float(fractions[0]),axis2_variance_fraction=float(fractions[1])))
                        for date,events in by_event.items():
                            for treatment in cohort['treatments']:
                                arm=[r for r in events if r['treatment']==treatment]
                                armids=[r['analysis_sample'] for r in arm]
                                if not armids: continue
                                transformed=hellinger_frame(relative[armids].T.to_numpy())
                                centroid=transformed.mean(axis=0)
                                for i,row in enumerate(arm):
                                    dispersion.append(dict(ctx,collection_date=date,treatment=treatment,analysis_sample=row['analysis_sample'],
                                        biological_plot_id=row['biological_plot_id'],n_plots_in_arm=len(arm),
                                        distance_to_own_event_treatment_centroid=float(np.linalg.norm(transformed[i]-centroid))))
                    infer_rows=[r for r in rows if r['collection_date'] in valid_dates]
                    glob=dict(ctx,test_family='global',test_type='treatment_adjusted_for_event',**{'function':''},feature_name='',
                              contrast='All declared treatments across eligible events',included_dates=';'.join(valid_dates),n_rows=len(infer_rows))
                    if infer_rows:
                        YY=hellinger_frame(relative[[r['analysis_sample'] for r in infer_rows]].T.to_numpy())
                        glob.update(treatment_test(YY,infer_rows,seed=stable_seed(cfg['seed'],domain,scope,cohort['system'],family,'treatment'),n_perm=cfg['permutations'],exact_limit=cfg['exact_limit']))
                    else: glob.update(skipped(cohort.get('hold_reason','No collection event has two independent plots in every declared arm')))
                    all_global.append(glob)

                    endpoints=cohort['paired_dates']; pairs=[]
                    if endpoints:
                        early={r['biological_plot_id']:r for r in by_event[endpoints[0]]}
                        late={r['biological_plot_id']:r for r in by_event[endpoints[1]]}
                        for plot in sorted(set(early)&set(late)):
                            require(early[plot]['treatment']==late[plot]['treatment'],'Treatment changed within paired plot')
                            pairs.append((early[plot],late[plot]))
                    timeglob=dict(ctx,test_family='global',test_type='paired_shared_date_change',**{'function':''},feature_name='',
                        contrast=' to '.join(endpoints) if endpoints else 'No paired event',included_dates=';'.join(endpoints or []),n_rows=len(pairs))
                    pair_ids0=[a['analysis_sample'] for a,b in pairs];pair_ids1=[b['analysis_sample'] for a,b in pairs]
                    if len(pairs)>=cfg['min_paired_plots']:
                        first=hellinger_frame(relative[pair_ids0].T.to_numpy());last=hellinger_frame(relative[pair_ids1].T.to_numpy())
                        timeglob.update(paired_change_test(last-first,seed=stable_seed(cfg['seed'],domain,scope,cohort['system'],family,'time'),n_perm=cfg['permutations'],exact_limit=cfg['exact_limit']))
                    else: timeglob.update(skipped('Fewer than three physical plots observed at both frozen dates'))
                    timeglob['paired_plots_by_treatment']=json.dumps(dict(Counter(a['treatment'] for a,b in pairs)),sort_keys=True)
                    timeglob['mean_change_norm']=math.sqrt(timeglob['statistic']) if timeglob.get('statistic') is not None else None
                    timeglob.pop('mean_delta',None)
                    all_global.append(timeglob)
                    for a,b in pairs:
                        x=relative[a['analysis_sample']].to_numpy();y=relative[b['analysis_sample']].to_numpy()
                        paired_rows.append(dict(ctx,biological_plot_id=a['biological_plot_id'],treatment=a['treatment'],
                            early_date=a['collection_date'],late_date=b['collection_date'],early_sample=a['analysis_sample'],late_sample=b['analysis_sample'],
                            Hellinger_distance=float(np.linalg.norm(np.sqrt(y)-np.sqrt(x))),Bray_Curtis_on_unit_sum_profiles=float(np.abs(y-x).sum()/2)))
                    for feature in selected_panel:
                        fid=feature['function']; present=fid in frame.index
                        series=relative.loc[fid] if present else pd.Series(0.0,index=frame.columns)
                        fctx=dict(ctx,**{'function':fid},feature_name=feature['feature_name'])
                        for row in rows:
                            sample_values.append(dict(fctx,analysis_sample=row['analysis_sample'],biological_plot_id=row['biological_plot_id'],
                                collection_date=row['collection_date'],treatment=row['treatment'],
                                relative_abundance=float(series[row['analysis_sample']]),in_original_table=present))
                        for date,events in by_event.items():
                            for t in cohort['treatments']:
                                arm=[r for r in events if r['treatment']==t]
                                vals=[float(series[r['analysis_sample']]) for r in arm]
                                if vals: summaries.append(dict(fctx,collection_date=date,treatment=t,n_plots=len(arm),mean_relative_abundance=float(np.mean(vals)),
                                    min_relative_abundance=float(min(vals)),max_relative_abundance=float(max(vals)),in_original_table=present))
                            for treatment in cohort['treatments']:
                                if treatment==cohort['control']: continue
                                target=dict(fctx,test_family='targeted',test_type='event_treatment_vs_control',
                                    contrast=treatment+' vs '+cohort['control'],collection_date=date,
                                    effect_definition='Mean relative abundance treatment minus control')
                                two=[r for r in events if r['treatment'] in [cohort['control'],treatment]]
                                values=[float(series[r['analysis_sample']]) for r in two]
                                labels=[r['treatment'] for r in two]
                                if not cohort['treatment_inference']:
                                    target.update(skipped(cohort.get('hold_reason','Treatment inference withheld')))
                                elif date not in valid_dates:
                                    target.update(skipped('Declared four/all-arm event gate failed; no selective subgroup testing'))
                                elif not present: target.update(skipped('Prespecified function absent from this profile table'))
                                else:
                                    target.update(feature_treatment_contrast(values,labels,cohort['control'],treatment,
                                        seed=stable_seed(cfg['seed'],domain,scope,cohort['system'],family,fid,date,treatment),
                                        n_perm=cfg['permutations'],exact_limit=cfg['exact_limit']))
                                target['effect']=target.get('mean_difference',target.get('mean_change'))
                                all_targeted.append(target)
                        target=dict(fctx,test_family='targeted',test_type='paired_shared_date_change',collection_date='',
                            contrast=' to '.join(endpoints) if endpoints else 'No paired event',
                            effect_definition='Mean within-plot late minus early relative abundance',
                            paired_plots_by_treatment=json.dumps(dict(Counter(a['treatment'] for a,b in pairs)),sort_keys=True))
                        if not present: target.update(skipped('Prespecified function absent from this profile table'))
                        elif len(pairs)<cfg['min_paired_plots']:target.update(skipped('Fewer than three physical plots at both frozen dates'))
                        else:
                            delta=[float(series[b['analysis_sample']]-series[a['analysis_sample']]) for a,b in pairs]
                            target.update(paired_feature_contrast(delta,seed=stable_seed(cfg['seed'],domain,scope,cohort['system'],family,fid,'time'),
                                n_perm=cfg['permutations'],exact_limit=cfg['exact_limit']))
                        target['effect']=target.get('mean_change')
                        all_targeted.append(target)

# Hypothesis identity excludes variant; neither version can be selected by its p-value.
identity_keys=['test_family','domain','scope','farm','analytic_system','family','test_type','function','contrast','collection_date']
def hypothesis_id(row):
    return '|'.join(str(row.get(key,'')) for key in identity_keys)
robust_groups=defaultdict(dict)
for row in all_global+all_targeted:
    row['hypothesis_id']=hypothesis_id(row)
    require(row['variant'] not in robust_groups[row['hypothesis_id']], 'Duplicate planned hypothesis')
    robust_groups[row['hypothesis_id']][row['variant']]=row
robustness=[]
for hid, variants in robust_groups.items():
    original=variants['original']; filtered=variants.get('filtered')
    tested_original=original['status']=='TESTED' and original.get('p_value') is not None
    tested_filtered=(filtered is not None and filtered['status']=='TESTED' and filtered.get('p_value') is not None)
    tested=tested_original and (original['domain']=='bacteria' or tested_filtered)
    po=original.get('p_value');pf=filtered.get('p_value') if filtered else None
    pc=max(po,pf) if tested and filtered else (po if tested else None)
    effect_key='effect' if original['test_family']=='targeted' else ('partial_r2' if original['test_type']=='treatment_adjusted_for_event' else 'mean_change_norm')
    eo=original.get(effect_key);ef=filtered.get(effect_key) if filtered else None
    direction='NOT_APPLICABLE_GLOBAL' if original['test_family']=='global' else ('SINGLE_BACTERIAL_VERSION' if filtered is None else ('AGREE' if eo is not None and ef is not None and np.sign(eo)==np.sign(ef) else 'DISAGREE_OR_NOT_ESTIMABLE'))
    robustness.append(dict({key:original.get(key,'') for key in identity_keys},hypothesis_id=hid,feature_name=original.get('feature_name',''),
        p_original=po,p_filtered=pf,p_conservative=pc,holm_p_conservative=None,direction_agreement=direction,
        effect_original=eo,effect_filtered=ef,status='TESTED_BOTH' if tested and filtered else ('TESTED_BACTERIA' if tested else 'NOT_ESTIMABLE'),
        original_status=original['status'],filtered_status=filtered['status'] if filtered else '',
        original_reason=original.get('reason',''),filtered_reason=filtered.get('reason','') if filtered else ''))
multiplicity_groups=defaultdict(list)
for i,r in enumerate(robustness):
    family_id=(r['scope']+'|global_all_systems_domains_profiles_testclasses' if r['test_family']=='global'
               else '|'.join(str(r[k]) for k in ['scope','test_family','farm','analytic_system','domain','family','test_type']))
    r['multiplicity_family_id']=family_id
    multiplicity_groups[family_id].append(i)
family_audit=[]
for family_id,indices in multiplicity_groups.items():
    adjusted=holm_adjust([robustness[i]['p_conservative'] if robustness[i]['p_conservative'] is not None else 1.0 for i in indices])
    family_audit.append({'multiplicity_family_id':family_id,'planned_hypotheses':len(indices),
        'estimable_hypotheses':sum(robustness[i]['p_conservative'] is not None for i in indices),
        'method':'Holm; untestable planned hypotheses count as p=1',
        'scope_of_control':'This declared family only; global family includes all systems/domains/profile types'})
    for i,padj in zip(indices,adjusted):
        r=robustness[i]
        if r['p_conservative'] is not None:r['holm_p_conservative']=padj
        r['multiplicity_family_size']=len(indices)
        r['conditional_support_in_all_versions']=bool(r['p_conservative'] is not None and padj<=0.05 and r['direction_agreement'] not in ['DISAGREE_OR_NOT_ESTIMABLE'])
csv_output('multiplicity_family_audit.csv',family_audit)

csv_output('global_tests.csv',all_global,default_fields=['hypothesis_id','domain','variant','scope','farm','analytic_system','family','test_family','test_type','function','feature_name','contrast','status','reason','p_value'])
csv_output('targeted_tests.csv',all_targeted,default_fields=['hypothesis_id','domain','variant','scope','farm','analytic_system','family','test_family','test_type','function','feature_name','contrast','collection_date','status','reason','p_value','effect'])
csv_output('robustness.csv',robustness)
csv_output('event_design_audit.csv',event_audit)
csv_output('paired_profile_changes.csv',paired_rows)
csv_output('feature_sample_values.csv',sample_values)
csv_output('feature_summaries.csv',summaries)
csv_output('dispersion_diagnostics.csv',dispersion)
csv_output('ordination.csv',ordinations)
shutil.copy2(run/'FEATURE_PANEL.csv',out/'FEATURE_PANEL.csv')
summary=[
 'NRCS FUNCTIONAL COMPARISONS - PUMA EXECUTION '+execution['job_id'],
 'Source predictions: job 24067682; sensitivity: job 24073133; corrected metadata: job 24067160.',
 'All original input sources are preserved. Seven farm/crop systems and the two sequencing domains are separate.',
 'Global tests compare Hellinger profiles; treatment-label permutations preserve physical plots and exact visit patterns.',
 'Treatment inference requires >=2 independent plots in every declared arm at each included event.',
 'Oatman 2025 treatment associations are descriptive because treatment and field side are confounded.',
 'Paired changes use a frozen exact date pair; every matched physical plot contributes once.',
 'The EC/pathway panel was selected from biology and verified annotations before testing.',
 'Original and filtered archaeal versions are paired checks; conservative p is their maximum.',
 'Holm adjustment counts planned hypotheses, with untestable comparisons counting as p=1.',
 'Global families include all systems/domains/profiles; targeted families separate system, domain, panel and test class.',
 'Targeted error control is within each declared family, with no project-wide targeted error guarantee.',
 'Primary and existing depth-sensitivity scopes have separate multiplicity families.',
 'Scalar uncertainty intervals are approximate small-sample Welch/paired-t intervals, with degeneracies exposed.',
 'Statistical associations assume exchangeable independent plots, or independent symmetric paired changes.',
 'Randomization and spatial independence are not established. Temporal changes may include season/weather/management effects.',
 'Predicted genetic potential does not measure genes, activity, nutrient flux or crop benefit.',
 'Global treatment/paired-time test rows: '+str(Counter(r['status'] for r in all_global)),
 'Targeted tests: '+str(Counter(r['status'] for r in all_targeted)),
 'Visual review of the newly generated figures is pending. No forecasting or soil/crop integration is performed here.'
]
(out/'RUN_SUMMARY.txt').write_text('\n'.join(summary)+'\n')
command=['Rscript','--vanilla',str(run/'render_report.R'),str(run)]
save(run/'audit/commands.json',[{'argv':command,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}])
with (run/'logs/report.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
require((out/'functional_comparison_review.pdf').is_file() and (out/'functional_comparison_review.pdf').stat().st_size>1000, 'Review PDF missing')
save(out/'COMPARISON_STATUS.json',clean({'execution':execution,'status':'FUNCTIONAL_COMPARISONS_COMPLETED_REVIEW_PENDING',
    'source_jobs':['24067160','24067682','24073133'],'seed':cfg['seed'], 'cohorts':cfg['cohorts'],
    'planned_hypotheses':len(robustness),'global_rows':len(all_global),'targeted_rows':len(all_targeted),
    'inference':'Exploratory conditional associations; no causal or measured functional claims',
    'original_scientific_data_changed':False,'visual_review':'Pending returned-PDF inspection','ten_year_forecast_generated':False}))
(run/'ANALYSES_COMPLETE.txt').write_text('Functional comparisons, sensitivity checks and figures completed on Puma; review pending.\n')
print(json.dumps({'status':'FUNCTIONAL_COMPARISONS_COMPLETED_REVIEW_PENDING','job_id':execution['job_id'],'planned_hypotheses':len(robustness)},indent=2))
