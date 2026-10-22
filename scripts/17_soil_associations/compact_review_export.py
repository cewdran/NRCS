"""Bounded review exports and selection, using standard-library code on Puma.

No test, adjustment, rank, or effect estimate is computed here. The only
row selection uses the exact PRIMARY-family and already-computed discovery
flags in a verified global CSV. Counts describe existing status/reason fields.
"""
from pathlib import Path
from collections import Counter
from contextlib import ExitStack
import csv
import gzip
import hashlib
import json
import os
import zipfile

from orchestration_common import require, safe_relative, save, sha, utc
from recovery_common import FAILED_WORKER_IDS

REVIEW_LIMIT_BYTES = 128 * 1024 * 1024
CORE_RESULT_FILES = (
    'results/NRCS_association_review.pdf', 'results/REPORT_STATUS.json',
    'results/COMBINE_STATUS.json', 'results/OUTPUT_SHA256SUMS.txt',
    'results/association_summary.csv', 'results/multiplicity_family_audit.csv',
    'results/figures/FIGURE_STATUS.json', 'results/figures/figure_row_legend.csv',
)
LOGICAL_METADATA_FILES = (
    'matched_endpoints.csv.gz', 'sample_metadata.csv.gz', 'cohorts.csv',
    'scenario_cohorts.csv', 'match_audit.csv.gz', 'WORKER_STATUS.json',
    'OUTPUT_SHA256SUMS.txt', 'SOURCE_VERIFICATION.json',
    'ADAPTER_SELF_CHECKS.json', 'ASSOCIATION_SELF_CHECKS.json',
    'CATEGORICAL_SELF_CHECKS.json',
)


def _safe_file(run, relative):
    path = run / safe_relative(relative)
    require(path.resolve().is_relative_to(run) and not path.is_symlink(),
            'Unsafe review file: ' + str(relative))
    require(path.is_file(), 'Missing review file: ' + str(relative))
    return path


