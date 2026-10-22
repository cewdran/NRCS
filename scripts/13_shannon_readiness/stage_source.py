#!/usr/bin/env python3
"""Stage immutable original engine and successful repair evidence; no scientific computation."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
import csv
import datetime
import json
import shutil
import subprocess
import sys
from common import require, save, sha, table


def safe_file(base, name):
    relative = Path(name)
    require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe source path: ' + name)
    path = (base / relative).resolve(strict=True)
    require(path.is_relative_to(base) and path.is_file(), 'Source file outside its run: ' + name)
    return path


def load(path):
    result = json.loads(path.read_text())
    require(isinstance(result, dict), 'Expected JSON object: ' + str(path))
    return result


def hashes(path, base):
    result = {}
    for line in path.read_text().splitlines():
        expected, relative = line.split('  ', 1)
        require(relative not in result and len(expected) == 64
                and all(c in '0123456789abcdef' for c in expected), 'Invalid checksum entry: ' + relative)
        require(sha(safe_file(base, relative)) == expected, 'Source checksum mismatch: ' + relative)
        result[relative] = expected
    require(result, 'Empty source checksum manifest: ' + str(path))
    return result


def same_origin(record, origin):
    actual = record.get('execution', {})
    require(isinstance(actual, dict) and all(str(actual.get(k)) == str(origin[k])
            for k in ('job_id', 'cluster', 'node')), 'Source execution provenance mismatch.')


def stage_repair(run, cfg, engine_original, engine_snapshot, engine_hashes, engine_origin, pilot):
    """Verify existing repair receipts and byte lineage; never calculate scientific values."""
    source = cfg['repair_source']
    expected_path = '/xdisk/dinusha1/dinusha1/NRCS_Shannon_report_repair_HPC_v1/runs/run_20261006T195916Z_3050d2a7'
    require(source.get('analysis_job_id') == '24126557' and source.get('cluster') == 'puma'
            and source.get('node') == 'r7u03n1' and source.get('path') == expected_path,
            'Unexpected fixed successful repair source.')
    origin = {'job_id': source['analysis_job_id'], 'cluster': source['cluster'], 'node': source['node']}
    original = Path(source['path']).resolve(strict=True)
    require(original not in (run, engine_original) and not run.is_relative_to(original)
            and not original.is_relative_to(run), 'Overlapping successful repair source.')
    target = run / 'repair_source'
    require(not target.exists(), 'Repair source snapshot already exists; use a new immutable run.')
    required = {'HPC_STATUS.json', 'audit/run_end.json', 'results/REPORT_STATUS.json',
                'results/report_output_manifest.csv', 'results/NRCS_Shannon_hindcast_review.pdf',
                'OUTPUT_SHA256SUMS.txt'}
    pins = source['files']
    require(isinstance(pins, dict) and set(pins) == required, 'Repair source must have the exact six reviewed byte pins.')
    for name, expected in pins.items():
        require(isinstance(expected, str) and len(expected) == 64
                and all(c in '0123456789abcdef' for c in expected)
                and sha(safe_file(original, name)) == expected, 'Reviewed repair source byte pin differs: ' + name)
    output_hashes = hashes(safe_file(original, 'OUTPUT_SHA256SUMS.txt'), original)
    require(all(output_hashes.get(name) == expected for name, expected in pins.items()
                if name != 'OUTPUT_SHA256SUMS.txt'), 'Repair output checksum manifest omits fixed source evidence.')
    hpc = load(safe_file(original, 'HPC_STATUS.json'))
    report = load(safe_file(original, 'results/REPORT_STATUS.json'))
    end = load(safe_file(original, 'audit/run_end.json'))
    same_origin(report, origin)
    same_origin(end, origin)
    require(hpc.get('analysis_job_id') == origin['job_id']
            and hpc.get('status') == 'HPC_SHANNON_REPORT_REPAIR_COMPLETED_VISUAL_REVIEW_PENDING'
            and hpc.get('scheduler_state') == 'COMPLETED' and hpc.get('scheduler_exit_code') == '0:0'
            and hpc.get('pipeline_exit_code') == 0 and hpc.get('report_repair_pipeline_completed') is True
            and hpc.get('package_integrity_passed') is True and hpc.get('engine_source_integrity_passed') is True
            and hpc.get('source_files_changed') is False
            and hpc.get('scientific_engine_reexecuted') is False
            and hpc.get('scientific_fixtures_reexecuted') is False
            and hpc.get('baseline_fits_recomputed') is False
            and hpc.get('baseline_predictions_recomputed') is False
            and hpc.get('baseline_predictions_reused_from_source') is True,
            'Successful repair main-job completion/preservation gates failed.')
    require(end.get('stage') == 'end' and end.get('pipeline_exit_code') == 0
            and end.get('runtime_interpreter_fallback') is False
            and end.get('interpreter') == str(Path(cfg['runtime_package']) / 'runtime_env/bin/python'),
            'Successful repair runtime completion/interpreter gate failed.')
    require(report.get('status') == 'REPORT_REPAIR_COMPLETED_VISUAL_REVIEW_PENDING'
            and report.get('engine_origin_execution') == engine_origin
            and report.get('engine_source_job_id') == engine_origin['job_id']
            and report.get('engine_source_path') == cfg['engine_source']['path']
            and report.get('engine_source_scheduler_state') == 'FAILED'
            and report.get('engine_source_scheduler_exit_code') == '1:0'
            and report.get('engine_source_pipeline_exit_code') == 1
            and report.get('pilot_status') == pilot['status']
            and report.get('baseline_predictions_reused_from_source') is True
            and report.get('baseline_fits_recomputed') is False
            and report.get('scientific_fixtures_reexecuted') is False
            and report.get('scientific_values_recomputed') is False
            and report.get('original_engine_files_verified_unchanged_after_report') is True
            and report.get('source_integrity_passed') is True
            and report.get('source_files_verified_unchanged_after_report') is True
            and report.get('source_files_changed') is False
            and report.get('engine_source_files_verified') == 124
            and report.get('engine_output_files_verified') == 21
            and report.get('source_files_verified') == 30
            and report.get('report_layout_checks_passed') is True,
            'Successful repair report does not preserve the exact original engine/source proof.')
    for record in (hpc, report, end):
        require(record.get('ten_year_forecast_generated') is False
                and record.get('predictive_validation_established') is False,
                'Historical repair scope flags were changed.')
    require(report.get('report_path') == 'results/NRCS_Shannon_hindcast_review.pdf'
            and report.get('report_sha256') == pins['results/NRCS_Shannon_hindcast_review.pdf']
            and report.get('report_output_manifest_path') == 'results/report_output_manifest.csv'
            and report.get('report_output_manifest_sha256') == pins['results/report_output_manifest.csv']
            and report.get('engine_output_manifest_path') == 'engine_source/results/output_manifest.csv'
            and report.get('engine_output_manifest_sha256') == pilot['output_manifest_sha256']
            and sha(safe_file(original, 'engine_source/results/output_manifest.csv')) == pilot['output_manifest_sha256'],
            'Successful report/manifest/original calculation linkage mismatch.')
    with safe_file(original, 'results/report_output_manifest.csv').open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == ['relative_path', 'sha256', 'size_bytes', 'file_type', 'description'],
                'Successful repair report manifest schema mismatch.')
        report_rows = list(reader)
    expected_report_files = {'results/NRCS_Shannon_hindcast_review.pdf'} | {
        'figures/' + domain + '_' + suffix + '.pdf'
        for domain in ('bacteria', 'archaea')
        for suffix in ('observed_vs_baselines', 'primary_absolute_errors', 'depth_sensitivity_absolute_errors')}
    require(len(report_rows) == 7 and {r.get('relative_path') for r in report_rows} == expected_report_files
            and report.get('report_output_files_verified') == 7 and report.get('figure_count') == 6,
            'Successful repair must contain the exact six figures and one PDF.')
    for row in report_rows:
        name = row['relative_path']
        path = safe_file(original, name)
        require(None not in row and sha(path) == row['sha256'] == output_hashes.get(name)
                and path.stat().st_size == int(row['size_bytes']), 'Repair report output changed: ' + name)
    stage = load(safe_file(original, 'audit/ENGINE_SOURCE_STATUS.json'))
    same_origin(stage, origin)
    require(stage.get('status') == 'IMMUTABLE_ENGINE_SOURCE_VERIFIED_AND_STAGED'
            and stage.get('engine_origin_execution') == engine_origin
            and stage.get('source_files_verified_unchanged_after_staging') is True
            and stage.get('source_files_changed') is False and stage.get('files_staged') == 124,
            'Historical repair engine staging evidence failed.')
    lineage_manifest = safe_file(original, 'audit/engine_source_manifest.csv')
    require(report.get('engine_source_manifest_path') == 'audit/engine_source_manifest.csv'
            and stage.get('engine_source_manifest_path') == 'audit/engine_source_manifest.csv'
            and report.get('engine_source_manifest_sha256') == stage.get('engine_source_manifest_sha256') == sha(lineage_manifest),
            'Successful repair engine-copy manifest linkage failed.')
    with lineage_manifest.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == ['relative_path', 'sha256', 'size_bytes', 'original_path', 'snapshot_path'],
                'Repair engine-copy manifest schema mismatch.')
        lineage = list(reader)
    require(len(lineage) == 124 and {r.get('relative_path') for r in lineage} == set(engine_hashes),
            'Successful repair engine copy differs from the exact 124 original files.')
    proof = []
    for row in lineage:
        name = row['relative_path']
        original_file = safe_file(engine_original, name)
        repair_file = safe_file(original, 'engine_source/' + name)
        current_file = safe_file(engine_snapshot, name)
        require(None not in row and row['sha256'] == engine_hashes[name]
                and Path(row['original_path']).resolve(strict=True) == original_file
                and Path(row['snapshot_path']).resolve(strict=True) == repair_file
                and sha(original_file) == sha(repair_file) == sha(current_file) == row['sha256']
                and original_file.stat().st_size == repair_file.stat().st_size == current_file.stat().st_size == int(row['size_bytes']),
                'Original/repair/current engine lineage changed: ' + name)
        proof.append({'relative_path': name, 'sha256': row['sha256'], 'size_bytes': row['size_bytes'],
                      'original_engine_path': str(original_file), 'successful_repair_engine_path': str(repair_file),
                      'current_engine_snapshot_path': str(current_file), 'verified_identical_bytes': True})
    completion = safe_file(original, 'ANALYSES_COMPLETE.txt').read_text().splitlines()
    require(completion == [
                'Shannon report-only repair completed on Puma job ' + origin['job_id']
                + '; scientific and visual review pending.',
                'REPORT_STATUS_SHA256=' + pins['results/REPORT_STATUS.json']],
            'Successful repair completion sentinel differs from the pinned report status.')
    accounting = subprocess.run(['sacct', '-j', origin['job_id'], '-S', '2026-10-06', '--parsable2',
        '--format=JobIDRaw,State,ExitCode,NodeList'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    require(accounting.returncode == 0, 'Cannot verify successful repair main-job accounting.')
    matches = [line.split('|') for line in accounting.stdout.splitlines()[1:]
               if line.split('|')[0] == origin['job_id']]
    require(len(matches) == 1 and matches[0][1:3] == ['COMPLETED', '0:0'],
            'Successful repair main-job accounting differs or is unavailable.')
    nodes = subprocess.run(['scontrol', 'show', 'hostnames', matches[0][3]],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    require(nodes.returncode == 0 and origin['node'] in nodes.stdout.splitlines(),
            'Successful repair main-job allocated node mismatch.')
    selected = required | expected_report_files | {'audit/ENGINE_SOURCE_STATUS.json',
        'audit/engine_source_manifest.csv', 'ANALYSES_COMPLETE.txt'}
    require(all(name in output_hashes for name in selected if name != 'OUTPUT_SHA256SUMS.txt'),
            'Successful repair collector manifest omits required lineage/completion evidence.')
    before = {name: sha(safe_file(original, name)) for name in sorted(selected)}
    target.mkdir()
    rows = []
    for name, expected in before.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe_file(original, name), destination)
        require(sha(destination) == expected, 'Repair snapshot copy changed: ' + name)
        rows.append({'relative_path': name, 'sha256': expected, 'size_bytes': destination.stat().st_size,
                     'original_path': str(original / name), 'snapshot_path': str(destination)})
    require(all(sha(safe_file(original, name)) == sha(safe_file(target, name)) == expected
                for name, expected in before.items()), 'Successful repair source/snapshot changed during staging.')
    require(all(sha(safe_file(original, 'engine_source/' + name)) == expected
                for name, expected in engine_hashes.items()), 'Successful repair engine copy changed during staging.')
    table(run / 'audit/repair_source_manifest.csv', rows,
          ['relative_path', 'sha256', 'size_bytes', 'original_path', 'snapshot_path'])
    table(run / 'audit/repair_engine_lineage_checks.csv', proof,
          ['relative_path', 'sha256', 'size_bytes', 'original_engine_path', 'successful_repair_engine_path',
           'current_engine_snapshot_path', 'verified_identical_bytes'])
    (run / 'audit/repair_source_sacct.tsv').write_text(accounting.stdout, encoding='utf-8')
    status = {'status': 'SUCCESSFUL_REPORT_REPAIR_SOURCE_VERIFIED_AND_STAGED', 'execution': execution,
        'stage_scope': 'IMMUTABLE_SOURCE_STAGING_ONLY', 'repair_origin_execution': origin,
        'repair_source_job_id': origin['job_id'], 'repair_source_path': source['path'],
        'repair_source_resolved_path': str(original), 'repair_source_scheduler_state': 'COMPLETED',
        'repair_source_scheduler_exit_code': '0:0', 'repair_source_pipeline_exit_code': 0,
        'repair_collector_final_accounting': 'NOT_ASSERTED_OR_REQUIRED',
        'original_engine_origin_execution': engine_origin, 'original_engine_scheduler_state': 'FAILED',
        'original_engine_scheduler_exit_code': '1:0', 'original_engine_pipeline_exit_code': 1,
        'source_integrity_passed': True, 'source_files_verified_unchanged_after_staging': True,
        'source_files_changed': False, 'original_engine_files_verified': 124,
        'engine_output_files_verified': 21, 'upstream_source_files_verified': 30,
        'baseline_fits_recomputed': False, 'scientific_values_recomputed': False,
        'scientific_fixtures_reexecuted': False,
        'repair_source_manifest_path': 'audit/repair_source_manifest.csv',
        'repair_source_manifest_sha256': sha(run / 'audit/repair_source_manifest.csv'),
        'repair_engine_lineage_checks_path': 'audit/repair_engine_lineage_checks.csv',
        'repair_engine_lineage_checks_sha256': sha(run / 'audit/repair_engine_lineage_checks.csv'),
        'original_report_sha256': pins['results/NRCS_Shannon_hindcast_review.pdf'],
        'original_report_status_sha256': pins['results/REPORT_STATUS.json'],
        'original_report_manifest_sha256': pins['results/report_output_manifest.csv'],
        'fixed_repair_byte_pins': pins, 'files_staged': len(rows),
        'staged_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'ten_year_forecast_generated': False, 'predictive_validation_established': False}
    save(run / 'audit/REPAIR_SOURCE_STATUS.json', status)
    return status


def main():
    require(len(sys.argv) == 2, 'Usage: stage_source.py RUN_PATH')
    run = Path(sys.argv[1]).resolve(strict=True)
    cfg = load(run / 'CONFIG.json')
    require(cfg.get('package') == 'NRCS_validation_forecast_readiness_HPC_v1', 'Unexpected validation/readiness package.')
    require(isinstance(cfg.get('backtest_protocol'), dict), 'Missing separate new backtest protocol.')
    source = cfg['engine_source']
    require(source.get('analysis_job_id') == '24124251' and source.get('cluster') == 'puma'
            and source.get('node') == 'r7u03n1' and source.get('pipeline_exit_code') == 1
            and source.get('scheduler_state') == 'FAILED' and source.get('scheduler_exit_code') == '1:0',
            'Unexpected fixed engine source gate.')
    origin = {'job_id': source['analysis_job_id'], 'cluster': source['cluster'], 'node': source['node']}
    original = Path(source['path']).resolve(strict=True)
    require(source['path'] == '/xdisk/dinusha1/dinusha1/NRCS_Shannon_hindcast_HPC_v2/runs/run_20261006T172508Z_ffdd5c3e'
            and original != run and not run.is_relative_to(original), 'Wrong or overlapping original engine run.')
    target = run / 'engine_source'
    require(not target.exists(), 'Engine source snapshot already exists; use a new immutable run.')
    released = safe_file(run, source['expected_package_manifest'])
    require(sha(released) == source['expected_package_manifest_sha256'], 'Reviewed source package manifest changed.')
    package_manifest = safe_file(original, 'PACKAGE_SHA256SUMS.txt')
    require(sha(package_manifest) == sha(released), 'Original run was not staged from the reviewed released v2 package.')
    package_hashes = hashes(package_manifest, original)
    output_manifest = safe_file(original, 'OUTPUT_SHA256SUMS.txt')
    output_hashes = hashes(output_manifest, original)
    source_cfg = load(safe_file(original, 'CONFIG.json'))
    require(source_cfg.get('package') == source['source_package']
            and source_cfg.get('package_revision') == source['source_package_revision'], 'Wrong source engine package/revision.')
    for key in ('runtime_package', 'runtime_expected_versions', 'runtime_requirements_sha256', 'source_runs', 'sources', 'protocol'):
        require(source_cfg.get(key) == cfg.get(key), 'Staging changed original engine configuration: ' + key)
    original_receipt = load(safe_file(original, 'submission.json'))
    require(original_receipt.get('jobs', {}).get('analysis') == origin['job_id']
            and Path(original_receipt.get('run_directory', '')).resolve(strict=True) == original,
            'Original submission receipt mismatch.')
    start = load(safe_file(original, 'audit/run_start.json'))
    end = load(safe_file(original, 'audit/run_end.json'))
    environment = load(safe_file(original, 'audit/software/environment.json'))
    checks = load(safe_file(original, 'audit/self_checks.json'))
    pilot = load(safe_file(original, 'results/PILOT_STATUS.json'))
    for record in (start, end, environment, checks, pilot):
        same_origin(record, origin)
    require(start.get('stage') == 'start' and end.get('stage') == 'end'
            and end.get('pipeline_exit_code') == 1, 'Original runtime is not the recorded failed-report attempt.')
    expected_python = str(Path(cfg['runtime_package']) / 'runtime_env/bin/python')
    require(start.get('interpreter') == end.get('interpreter') == environment.get('executable') == expected_python
            and start.get('runtime_interpreter_fallback') is False and end.get('runtime_interpreter_fallback') is False,
            'Original engine did not use the pinned interpreter.')
    require(environment.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED'
            and environment.get('versions') == cfg['runtime_expected_versions']
            and environment.get('requirements_sha256') == cfg['runtime_requirements_sha256']
            and environment.get('software_installed') is False, 'Original engine environment was not verified.')
    current_environment = load(run / 'audit/software/environment.json')
    require(current_environment.get('python') == environment.get('python')
            and current_environment.get('runtime_ready_sha256') == environment.get('runtime_ready_sha256'),
            'Current corrected Python build/READY differs from original engine.')
    require(checks.get('status') == 'PASS' and checks.get('scientific_fixtures_executed_on_puma') is True
            and checks.get('fixture_data_are_synthetic') is True and checks.get('real_source_data_used') is False
            and bool(checks.get('checks')) and all(c.get('status') == 'PASS' for c in checks['checks']),
            'Original Puma self-check receipt did not pass.')
    require(pilot.get('status') == 'PILOT_POINT_DIAGNOSTICS_COMPLETED_REVIEW_PENDING'
            and pilot.get('source_integrity_passed') is True
            and pilot.get('source_files_verified_unchanged_after_audit') is True
            and pilot.get('source_files_changed') is False and pilot.get('code_protocol_verified_unchanged') is True
            and pilot.get('baseline_predictions_computed') is True and pilot.get('baseline_models_fitted') is True
            and pilot.get('complex_models_fitted') is False and pilot.get('numerical_ten_year_forecast') is False
            and pilot.get('ten_year_forecast_generated') is False and pilot.get('predictive_validation_established') is False
            and pilot.get('scientific_review_pending') is True, 'Original engine completion gates did not pass.')
    failed = load(safe_file(original, 'HPC_STATUS.json'))
    require(failed.get('analysis_job_id') == origin['job_id'] and failed.get('status') == 'HPC_RUN_FAILED_OR_INCOMPLETE'
            and failed.get('scheduler_state') == 'FAILED' and failed.get('scheduler_exit_code') == '1:0'
            and failed.get('pipeline_exit_code') == 1 and failed.get('scientific_scripts_completed') is False,
            'Original failed-run status differs from the recorded report failure.')
    require(not (original / 'results/REPORT_STATUS.json').exists()
            and not (original / 'ANALYSES_COMPLETE.txt').exists(), 'Original report is already marked complete.')
    log = safe_file(original, 'logs/analysis.log').read_text(errors='replace')
    require('Traceback (most recent call last):' in log and 'render_report.py' in log
            and 'text_report' in log and 'report_table' in log
            and 'RuntimeError: Report table header overflow.' in log, 'Expected report header-overflow traceback is absent.')
    engine_manifest = safe_file(original, 'results/output_manifest.csv')
    require(pilot.get('output_manifest') == 'results/output_manifest.csv'
            and pilot.get('output_manifest_sha256') == sha(engine_manifest), 'Original engine manifest pin mismatch.')
    with engine_manifest.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == ['relative_path', 'sha256', 'size_bytes'], 'Original engine manifest schema mismatch.')
        engine_rows = list(reader)
    seen = set()
    for row in engine_rows:
        name = row['relative_path']
        require(name.startswith('results/') and name not in seen and None not in row, 'Invalid/duplicate engine output entry.')
        seen.add(name)
        path = safe_file(original, name)
        require(sha(path) == row['sha256'] and path.stat().st_size == int(row['size_bytes']), 'Engine output hash/size changed: ' + name)
    required_outputs = {'results/' + name for name in (
        'training_fits.json', 'training_plot_means.csv', 'training_membership.csv', 'baseline_support.csv',
        'predictions.csv', 'prediction_freeze.json', 'cohort_qc.csv', 'exclusions.csv', 'cohort_support.csv',
        'plot_errors.csv', 'performance.csv', 'paired_errors.csv', 'paired_performance.csv', 'source_hashes.csv',
        'source_provenance.csv', 'response_count_verification.csv', 'source_integrity_after_pilot.csv',
        'source_preservation.json', 'integrity_checks.csv', 'code_protocol_hashes.json', 'RUN_SUMMARY.txt')}
    require(seen == required_outputs, 'Original engine manifest differs from its exact frozen 21-file schema.')
    require(pilot.get('predictions_csv_sha256') == sha(original / 'results/predictions.csv')
            and pilot.get('prediction_freeze_sha256') == sha(original / 'results/prediction_freeze.json'),
            'Original predictions or freeze receipt changed.')
    freeze = load(original / 'results/prediction_freeze.json')
    same_origin(freeze, origin)
    require(freeze.get('status') == 'FROZEN_BEFORE_SCORING'
            and freeze.get('predictions_csv_sha256') == pilot['predictions_csv_sha256']
            and freeze.get('training_response_partition') == 'PRE_2025_ONLY'
            and freeze.get('future_availability_used_for_training') is False
            and set(freeze.get('scope_prediction_digests', {})) == {
                d + '__' + s for d in ('bacteria', 'archaea') for s in ('PRIMARY', 'DEPTH_SENSITIVITY')}
            and all(isinstance(value, str) and len(value) == 64
                    and all(c in '0123456789abcdef' for c in value)
                    for value in freeze['scope_prediction_digests'].values()), 'Original prediction-freeze provenance/gates failed.')
    preservation = load(original / 'results/source_preservation.json')
    same_origin(preservation, origin)
    require(preservation.get('source_integrity_passed') is True
            and preservation.get('source_files_verified_unchanged_after_audit') is True
            and preservation.get('source_files_changed') is False and preservation.get('verified_after_scoring') is True,
            'Original post-scoring source-preservation receipt failed.')
    with (original / 'results/integrity_checks.csv').open(newline='', encoding='utf-8-sig') as stream:
        integrity = list(csv.DictReader(stream))
    expected_checks = {'source_provenance_and_locks', 'source_response_definition', 'metadata_projection',
        'raw_count_response_verification', 'primary_fixed_design', 'training_holdout_partition',
        'fixed_prediction_scoring', 'paired_common_cohort'}
    require(len(integrity) == len(expected_checks) and {r.get('check') for r in integrity} == expected_checks
            and all(r.get('status') == 'PASS' for r in integrity), 'Original exact eight integrity checks did not pass.')
    code_hashes = load(original / 'results/code_protocol_hashes.json')
    require(set(code_hashes) == {'pilot.py', 'baseline_core.py', 'self_checks.py', 'CONFIG.json', 'PROTOCOL.txt',
                              'review/SHANNON_DEFINITION_GATE.txt'}
            and all(package_hashes.get(name) == expected and sha(safe_file(original, name)) == expected
            for name, expected in code_hashes.items()), 'Engine code/protocol receipt differs from reviewed release.')
    require(cfg.get('source_core_sha256') == code_hashes['baseline_core.py'],
            'New package source-core pin differs from the preserved executed baseline core.')
    upstream_hashes = {}
    for alias, files in cfg['sources'].items():
        upstream = Path(cfg['source_runs'][alias]['path']).resolve(strict=True)
        for relative, expected in files.items():
            original_upstream = safe_file(upstream, relative)
            snapshot = safe_file(original, 'source_snapshot/' + alias + '/' + relative)
            require(sha(original_upstream) == sha(snapshot) == expected, 'Upstream original/snapshot changed: ' + alias + '/' + relative)
            upstream_hashes[str(original_upstream)] = expected
    accounting = subprocess.run(['sacct', '-j', origin['job_id'], '-S', '2026-10-05', '--parsable2',
        '--format=JobIDRaw,State,ExitCode,NodeList'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    require(accounting.returncode == 0, 'Cannot verify original failed-job accounting.')
    matches = [line.split('|') for line in accounting.stdout.splitlines()[1:]
               if line.split('|')[0] == origin['job_id']]
    require(len(matches) == 1 and matches[0][1:3] == ['FAILED', '1:0'], 'Original scheduler failure changed or is unavailable.')
    nodes = subprocess.run(['scontrol', 'show', 'hostnames', matches[0][3]],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    require(nodes.returncode == 0 and origin['node'] in nodes.stdout.splitlines(), 'Original allocated node mismatch.')
    selected = set(package_hashes) | {'PACKAGE_SHA256SUMS.txt', 'OUTPUT_SHA256SUMS.txt', 'HPC_STATUS.json',
                                    'submission.json', 'logs/analysis.log'}
    for directory in ('results', 'audit', 'source_snapshot'):
        selected.update(str(path.relative_to(original)) for path in (original / directory).rglob('*')
                        if path.is_file() and '__pycache__' not in path.parts)
    before = {name: sha(safe_file(original, name)) for name in sorted(selected)}
    require(len(before) == 124 and len(upstream_hashes) == 30, 'Original exact 124-file/30-source staging scope changed.')
    require(set(package_hashes).issubset(output_hashes)
            and set(seen).issubset(output_hashes)
            and all(name in output_hashes for name in ('results/PILOT_STATUS.json', 'audit/run_start.json',
                'audit/run_end.json', 'audit/software/environment.json', 'audit/self_checks.json', 'logs/analysis.log')),
            'Original collector checksum manifest omits required engine evidence.')
    target.mkdir()
    rows = []
    for name, expected in before.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe_file(original, name), destination)
        require(sha(destination) == expected, 'Engine snapshot copy changed: ' + name)
        rows.append({'relative_path': name, 'sha256': expected, 'size_bytes': destination.stat().st_size,
                     'original_path': str(original / name), 'snapshot_path': str(destination)})
    require(all(sha(safe_file(original, name)) == sha(safe_file(target, name)) == expected
                for name, expected in before.items()), 'Original engine source or snapshot changed during staging.')
    require(all(sha(Path(name)) == expected for name, expected in upstream_hashes.items()), 'Upstream source changed during staging.')
    table(run / 'audit/engine_source_manifest.csv', rows, ['relative_path', 'sha256', 'size_bytes', 'original_path', 'snapshot_path'])
    (run / 'audit/source_sacct.tsv').write_text(accounting.stdout, encoding='utf-8')
    repair_status = stage_repair(run, cfg, original, target, before, origin, pilot)
    require(all(sha(safe_file(original, name)) == sha(safe_file(target, name)) == expected
                for name, expected in before.items()), 'Original engine changed while verifying repair evidence.')
    require(all(sha(Path(name)) == expected for name, expected in upstream_hashes.items()),
            'Upstream source changed while verifying repair evidence.')
    save(run / 'audit/ENGINE_SOURCE_STATUS.json', {
        'status': 'IMMUTABLE_ENGINE_SOURCE_VERIFIED_AND_STAGED', 'execution': execution,
        'engine_origin_execution': origin, 'engine_source_job_id': origin['job_id'],
        'engine_source_path': source['path'], 'engine_source_resolved_path': str(original),
        'engine_source_scheduler_state': 'FAILED',
        'engine_source_scheduler_exit_code': '1:0', 'engine_source_pipeline_exit_code': 1,
        'stage_scope': 'IMMUTABLE_SOURCE_STAGING_ONLY',
        'source_integrity_passed': True, 'source_files_verified_unchanged_after_staging': True,
        'source_files_changed': False, 'baseline_predictions_reused_from_source': True,
        'baseline_fits_recomputed': False, 'scientific_fixtures_reexecuted': False,
        'engine_output_manifest_sha256': sha(engine_manifest),
        'engine_pilot_status_sha256': sha(original / 'results/PILOT_STATUS.json'),
        'engine_source_manifest_path': 'audit/engine_source_manifest.csv',
        'engine_source_manifest_sha256': sha(run / 'audit/engine_source_manifest.csv'),
        'source_package_manifest_sha256': sha(package_manifest), 'files_staged': len(rows),
        'staged_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'ten_year_forecast_generated': False, 'predictive_validation_established': False,
        'scientific_review_pending': True,
        'historical_baseline_flags_apply_to': 'SOURCE_STAGING_ONLY_NOT_LATER_BACKTEST',
        'repair_source_job_id': repair_status['repair_source_job_id'],
        'repair_source_status_path': 'audit/REPAIR_SOURCE_STATUS.json',
        'repair_source_status_sha256': sha(run / 'audit/REPAIR_SOURCE_STATUS.json'),
        'successful_report_source_verified': True,
    })
    print('SOURCES=VERIFIED_AND_STAGED; original engine job 24124251 remains FAILED 1:0; repair main job 24126557 COMPLETED 0:0; staging performed no scientific calculation.')


if __name__ == '__main__':
    main()
