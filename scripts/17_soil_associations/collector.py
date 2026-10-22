#!/usr/bin/env python3
"""Collect honest recovery evidence and two immutable result ZIPs on Puma.

The full ZIP is an archival byte snapshot, retained on Puma. The strict-size
review ZIP contains inspectable scientific outputs and provenance. Numerical
work in this collector is limited to copying existing PRIMARY discovery flags
and counting existing status/reason strings on the verified compute allocation.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import traceback
import zipfile

from hpc_guard import require_hpc
from orchestration_common import (TERMINAL, json_file, load_shards, normalize_status,
                                  require, safe_relative, save, scheduler_main_rows,
                                  sha, utc, verify_manifest)
from recovery_common import ORIGINAL_WORKER_JOBS, RETRY_CHUNKS, role_layout
from compact_review_export import export_global_review_rows, select_review_files, write_review_zip

EXPECTED_ROLES = ['stage', 'join', 'combine', 'collector']
COMBINE_OUTPUTS = [
    'associations_global.csv.gz', 'association_summary.csv', 'multiplicity_family_audit.csv',
    'NRCS_association_review.pdf', 'REPORT_STATUS.json', 'figures/FIGURE_STATUS.json',
    'figures/figure_row_legend.csv', 'primary_top_associations.csv',
    'association_display_rows.csv',
]


def field(value, key):
    item = (value or {}).get(key)
    return item if isinstance(item, dict) else {}


def attempt(errors, label, function, default=None):
    try:
        return function()
    except Exception as exc:
        errors.append(label + ': ' + str(exc))
        return default


def check_identity(value, job, node, label):
    actual = field(value, 'execution')
    require(actual.get('job_id') == job, label + ' job does not match submitted job.')
    require(actual.get('cluster') == 'puma', label + ' did not establish Puma execution.')
    require(bool(actual.get('node')) and (node is None or actual.get('node') == node),
            label + ' compute node does not match runtime evidence.')
    return actual['node']


def verify_source_receipt(path, job, node):
    value = json_file(path)
    require(value.get('passed') is True and value.get('source_files_changed') is False,
            'Source immutability evidence did not pass.')
    check_identity(value, job, node, 'Source verification')
    records = value.get('files')
    require(isinstance(records, list) and bool(records), 'Source verification has no file evidence.')
    names = set()
    for record in records:
        name = record.get('path')
        require(isinstance(name, str) and name and name not in names,
                'Source verification has missing or duplicate paths.')
        names.add(name)
        expected = record.get('expected_sha256', '')
        require(re.fullmatch(r'[a-f0-9]{64}', expected) is not None and
                expected == record.get('before_sha256') == record.get('after_sha256'),
                'Source before/after digest differs: ' + name)
    return value


def verify_self_check(path, job, node):
    value = json_file(path)
    require(value.get('passed') is True, 'Numerical self-check did not pass.')
    check_identity(value, job, node, 'Numerical self-check')
    checks = value.get('checks')
    require(isinstance(checks, list) and bool(checks) and
            all(isinstance(item, dict) and item.get('passed') is True for item in checks),
            'Numerical self-check has missing or failed checks.')
    return value


def manifest_evidence(directory, status, required):
    manifest = directory / 'OUTPUT_SHA256SUMS.txt'
    require(sha(manifest) == status.get('output_manifest_sha256'),
            'Output manifest digest differs from completion status.')
    entries = {str(item) for item in verify_manifest(directory, 'OUTPUT_SHA256SUMS.txt')}
    require(set(required).issubset(entries), 'Required output missing from checksum manifest.')
    digests = {}
    for line in manifest.read_text().splitlines():
        if line.strip():
            digest, relative = line.split('  ', 1)
            digests[str(safe_relative(relative))] = digest
    return digests


def scheduled_roles(receipt):
    jobs = receipt.get('jobs')
    require(isinstance(jobs, dict), 'Submission receipt has no job mapping.')
    declared = receipt.get('scheduled_job_roles', EXPECTED_ROLES)
    if isinstance(declared, dict):
        declared = list(declared)
    require(isinstance(declared, list) and len(declared) == len(set(declared)) and
            set(declared) == set(EXPECTED_ROLES),
            'Actual scheduled roles differ from the pinned four-job join repair.')
    result = {role: jobs.get(role) for role in declared}
    require(all(isinstance(job, str) and job.isdigit() for job in result.values()),
            'Actual scheduled roles have missing or invalid job IDs.')
    require(len(set(result.values())) == len(result), 'Actual scheduled job IDs are duplicated.')
    return result


def accounting(roles, audit, errors, receipt, config):
    rows, raw = {}, ''
    upstream = [job for role, job in roles.items() if role != 'collector']
    repair = config.get('join_repair', {})
    aliases = [job for role, job in receipt.get('jobs', {}).items()
               if role.startswith(('chunk:', 'worker:'))]
    historical = [repair.get('source_' + role + '_job_id')
                  for role in ('stage', 'join', 'combine', 'collector')]
    all_jobs = sorted(set(roles.values()) | set(ORIGINAL_WORKER_JOBS.values()) |
                      set(aliases) | {job for job in historical if isinstance(job, str) and job.isdigit()})
    for number in range(6):
        try:
            rows, raw = scheduler_main_rows(all_jobs)
            if (all(job in rows and rows[job]['state'] in TERMINAL for job in upstream) and
                    rows.get(roles.get('collector'), {}).get('state') == 'RUNNING'):
                break
        except Exception as exc:
            if number == 5:
                errors.append('Scheduler accounting unavailable: ' + str(exc))
        if number < 5:
            time.sleep(2)
    (audit / 'sacct_main_jobs.txt').write_text(raw)
    try:
        full = subprocess.run(['sacct', '--parsable2', '--jobs', ','.join(all_jobs),
                               '--format=JobIDRaw,JobID,State,ExitCode,Elapsed,ReqMem,MaxRSS,NodeList'],
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
        (audit / 'sacct_all_steps.txt').write_text(full.stdout + full.stderr)
        require(full.returncode == 0, 'Full Slurm step accounting failed: ' + full.stderr)
    except Exception as exc:
        errors.append('Scheduler step accounting: ' + str(exc))
    return rows


def runtime_evidence(run, role, job, row, execution, errors, expected_ready_sha256):
    location, status_path = role_layout(role)
    directory = run / location
    start, start_error = normalize_status(directory / 'EXECUTION_START.json')
    end, end_error = normalize_status(directory / 'EXECUTION_END.json')
    environment, environment_error = normalize_status(directory / 'ENVIRONMENT_STATUS.json')
    status, status_error = normalize_status(run / status_path)
    evidence = {'job_id': job, 'scheduler': row, 'runtime_start': start,
                'runtime_end': end, 'environment': environment,
                'completion_status_path': status_path, 'completion_status': status,
                'runtime_verified': False, 'scheduler_completed': False,
                'source_hash_verified': False, 'errors': []}
    local = evidence['errors']
    if role == 'collector':
        try:
            require(execution['job_id'] == job, 'Actual collector does not match submission receipt.')
            require(row is not None and row['state'] == 'RUNNING',
                    'Collector accounting snapshot is not RUNNING.')
            require(start_error is None and environment_error is None,
                    'Collector start/environment receipt is missing or malformed.')
            check_identity(start, job, execution['node'], 'Collector runtime start')
            check_identity(environment, job, execution['node'], 'Collector environment')
            require(row.get('node_list') == execution['node'],
                    'Collector execution node differs from scheduler accounting.')
            require(environment.get('status') == 'COMPUTE_RUNTIME_VERIFIED' and
                    environment.get('package_integrity_passed') is True,
                    'Collector environment/package verification did not pass.')
            require(environment.get('runtime_installation_performed') is False and
                    bool(field(environment, 'versions')) and
                    re.fullmatch(r'[a-f0-9]{64}', expected_ready_sha256 or '') is not None and
                    environment.get('ready_sha256') == expected_ready_sha256,
                    'Collector frozen READY/runtime evidence differs.')
            evidence['runtime_verified'] = True
            evidence['scheduler_snapshot_running'] = True
            evidence['final_completion_verification'] = 'CHECK.sh after collector exits; not claimed here.'
        except Exception as exc:
            local.append(str(exc))
    else:
        try:
            require(row is not None and row['state'] in TERMINAL,
                    'Missing terminal scheduler accounting.')
            require(row['state'] == 'COMPLETED' and row['exit_code'] == '0:0',
                    'Scheduler recorded ' + str(row['state']) + ' exit ' + str(row['exit_code']))
            evidence['scheduler_completed'] = True
            require(start_error is None and end_error is None and environment_error is None,
                    'Missing or malformed runtime START/END/ENV receipt.')
            node = check_identity(end, job, None, 'Runtime end')
            require(row.get('node_list') == node,
                    'Runtime compute node differs from scheduler accounting.')
            check_identity(start, job, node, 'Runtime start')
            check_identity(environment, job, node, 'Environment status')
            require(end.get('pipeline_exit_code') == 0,
                    'Runtime end does not establish a zero pipeline exit.')
            require(environment.get('status') == 'COMPUTE_RUNTIME_VERIFIED' and
                    environment.get('package_integrity_passed') is True,
                    'Compute environment/package integrity did not pass.')
            require(environment.get('runtime_installation_performed') is False and
                    bool(field(environment, 'versions')) and
                    re.fullmatch(r'[a-f0-9]{64}', expected_ready_sha256 or '') is not None and
                    environment.get('ready_sha256') == expected_ready_sha256,
                    'Frozen READY/runtime evidence differs.')
            require(status_error is None and status.get('completed') is True,
                    'Scientific stage completion status is missing or not completed.')
            check_identity(status, job, node, 'Stage scientific completion')
            require(status.get('source_files_changed') is False and
                    status.get('package_integrity_passed') is True,
                    'Stage source/package flags do not establish integrity.')
            evidence['runtime_verified'] = True
            source_path = (run / 'workers/chunks' / role.split(':', 1)[1] / 'SOURCE_VERIFICATION.json'
                           if role.startswith('chunk:') else directory / 'SOURCE_VERIFICATION.json')
            evidence['source_hash_evidence'] = verify_source_receipt(source_path, job, node)
            evidence['source_hash_verified'] = True
        except Exception as exc:
            local.append(str(exc))
    errors.extend(role + ': ' + error for error in local)
    return evidence


def verify_combined(run, receipt, shards, stages, records):
    from recovery_validation import remember_file, verify_source_receipt as remember_source_receipt
    job = receipt['jobs']['combine']
    node = field(stages['combine'].get('runtime_end'), 'execution').get('node')
    status = json_file(run / 'results/COMBINE_STATUS.json')
    check_identity(status, job, node, 'Combined result')
    require(status.get('completed') is True and status.get('worker_count') == len(shards),
            'Combined result completion/worker count differs.')
    for key, expected in {'source_files_changed': False, 'package_integrity_passed': True,
                          'scientific_review_pending': True, 'visual_review_pending': True,
                          'numerical_self_checks_passed': True}.items():
        require(status.get(key) is expected, 'Combined result flag differs: ' + key)
    source = verify_source_receipt(run / 'audit/combine/SOURCE_VERIFICATION.json', job, node)
    remember_source_receipt(run / 'audit/combine/SOURCE_VERIFICATION.json', job, records)
    checks = verify_self_check(run / 'audit/combine/MERGE_SELF_CHECKS.json', job, node)
    farm_outputs = ['per_farm/' + re.sub(r'[^A-Za-z0-9]+', '_', farm).strip('_') +
                    '_associations_global.csv.gz'
                    for farm in sorted({shard['farm'] for shard in shards})]
    require(len(farm_outputs) == 3 and len(set(farm_outputs)) == 3,
            'Per-farm output identities differ from the planned three farms.')
    digests = manifest_evidence(run / 'results', status, COMBINE_OUTPUTS + farm_outputs)
    remember_file(records, run / 'results/COMBINE_STATUS.json')
    remember_file(records, run / 'results/OUTPUT_SHA256SUMS.txt', status['output_manifest_sha256'])
    for name, digest in digests.items():
        remember_file(records, run / 'results' / safe_relative(name), digest)
    for name in ('EXECUTION_START.json', 'EXECUTION_END.json', 'ENVIRONMENT_STATUS.json',
                 'MERGE_SELF_CHECKS.json'):
        remember_file(records, run / 'audit/combine' / name)
    report = json_file(run / 'results/REPORT_STATUS.json')
    check_identity(report, job, node, 'Report')
    require(report.get('created') is True and report.get('scientific_review_pending') is True and
            report.get('visual_review_pending') is True and
            report.get('pdf') == 'results/NRCS_association_review.pdf' and
            isinstance(report.get('pages'), int) and report['pages'] > 0,
            'Report creation/pending-review receipt differs.')
    figures = json_file(run / 'results/figures/FIGURE_STATUS.json')
    check_identity(figures, job, node, 'Figures')
    figure_records = figures.get('figures')
    require(figures.get('created') is True and figures.get('figure_count') == len(shards) and
            figures.get('scientific_review_pending') is True and figures.get('visual_review_pending') is True and
            isinstance(figure_records, list) and len(figure_records) == len(shards) and
            all(isinstance(item, dict) for item in figure_records) and
            {item.get('shard_id') for item in figure_records} == {shard['id'] for shard in shards},
            'Figure creation/count/pending-review receipt differs.')
    for record in figure_records:
        for kind in ('png', 'svg'):
            relative = safe_relative(record.get(kind, ''))
            require(str(relative) == 'results/figures/' + record['shard_id'] +
                    '_primary_associations.' + kind,
                    'Figure filename differs from its logical shard.')
            path = run / relative
            require(path.is_file() and not path.is_symlink() and path.stat().st_size > 1000 and
                    path.suffix == '.' + kind and path.is_relative_to(run / 'results') and
                    str(path.relative_to(run / 'results')) in digests,
                    'Figure missing from verified outputs: ' + str(relative))
    return {'status': status, 'source': source, 'self_checks': checks,
            'output_digests': digests, 'report': report, 'figures': figures,
            'verified': True}


def snapshot_log_tails(run):
    output = run / 'log_tails'
    for root in ('logs',):
        for source in sorted((run / root).rglob('*')) if (run / root).is_dir() else []:
            if not source.is_file() or source.is_symlink():
                continue
            target = output / (str(source.relative_to(run)) + '.tail.txt')
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open('rb') as handle:
                handle.seek(max(0, source.stat().st_size - 128 * 1024))
                content = handle.read()
            target.write_bytes(content)


def excluded_from_full(relative):
    parts = relative.parts
    return ('__pycache__' in parts or relative in {
        Path('HPC_STATUS.json'), Path('OUTPUT_SHA256SUMS.txt'),
        Path('audit/collector/SOURCE_VERIFICATION.json'),
        Path('audit/collector/COLLECTION_IMMUTABILITY.json'),
        Path('audit/collector/BUNDLE_STATUS.json'),
        Path('audit/collector/EXECUTION_END.json'),
    } or (parts and parts[0] == 'logs' and 'collector' in relative.name))


def immutable_inventory(run):
    files = []
    for path in sorted(run.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(run)
        if excluded_from_full(relative):
            continue
        require(not path.is_symlink(), 'Refusing to archive a symlink: ' + str(relative))
        files.append({'path': str(relative), 'sha256': sha(path), 'bytes': path.stat().st_size})
    return files


def write_verified_member(bundle, run, record):
    path = run / safe_relative(record['path'])
    digest = hashlib.sha256()
    info = zipfile.ZipInfo.from_file(path, str(Path(run.name) / record['path']))
    info.compress_type = (zipfile.ZIP_STORED if path.suffix.lower() in
                          {'.gz', '.png', '.zip', '.xlsx', '.pptx', '.docx'}
                          else zipfile.ZIP_DEFLATED)
    info._compresslevel = 6
    with path.open('rb') as incoming, bundle.open(info, 'w', force_zip64=True) as outgoing:
        for block in iter(lambda: incoming.read(1024 * 1024), b''):
            outgoing.write(block)
            digest.update(block)
    require(digest.hexdigest() == record['sha256'],
            'Bytes changed while archiving: ' + record['path'])


def finalize_sources(records, execution, destination, errors):
    try:
        from recovery_validation import finish_sources
        require(bool(records), 'No validated source records were collected.')
        evidence = finish_sources(records, execution, destination)
        if evidence is None:
            evidence = json_file(destination)
        require(evidence.get('passed') is True and evidence.get('source_files_changed') is False,
                'Collector source before/after hashes did not pass.')
        return evidence
    except Exception as exc:
        errors.append('Collector source immutability: ' + str(exc))
        evidence = {'passed': False, 'source_files_changed': None, 'execution': execution,
                    'utc': utc(), 'files': list(records.values()) if isinstance(records, dict) else [],
                    'error': str(exc), 'no_missing_evidence_assumed_passed': True}
        save(destination, evidence)
        return evidence


def update_result(result, errors, sources):
    result['completion_validation_errors'] = list(errors)
    success = not errors
    result['status'] = ('HPC_ASSOCIATIONS_COMPLETED_SCIENTIFIC_AND_VISUAL_REVIEW_PENDING'
                        if success else 'HPC_RUN_FAILED_OR_INCOMPLETE')
    result['scientific_scripts_completed'] = success
    result['source_runs_unchanged_verified'] = (sources.get('passed') is True and
                                               sources.get('source_files_changed') is False and
                                               result.get('source_evidence_complete') is True)
    result['collector_source_hash_evidence'] = sources
    return success


def build_full_archive(run, archive, result, records, execution, errors):
    require(not archive.exists(), 'Refusing to overwrite full result archive.')
    temporary = archive.with_name(archive.name + '.part')
    require(not temporary.exists(), 'Refusing to overwrite incomplete full result archive.')
    inventory = immutable_inventory(run)
    snapshot_utc = utc()
    with zipfile.ZipFile(temporary, 'x', allowZip64=True) as bundle:
        for record in inventory:
            write_verified_member(bundle, run, record)
        mismatches = []
        for record in inventory:
            path = run / safe_relative(record['path'])
            if not path.is_file() or path.is_symlink() or sha(path) != record['sha256']:
                mismatches.append(record['path'])
        if mismatches:
            errors.append('Collected input bytes changed: ' + ', '.join(mismatches))
        sources = finalize_sources(records, execution,
                                   run / 'audit/collector/SOURCE_VERIFICATION.json', errors)
        immutability = {'snapshot_started_utc': snapshot_utc, 'snapshot_finished_utc': utc(),
                        'execution': execution, 'file_count': len(inventory),
                        'all_archived_bytes_matched_before_digests': True,
                        'all_input_bytes_matched_after_digests': not mismatches,
                        'changed_files': mismatches,
                        'source_before_after_verified': sources.get('passed') is True,
                        'excluded_live_files': ['logs/*collector*', 'audit/collector/EXECUTION_END.json'],
                        'timeline': 'Collector was RUNNING. Its log tail is a byte snapshot. The wrapper writes its runtime END after this script exits; CHECK.sh must verify final COMPLETED 0:0. Bundle status/checksums are generated after archive finalization.',
                        'files': inventory}
        save(run / 'audit/collector/COLLECTION_IMMUTABILITY.json', immutability)
        result['collection_byte_snapshot_verified'] = not mismatches
        update_result(result, errors, sources)
        save(run / 'HPC_STATUS.json', result)
        for name in ('audit/collector/SOURCE_VERIFICATION.json',
                     'audit/collector/COLLECTION_IMMUTABILITY.json', 'HPC_STATUS.json'):
            path = run / name
            record = {'path': name, 'sha256': sha(path), 'bytes': path.stat().st_size}
            inventory.append(record)
            write_verified_member(bundle, run, record)
        manifest = run / 'OUTPUT_SHA256SUMS.txt'
        manifest.write_text(''.join(record['sha256'] + '  ' + record['path'] + '\n'
                                    for record in sorted(inventory, key=lambda item: item['path'])))
        write_verified_member(bundle, run, {'path': manifest.name, 'sha256': sha(manifest),
                                           'bytes': manifest.stat().st_size})
    with zipfile.ZipFile(temporary) as bundle:
        require(bundle.testzip() is None, 'Full result ZIP integrity failed.')
        require(len(bundle.namelist()) == len(set(bundle.namelist())), 'Duplicate full ZIP entries.')
    os.replace(temporary, archive)
    checksum = Path(str(archive) + '.sha256.txt')
    require(not checksum.exists(), 'Refusing to overwrite full archive checksum.')
    checksum.write_text(sha(archive) + '  ' + archive.name + '\n')
    archive.chmod(0o400)
    checksum.chmod(0o400)
    return {'path': str(archive), 'checksum_path': str(checksum), 'sha256': sha(archive),
            'bytes': archive.stat().st_size, 'zip_integrity_verified': True,
            'file_count': len(inventory) + 1,
            'retained_on_puma': True, 'complete_collected_byte_snapshot': True}


def main():
    execution = require_hpc()
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-root', required=True)
    args = parser.parse_args()
    run = Path(args.run_root).resolve()
    require(run.is_dir() and not run.is_symlink(), 'Run directory is missing or unsafe.')
    audit = run / 'audit/collector'
    audit.mkdir(parents=True, exist_ok=True)
    errors = []
    receipt = attempt(errors, 'Submission receipt', lambda: json_file(run / 'submission.json'), {})
    config = attempt(errors, 'Configuration', lambda: json_file(run / 'CONFIG.json'), {})
    shards = attempt(errors, 'Logical shard plan', lambda: load_shards(run), [])
    roles = attempt(errors, 'Actual recovery schedule', lambda: scheduled_roles(receipt), {})
    rows = accounting(roles, audit, errors, receipt, config) if roles else {}
    stages = {role: runtime_evidence(run, role, job, rows.get(job), execution, errors,
                                    config.get('recovery', {}).get('expected_runtime_ready_sha256'))
              for role, job in roles.items()}
    package_ok = attempt(errors, 'Current package integrity', lambda: bool(verify_manifest(run)), False)
    records = {}
    verified_recovery = None
    try:
        from recovery_validation import verify_recovery_stages, verify_worker
        verified_recovery = verify_recovery_stages(run, records=records, require_join=True)
    except Exception as exc:
        errors.append('Recovery stage/chunk/JOIN provenance: ' + str(exc))
    logical = {}
    if verified_recovery is not None:
        for shard in shards:
            verified = attempt(errors, shard['id'] + ' logical output provenance',
                               lambda shard=shard: verify_worker(
                                   run, shard, records, dict(verified_recovery['scheduling'], **rows),
                                   stages=verified_recovery))
            logical[shard['id']] = {'verified': verified is not None,
                                    'evidence': verified}
    else:
        for shard in shards:
            status, error = normalize_status(run / 'workers' / shard['id'] / 'WORKER_STATUS.json')
            logical[shard['id']] = {'verified': False, 'status': status,
                                    'error': error or 'Recovery provenance validation failed.'}
    combined = attempt(errors, 'Combined scientific outputs',
                       lambda: verify_combined(run, receipt, shards, stages, records), {})
    for name in config.get('output_contract', {}).get('required_files', []):
        attempt(errors, 'Required output ' + str(name), lambda name=name: require(
            (run / safe_relative(name)).is_file() and (run / safe_relative(name)).stat().st_size > 0,
            'Output is missing or empty.'))
    extraction = None
    if combined.get('verified'):
        extraction = attempt(errors, 'Compact global PRIMARY exports',
                             lambda: export_global_review_rows(run, execution,
                                  combined['output_digests']['associations_global.csv.gz']))
    else:
        errors.append('Compact global PRIMARY exports skipped: full combined output integrity was not established.')
    snapshot_log_tails(run)
    download = Path(receipt.get('download_directory', str(run.parent / 'downloads')))
    require(download.is_absolute(), 'Download directory must be absolute.')
    download.mkdir(parents=True, exist_ok=True)
    package = str(receipt.get('package_name', config.get('package', 'NRCS_recovery')))
    require(re.fullmatch(r'[A-Za-z0-9_-]+', package) is not None, 'Unsafe package identifier.')
    identifier = str(receipt.get('jobs', {}).get('combine', 'no_combine'))
    stem = package + '_' + identifier + '_' + run.name
    full_archive = download / (stem + '_results.zip')
    review_archive = download / (stem + '_review.zip')
    result = {
        'status': 'HPC_RUN_FAILED_OR_INCOMPLETE', 'collected_utc': utc(),
        'collector_execution': execution, 'collector_scheduler_snapshot': rows.get(execution['job_id']),
        'collector_final_completion_verified': False,
        'collector_final_completion_check': 'Run CHECK.sh after collection; require collector COMPLETED and exit 0:0 plus runtime END.',
        'receipt_state_not_scheduler_state': receipt.get('state'),
        'scheduled_job_roles': list(roles), 'scheduled_job_count': len(roles), 'stages': stages,
        'logical_worker_count': len(shards), 'logical_workers': logical,
        'logical_worker_identity_policy': 'Ten original successful worker job identities and six completed source chunk job identities remain unchanged. Two assembled logical workers use the actual new JOIN job/node with validated immutable historical chunks.',
        'reused_complete_chunk_count': 6,
        'reused_complete_chunk_jobs': config.get('join_repair', {}).get('source_chunk_jobs', {}),
        'historical_source_stage_job_id': config.get('join_repair', {}).get('source_stage_job_id'),
        'historical_source_run': config.get('join_repair', {}).get('source_run'),
        'reused_complete_chunks': (verified_recovery.get('chunks') if verified_recovery else None),
        'scientific_inference_recomputed': False,
        'historical_partial_join_outputs_imported': False,
        'scientific_scripts_completed': False, 'scientific_review_pending': True,
        'visual_review_pending': True, 'package_integrity_passed': package_ok is True,
        'upstream_scheduler_terminal_success_verified': bool(roles) and all(
            stage['scheduler_completed'] for role, stage in stages.items() if role != 'collector'),
        'runtime_evidence_verified': bool(roles) and all(stage['runtime_verified'] for stage in stages.values()),
        'source_evidence_complete': bool(verified_recovery) and len(logical) == 12 and
            all(item['verified'] for item in logical.values()) and combined.get('verified') is True,
        'source_runs_unchanged_verified': False,
        'output_manifest_verification': {'logical_workers': {key: item['verified'] for key, item in logical.items()},
                                         'combine': combined.get('verified') is True},
        'numerical_self_checks_verified': bool(verified_recovery) and
            all(item['verified'] for item in logical.values()) and combined.get('verified') is True,
        'figure_count': field(combined, 'figures').get('figure_count'),
        'review_export': extraction, 'completion_validation_errors': errors,
        'ten_year_forecast_generated': False, 'predictive_validation_established': False,
        'source_runs_policy': 'Original run and frozen source bytes are read only; missing evidence never counts as success.',
        'source_immutability_verification_scope': 'This frozen receipt records before/after validation of the full byte snapshot. Compact review performs another source check after copying; its evidence is embedded in the review ZIP and its outcome is recorded in BUNDLE_STATUS.json.',
        'report_review': 'Inspect effects, matching/cohort exclusions, failed eligible tests, global BY/BH multiplicity, and all twelve displays before scientific use.',
        'download_results': str(full_archive), 'download_results_checksum': str(full_archive) + '.sha256.txt',
        'download_review': str(review_archive), 'download_review_checksum': str(review_archive) + '.sha256.txt',
        'archive_snapshot_policy': 'Full archive contains all collected run bytes except live collector log, later runtime END, bytecode caches and post-finalization bundle status. Collector log tail and explicit timeline are included.',
    }
    save(run / 'HPC_STATUS.json', result)
    (run / 'DOWNLOAD_RESULTS.txt').write_text('DOWNLOAD_RESULTS=' + str(full_archive) + '\n' +
        'DOWNLOAD_RESULTS_CHECKSUM=' + str(full_archive) + '.sha256.txt\n')
    (run / 'DOWNLOAD_REVIEW.txt').write_text('DOWNLOAD_REVIEW=' + str(review_archive) + '\n' +
        'DOWNLOAD_REVIEW_CHECKSUM=' + str(review_archive) + '.sha256.txt\n')
    archive_results = {}
    packaging_errors = []
    review_source_check = {'attempted': False, 'passed': False}
    # Preflight mandatory review content before freezing HPC_STATUS in the full ZIP.
    # These three files are generated by the byte-collection finalization itself.
    select_review_files(run, shards, errors, pending_files={
        'OUTPUT_SHA256SUMS.txt', 'audit/collector/SOURCE_VERIFICATION.json',
        'audit/collector/COLLECTION_IMMUTABILITY.json'})
    try:
        archive_results['results'] = build_full_archive(run, full_archive, result, records, execution, errors)
    except Exception as exc:
        message = 'Full archival result collection: ' + str(exc)
        errors.append(message)
        packaging_errors.append(message)
        result['collector_failure_traceback'] = traceback.format_exc()
        sources = finalize_sources(records, execution, audit / 'SOURCE_VERIFICATION.json', errors)
        update_result(result, errors, sources)
        save(run / 'HPC_STATUS.json', result)
    # The full ZIP freezes HPC_STATUS; this descriptor records packaging after that freeze.
    save(audit / 'BUNDLE_STATUS.json', {'utc': utc(), 'execution': execution, 'archives': archive_results,
                                      'scientific_review_pending': True, 'visual_review_pending': True,
                                      'full_archive_status_is_a_collection_snapshot': True})
    try:
        review_errors = []
        from recovery_validation import finish_sources
        def review_sources():
            # A partial failed upstream run still receives a review ZIP with
            # explicit failed source evidence; it must not claim verification.
            if not result.get('source_evidence_complete'):
                return None
            review_source_check['attempted'] = True
            evidence = finish_sources(records, execution,
                                      audit / 'REVIEW_COLLECTION_SOURCE_VERIFICATION.json')
            review_source_check['passed'] = evidence.get('passed') is True
            review_source_check['path'] = str(audit / 'REVIEW_COLLECTION_SOURCE_VERIFICATION.json')
            review_source_check['sha256'] = sha(audit / 'REVIEW_COLLECTION_SOURCE_VERIFICATION.json')
            return evidence
        archive_results['review'] = write_review_zip(run, review_archive, shards, review_errors,
                                                     verify_sources=review_sources)
        for error in review_errors:
            if error not in errors:
                errors.append(error)
                packaging_errors.append(error)
    except Exception as exc:
        message = 'Compact review collection: ' + str(exc)
        errors.append(message)
        packaging_errors.append(message)
    result['archives'] = archive_results
    result['completion_validation_errors'] = list(errors)
    if errors:
        result['status'] = 'HPC_RUN_FAILED_OR_INCOMPLETE'
        result['scientific_scripts_completed'] = False
    result['collection_finalized_utc'] = utc()
    result['review_bundle_within_cap'] = field(archive_results, 'review').get('within_cap') is True
    # A successful full snapshot already manifested HPC_STATUS. Never rewrite
    # that byte-frozen scientific receipt; packaging completion is separate.
    if 'results' not in archive_results:
        save(run / 'HPC_STATUS.json', result)
    save(audit / 'BUNDLE_STATUS.json', {'utc': utc(), 'execution': execution, 'archives': archive_results,
                                      'errors': errors, 'scientific_review_pending': True,
                                      'visual_review_pending': True,
                                      'post_snapshot_status_update': True,
                                      'scientific_status_snapshot': str(run / 'HPC_STATUS.json'),
                                      'packaging_success': len(archive_results) == 2 and not packaging_errors,
                                      'packaging_errors': packaging_errors,
                                      'review_source_hash_check': review_source_check})
    print(json.dumps(result, indent=2, sort_keys=True))
    for kind in ('REVIEW', 'RESULTS'):
        record = archive_results.get(kind.lower(), {})
        if record:
            print('DOWNLOAD_' + kind + '=' + record['path'])
            print('DOWNLOAD_' + kind + '_CHECKSUM=' + record['checksum_path'])
    return 0 if len(archive_results) == 2 and not packaging_errors else 1


if __name__ == '__main__':
    raise SystemExit(main())