def export_global_review_rows(run, execution, expected_sha256=None):
    """Copy every PRIMARY BY discovery and count all PRIMARY status/reasons.

    The input is rehashed after the complete streaming read. Boolean flags
    are parsed strictly, without p-value or q-value comparisons. Raw p, q,
    effects and every other column are copied without numeric conversion.
    """
    from hpc_guard import require_hpc
    actual = require_hpc()
    require(all(actual.get(key) == execution.get(key)
                for key in ('job_id', 'cluster', 'node')),
            'Review extraction execution identity changed.')
    run = Path(run).resolve()
    source = _safe_file(run, 'results/associations_global.csv.gz')
    before = sha(source)
    if expected_sha256 is not None:
        require(before == expected_sha256, 'Global CSV differs from verified result manifest.')
    output = run / 'review_exports'
    output.mkdir(parents=True, exist_ok=True)
    discoveries = output / 'global_primary_BY_discoveries.csv.gz'
    failed = output / 'global_primary_failed_eligible.csv.gz'
    counts_path = output / 'global_primary_status_reason_counts.csv'
    temporary_discoveries = discoveries.with_name(discoveries.name + '.part')
    temporary_failed = failed.with_name(failed.name + '.part')
    require(not temporary_discoveries.exists() and not temporary_failed.exists(),
            'Previous review extraction is incomplete; refusing to overwrite it.')
    require(not discoveries.exists() and not failed.exists() and not counts_path.exists(),
            'Review extraction already exists; refusing to overwrite it.')
    grouping = ['farm', 'domain', 'feature_class', 'association_type', 'status', 'skip_reason']
    counts = Counter()
    total_rows = primary_rows = discovery_rows = failed_rows = 0
    try:
        with ExitStack() as stack:
            incoming = stack.enter_context(gzip.open(source, 'rt', newline=''))
            reader = csv.DictReader(incoming)
            fields = reader.fieldnames
            require(fields and len(fields) == len(set(fields)) and
                    set(grouping + ['global_family', 'by_discovery', 'test_id']).issubset(fields),
                    'Global CSV lacks unique required review-export columns.')
            outgoing = stack.enter_context(gzip.open(temporary_discoveries, 'wt', newline='', compresslevel=6))
            failures = stack.enter_context(gzip.open(temporary_failed, 'wt', newline='', compresslevel=6))
            discovery_writer = csv.DictWriter(outgoing, fieldnames=fields)
            failed_writer = csv.DictWriter(failures, fieldnames=fields)
            discovery_writer.writeheader()
            failed_writer.writeheader()
            for row in reader:
                require(None not in row and all(value is not None for value in row.values()),
                        'Malformed global CSV row during review export.')
                total_rows += 1
                require(row['by_discovery'] in {'True', 'False', 'true', 'false', '1', '0'},
                        'Unrecognized already-computed BY discovery flag.')
                if row['global_family'] != 'PRIMARY':
                    continue
                primary_rows += 1
                counts[tuple(row[key] for key in grouping)] += 1
                if row['by_discovery'] in {'True', 'true', '1'}:
                    discovery_writer.writerow(row)
                    discovery_rows += 1
                if row['status'] == 'FAILED_ELIGIBLE':
                    failed_writer.writerow(row)
                    failed_rows += 1
        after = sha(source)
        require(after == before, 'Global CSV changed during review export.')
        with counts_path.open('x', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=grouping + ['rows'])
            writer.writeheader()
            for key, count in sorted(counts.items()):
                writer.writerow(dict(zip(grouping, key), rows=count))
        os.replace(temporary_discoveries, discoveries)
        os.replace(temporary_failed, failed)
        result = {
            'created': True, 'utc': utc(), 'execution': execution,
            'source': str(source.relative_to(run)), 'before_sha256': before,
            'after_sha256': after, 'source_bytes_unchanged': before == after,
            'global_rows_scanned': total_rows, 'primary_rows_scanned': primary_rows,
            'all_primary_BY_discovery_rows_exported': discovery_rows,
            'all_primary_failed_eligible_rows_exported': failed_rows,
            'all_primary_status_and_skip_reason_counts_exported': True,
            'selection': 'global_family == PRIMARY and existing by_discovery flag is true',
            'no_statistics_recomputed': True,
            'scope': 'Complete global PRIMARY family; sensitivity families excluded explicitly.',
            'full_table_retained_on_puma': str(source),
            'outputs': [{'path': str(path.relative_to(run)), 'sha256': sha(path),
                         'bytes': path.stat().st_size}
                        for path in (discoveries, failed, counts_path)],
            'scientific_review_pending': True, 'visual_review_pending': True,
        }
        save(output / 'REVIEW_EXPORT_STATUS.json', result)
        return result
    except BaseException:
        for path in (temporary_discoveries, temporary_failed):
            if path.exists():
                path.unlink()
        raise


