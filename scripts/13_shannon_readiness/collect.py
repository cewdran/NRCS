#!/usr/bin/env python3
"""Guarded standard-library collection after any main exit; no scientific work."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
import csv
import datetime
import json
import os
import subprocess
import sys
import time
import zipfile
from common import require, save, sha, readcsv, table

run, main = Path(sys.argv[1]).resolve(strict=True), sys.argv[2]
receipt = json.loads((run/'submission.json').read_text())
require(main.isdigit() and receipt.get('jobs',{}).get('analysis')==main,
        'Collector main job disagrees with submission receipt.')
require(receipt.get('jobs',{}).get('collector')==execution['job_id'],
        'Collector execution disagrees with submission receipt.')
state, code, node_list = 'UNKNOWN', '', ''
stdout, stderr = '', ''
for attempt in range(4):
    try:
        proc=subprocess.run(['sacct','-j',main,'--starttime='+receipt['accounting_since'],
            '--parsable2','--format=JobIDRaw,State,ExitCode,NodeList'],
            capture_output=True,text=True,timeout=30)
        stdout,stderr=proc.stdout,proc.stderr
        for line in stdout.splitlines()[1:]:
            parts=line.split('|')
            if len(parts)>=4 and parts[0]==main:state,code,node_list=parts[1:4]
    except (OSError,subprocess.TimeoutExpired) as exc:stderr=str(exc)
    if state not in ('UNKNOWN','PENDING','RUNNING','COMPLETING'):break
    if attempt<3:time.sleep(5)
(run/'audit/sacct_analysis.tsv').write_text(stdout)
(run/'audit/sacct_stderr.txt').write_text(stderr)

errors=[]; records={}
for name in ['audit/run_start.json','audit/run_end.json','audit/software/environment.json',
        'audit/ENGINE_SOURCE_STATUS.json','audit/REPAIR_SOURCE_STATUS.json',
        'audit/self_checks.json','audit/fold_self_checks.json',
        'results/BACKTEST_STATUS.json','results/FORECAST_READINESS.json','results/REPORT_STATUS.json']:
    try:
        value=json.loads((run/name).read_text())
        require(isinstance(value,dict),'Status is not an object: '+name)
        records[name]=value
    except (ValueError,OSError,RuntimeError) as exc:errors.append(name+': '+str(exc))

def safe_file(base,name):
    rel=Path(name)
    require(not rel.is_absolute() and '..' not in rel.parts,'Unsafe evidence path: '+name)
    path=(base/rel).resolve(strict=True)
    require(path.is_relative_to(base) and path.is_file(),'Evidence escaped its run: '+name)
    return path

def verify_manifest(path,base):
    rows=readcsv(path);require(rows,'Empty output manifest: '+str(path));seen=set()
    for row in rows:
        name=row['relative_path'];require(name not in seen,'Duplicate manifest entry: '+name);seen.add(name)
        target=safe_file(base,name)
        require(sha(target)==row['sha256'] and target.stat().st_size==int(row['size_bytes']),
                'Output hash/size changed: '+name)
    return seen

pipeline=False;cfg=json.loads((run/'CONFIG.json').read_text())
end=records.get('audit/run_end.json',{});report=records.get('results/REPORT_STATUS.json',{})
if not errors:
    try:
        node=end.get('execution',{}).get('node')
        for name,record in records.items():
            origin=record.get('execution',{})
            require(origin.get('job_id')==main and origin.get('cluster')=='puma'
                    and origin.get('node')==node and bool(node),'Current execution provenance mismatch: '+name)
        start=records['audit/run_start.json'];env=records['audit/software/environment.json']
        require(start.get('stage')=='start' and end.get('stage')=='end' and end.get('pipeline_exit_code')==0,
                'Main pipeline did not complete.')
        require(start.get('interpreter')==end.get('interpreter')==env.get('executable')==receipt['environment_python']
                and start.get('runtime_interpreter_fallback') is False and end.get('runtime_interpreter_fallback') is False,
                'Scientific stages did not use the pinned runtime.')
        require(env.get('status')=='PINNED_CORRECTED_ENVIRONMENT_VERIFIED'
                and env.get('versions')==cfg['runtime_expected_versions']
                and env.get('requirements_sha256')==cfg['runtime_requirements_sha256']
                and env.get('software_installed') is False,'Pinned environment verification failed.')
        for path in ['audit/self_checks.json','audit/fold_self_checks.json']:
            check=records[path]
            require(check.get('status')=='PASS' and bool(check.get('checks'))
                    and check.get('scientific_fixtures_executed_on_puma') is True
                    and all(r.get('status')=='PASS' for r in check['checks']),
                    'Puma fixture receipt did not pass: '+path)
        stage=records['audit/ENGINE_SOURCE_STATUS.json']
        require(stage.get('status')=='IMMUTABLE_ENGINE_SOURCE_VERIFIED_AND_STAGED'
                and stage.get('source_integrity_passed') is True and stage.get('source_files_changed') is False,
                'Original engine staging failed.')
        repair=records['audit/REPAIR_SOURCE_STATUS.json']
        require(repair.get('status')=='SUCCESSFUL_REPORT_REPAIR_SOURCE_VERIFIED_AND_STAGED'
                and repair.get('source_integrity_passed') is True and repair.get('source_files_changed') is False
                and stage.get('repair_source_status_sha256')==sha(run/'audit/REPAIR_SOURCE_STATUS.json')
                and stage.get('successful_report_source_verified') is True,
                'Successful report-repair staging or linkage failed.')
        require(stage.get('engine_origin_execution')=={'job_id':'24124251','cluster':'puma','node':'r7u03n1'}
                and repair.get('repair_origin_execution')=={'job_id':'24126557','cluster':'puma','node':'r7u03n1'}
                and repair.get('original_engine_origin_execution')==stage.get('engine_origin_execution')
                and repair.get('original_engine_scheduler_state')=='FAILED'
                and repair.get('original_engine_scheduler_exit_code')=='1:0'
                and repair.get('repair_source_scheduler_state')=='COMPLETED'
                and repair.get('repair_source_scheduler_exit_code')=='0:0',
                'Fixed original failed-engine/successful-repair origins differ.')
        for manifest,source_key,dirname in [('audit/engine_source_manifest.csv','engine_source','engine_source'),
                                           ('audit/repair_source_manifest.csv','repair_source','repair_source')]:
            staging=stage if source_key=='engine_source' else repair
            require(staging.get(source_key+'_manifest_path')==manifest
                    and staging.get(source_key+'_manifest_sha256')==sha(run/manifest),
                    'Staging manifest receipt differs: '+manifest)
            original_root=Path(cfg[source_key]['path']).resolve(strict=True)
            snapshot_root=(run/dirname).resolve(strict=True)
            rows=readcsv(run/manifest);require(rows,'Missing staging records.');seen=set()
            for row in rows:
                name=row['relative_path'];require(name not in seen,'Duplicate staged path.');seen.add(name)
                require(sha(safe_file(original_root,name))==sha(safe_file(snapshot_root,name))==row['sha256'],
                        'Original or staged file changed after scientific/report stages: '+name)
                original_file=safe_file(original_root,name);snapshot_file=safe_file(snapshot_root,name)
                require(original_file.stat().st_size==snapshot_file.stat().st_size==int(row['size_bytes'])
                        and Path(row['original_path']).resolve(strict=True)==original_file
                        and Path(row['snapshot_path']).resolve(strict=True)==snapshot_file,
                        'Staging size/path identity changed: '+name)
            require(len(seen)==staging.get('files_staged'),'Staging file set count changed: '+manifest)
            if source_key=='repair_source':
                expected=set(cfg[source_key]['files'])|{'results/NRCS_Shannon_hindcast_review.pdf',
                    'audit/ENGINE_SOURCE_STATUS.json','audit/engine_source_manifest.csv','ANALYSES_COMPLETE.txt'}
                expected|={'figures/'+domain+'_'+suffix+'.pdf' for domain in ('bacteria','archaea')
                    for suffix in ('observed_vs_baselines','primary_absolute_errors','depth_sensitivity_absolute_errors')}
                require(seen==expected,'Exact successful-repair staging set differs.')
                for name,expected in cfg[source_key]['files'].items():
                    require(sha(safe_file(original_root,name))==sha(safe_file(snapshot_root,name))==expected,
                            'Reviewed successful repair pin changed: '+name)
            else:
                expected={line.split('  ',1)[1] for line in (original_root/'PACKAGE_SHA256SUMS.txt').read_text().splitlines()}
                expected|={'PACKAGE_SHA256SUMS.txt','OUTPUT_SHA256SUMS.txt','HPC_STATUS.json','submission.json','logs/analysis.log'}
                for directory in ('results','audit','source_snapshot'):
                    expected.update(str(p.relative_to(original_root)) for p in (original_root/directory).rglob('*')
                                    if p.is_file() and '__pycache__' not in p.parts)
                require(seen==expected and len(seen)==124,'Exact original engine staging set differs.')
        for alias,pins in cfg['sources'].items():
            original=Path(cfg['source_runs'][alias]['path']).resolve(strict=True)
            snapshot=(run/'engine_source/source_snapshot'/alias).resolve(strict=True)
            for name,expected in pins.items():
                require(sha(safe_file(original,name))==sha(safe_file(snapshot,name))==expected,
                        'Upstream original/snapshot changed: '+alias+'/'+name)
        for name,expected in cfg['review_locks'].items():
            require(sha(safe_file(run,name))==expected,'Accepted review lock changed: '+name)
        backtest=records['results/BACKTEST_STATUS.json'];forecast=records['results/FORECAST_READINESS.json']
        require(backtest.get('status')=='TEMPORAL_BACKTEST_COMPLETED_REVIEW_PENDING'
                and backtest.get('new2024_baseline_fits_computed') is True
                and backtest.get('reused2025_fits_recomputed') is False
                and backtest.get('source_files_changed') is False
                and backtest.get('source_preservation_passed') is True,
                'New2024/original2025 calculation gates did not pass.')
        require(forecast.get('status')=='NUMERICAL_TEN_YEAR_FORECAST_WITHHELD_NOT_SUPPORTED_CURRENT_DATA'
                and forecast.get('user_authorizes_work') is True and forecast.get('current_files_only') is True
                and forecast.get('readiness_audit_completed') is True
                and forecast.get('forecast_requirements_sha256')==sha(safe_file(run,forecast['forecast_requirements_path']))
                and backtest.get('forecast_readiness_sha256')==sha(run/'results/FORECAST_READINESS.json'),
                'Forecast readiness status/intent differs from the fixed current-data scope.')
        for record in (backtest,forecast,report):
            require(record.get('predictive_validation_established') is False
                    and record.get('ten_year_forecast_generated') is False,'Unearned forecast/validation claim.')
        require(report.get('status')=='TEMPORAL_BACKTEST_REPORT_COMPLETED_REVIEW_PENDING'
                and report.get('source_preservation_passed') is True
                and report.get('source_files_changed') is False and report.get('figure_count')==6,
                'Report provenance/source/figure gates did not pass.')
        require(report.get('backtest_status_sha256')==sha(run/'results/BACKTEST_STATUS.json')
                and report.get('forecast_readiness_sha256')==sha(run/'results/FORECAST_READINESS.json'),
                'Report does not pin its scientific/readiness statuses.')
        expected_backtest={'results/new_2024/'+name for name in (
            'training_fits.json','training_membership.csv','training_plot_means.csv','baseline_support.csv',
            'predictions.csv','prediction_freeze.json','plot_errors.csv','performance.csv','paired_errors.csv',
            'paired_performance.csv','cohort_qc.csv','cohort_support.csv')}|{'results/'+name for name in (
            'response_count_verification.csv','RAW_COUNT_AUDIT.json','plot_errors.csv','performance.csv','paired_errors.csv',
            'paired_performance.csv','cohort_support.csv','forecast_requirements.csv','FORECAST_READINESS.json',
            'source_integrity_after_backtest.csv','REUSED_2025_OUTPUTS.json')}
        for record in [backtest,report]:
            name=record['output_manifest_path']
            require(sha(safe_file(run,name))==record['output_manifest_sha256'],'Manifest receipt changed.')
            outputs=verify_manifest(safe_file(run,name),run)
            if record is backtest:
                require(outputs==expected_backtest,'Exact backtest output set differs.')
            else:
                figure_paths={'figures/'+domain+'_2024_'+suffix+'.pdf' for domain in ('bacteria','archaea')
                    for suffix in ('observed_vs_baselines','primary_absolute_errors','depth_sensitivity_absolute_errors')}
                require(report['report_relative_path']=='results/NRCS_validation_forecast_readiness_review.pdf'
                        and outputs==figure_paths|{report['report_relative_path']},
                        'Exact six-figure and one-PDF report output set differs.')
        require(report.get('report_sha256')==sha(safe_file(run,report['report_relative_path'])),
                'Combined report hash changed.')
        lines=(run/'ANALYSES_COMPLETE.txt').read_text().splitlines()
        require(bool(lines) and ('on Puma job '+main+';') in lines[0]
                and ('REPORT_STATUS_SHA256='+sha(run/'results/REPORT_STATUS.json')) in lines,
                'Final report sentinel job/hash mismatch.')
        paths=set()
        for line in (run/'PACKAGE_SHA256SUMS.txt').read_text().splitlines():
            digest,name=line.split('  ',1)
            require(name not in paths and len(digest)==64 and all(c in '0123456789abcdef' for c in digest),
                    'Malformed package manifest.');paths.add(name)
            require(sha(safe_file(run,name))==digest,'Package changed during execution: '+name)
        require(paths,'Empty package manifest.')
        require(node_list and node_list not in ('None','(null)'), 'Main allocated node absent from accounting.')
        proc=subprocess.run(['scontrol','show','hostnames',node_list],capture_output=True,text=True,timeout=30)
        require(proc.returncode==0 and node in proc.stdout.splitlines(),'Main accounting node differs from execution.')
        pipeline=True
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.TimeoutExpired) as exc:errors.append(str(exc))

label=('HPC_TEMPORAL_BACKTEST_READINESS_COMPLETED_REVIEW_PENDING' if pipeline and state=='COMPLETED' and code=='0:0'
       else 'BACKTEST_READINESS_COMPLETED_SCHEDULER_CONFIRMATION_PENDING' if pipeline and state in ('UNKNOWN','RUNNING','COMPLETING')
       else 'HPC_BACKTEST_READINESS_FAILED_OR_INCOMPLETE')
status={'status':label,'analysis_job_id':main,'collector_execution':execution,
    'collector_interpreter':sys.executable,'collector_standard_library_only':True,
    'collector_interpreter_fallback':bool(os.environ.get('NRCS_RUNTIME_FALLBACK_REASON')),
    'collector_interpreter_fallback_reason':os.environ.get('NRCS_RUNTIME_FALLBACK_REASON') or None,
    'scheduler_state':state,'scheduler_exit_code':code,'main_accounting_node_list':node_list,
    'pipeline_exit_code':end.get('pipeline_exit_code'),'scientific_and_report_stages_completed':pipeline,
    'new2024_backtest_computed':True if pipeline else None,'reused2025_fits_recomputed':False,
    'original_engine_job_id':'24124251','original_engine_whole_job_state':'FAILED',
    'report_repair_source_job_id':'24126557','user_authorizes_validation_and_forecast_work':True,
    'predictive_validation_established':False,'ten_year_forecast_generated':False,
    'ten_year_forecast_support':'NOT_SUPPORTED_CURRENT_DATA','source_files_changed':False if pipeline else None,
    'package_integrity_passed':True if pipeline else None,'scientific_review_pending':True,'visual_review_pending':True,
    'completion_validation_errors':errors,'figure_count':report.get('figure_count') if pipeline else None,
    'collected_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'review':'Review annual-round errors, explicit depth support, reused2025 evidence and the ten-year requirements register. Audit completion is not forecast validation.'}
save(run/'HPC_STATUS.json',status)
(run/'AUDIT_README.txt').write_text('''TEMPORAL BACKTEST AND TEN-YEAR READINESS ATTEMPT
Consult HPC_STATUS and main accounting before treating stages as complete.
New2024 calculations are distinct from byte-reused original2025 evidence.
Original24124251 overall failure and successful report repair24126557 stay preserved.
The audit can complete while numerical forecasts remain unsupported/withheld.
Scientific and visual review remains pending. Collector is captured while running;
final collector accounting is not included. Its active collector.log is omitted.
Large source snapshots, if any, are listed in REMOTE_ONLY_FILES.csv with HPC paths.
''')
excluded={run/'logs/collector.log',run/'OUTPUT_SHA256SUMS.txt',run/'REMOTE_ONLY_FILES.csv'}
included=[];remote=[]
for path in sorted(run.rglob('*')):
    if not path.is_file() or '__pycache__' in path.parts or path in excluded:continue
    relative=path.relative_to(run);digest=sha(path)
    if relative.parts[:2]==('engine_source','source_snapshot') and path.stat().st_size>8*1024*1024:
        remote.append({'relative_path':str(relative),'absolute_HPC_path':str(path),'bytes':path.stat().st_size,
                       'sha256':digest,'reason':'Large source retained unchanged on Puma; compact archive omits bytes'})
    else:included.append((path,digest))
table(run/'REMOTE_ONLY_FILES.csv',remote,['relative_path','absolute_HPC_path','bytes','sha256','reason'])
included.append((run/'REMOTE_ONLY_FILES.csv',sha(run/'REMOTE_ONLY_FILES.csv')))
(run/'OUTPUT_SHA256SUMS.txt').write_text(''.join(d+'  '+str(p.relative_to(run))+'\n' for p,d in included))
archive=Path(receipt['download_directory'])/(cfg['package']+'_'+main+'_'+run.name+'.zip')
temporary=archive.with_suffix('.zip.partial')
require(not archive.exists() and not temporary.exists(),'Archive already exists; preserve original evidence.')
with zipfile.ZipFile(temporary,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in [p for p,_ in included]+[run/'OUTPUT_SHA256SUMS.txt']:z.write(p,p.relative_to(run.parent))
with zipfile.ZipFile(temporary) as z:require(z.testzip() is None,'Archive CRC failed.')
temporary.rename(archive)
with archive.with_suffix('.zip.sha256.txt').open('x') as f:f.write(sha(archive)+'  '+archive.name+'\n')
(run.parent/(run.name+'_DOWNLOAD.txt')).write_text(str(archive)+'\n')
print(json.dumps(status,indent=2));print('DOWNLOAD_RESULTS='+str(archive))
