#!/usr/bin/env python3
"""Frozen Puma-only, known-plot, one-event Shannon point diagnostic."""
from hpc_guard import require_hpc
execution = require_hpc()  # Must precede source reads, fixture or baseline imports.

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
import csv
import gzip
import hashlib
import json
import math
import shutil
import sys
from baseline_core import (METHODS, NO_SUPPORT, eligible, fit_baselines, select_training,
                           predict, score, metrics, compare_fixed_errors, stable_digest, require)


REQUIRED = {
    'corrected': ['HPC_STATUS.json','audit/run_end.json','ANALYSES_COMPLETE.txt',
        'payload/inputs/workbook_data.json','payload/inputs/bacteria_counts.tsv.gz',
        'payload/inputs/archaea_counts.tsv.gz','audit/derived_inputs.json',
        'payload/prepare_inputs.py','payload/METHODS.md','payload/inputs/workbook_data_before_revision.json'],
    'functional': ['HPC_STATUS.json','audit/run_end.json','ANALYSES_COMPLETE.txt',
        'validated/bacteria/sample_metadata.csv','validated/archaea/sample_metadata.csv','results/INPUT_VALIDATION.json'],
    'feasibility_v2': ['HPC_STATUS.json','audit/run_end.json','ANALYSES_COMPLETE.txt',
        'results/FEASIBILITY_STATUS.json','results/microbiome_sample_audit.csv',
        'results/microbiome_plot_visit_support.csv','results/microbiome_cohort_support.csv']}
ARMS = ('Blood_meal_and_Feather_meal','Blood_meal_and_Feather_meal_and_cover_crop',
        'Control','Cover_crop_only')
DATES = ('2023-11-16','2024-07-06','2024-08-14')
SCOPES = ('PRIMARY','DEPTH_SENSITIVITY')
IDENTITY_FIELDS = ('farm','analytic_system','microbial_domain','collection_date',
    'biological_plot_id','treatment','sample_key','analysis_sample')
NUMBER_FIELDS = ('Shannon','target_read_count','observed_ASVs')
FLAG_FIELDS = ('primary_included','depth_sensitivity_included','descriptive_reference')


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576),b''):
            result.update(block)
    return result.hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path,value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(path)


def readcsv(path,required=()):
    with path.open(newline='',encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames is not None and set(required).issubset(reader.fieldnames),
                'Missing CSV schema: '+str(path))
        rows = list(reader)
    require(all(None not in row for row in rows),'Malformed CSV row: '+str(path))
    return rows