def select_review_files(run, shards, errors, pending_files=()):
    """Select files by declared role; never include source snapshots or big tables."""
    run = Path(run).resolve()
    mandatory, optional = set(), set()
    missing = []

    def include(name, essential=True):
        relative = Path(name)
        if str(relative) in pending_files and not (run / relative).exists():
            return
        try:
            path = _safe_file(run, relative)
            require(path.stat().st_size > 0, 'Empty review file: ' + str(relative))
            (mandatory if essential else optional).add(relative)
        except Exception as exc:
            missing.append({'path': str(relative), 'essential': essential, 'reason': str(exc)})

    for name in CORE_RESULT_FILES:
        include(name)
    for name in ('HPC_STATUS.json', 'submission.json', 'CONFIG.json', 'SOURCES.json',
                 'SHARDS.json', 'PACKAGE_SHA256SUMS.txt', 'OUTPUT_SHA256SUMS.txt',
                 'DOWNLOAD_RESULTS.txt', 'DOWNLOAD_REVIEW.txt', 'READ_ME_FIRST.md',
                 'STATISTICAL_PROTOCOL.txt', 'HPC_ONLY_ANALYSIS_POLICY.md', 'AGENTS.md'):
        include(name, essential=name not in {'READ_ME_FIRST.md'})
    for name in ('audit/collector/SOURCE_VERIFICATION.json',
                 'audit/collector/COLLECTION_IMMUTABILITY.json',
                 'audit/collector/sacct_main_jobs.txt',
                 'audit/collector/sacct_all_steps.txt',
                 'audit/join/JOIN_ENGINE_SELF_CHECKS.json',
                 'audit/join/CHUNK_VERIFICATION.json',
                 'audit/history/source_stage/STAGE_STATUS.json',
                 'audit/history/source_stage/EXECUTION_START.json',
                 'audit/history/source_stage/EXECUTION_END.json',
                 'audit/history/source_stage/ENVIRONMENT_STATUS.json',
                 'audit/history/source_stage/SOURCE_VERIFICATION.json',
                 'audit/history/recovery_source/CONFIG.json',
                 'audit/history/recovery_source/submission.json',
                 'audit/history/recovery_source/HPC_STATUS.json',
                 'audit/history/recovery_source/PACKAGE_SHA256SUMS.txt'):
        include(name)
    for name in ('review_exports/global_primary_BY_discoveries.csv.gz',
                 'review_exports/global_primary_failed_eligible.csv.gz',
                 'review_exports/global_primary_status_reason_counts.csv',
                 'review_exports/REVIEW_EXPORT_STATUS.json'):
        include(name)
    for name in ('results/primary_top_associations.csv', 'results/association_display_rows.csv'):
        include(name)
    for shard in shards:
        identifier = shard['id']
        for suffix in ('png', 'svg'):
            include('results/figures/' + identifier + '_primary_associations.' + suffix)
        for name in LOGICAL_METADATA_FILES:
            include('workers/' + identifier + '/' + name)
        if identifier in FAILED_WORKER_IDS:
            include('workers/' + identifier + '/ASSEMBLY_PROVENANCE.json')
        include('workers/' + identifier + '/FEATURE_REFERENCE.json', essential=False)
    for relative in sorted(run.rglob('*')):
        if not relative.is_file():
            continue
        rel = relative.relative_to(run)
        parts = rel.parts
        if any(part in {'private_source_snapshots', 'source_snapshots', 'source_snapshot',
                        '__pycache__'} for part in parts):
            continue
        if len(parts) == 1 and relative.suffix in {'.py', '.sh', '.md', '.json', '.txt'}:
            include(rel)
        elif parts[0] in {'audit', 'log_tails'}:
            # Audit evidence has a bounded per-file size; full copies remain in results ZIP.
            if relative.suffix in {'.json', '.txt', '.csv'} and relative.stat().st_size <= 16 * 1024 * 1024:
                include(rel)
        elif parts[0] == 'review_exports' and relative.name in {
                'global_primary_BY_discoveries.csv.gz',
                'global_primary_failed_eligible.csv.gz',
                'global_primary_status_reason_counts.csv', 'REVIEW_EXPORT_STATUS.json'}:
            include(rel)
        elif parts[:2] == ('workers', 'chunks') and relative.name in set(LOGICAL_METADATA_FILES):
            include(rel)
    for item in missing:
        if item['essential']:
            message = 'Compact review: ' + item['reason']
            if message not in errors:
                errors.append(message)
    return sorted(mandatory), sorted(optional - mandatory), missing


def write_review_zip(run, archive, shards, errors, cap=REVIEW_LIMIT_BYTES, verify_sources=None):
    """Create a real-size bounded ZIP, omitting optional reference files if needed."""
    run, archive = Path(run).resolve(), Path(archive)
    require(isinstance(cap, int) and 0 < cap <= REVIEW_LIMIT_BYTES,
            'Review archive cap must not exceed 128 MiB.')
    require(not archive.exists(), 'Refusing to overwrite review archive: ' + str(archive))
    mandatory, optional, missing = select_review_files(run, shards, errors)
    temporary = archive.with_name(archive.name + '.part')
    require(not temporary.exists(), 'Refusing to overwrite incomplete review archive.')
    selected = mandatory + optional
    omitted = []
    frozen = {}
    for line in (run / 'OUTPUT_SHA256SUMS.txt').read_text().splitlines():
        if line.strip():
            digest, relative = line.split('  ', 1)
            frozen[str(safe_relative(relative))] = digest
    frozen['OUTPUT_SHA256SUMS.txt'] = sha(run / 'OUTPUT_SHA256SUMS.txt')
    # This explicit descriptor is generated after finalizing the full archive.
    bundle_status = 'audit/collector/BUNDLE_STATUS.json'
    if (run / bundle_status).is_file():
        frozen[bundle_status] = sha(run / bundle_status)
    while True:
        with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as bundle:
            manifest = []
            for relative in sorted(selected):
                path = _safe_file(run, relative)
                digest = sha(path)
                require(str(relative) in frozen and digest == frozen[str(relative)],
                        'Review file differs from frozen full snapshot: ' + str(relative))
                archived_digest = hashlib.sha256()
                with path.open('rb') as incoming, bundle.open(
                        str(Path(run.name) / relative), 'w', force_zip64=True) as outgoing:
                    for block in iter(lambda: incoming.read(1024 * 1024), b''):
                        outgoing.write(block)
                        archived_digest.update(block)
                require(archived_digest.hexdigest() == digest,
                        'Copied review bytes differ from frozen digest: ' + str(relative))
                require(sha(path) == digest, 'Review file changed during packaging: ' + str(relative))
                manifest.append({'path': str(relative), 'sha256': digest, 'bytes': path.stat().st_size})
            source_evidence = verify_sources() if verify_sources is not None else None
            if source_evidence is not None:
                require(source_evidence.get('passed') is True and
                        source_evidence.get('source_files_changed') is False,
                        'Source bytes changed during compact review collection.')
                source_text = json.dumps(source_evidence, indent=2, sort_keys=True) + '\n'
                source_bytes = source_text.encode('utf-8')
                source_name = 'REVIEW_COLLECTION_SOURCE_VERIFICATION.json'
                bundle.writestr(str(Path(run.name) / source_name), source_text)
                manifest.append({'path': source_name,
                                 'sha256': hashlib.sha256(source_bytes).hexdigest(),
                                 'bytes': len(source_bytes)})
            descriptor = {
                'kind': 'compact_review', 'created_utc': utc(), 'cap_bytes': cap,
                'contains_complete_global_association_table': False,
                'full_results_location': str(archive).replace('_review.zip', '_results.zip'),
                'selection_does_not_change_global_correction_families': True,
                'review_bytes_bound_to_frozen_full_snapshot': True,
                'source_hashes_rechecked_after_review_byte_collection': source_evidence is not None,
                'all_primary_BY_discoveries_included': any(str(item).endswith(
                    'global_primary_BY_discoveries.csv.gz') for item in selected),
                'missing_files': missing, 'optional_files_omitted_for_size': omitted,
                'exclusions': ['Full association CSVs and per-farm copies',
                               'large diagnostics and feature screen tables',
                               'private source snapshots and raw/CLR matrices',
                               'live collector log and later collector runtime end'],
                'scientific_review_pending': True, 'visual_review_pending': True,
                'files': manifest,
            }
            bundle.writestr(str(Path(run.name) / 'COMPACT_REVIEW_CONTENTS.json'),
                            json.dumps(descriptor, indent=2, sort_keys=True) + '\n')
            bundle.writestr(str(Path(run.name) / 'COMPACT_REVIEW_SHA256SUMS.txt'),
                            ''.join(item['sha256'] + '  ' + item['path'] + '\n' for item in manifest))
        if temporary.stat().st_size <= cap:
            break
        temporary.unlink()
        require(bool(optional), 'Essential compact review exceeds the strict 128 MiB cap.')
        largest = max(optional, key=lambda item: (run / item).stat().st_size)
        optional.remove(largest)
        selected.remove(largest)
        omitted.append(str(largest))
    with zipfile.ZipFile(temporary) as bundle:
        require(bundle.testzip() is None, 'Compact review ZIP integrity failed.')
    os.replace(temporary, archive)
    checksum = Path(str(archive) + '.sha256.txt')
    require(not checksum.exists(), 'Refusing to overwrite review archive checksum.')
    checksum.write_text(sha(archive) + '  ' + archive.name + '\n')
    archive.chmod(0o400)
    checksum.chmod(0o400)
    return {'path': str(archive), 'checksum_path': str(checksum),
            'bytes': archive.stat().st_size, 'sha256': sha(archive),
            'cap_bytes': cap, 'within_cap': archive.stat().st_size <= cap,
            'zip_integrity_verified': True, 'file_count': len(manifest),
            'optional_files_omitted_for_size': omitted, 'missing_files': missing}