def table(path,rows,fields):
    columns = list(fields)
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    require(columns,'Empty output needs schema.')
    temporary = path.with_name(path.name+'.tmp')
    with temporary.open('w',newline='',encoding='utf-8') as stream:
        writer = csv.DictWriter(stream,fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def boolean(value):
    text = str(value).strip().lower()
    require(text in ('true','false'),'Missing or nonexplicit source boolean: '+repr(value))
    return text == 'true'


def numeric(value):
    try:
        result = float(value)
    except (ValueError,TypeError):
        return None
    return result if math.isfinite(result) else None


def exact_date(value):
    try:
        result = date.fromisoformat(value)
    except (ValueError,TypeError):
        raise RuntimeError('Malformed actual collection date: '+repr(value))
    require(result.isoformat()==value,'Noncanonical date.')
    return result


def protocol_gate(config):
    p = config['protocol']
    exact = dict(farm='Whipstone',system='Annual',domains=['bacteria','archaea'],
        scopes=list(SCOPES),response='Shannon',training_dates=list(DATES),
        cutoff_date='2025-01-01',held_out_date='2025-08-17',treatment_arms=list(ARMS),
        primary_expected_plots=12,primary_expected_plots_per_arm=3,
        response_definition='H=-sum(p*ln(p)); positive-ASV counts / total; unrarefied target counts',
        response_unit='nats',source_definition_gate='PASSED_EXECUTED_PUMA_GENERATOR_VERIFIED',
        methods=list(METHODS))
    for key,value in exact.items():
        require(p.get(key)==value,'Frozen protocol disagreement: '+key)
    require(p.get('Shannon_count_audit_abs_tolerance')==1e-10,'Unexpected response audit tolerance.')
    require(p.get('Shannon_count_audit_rel_tolerance')==1e-10,'Unexpected relative response audit tolerance.')
    require(all(d < p['cutoff_date'] for d in DATES),'Training date crosses cutoff.')
    require(p['held_out_date'] >= p['cutoff_date'],'Invalid whole-event holdout.')
    require(all(config.get(field) is False for field in
                ('automatic_forecast_authorization','ten_year_forecast_generated','predictive_validation_established')),
            'Forecast/validation authorization flags must remain explicitly false.')
    return p


def snapshot_sources(run,config):
    require(set(config['source_runs'])==set(REQUIRED) and set(config['sources'])==set(REQUIRED),
            'Unexpected source aliases.')
    snapshot = run/'source_snapshot'
    snapshot.mkdir(exist_ok=False)
    hashes, provenance = [], []
    for alias,required in REQUIRED.items():
        pins = config['sources'][alias]
        require(set(required).issubset(pins),'Missing necessary source locks: '+alias)
        root = Path(config['source_runs'][alias]['path']).resolve(strict=True)
        job = str(config['source_runs'][alias]['analysis_job_id'])
        require(job.isdigit(),'Invalid source job ID.')
        for relative,expected in sorted(pins.items()):
            rel = Path(relative)
            require(not rel.is_absolute() and '..' not in rel.parts,'Unsafe source relative path.')
            original = (root/rel).resolve(strict=True)
            require(original.is_relative_to(root) and original.is_file(),'Source escaped locked run.')
            require(len(expected)==64 and all(c in '0123456789abcdef' for c in expected),
                    'Invalid source SHA256.')
            require(sha(original)==expected,'Original locked source checksum mismatch: '+alias+'/'+relative)
            copied = snapshot/alias/rel
            copied.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(original,copied)
            require(sha(copied)==expected,'Copied source checksum mismatch.')
            hashes.append(dict(source=alias,relative_path=relative,sha256=expected,
                source_job_id=job,source_path=str(original),snapshot_path=str(copied)))
        status = load(snapshot/alias/'HPC_STATUS.json')
        end = load(snapshot/alias/'audit/run_end.json')
        require(str(status.get('analysis_job_id'))==job,'Source status job mismatch.')
        require(status.get('scheduler_state')=='COMPLETED' and status.get('scheduler_exit_code')=='0:0'
                and status.get('pipeline_exit_code')==0,'Source scheduler/pipeline did not succeed.')
        completion = 'audit_execution_completed' if alias=='feasibility_v2' else 'scientific_scripts_completed'
        require(status.get(completion) is True,'Source execution completion flag absent.')
        require(end.get('pipeline_exit_code')==0 and str(end.get('execution',{}).get('job_id'))==job
                and end.get('execution',{}).get('cluster')=='puma','Source runtime provenance mismatch.')
        require((snapshot/alias/'ANALYSES_COMPLETE.txt').read_text().strip(),'Empty source sentinel.')
        provenance.append(dict(source=alias,analysis_job_id=job,scheduler_state='COMPLETED',
            pipeline_exit_code=0,pinned_files=len(pins),gate='COMPLETED_PUMA_SOURCE_VERIFIED'))
    v2 = load(snapshot/'feasibility_v2/results/FEASIBILITY_STATUS.json')
    require(str(v2.get('execution',{}).get('job_id'))==
            str(config['source_runs']['feasibility_v2']['analysis_job_id']), 'Feasibility runtime job disagreement.')
    require(v2.get('audit_revision')=='v2_catalog_hold_membership_and_missing_method_labels',
            'Wrong feasibility revision.')
    require(v2.get('source_integrity_passed') is True and
            v2.get('source_files_verified_unchanged_after_audit') is True and
            v2.get('source_files_changed') is False,'Feasibility source integrity not established.')
    for alias in ('corrected','functional'):
        require(str(v2.get('source_jobs',{}).get(alias))==str(config['source_runs'][alias]['analysis_job_id']),
                'Feasibility lineage used a different source: '+alias)
    derived = load(snapshot/'corrected/audit/derived_inputs.json')
    require(str(derived.get('execution',{}).get('job_id'))==str(config['source_runs']['corrected']['analysis_job_id'])
            and derived.get('execution',{}).get('cluster')=='puma','Shannon generator receipt provenance mismatch.')
    require('Shannon=-sum(p*ln(p))' in derived.get('method',''),'Pinned source formula receipt missing.')
    validation=load(snapshot/'functional/results/INPUT_VALIDATION.json')
    require(str(validation.get('execution',{}).get('job_id'))==str(config['source_runs']['functional']['analysis_job_id'])
            and validation.get('execution',{}).get('cluster')=='puma','Functional input-validation provenance mismatch.')
    require({row.get('domain') for row in validation.get('domains',[])}=={'bacteria','archaea'}
            and all(row.get('source_counts_identical') is True and row.get('fasta_catalogue_identical') is True
                    for row in validation['domains']),'Functional source count/catalogue verification absent.')
    return snapshot,hashes,provenance


def reverify_sources(hashes):
    verified=[]
    for item in hashes:
        original, copied = sha(Path(item['source_path'])),sha(Path(item['snapshot_path']))
        require(original==item['sha256'] and copied==item['sha256'],
                'Original or snapshot source changed during pilot: '+item['relative_path'])
        verified.append(dict(source=item['source'],relative_path=item['relative_path'],
            expected_sha256=item['sha256'],original_sha256_after_pilot=original,
            snapshot_sha256_after_pilot=copied,verified_unchanged=True))
    return verified


def metadata_gate(snapshot):
    source = load(snapshot/'corrected/payload/inputs/workbook_data.json')['tables']['16S samples']
    before = load(snapshot/'corrected/payload/inputs/workbook_data_before_revision.json')['tables']['16S samples']
    require(source==before,'Corrected soil revision unexpectedly changed original recomputed 16S metadata.')
    fields = IDENTITY_FIELDS+NUMBER_FIELDS+FLAG_FIELDS
    require(set(fields).issubset(source['columns']),'Missing corrected 16S schema.')
    require(len(source['columns'])==len(set(source['columns'])),'Duplicate corrected columns.')
    originals={}
    for values in source['rows']:
        require(len(values)==len(source['columns']),'Malformed corrected 16S record.')
        row={k:('' if v is None else str(v)) for k,v in zip(source['columns'],values)}
        key=(row['microbial_domain'],row['analysis_sample'])
        require(row['analysis_sample'] and key not in originals,'Duplicate corrected library identity.')
        originals[key]=row
    audits=readcsv(snapshot/'feasibility_v2/results/microbiome_sample_audit.csv',
        ('domain','analysis_sample','farm','system','collection_date','physical_plot','sample_key',
         'treatment','positive_reads','primary_included','descriptive_reference','scope_membership','identity_gate'))
    audit_by_id={}
    for row in audits:
        key=(row['domain'],row['analysis_sample'])
        require(key not in audit_by_id,'Duplicate v2 library audit.')
        audit_by_id[key]=row
    require(set(audit_by_id)==set(originals),'V2 all-recorded identity projection differs from corrected workbook.')
    for domain in ('bacteria','archaea'):
        validated=readcsv(snapshot/('functional/validated/'+domain+'/sample_metadata.csv'),fields)
        seen=set()
        for row in validated:
            key=(domain,row['analysis_sample'])
            require(row['microbial_domain']==domain and key in originals and key not in seen,
                    'Missing/duplicate validated identity.')
            seen.add(key); original=originals[key]
            require(all(row[f]==original[f] for f in IDENTITY_FIELDS),'Corrected/functional identity mismatch.')
            require(all(numeric(row[f]) is not None and numeric(row[f])==numeric(original[f])
                        for f in NUMBER_FIELDS),'Corrected/functional numerical Shannon/reads/richness mismatch.')
            require(all(boolean(row[f])==boolean(original[f]) for f in FLAG_FIELDS),
                    'Corrected/functional eligibility flag mismatch.')
        expected={key for key,row in originals.items() if key[0]==domain and
                  numeric(row['target_read_count']) is not None and numeric(row['target_read_count'])>0
                  and not boolean(row['descriptive_reference'])}
        require(seen==expected,'Functional metadata is not the exact positive nonreference source projection.')
    scoped=[]
    for key,original in originals.items():
        audit=audit_by_id[key]
        pairs=(('farm','farm'),('system','analytic_system'),('collection_date','collection_date'),
               ('physical_plot','biological_plot_id'),('sample_key','sample_key'),('treatment','treatment'))
        require(all(audit[a]==original[b] for a,b in pairs),'V2 recorded identity differs from corrected source.')
        positive=numeric(original['target_read_count']) is not None and numeric(original['target_read_count'])>0
        primary,depth,reference=(boolean(original[f]) for f in FLAG_FIELDS)
        memberships={'all_recorded'}
        if positive and not reference:
            memberships.add('all_positive_nonreference')
            if primary:memberships.add('primary_positive_nonreference')
            if depth:memberships.add('depth_sensitivity_positive_nonreference')
        require(set(audit['scope_membership'].split(';'))==memberships and
                boolean(audit['positive_reads'])==positive and boolean(audit['primary_included'])==primary
                and boolean(audit['descriptive_reference'])==reference,'V2 eligibility audit disagrees with source flags.')
        if original['farm']!='Whipstone' or original['analytic_system']!='Annual':
            continue
        require(audit['identity_gate']=='RECORDED_IDENTITY','Scoped identity is held in the v2 source audit.')
        require(original['microbial_domain'] in ('bacteria','archaea'),'Unexpected pilot domain.')
        require(all(original[f] for f in IDENTITY_FIELDS),'Missing scoped physical/source identity.')
        when=original['collection_date']; exact_date(when)
        require(when in set(DATES)|{'2025-08-17'},'Unfrozen Whipstone Annual collection date.')
        require(original['treatment'] in ARMS,'Unknown Whipstone Annual treatment.')
        values={f:numeric(original[f]) for f in NUMBER_FIELDS}
        require(values['target_read_count'] is not None and values['target_read_count']>=0
                and values['target_read_count'].is_integer(),'Invalid scoped target reads.')
        require(values['observed_ASVs'] is not None and values['observed_ASVs']>=0
                and values['observed_ASVs'].is_integer(),'Invalid scoped observed richness.')
        if primary and not reference:
            require(values['Shannon'] is not None and values['Shannon']>=0 and values['target_read_count']>0,
                    'Source-primary Whipstone Annual response/depth is invalid; refuse rather than adapt.')
        require(values['Shannon'] is not None and values['Shannon']>=0,
                'Unexpected scoped nonfinite/negative Shannon; no silent replacement.')
        scoped.append(dict(original,domain=original['microbial_domain'],plot=original['biological_plot_id'],
            date=when,primary_included=primary,depth_sensitivity_included=depth,
            descriptive_reference=reference,**values))
    seen=set(); treatments=defaultdict(set)
    for row in scoped:
        key=(row['domain'],row['plot'],row['date'])
        require(key not in seen,'Duplicate biological plot/date; technical library split is forbidden.')
        seen.add(key);treatments[(row['domain'],row['plot'])].add(row['treatment'])
    require(all(len(values)==1 for values in treatments.values()),'Physical plot changed treatment.')
    return scoped


def expected_primary_gate(snapshot,rows):
    cohorts=readcsv(snapshot/'feasibility_v2/results/microbiome_cohort_support.csv')
    visits=readcsv(snapshot/'feasibility_v2/results/microbiome_plot_visit_support.csv')
    for domain in ('bacteria','archaea'):
        primary=[r for r in rows if r['domain']==domain and eligible(r,'PRIMARY')]
        training=[r for r in primary if r['date'] in DATES]
        held=[r for r in primary if r['date']=='2025-08-17']
        plots={r['plot'] for r in primary}
        require(len(plots)==12 and len(held)==12 and len(training)==24,'Unexpected primary recorded plot/visit design.')
        require({r['plot'] for r in training}=={r['plot'] for r in held}==plots,'Primary holdout is not all 12 known plots.')
        require(set(r['date'] for r in training)==set(DATES),'Primary actual pre-2025 collection-date support changed.')
        require(dict(Counter(r['treatment'] for r in held))=={arm:3 for arm in ARMS},'Primary arms not 3 plots each.')
        for plot in plots:
            history=[r for r in primary if r['plot']==plot]
            require(len(history)==3 and {r['date'] for r in history}&{'2023-11-16'}
                    and len([r for r in history if r['date'] in ('2024-07-06','2024-08-14')])==1,
                    'Primary plot lacks the recorded 2023/actual-2024/2025 history.')
        cohort=[r for r in cohorts if r['farm']=='Whipstone' and r['domain']==domain and r['system']=='Annual'
                and r['scope']=='primary_positive_nonreference']
        require(len(cohort)==1 and cohort[0]['n_physical_plots']=='12' and cohort[0]['n_library_rows']=='36',
                'Primary v2 cohort receipt changed.')
        evidence=[r for r in visits if r['farm']=='Whipstone' and r['domain']==domain and r['system']=='Annual'
                  and r['scope']=='primary_positive_nonreference']
        require({r['physical_plot'] for r in evidence}==plots and len(evidence)==12,
                'Primary fixed plot set differs from reviewed v2 support.')
        for row in evidence:
            actual={r['date'] for r in primary if r['plot']==row['physical_plot']}
            require(set(row['collection_dates'].split(';'))==actual and row['n_distinct_visits']=='3',
                    'Primary fixed plot/date support differs from v2 audit.')


def count_response_audit(snapshot,rows,tolerance,relative_tolerance):
    checks=[]
    for domain in ('bacteria','archaea'):
        expected={r['analysis_sample']:r for r in rows if r['domain']==domain}
        found=set(); sample_ids=set()
        with gzip.open(snapshot/('corrected/payload/inputs/'+domain+'_counts.tsv.gz'),
                       'rt',newline='',encoding='utf-8') as stream:
            reader=csv.reader(stream,delimiter='\t')
            header=next(reader)
            require(len(header)>1 and all(header[1:]) and len(set(header[1:]))==len(header)-1,
                    'Missing/duplicate raw target ASV IDs.')
            for fields in reader:
                require(len(fields)==len(header) and fields[0] and fields[0] not in sample_ids,
                        'Malformed or duplicate raw target library ID.')
                sample_ids.add(fields[0]);counts=[]
                for text in fields[1:]:
                    value=numeric(text)
                    require(value is not None and value>=0 and value.is_integer(),
                            'Raw target count is noninteger/nonfinite/negative.')
                    counts.append(int(value))
                if fields[0] not in expected:
                    continue
                row=expected[fields[0]];total=sum(counts);richness=sum(c>0 for c in counts)
                require(total>0,'Pilot raw profile has no positive reads.')
                entropy=-math.fsum((c/total)*math.log(c/total) for c in counts if c>0)
                difference=abs(entropy-row['Shannon'])
                require(total==row['target_read_count'] and richness==row['observed_ASVs']
                        and math.isclose(entropy,row['Shannon'],abs_tol=tolerance,rel_tol=relative_tolerance),
                        'Whipstone Annual raw-count Shannon/reads/richness audit failed.')
                found.add(fields[0])
                checks.append(dict(domain=domain,analysis_sample=fields[0],sample_key=row['sample_key'],
                    biological_plot_id=row['plot'],collection_date=row['date'],recorded_target_reads=row['target_read_count'],
                    verified_target_reads=total,recorded_observed_ASVs=row['observed_ASVs'],verified_observed_ASVs=richness,
                    recorded_Shannon=row['Shannon'],verification_Shannon_nats=entropy,
                    absolute_difference=difference,absolute_tolerance=tolerance,
                    relative_tolerance=relative_tolerance,status='PASS',
                    source_metric_overwritten=False,verification_only=True))
        require(found==set(expected),'Raw target matrix lacks a scoped source library.')
    return checks


def calculations(rows,p,results):
    qc=[];means=[];memberships=[];support=[];cohorts=[];predictions=[];fits={};frozen={}
    for domain in p['domains']:
        source=[r for r in rows if r['domain']==domain]
        for scope in SCOPES:
            training=select_training(source,scope,DATES,p['cutoff_date'])
            fit=fit_baselines(training,ARMS,DATES,p['cutoff_date'])
            fits[(domain,scope)]=fit
            for row in fit['plot_summaries'].values():
                means.append(dict(domain=domain,scope=scope,biological_plot_id=row['plot'],
                    **{k:v for k,v in row.items() if k!='plot'},training_digest=fit['training_digest']))
            memberships.extend(dict(domain=domain,scope=scope,biological_plot_id=r['plot'],
                collection_date=r['date'],**{k:v for k,v in r.items() if k not in ('plot','date')}) for r in training)
            for method in METHODS:
                support.append(dict(domain=domain,scope=scope,method=method,status=fit['method_status'][method],
                    n_training_plots=len(fit['plot_summaries']),n_training_rows=len(training),
                    training_arm_plot_counts=json.dumps(fit['arm_plot_counts'],sort_keys=True),
                    global_training_mean=fit['global_training_mean'],
                    arm_training_means=json.dumps(fit['arm_training_means'],sort_keys=True),
                    training_digest=fit['training_digest'],fit_uses_held_out_response=False,
                    fit_conditioned_on_held_out_availability=False))
    # All four training fits/memberships are serialized before target selection/scoring.
    save(results/'training_fits.json',{domain+'__'+scope:fit for (domain,scope),fit in fits.items()})
    table(results/'training_plot_means.csv',means,('domain','scope','biological_plot_id','treatment',
        'n_training_visits','training_dates','last_prior_date','last_prior_Shannon','plot_training_mean'))
    table(results/'training_membership.csv',memberships,('domain','scope','biological_plot_id','collection_date',
        'treatment','Shannon','analysis_sample','sample_key'))
    table(results/'baseline_support.csv',support,('domain','scope','method','status','n_training_plots'))
    for domain in p['domains']:
        source=[r for r in rows if r['domain']==domain]
        for scope in SCOPES:
            fit=fits[(domain,scope)];targets=[]
            for row in source:
                in_scope=eligible(row,scope);is_test=row['date']==p['held_out_date']
                known=row['plot'] in fit['plot_summaries']
                if in_scope and not is_test:
                    disposition='TRAINING_ELIGIBLE';reason='Frozen pre-2025 row flags only'
                elif in_scope and is_test and known:
                    disposition='TEST_EVALUATED';reason='Eligible held-out row and independently fitted historical plot'
                    targets.append(dict(plot=row['plot'],treatment=row['treatment'],sample_key=row['sample_key'],
                                        analysis_sample=row['analysis_sample']))
                elif in_scope and is_test:
                    disposition='HELD_NO_ELIGIBLE_PRIOR';reason='No training history in this scope; no history borrowed'
                else:
                    reasons=[]
                    if not row['primary_included']:reasons.append('source_primary_included=False')
                    if row['descriptive_reference']:reasons.append('descriptive_reference=True')
                    if row['target_read_count']<=0:reasons.append('nonpositive target reads')
                    if scope=='DEPTH_SENSITIVITY' and not row['depth_sensitivity_included']:
                        reasons.append('source_depth_sensitivity_included=False')
                    disposition='EXCLUDED_SOURCE_FLAGS';reason='; '.join(reasons)
                qc.append(dict(domain=domain,scope=scope,sample_key=row['sample_key'],analysis_sample=row['analysis_sample'],
                    biological_plot_id=row['plot'],treatment=row['treatment'],collection_date=row['date'],
                    partition='HELD_OUT' if is_test else 'TRAINING_ALLOWED',
                    source_primary_included=row['primary_included'],
                    source_depth_sensitivity_included=row['depth_sensitivity_included'],
                    descriptive_reference=row['descriptive_reference'],target_read_count=row['target_read_count'],
                    observed_ASVs=row['observed_ASVs'],recorded_Shannon=row['Shannon'],
                    eligible_in_scope=in_scope,known_plot_for_evaluation=known if is_test else '',
                    disposition=disposition,reason=reason))
            preds=predict(fit,targets,p['held_out_date'],p['cutoff_date'])
            digest=stable_digest(preds)
            frozen[(domain,scope)]=(preds,digest)
            predictions.extend(dict(row,domain=domain,scope=scope,prediction_digest=digest) for row in preds)
            cohorts.append(dict(domain=domain,scope=scope,n_training_rows=len(fit['training_membership']),
                n_training_plots=len(fit['plot_summaries']),n_known_eligible_test_plots=len(targets),
                training_plot_ids=';'.join(sorted(fit['plot_summaries'])),
                known_test_plot_ids=';'.join(sorted(r['plot'] for r in targets)),
                training_digest=fit['training_digest'],prediction_digest=digest,
                training_selection_uses_future_flags=False,evaluation_unit='physical_plot'))
    table(results/'predictions.csv',predictions,('domain','scope','method','biological_plot_id','treatment',
        'held_out_date','predicted_Shannon','last_prior_date','prior_to_test_gap_days','n_training_visits',
        'training_dates','training_digest','prediction_digest'))
    prediction_file_hash=sha(results/'predictions.csv')
    save(results/'prediction_freeze.json',dict(status='FROZEN_BEFORE_SCORING',
        execution=execution,predictions_csv_sha256=prediction_file_hash,
        training_response_partition='PRE_2025_ONLY',future_availability_used_for_training=False,
        scope_prediction_digests={d+'__'+s:v[1] for (d,s),v in frozen.items()}))
    errors=[];performance=[];errors_by_method={}
    for domain in p['domains']:
        for scope in SCOPES:
            preds,digest=frozen[(domain,scope)]
            # Test Shannon enters scoring only after original predictions are serialized.
            observed={r['plot']:dict(date=r['date'],Shannon=r['Shannon'],sample_key=r['sample_key'])
                      for r in rows if r['domain']==domain and r['date']==p['held_out_date'] and eligible(r,scope)}
            evaluated=score(preds,observed)
            require(stable_digest(preds)==digest,'Fixed predictions changed while scoring.')
            for row in evaluated:
                row.update(domain=domain,scope=scope,prediction_digest=digest)
            errors.extend(evaluated)
            for method in METHODS:
                selected=[r for r in evaluated if r['method']==method]
                errors_by_method[(domain,scope,method)]=selected
                status=fits[(domain,scope)]['method_status'][method]
                if selected:
                    record=dict(domain=domain,scope=scope,method=method,status='DESCRIPTIVE_POINT_DIAGNOSTIC',
                                reason='One held-out event; no predictive skill or uncertainty distribution established',
                                **metrics(selected))
                else:
                    record=dict(domain=domain,scope=scope,method=method,
                        status=status if status!='ESTIMATED_TRAINING_ONLY' else NO_SUPPORT,
                        n_evaluated_plots=0,evaluation_plot_ids='',MAE='',RMSE='',
                        reason='No fallback, interval, or cross-system/domain substitution')
                performance.append(record)
    require(sha(results/'predictions.csv')==prediction_file_hash,'Serialized predictions changed while scoring.')
    paired_rows=[];paired_metrics=[]
    def compare(meta,a,b):
        if not a or not b:
            summary=dict(status='NOT_COMPARABLE',n_common_plots=0,common_plot_ids='',
                         reason='A method/scope has no estimated evaluated predictions')
            pair=[]
        else:
            pair,summary=compare_fixed_errors(a,b)
            summary['reason']='Fixed original fits re-scored on identical common plots; differences are descriptive'
        paired_rows.extend(dict(meta,**row) for row in pair)
        paired_metrics.append(dict(meta,**summary))
    for domain in p['domains']:
        for scope in SCOPES:
            for i,method_A in enumerate(METHODS):
                for method_B in METHODS[i+1:]:
                    compare(dict(comparison_type='WITHIN_SCOPE_METHODS',domain=domain,scope=scope,
                        scope_A=scope,scope_B=scope,method_A=method_A,method_B=method_B),
                        errors_by_method[(domain,scope,method_A)],errors_by_method[(domain,scope,method_B)])
        for method in METHODS:
            compare(dict(comparison_type='MATCHED_TEST_COHORT_DIFFERENT_TRAINING_SCOPES',domain=domain,
                scope='',scope_A='PRIMARY',scope_B='DEPTH_SENSITIVITY',method_A=method,method_B=method),
                errors_by_method[(domain,'PRIMARY',method)],errors_by_method[(domain,'DEPTH_SENSITIVITY',method)])
    table(results/'cohort_qc.csv',qc,('domain','scope','sample_key','analysis_sample','biological_plot_id',
        'treatment','collection_date','partition','eligible_in_scope','disposition','reason'))
    table(results/'exclusions.csv',[r for r in qc if r['disposition'] not in ('TRAINING_ELIGIBLE','TEST_EVALUATED')],
        ('domain','scope','sample_key','analysis_sample','biological_plot_id','collection_date','disposition','reason'))
    table(results/'cohort_support.csv',cohorts,('domain','scope','n_training_rows','n_training_plots',
        'n_known_eligible_test_plots','training_plot_ids','known_test_plot_ids'))
    table(results/'plot_errors.csv',errors,('domain','scope','method','biological_plot_id','treatment',
        'held_out_date','observed_Shannon','predicted_Shannon','signed_error','absolute_error','squared_error'))
    table(results/'performance.csv',performance,('domain','scope','method','status','n_evaluated_plots',
        'evaluation_plot_ids','MAE','RMSE','reason'))
    pair_fields=('comparison_type','domain','scope','method_A','method_B','scope_A','scope_B')
    table(results/'paired_errors.csv',paired_rows,pair_fields+('biological_plot_id','held_out_date',
        'observed_Shannon','prediction_A','prediction_B','absolute_error_A','absolute_error_B','delta_abs_error',
        'origin_A','origin_B'))
    table(results/'paired_performance.csv',paired_metrics,pair_fields+('status','n_common_plots',
        'common_plot_ids','MAE_A','MAE_B','RMSE_A','RMSE_B','mean_delta_abs_error','delta_RMSE','reason'))
    return cohorts,prediction_file_hash


def main():
    run=Path(sys.argv[1]).resolve(strict=True)
    config=load(run/'CONFIG.json');p=protocol_gate(config)
    record=Path(p['source_definition_record'])
    require(not record.is_absolute() and '..' not in record.parts,'Unsafe source-definition record path.')
    definition=(run/record).resolve(strict=True)
    require(definition.is_relative_to(run) and 'SOURCE_DEFINITION_GATE_PASSED' in definition.read_text(),
            'Frozen response definition gate record missing.')
    code_files=('pilot.py','baseline_core.py','self_checks.py','CONFIG.json','PROTOCOL.txt',str(record))
    code_hashes={name:sha(run/name) for name in code_files}
    results=run/'results';results.mkdir(exist_ok=False)
    checks=load(run/'audit/self_checks.json')
    require(checks.get('status')=='PASS' and checks.get('scientific_fixtures_executed_on_puma') is True
            and str(checks.get('execution',{}).get('job_id'))==str(execution['job_id'])
            and checks.get('execution',{}).get('cluster')=='puma','Current Puma self-check receipt absent/inconsistent.')
    snapshot,hashes,provenance=snapshot_sources(run,config)
    table(results/'source_hashes.csv',hashes,('source','relative_path','sha256','source_job_id','source_path','snapshot_path'))
    table(results/'source_provenance.csv',provenance,('source','analysis_job_id','scheduler_state','pipeline_exit_code','gate'))
    rows=metadata_gate(snapshot)
    expected_primary_gate(snapshot,rows)
    counts=count_response_audit(snapshot,rows,p['Shannon_count_audit_abs_tolerance'],p['Shannon_count_audit_rel_tolerance'])
    table(results/'response_count_verification.csv',counts,('domain','analysis_sample','sample_key',
        'biological_plot_id','collection_date','recorded_Shannon','verification_Shannon_nats','absolute_difference',
        'status','source_metric_overwritten'))
    cohorts,prediction_hash=calculations(rows,p,results)
    integrity=reverify_sources(hashes)
    table(results/'source_integrity_after_pilot.csv',integrity,('source','relative_path','expected_sha256',
        'original_sha256_after_pilot','snapshot_sha256_after_pilot','verified_unchanged'))
    save(results/'source_preservation.json',dict(source_integrity_passed=True,
        source_files_verified_unchanged_after_audit=True,source_files_changed=False,
        verified_after_scoring=True,execution=execution))
    engine_checks=[dict(check=name,status='PASS',detail=detail) for name,detail in (
        ('source_provenance_and_locks','Successful original Puma jobs, exact locks, unchanged originals and snapshots'),
        ('source_response_definition','Pinned executed natural-log target-count Shannon generator; source metric retained'),
        ('metadata_projection','Corrected/functional identity, numeric alpha/depth and booleans agree; v2 all-recorded audit agrees'),
        ('raw_count_response_verification','Unique sample/ASV IDs; finite nonnegative integer counts; scoped reads/richness/H verified'),
        ('primary_fixed_design','Twelve known plots per domain, three per four arms; 2023/actual-2024/2025 visits preserved'),
        ('training_holdout_partition','All fits use pre-2025 exact allowed dates; future availability does not select training'),
        ('fixed_prediction_scoring','Original training fits/predictions serialized before scoring; prediction file remained unchanged'),
        ('paired_common_cohort','Same observed plot/date pairs; original fits re-scored without refitting'))]
    table(results/'integrity_checks.csv',engine_checks,('check','status','detail'))
    require(all(sha(run/name)==expected for name,expected in code_hashes.items()),
            'Engine code, config, protocol or source-definition record changed during pilot.')
    save(results/'code_protocol_hashes.json',code_hashes)
    summary=('LIMITED KNOWN-PLOT RETROSPECTIVE SHANNON POINT DIAGNOSTIC\n'
        'Whipstone Annual, bacteria and archaea separately. Natural-log observed unrarefied target-library Shannon (nats).\n'
        'All 2025 outcomes held out of training. Baselines use only exact pre-2025 eligible source histories; all training plots contribute.\n'
        'Persistence, equal-plot training mean, and conditionally balanced equal-plot arm mean; no trend, optimizer or regression design matrix.\n'
        'One held-out event; errors and paired differences are descriptive. No predictive validation, intervals, p-values, causal effect or ten-year forecast.\n'
        'Primary/depth paired comparisons retain original distinct training fits; differences are not pure depth-adjustment effects.\n'
        'This administrative retrospective cutoff does not establish historical assay availability or untouched/blinded outcomes.\n'
        'Source values and merged physical library identities remain unchanged. Review cohort QC, skips, older persistence origins and common-plot tables.\n')
    (results/'RUN_SUMMARY.txt').write_text(summary,encoding='utf-8')
    manifest=[dict(relative_path=str(path.relative_to(run)),sha256=sha(path),size_bytes=path.stat().st_size)
              for path in sorted(results.iterdir()) if path.is_file()]
    table(results/'output_manifest.csv',manifest,('relative_path','sha256','size_bytes'))
    save(results/'PILOT_STATUS.json',dict(status='PILOT_POINT_DIAGNOSTICS_COMPLETED_REVIEW_PENDING',
        execution=execution,completed_utc=datetime.now(timezone.utc).isoformat(),
        scientific_scope='LIMITED_KNOWN_PLOT_RETROSPECTIVE_DIAGNOSTIC_HINDCAST',
        source_integrity_passed=True,source_files_verified_unchanged_after_audit=True,source_files_changed=False,
        baseline_predictions_computed=True,baseline_models_fitted=True,complex_models_fitted=False,
        predictive_validation_established=False,scientific_review_pending=True,ten_year_forecast_generated=False,
        numerical_ten_year_forecast=False,code_protocol_verified_unchanged=True,
        near_term_validation_status='VALIDATION_NOT_ESTABLISHED',
        ten_year_forecast_status='NOT_SUPPORTED_BY_THIS_PILOT',
        source_jobs={a:str(c['analysis_job_id']) for a,c in config['source_runs'].items()},
        output_manifest='results/output_manifest.csv',output_manifest_sha256=sha(results/'output_manifest.csv'),
        predictions_csv_sha256=prediction_hash,prediction_freeze_sha256=sha(results/'prediction_freeze.json'),
        cohort_support=cohorts,
        response='Observed unrarefied target-library natural-log Shannon',response_unit='nats',
        intervals_generated=False,p_values_computed=False,winner_selected=False,
        limits=['Single held-out event does not establish general temporal predictive skill',
                'Known-plot future-event diagnostic is not new-plot/new-farm validation',
                'Realized source sequencing-depth eligibility conditions the observed assay response',
                'Actual July/August 2024 collections are subsets of one annual round',
                'No external calibration, ecological causal claim, or numerical ten-year projection']))
    # No successful sentinel here: rendering/orchestration must finish first.


if __name__=='__main__':
    main()
