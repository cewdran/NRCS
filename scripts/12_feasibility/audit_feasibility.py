#!/usr/bin/env python3
"""Puma-only, descriptive support audit. No fitting, tests, or forecasts."""
from hpc_guard import require_hpc
execution = require_hpc()  # Must precede configuration or scientific input reads.

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
import csv
import hashlib
import json
import math
import shutil
import sys


REQUIRED = {
    'corrected': [
        'HPC_STATUS.json', 'audit/run_end.json', 'ANALYSES_COMPLETE.txt',
        'payload/tables/analysis_observations.csv',
        'payload/tables/record_disposition.csv', 'payload/tables/held_records.csv',
        'payload/tables/retained_measurements.csv',
        'payload/tables/confirmed_soil_microbiome_matches.csv',
        'payload/corrections/laboratory_records.csv',
        'payload/corrections/laboratory_measurements.csv',
        'payload/inputs/workbook_data.json',
        'payload/corrections/NRCS_Sample_Decisions_and_Open_Questions_2026-09-30.txt'],
    'functional': [
        'HPC_STATUS.json', 'audit/run_end.json', 'ANALYSES_COMPLETE.txt',
        'validated/bacteria/sample_metadata.csv', 'validated/archaea/sample_metadata.csv',
        'results/modeling_support/confirmed_join_audit.csv',
        'results/modeling_support/SUPPORT_COMPLETION.json',
        'results/modeling_support/microbiome_plot_visit_support.csv',
        'results/modeling_support/microbiome_cohort_support.csv',
        'results/modeling_support/measured_outcome_visit_support.csv'],
    'comparisons': [
        'HPC_STATUS.json', 'audit/run_end.json', 'ANALYSES_COMPLETE.txt',
        'results/COMPARISON_STATUS.json', 'results/FEATURE_PANEL.csv',
        'results/feature_summaries.csv', 'results/sample_eligibility.csv',
        'results/targeted_tests.csv']}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def truth(value):
    return str(value).strip().lower() in {'true', '1', 'yes'}


def exact_date(value):
    text = str(value or '').strip()
    try:
        parsed = date.fromisoformat(text)
        return parsed if parsed.isoformat() == text else None
    except ValueError:
        return None


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def missing_method_text(value):
    """Recognize only explicit missingness labels; do not guess assay meaning."""
    normalized = ' '.join(str(value or '').strip().casefold().split())
    return normalized in {
        '', 'unknown', 'not recorded', 'notrecorded', 'not_recorded',
        'not provided', 'notprovided', 'not_provided', 'not specified',
        'not specified on report', 'unspecified', 'not reported',
        'not available', 'unreported', 'undocumented', 'missing',
        '-', '—', '–'}


def tokens(value):
    return [part.strip() for part in str(value or '').split(';') if part.strip()]


def joined(values):
    return ';'.join(sorted({str(value) for value in values if value is not None and str(value)}))


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def plot_count_basis(rows):
    """Label existing plot counts without reconciling or creating identities."""
    countable = [row for row in rows if row['plot']]
    if not countable:
        return 'NO_COUNTABLE_PLOT_IDENTITIES'
    if all(row['identity_confirmed'] for row in countable):
        return 'CONFIRMED_BIOLOGICAL_IDENTITIES'
    return 'INCLUDES_UNCONFIRMED_RECORDED_PLOT_IDENTITIES'


def measured_plot_basis(rows):
    return plot_count_basis([dict(plot=row['_physical_plot'], identity_confirmed=row['_date_confirmed'])
                             for row in rows])


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_csv(path, fields=(), project=False):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames is not None, 'Missing CSV header: ' + str(path))
        require(set(fields).issubset(reader.fieldnames), 'Missing required columns in ' + str(path))
        rows = list(reader)
    require(all(None not in row for row in rows), 'Malformed CSV row in ' + str(path))
    return [{field: row[field] for field in fields} for row in rows] if project else rows


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(path)


def write_csv(path, rows, fields=()):
    columns = list(fields)
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    require(columns, 'An empty table needs an explicit schema: ' + str(path))
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def identifier(parts):
    return hashlib.sha256(compact(list(parts)).encode()).hexdigest()[:16]


def locked_sources(run, config):
    """Snapshot only pinned small inputs; fail closed on provenance disagreement."""
    require(config.get('automatic_forecast_authorization') is False,
            'Automatic forecast authorization must remain false.')
    require(set(config['source_runs']) == set(REQUIRED), 'Unexpected source run names.')
    require(set(config['sources']) == set(REQUIRED), 'Unexpected source lock names.')
    snapshot = run / 'source_snapshot'
    snapshot.mkdir(exist_ok=False)
    hashes = []
    provenance = []
    for name in REQUIRED:
        pinned = config['sources'][name]
        require(set(REQUIRED[name]).issubset(pinned), 'Missing necessary locked inputs: ' + name)
        source_root = Path(config['source_runs'][name]['path']).resolve(strict=True)
        expected_job = str(config['source_runs'][name]['job_id'])
        require(expected_job.isdigit(), 'Invalid locked source job ID: ' + name)
        for relative, expected_hash in sorted(pinned.items()):
            relative_path = Path(relative)
            require(not relative_path.is_absolute() and '..' not in relative_path.parts,
                    'Unsafe source relative path: ' + relative)
            source = (source_root / relative_path).resolve(strict=True)
            require(source.is_relative_to(source_root) and source.is_file(),
                    'Source path escaped its pinned run: ' + relative)
            require(len(expected_hash) == 64 and all(c in '0123456789abcdef' for c in expected_hash),
                    'Invalid source SHA256: ' + relative)
            require(digest(source) == expected_hash, 'Pinned source checksum mismatch: ' + name + '/' + relative)
            destination = snapshot / name / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            require(digest(destination) == expected_hash, 'Snapshot checksum mismatch: ' + relative)
            hashes.append(dict(source=name, relative_path=relative, sha256=expected_hash,
                               source_job_id=expected_job, source_path=str(source)))
        status = read_json(snapshot / name / 'HPC_STATUS.json')
        end = read_json(snapshot / name / 'audit/run_end.json')
        require(str(status.get('analysis_job_id')) == expected_job, 'Source receipt job disagreement: ' + name)
        require(status.get('scheduler_state') == 'COMPLETED' and status.get('scheduler_exit_code') == '0:0',
                'Source scheduler completion was not recorded: ' + name)
        require(status.get('pipeline_exit_code') == 0 and status.get('scientific_scripts_completed') is True,
                'Source scientific completion was not recorded: ' + name)
        require(end.get('pipeline_exit_code') == 0, 'Source runtime did not finish successfully: ' + name)
        require(str(end.get('execution', {}).get('job_id')) == expected_job
                and end.get('execution', {}).get('cluster') == 'puma',
                'Source runtime provenance disagrees with Puma source job: ' + name)
        require((snapshot / name / 'ANALYSES_COMPLETE.txt').read_text().strip(),
                'Empty source completion sentinel: ' + name)
        provenance.append(dict(source=name, source_job_id=expected_job, source_path=str(source_root),
                               scheduler_state=status['scheduler_state'], pipeline_exit_code=0,
                               status=status.get('status', ''), pinned_files=len(pinned),
                               provenance_gate='COMPLETED_PUMA_SOURCE_VERIFIED'))
    support = read_json(snapshot / 'functional/results/modeling_support/SUPPORT_COMPLETION.json')
    require(str(support.get('corrected_source_job')) == str(config['source_runs']['corrected']['job_id']),
            'Functional modeling support used a different corrected metadata source.')
    comparison = read_json(snapshot / 'comparisons/results/COMPARISON_STATUS.json')
    require(str(comparison.get('execution', {}).get('job_id')) == str(config['source_runs']['comparisons']['job_id']),
            'Comparison status job disagreement.')
    require({str(config['source_runs'][n]['job_id']) for n in ('corrected', 'functional')}
            .issubset({str(job) for job in comparison.get('source_jobs', [])}),
            'Comparison provenance does not include both corrected and functional jobs.')
    return snapshot, hashes, provenance


def reverify_sources(snapshot, hashes):
    """Verify both immutable originals and snapshots after all source-data auditing."""
    checked = []
    for item in hashes:
        original = Path(item['source_path'])
        copied = snapshot / item['source'] / item['relative_path']
        original_sha = digest(original)
        copied_sha = digest(copied)
        require(original_sha == item['sha256'] and copied_sha == item['sha256'],
                'Source or snapshot changed during audit: ' + item['source'] + '/' + item['relative_path'])
        checked.append(dict(source=item['source'], relative_path=item['relative_path'],
            expected_sha256=item['sha256'], original_sha256_after_audit=original_sha,
            snapshot_sha256_after_audit=copied_sha, verified_unchanged=True))
    return checked


def metadata_audit(snapshot):
    fields = ('farm', 'collection_date', 'microbial_domain', 'target_read_count', 'observed_ASVs',
              'Shannon', 'primary_included', 'sample_key', 'analysis_sample', 'biological_plot_id',
              'analytic_system', 'treatment', 'descriptive_reference')
    # This is the already-corrected HPC structured metadata table, not the raw workbook.
    # No outcome or soil confirmation is reconstructed from any workbook table.
    table = read_json(snapshot / 'corrected/payload/inputs/workbook_data.json')['tables']['16S samples']
    require(set(fields).issubset(table['columns']), 'Corrected 16S metadata schema disagreement.')
    require(all(len(row) == len(table['columns']) for row in table['rows']), 'Malformed corrected metadata row.')
    metadata = [{key: '' if value is None else str(value) for key, value in zip(table['columns'], row)}
                for row in table['rows']]
    require(metadata, 'Corrected metadata table is empty.')
    require({row['microbial_domain'] for row in metadata} == {'bacteria', 'archaea'}, 'Corrected metadata domain disagreement.')
    full_ids = {}
    for row in metadata:
        key = (row['microbial_domain'], row['analysis_sample'])
        require(row['analysis_sample'] and key not in full_ids, 'Duplicate corrected domain/library identity: ' + repr(key))
        full_ids[key] = row
    for domain in ('bacteria', 'archaea'):
        validated = read_csv(snapshot / ('functional/validated/' + domain + '/sample_metadata.csv'), fields)
        require(validated and all(row['microbial_domain'] == domain for row in validated), 'Domain prediction metadata disagreement.')
        seen = set()
        for row in validated:
            key = (domain, row['analysis_sample'])
            require(key in full_ids and key not in seen, 'Missing or duplicate validated library: ' + repr(key))
            seen.add(key)
            source = full_ids[key]
            require(all(row[field] == source[field] for field in (
                'farm', 'collection_date', 'sample_key', 'analysis_sample', 'biological_plot_id', 'analytic_system', 'treatment')),
                'Validated prediction metadata differs from corrected identity: ' + repr(key))
            require(all(number(row[field]) == number(source[field]) for field in ('target_read_count', 'observed_ASVs', 'Shannon')),
                'Validated recorded alpha/depth data differs from corrected source: ' + repr(key))
        expected = {(domain, r['analysis_sample']) for r in metadata if r['microbial_domain'] == domain
                    and (number(r['target_read_count']) or 0) > 0 and not truth(r['descriptive_reference'])}
        require(seen == expected, 'Prediction metadata is not the corrected positive nonreference projection: ' + domain)
    by_key = defaultdict(list)
    sample_audit, visits, cohorts, events, issues = [], [], [], [], []
    plot_groups = defaultdict(list)
    cohort_groups = defaultdict(list)
    event_groups = defaultdict(list)
    for row in metadata:
        by_key[(row['farm'], row['sample_key'], row['collection_date'])].append(row)
        positive = number(row['target_read_count']) is not None and number(row['target_read_count']) > 0
        reference = truth(row['descriptive_reference'])
        identity_ok = bool(row['farm'] and row['analytic_system'] and row['biological_plot_id']
                           and row['sample_key'] and exact_date(row['collection_date']))
        scopes = ['all_recorded']
        if positive and not reference:
            scopes.append('all_positive_nonreference')
            if truth(row['primary_included']):
                scopes.append('primary_positive_nonreference')
            if truth(row.get('depth_sensitivity_included')):
                scopes.append('depth_sensitivity_positive_nonreference')
        sample_audit.append(dict(domain=row['microbial_domain'], analysis_sample=row['analysis_sample'],
            farm=row['farm'], system=row['analytic_system'], collection_date=row['collection_date'],
            physical_plot=row['biological_plot_id'], sample_key=row['sample_key'], treatment=row['treatment'],
            positive_reads=positive, primary_included=truth(row['primary_included']),
            descriptive_reference=reference, merge_status=row.get('merge_status', ''),
            identity_note=row.get('identity_note', ''), source_plot_group_2025=row.get('source_plot_group_2025', ''),
            scope_membership=joined(scopes), identity_gate='RECORDED_IDENTITY' if identity_ok else 'HELD_IDENTITY',
            read_count_role='QC_ONLY', richness_shannon_note='Unrarefied depth-dependent outcomes; no new values calculated'))
        if not identity_ok:
            issues.append(dict(kind='missing_microbiome_identity', domain=row['microbial_domain'],
                               analysis_sample=row['analysis_sample'], reason='Missing exact date, farm, system, key or physical plot'))
        for scope in scopes:
            ck = (row['farm'], row['microbial_domain'], row['analytic_system'], scope)
            cohort_groups[ck].append(row)
            event_groups[ck + (row['collection_date'], row['treatment'])].append(row)
            if identity_ok:
                plot_groups[ck + (row['biological_plot_id'],)].append(row)
    for key, rows in sorted(plot_groups.items()):
        dates = sorted({row['collection_date'] for row in rows})
        visits.append(dict(zip(('farm', 'domain', 'system', 'scope', 'physical_plot'), key),
            n_distinct_visits=len(dates), first_date=dates[0], last_date=dates[-1],
            span_days=(exact_date(dates[-1]) - exact_date(dates[0])).days,
            collection_dates=joined(dates), treatments=joined(row['treatment'] for row in rows),
            n_library_rows=len(rows), duplicate_plot_date_rows=len(rows) - len(dates),
            modeling_gate='METADATA_SUPPORTED_REVIEW_PENDING', forecast_gate='VALIDATION_NOT_ESTABLISHED'))
    for key, rows in sorted(cohort_groups.items()):
        plot_dates = defaultdict(set)
        for row in rows:
            if row['biological_plot_id'] and exact_date(row['collection_date']):
                plot_dates[row['biological_plot_id']].add(row['collection_date'])
        dated = sorted({d for dates in plot_dates.values() for d in dates})
        cohorts.append(dict(zip(('farm', 'domain', 'system', 'scope'), key), n_library_rows=len(rows),
            n_physical_plots=len(plot_dates), n_collection_events=len(dated), collection_events=joined(dated),
            plots_with_2plus_visits=sum(len(dates) >= 2 for dates in plot_dates.values()),
            plots_with_3plus_visits=sum(len(dates) >= 3 for dates in plot_dates.values()),
            plots_with_4plus_visits=sum(len(dates) >= 4 for dates in plot_dates.values()),
            undated_rows=sum(not exact_date(row['collection_date']) for row in rows),
            interpretation='Cohort date count is not visits per physical plot; no fit or forecast eligibility conferred'))
    for key, rows in sorted(event_groups.items()):
        plots = {row['biological_plot_id'] for row in rows if row['biological_plot_id']}
        events.append(dict(zip(('farm', 'domain', 'system', 'scope', 'collection_date', 'treatment'), key),
            n_library_rows=len(rows), n_physical_plots=len(plots), physical_plots=joined(plots),
            positive_read_libraries=sum((number(row['target_read_count']) or 0) > 0 for row in rows),
            replication_gate='ARM_COUNT_REVIEW_PENDING',
            arm_count_note='One physical plot; no within-arm biological replication' if len(plots) == 1 else 'Physical plot count only; design validity and power not assessed',
            automatic_model_approval=False))
    return metadata, by_key, sample_audit, visits, cohorts, events, issues


def joins_audit(snapshot, by_key, held_ids):
    rows = read_csv(snapshot / 'functional/results/modeling_support/confirmed_join_audit.csv',
                    ('record_id', 'farm', 'collection_date', 'confirmed_sample_key', 'identity_match_status'))
    audit, identities = [], []
    confirmed_by_key = defaultdict(list)
    for row in rows:
        key = (row['farm'], row['confirmed_sample_key'], row['collection_date'])
        compatible = by_key.get(key, []) if row['confirmed_sample_key'] and exact_date(row['collection_date']) else []
        reasons = tokens(row.get('reasons'))
        if row['identity_match_status'] != 'CONFIRMED_IDENTITY_ONLY':
            reasons.append('source_join_not_confirmed')
        if row['record_id'] in held_ids:
            reasons.append('held_by_corrected_source')
        if (number(row.get('pool_member_count')) or 0) > 1:
            reasons.append('composite_not_independent_plot_measurement')
        if not compatible:
            reasons.append('no_exact_confirmed_farm_key_date_match')
        eligible_meta = [m for m in compatible if not truth(m['descriptive_reference'])]
        if compatible and not eligible_meta:
            reasons.append('reference_only')
        domains = [m['microbial_domain'] for m in eligible_meta]
        require(len(domains) == len(set(domains)), 'Duplicate domain identity for confirmed join: ' + row['record_id'])
        confirmed = not reasons
        audit.append(dict(row, verified_domains=joined(domains),
                          audit_gate='CONFIRMED_IDENTITY_ONLY' if confirmed else 'HELD_IDENTITY',
                          audit_reasons=joined(reasons), model_eligibility='NOT_ESTABLISHED'))
        if confirmed:
            confirmed_by_key[key].append(row)
            for meta in eligible_meta:
                identities.append(dict(record_id=row['record_id'], farm=row['farm'], dataset=row['dataset'],
                    collection_date=row['collection_date'], sample_key=row['confirmed_sample_key'],
                    domain=meta['microbial_domain'], analysis_sample=meta['analysis_sample'],
                    physical_plot=meta['biological_plot_id'], analytic_system=meta['analytic_system'],
                    treatment=meta['treatment'], primary_included=truth(meta['primary_included']),
                    positive_target_reads=(number(meta['target_read_count']) or 0) > 0,
                    read_count_role='QC_ONLY',
                    identity_gate='CONFIRMED_IDENTITY_ONLY', integration_gate='METADATA_SUPPORTED_REVIEW_PENDING'))
    # Repeated predictor/read-threshold rows in the corrected output never count as additional samples.
    recorded = read_csv(snapshot / 'corrected/payload/tables/confirmed_soil_microbiome_matches.csv',
                        ('farm', 'system', 'event', 'domain', 'sample_key', 'analysis_sample', 'record_id'))
    distinct = {}
    for row in recorded:
        key = (row['farm'], row['system'], row['event'], row['domain'], row['sample_key'],
               row['analysis_sample'], row['record_id'])
        matches = [m for m in by_key.get((row['farm'], row['sample_key'], row['event']), [])
                   if m['microbial_domain'] == row['domain'] and m['analysis_sample'] == row['analysis_sample']]
        require(len(matches) == 1, 'Explicit corrected soil match has no unique metadata identity: ' + repr(key))
        require(row['record_id'] not in held_ids, 'Held record in explicit corrected soil matches: ' + row['record_id'])
        require((row['farm'], row['sample_key'], row['event']) in confirmed_by_key,
                'Explicit corrected soil match lacks confirmed source join: ' + repr(key))
        if key not in distinct:
            distinct[key] = dict(zip(('farm', 'source_system', 'collection_date', 'domain', 'sample_key',
                                      'analysis_sample', 'record_id'), key),
                physical_plot=matches[0]['biological_plot_id'], analytic_system=matches[0]['analytic_system'],
                source_predictors=set(), source_rows=0)
        distinct[key]['source_predictors'].add(row.get('predictor', ''))
        distinct[key]['source_rows'] += 1
    explicit = []
    for key in sorted(distinct):
        row = distinct[key]
        row['source_predictors'] = joined(row['source_predictors'])
        row['identity_gate'] = 'CONFIRMED_IDENTITY_ONLY'
        row['counting_note'] = 'One identity; repeated predictor/read-threshold rows do not increase biological n'
        explicit.append(row)
    return audit, identities, explicit, confirmed_by_key


def outcome_audit(snapshot, by_key, confirmed_by_key, held_ids):
    observations = read_csv(snapshot / 'corrected/payload/tables/analysis_observations.csv',
        ('farm', 'system', 'dataset', 'event', 'plot', 'treatment', 'variable', 'value', 'unit', 'sample_key', 'record_id'))
    require(observations, 'Corrected outcome observation table is empty.')
    retained = read_csv(snapshot / 'corrected/payload/tables/retained_measurements.csv',
                       ('record_id', 'variable', 'unit', 'method'))
    methods = defaultdict(set)
    for row in retained:
        methods[(row['record_id'], row['variable'].strip(), row['unit'])].add(row['method'].strip())
    # Exact heading/name links only; aliases and chemistry compatibility are not inferred.
    lab_records = read_csv(snapshot / 'corrected/payload/corrections/laboratory_records.csv',
                           ('source_record_key', 'legacy_record_id'))
    legacy = {row['source_record_key']: row['legacy_record_id'] for row in lab_records}
    for row in read_csv(snapshot / 'corrected/payload/corrections/laboratory_measurements.csv',
                        ('source_record_key', 'analyte', 'report_heading', 'unit', 'method')):
        rid = legacy.get(row['source_record_key'])
        if rid:
            for field in ('analyte', 'report_heading'):
                methods[(rid, row[field].strip(), row['unit'])].add(row['method'].strip())
    groups, plot_groups, event_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    row_audit = []
    for index, source in enumerate(observations, 1):
        row = dict(source)
        rids = tokens(row['record_id'])
        require(not (set(rids) & held_ids), 'Held record was promoted into outcome observations: ' + row['record_id'])
        require(number(row['value']) is not None, 'Nonfinite corrected numeric observation at row ' + str(index))
        recorded_methods = set()
        for rid in rids:
            recorded_methods.update(methods.get((rid, row['variable'].strip(), row['unit']), set()))
        missing = {method for method in recorded_methods if missing_method_text(method)}
        known = recorded_methods - missing
        if len(known) == 1 and not missing:
            assay = next(iter(known))
            assay_gate = 'RECORDED_ASSAY; CROSS_EVENT_COMPATIBILITY_UNREVIEWED'
        elif known:
            assay = 'UNRESOLVED_MULTIPLE_OR_PARTIAL_METHODS:' + joined(known)
            assay_gate = 'HELD_ASSAY_COMPATIBILITY'
        else:
            assay = 'UNRECORDED_ASSAY'
            assay_gate = 'HELD_ASSAY_COMPATIBILITY'
        key = (row['farm'], row['system'], row['dataset'], row['variable'], row['unit'], assay)
        oid = identifier(key)
        date_ok = bool(exact_date(row['event']))
        join_key = (row['farm'], row['sample_key'], row['event'])
        exact_confirmed = bool(row['sample_key'] and join_key in confirmed_by_key)
        micro = by_key.get(join_key, []) if exact_confirmed else []
        physical = {m['biological_plot_id'] for m in micro if m['biological_plot_id']}
        systems = {m['analytic_system'] for m in micro}
        require(len(physical) <= 1 and len(systems) <= 1, 'Conflicting physical identity for corrected outcome: ' + repr(join_key))
        row['_physical_plot'] = next(iter(physical)) if physical else row['plot']
        row['_date_confirmed'] = exact_confirmed
        row['_date_ok'] = date_ok
        row['_assay_gate'] = assay_gate
        row['_outcome_id'] = oid
        groups[key].append(row)
        if date_ok and row['_physical_plot']:
            plot_groups[(oid, row['_physical_plot'])].append(row)
        event_groups[(oid, row['event'], row['treatment'])].append(row)
        row_audit.append(dict(observation_row=index, outcome_id=oid, farm=row['farm'], source_system=row['system'],
            dataset=row['dataset'], outcome=row['variable'], unit=row['unit'], assay=assay,
            source_event=row['event'], source_plot=row['plot'], physical_plot=row['_physical_plot'],
            physical_plot_basis='Confirmed soil-microbiome key' if physical else 'Recorded corrected outcome plot; cross-assay identity not inferred',
            plot_identity_basis=measured_plot_basis([row]) if row['_physical_plot'] else 'NO_COUNTABLE_PLOT_IDENTITIES',
            treatment=row['treatment'], sample_key=row['sample_key'], source_id=row['source_id'], record_id=row['record_id'],
            date_gate='CONFIRMED_KEY_DATE' if exact_confirmed else ('RECORDED_EXACT_DATE_REVIEW_PENDING' if date_ok else 'HELD_EXACT_DATE'),
            linked_method_texts_as_recorded=joined(recorded_methods),
            explicit_missing_method_texts=joined(missing),
            explicit_missing_method_detected=bool(missing),
            assay_gate=assay_gate, censored=truth(row.get('censored')), numeric_value_recalculated=False))
    inventory = []
    for key, rows in sorted(groups.items()):
        dated = [row for row in rows if row['_date_ok'] and row['_physical_plot']]
        confirmed = [row for row in dated if row['_date_confirmed']]
        dates = sorted({row['event'] for row in dated})
        plots = defaultdict(set)
        for row in dated:
            plots[row['_physical_plot']].add(row['event'])
        inventory.append(dict(zip(('farm', 'system', 'dataset', 'outcome', 'unit', 'assay'), key),
            outcome_id=rows[0]['_outcome_id'], n_source_observations=len(rows), n_physical_plots=len(plots),
            plot_count_basis=measured_plot_basis(dated),
            plot_identity_basis=measured_plot_basis(rows),
            n_recorded_exact_dates=len(dates), recorded_exact_dates=joined(dates),
            first_recorded_date=dates[0] if dates else '', last_recorded_date=dates[-1] if dates else '',
            span_days=(exact_date(dates[-1]) - exact_date(dates[0])).days if dates else '',
            n_confirmed_key_date_observations=len(confirmed),
            n_confirmed_key_dates=len({row['event'] for row in confirmed}),
            undated_or_year_only_events=joined(row['event'] for row in rows if not row['_date_ok']),
            plots_with_2plus_visits=sum(len(dd) >= 2 for dd in plots.values()),
            plots_with_3plus_visits=sum(len(dd) >= 3 for dd in plots.values()),
            plots_with_4plus_visits=sum(len(dd) >= 4 for dd in plots.values()),
            duplicate_plot_event_rows=sum(v - 1 for v in Counter((r['_physical_plot'], r['event']) for r in dated).values()),
            censored_rows=sum(truth(row.get('censored')) for row in rows),
            source_ids=joined(row['source_id'] for row in rows), source_record_ids=joined(rid for row in rows for rid in tokens(row['record_id'])),
            assay_gate=rows[0]['_assay_gate'], unit_gate='NO_UNIT_CONVERSION; METHOD_EQUIVALENCE_NOT_INFERRED',
            modeling_gate='METADATA_SUPPORTED_REVIEW_PENDING' if confirmed else 'HELD_IDENTITY',
            forecast_gate='VALIDATION_NOT_ESTABLISHED'))
    visits = []
    for (oid, plot), rows in sorted(plot_groups.items()):
        dates = sorted({row['event'] for row in rows})
        visits.append(dict(outcome_id=oid, farm=rows[0]['farm'], system=rows[0]['system'], dataset=rows[0]['dataset'],
            outcome=rows[0]['variable'], unit=rows[0]['unit'], physical_plot=plot,
            n_distinct_recorded_visits=len(dates), n_confirmed_key_visits=len({row['event'] for row in rows if row['_date_confirmed']}),
            collection_dates=joined(dates), first_date=dates[0], last_date=dates[-1],
            span_days=(exact_date(dates[-1]) - exact_date(dates[0])).days,
            treatments=joined(row['treatment'] for row in rows), n_source_observations=len(rows),
            plot_count_basis=measured_plot_basis(rows), plot_identity_basis=measured_plot_basis(rows),
            duplicate_plot_event_rows=len(rows) - len(dates), identity_and_method_review_pending=True))
    events = []
    for (oid, event, treatment), rows in sorted(event_groups.items()):
        events.append(dict(outcome_id=oid, farm=rows[0]['farm'], system=rows[0]['system'], dataset=rows[0]['dataset'],
            outcome=rows[0]['variable'], unit=rows[0]['unit'], event=event, treatment=treatment,
            n_source_observations=len(rows), n_recorded_physical_plots=len({r['_physical_plot'] for r in rows if r['_physical_plot']}),
            n_confirmed_key_physical_plots=len({r['_physical_plot'] for r in rows if r['_date_confirmed']}),
            plot_count_basis=measured_plot_basis(rows), plot_identity_basis=measured_plot_basis(rows),
            exact_date_recorded=bool(exact_date(event)), automatic_model_approval=False))
    return groups, inventory, visits, events, row_audit


def source_catalogs(snapshot, observation_audit, dispositions, held):
    """Catalog original source rows, including holds; neither values nor identities are created."""
    used_records = {rid for row in observation_audit for rid in tokens(row['record_id'])}
    exact_used = set()
    for row in observation_audit:
        for rid in tokens(row['record_id']):
            exact_used.add((rid, row['outcome'].strip(), row['unit']))
    disposition_by_id = {}
    held_by_id = {}
    held_ids = frozenset(row['record_id'] for row in held)
    for row in dispositions:
        disposition_by_id.setdefault(row['record_id'], []).append(row)
    for row in held:
        held_by_id.setdefault(row['record_id'], []).append(row)
    measurements = []
    for index, source in enumerate(read_csv(snapshot / 'corrected/payload/tables/retained_measurements.csv'), 1):
        rid = source['record_id']
        exact = (rid, source['variable'].strip(), source['unit']) in exact_used
        measurements.append(dict(source, audit_source_row=index,
            audit_direct_record_reference=rid in used_records,
            audit_exact_record_variable_unit_link=exact,
            audit_held_by_corrected_source=rid in held_ids,
            audit_held_reasons=joined(r['reason'] for r in held_by_id.get(rid, ())),
            audit_source_dispositions=joined(r['status'] for r in disposition_by_id.get(rid, ())),
            audit_disposition_reasons=joined(r['reason'] for r in disposition_by_id.get(rid, ())),
            audit_eligibility='NOT_INFERRED_FROM_SOURCE_CATALOG',
            audit_lineage_note='Literal source row; lack of a direct record/variable/unit link does not disprove lineage through prior laboratory plot means',
            audit_values_recalculated=False, audit_collection_dates_created=False))
    records = []
    lab_to_legacy = {}
    for index, source in enumerate(read_csv(snapshot / 'corrected/payload/corrections/laboratory_records.csv'), 1):
        rid = source.get('legacy_record_id', '')
        lab_to_legacy[source['source_record_key']] = rid
        records.append(dict(source, audit_source_row=index, audit_direct_record_reference=rid in used_records,
            audit_held_by_corrected_source=rid in held_ids,
            audit_held_reasons=joined(r['reason'] for r in held_by_id.get(rid, ())),
            audit_source_dispositions=joined(r['status'] for r in disposition_by_id.get(rid, ())),
            audit_eligibility='NOT_INFERRED_FROM_SOURCE_CATALOG', audit_collection_dates_created=False))
    laboratory = []
    for index, source in enumerate(read_csv(snapshot / 'corrected/payload/corrections/laboratory_measurements.csv'), 1):
        rid = lab_to_legacy.get(source['source_record_key'], '')
        exact = any((rid, source[field].strip(), source['unit']) in exact_used for field in ('analyte', 'report_heading'))
        laboratory.append(dict(source, audit_source_row=index, audit_legacy_record_id=rid,
            audit_direct_record_reference=rid in used_records, audit_exact_record_heading_unit_link=exact,
            audit_held_by_corrected_source=rid in held_ids,
            audit_eligibility='NOT_INFERRED_FROM_SOURCE_CATALOG', audit_values_recalculated=False))
    require(frozenset(held_by_id) == held_ids,
            'Source hold lookup membership changed while producing catalogs.')
    for catalog in (measurements, records):
        for row in catalog:
            rid = row['record_id'] if 'record_id' in row else row.get('legacy_record_id', '')
            require(row['audit_held_by_corrected_source'] == (rid in held_ids),
                    'Catalog hold flag disagrees with authoritative corrected held IDs: ' + rid)
    for row in laboratory:
        require(row['audit_held_by_corrected_source'] == (row['audit_legacy_record_id'] in held_ids),
                'Laboratory measurement hold flag disagrees with authoritative corrected held IDs.')
    return measurements, records, laboratory


def audit_label_integrity_checks(measurements, records, laboratory, held, observation_audit):
    """Real-input regression evidence, evaluated only after the Puma guard."""
    authoritative_held_ids = frozenset(row['record_id'] for row in held)
    checks = []
    for name, rows, field in (
        ('retained_measurement_catalog', measurements, 'record_id'),
        ('laboratory_record_catalog', records, 'legacy_record_id'),
        ('laboratory_measurement_catalog', laboratory, 'audit_legacy_record_id')):
        require(all(row['audit_held_by_corrected_source'] == (row.get(field, '') in authoritative_held_ids)
                    for row in rows),
                'Audit-label consistency failed for ' + name)
        checks.append(dict(check='v2_authoritative_hold_membership_' + name, status='PASS',
            rows_reviewed=len(rows),
            detail='Every held flag exactly matches immutable IDs from corrected held_records.csv; no lookup-derived membership'))
    explicit_missing_rows = [row for row in observation_audit if row['explicit_missing_method_detected']]
    require(all(row['assay_gate'] == 'HELD_ASSAY_COMPATIBILITY' for row in explicit_missing_rows),
            'Explicit missing method text was promoted to a known assay.')
    require(all(not missing_method_text(row['assay']) for row in observation_audit),
            'A missing method placeholder remains an outcome assay identifier.')
    checks.append(dict(check='v2_explicit_missing_methods_remain_unresolved', status='PASS',
        rows_reviewed=len(explicit_missing_rows),
        detail='Explicit missing method text cannot receive RECORDED_ASSAY; literal methods retained in source catalogs'))
    return checks


def evidence_requirements():
    requirements = [
        ('independent_near_term_validation', 'Empirical prospective or held-out actual-occasion predictions with training-only preprocessing and uncertainty assessment'),
        ('long_horizon_external_calibration', 'Independent calibration evidence at an appropriate horizon and under comparable farming/soil conditions'),
        ('future_weather_drivers', 'Observed weather provenance, appropriate future driver series, and an explicit uncertainty strategy'),
        ('management_and_treatment_history', 'Verified dates and intensities of tillage, irrigation, fertilizer, manure, cover crops, grazing and establishment'),
        ('measured_starting_state', 'Compatible baseline soil/crop/physical measurements at the forecast origin'),
        ('soil_stock_conversion', 'Verified depth, bulk density, coarse fragments, units and carbon/nitrogen assay before any concentration-to-stock conversion'),
        ('process_model_parameters', 'Documented process relationships, calibrated parameters and parameter uncertainty with identifiable units'),
        ('functional_prediction_quality', 'Individual feature predictions, reference-domain agreement, represented reads and NSTI review; measured rates required for process claims'),
        ('sampling_design_and_covariance', 'Independent physical units, treatment assignment, spatial/field confounding, repeated sampling and missingness review'),
        ('cross_assay_compatibility', 'Laboratory methods, specimen/depth/pooling identity, collection events and unit compatibility established without name-based pooling'),
        ('cross_farm_transportability', 'Independent evidence before combining farms, crops, cultivars, fields or microbial domains'),
        ('NIFA_NRCS_reuse', 'Resolve sample and sequence reuse before claiming independent evidence'),
        ('forecast_target_and_horizon', 'Prespecified scientifically meaningful target, origin, requested horizon, evaluation criterion and reporting of scenario assumptions')]
    return [dict(evidence_item=item, recorded_status='NOT_ASSESSED_IN_THIS_RUN',
                 required_review_next=description, absence_inferred=False,
                 numerical_model_approved=False, ten_year_forecast_approved=False)
            for item, description in requirements]


def outcome_domain_join_support(groups, by_key):
    support = []
    for key, rows in sorted(groups.items()):
        joined_groups = defaultdict(list)
        for row in rows:
            if not row['_date_confirmed']:
                continue
            for meta in by_key.get((row['farm'], row['sample_key'], row['event']), []):
                if truth(meta['descriptive_reference']):
                    continue
                joined_groups[(meta['microbial_domain'], meta['analytic_system'])].append((row, meta))
        for (domain, system), pairs in sorted(joined_groups.items()):
            identities = {(m['sample_key'], m['analysis_sample']) for _, m in pairs}
            positive = {(m['sample_key'], m['analysis_sample']) for _, m in pairs if (number(m['target_read_count']) or 0) > 0}
            primary = {(m['sample_key'], m['analysis_sample']) for _, m in pairs
                       if (number(m['target_read_count']) or 0) > 0 and truth(m['primary_included'])}
            support.append(dict(zip(('farm', 'source_system', 'dataset', 'outcome', 'unit', 'assay'), key),
                outcome_id=identifier(key), domain=domain, analytic_system=system,
                n_unique_confirmed_library_date_identities=len(identities),
                n_positive_read_identities=len(positive), n_primary_positive_identities=len(primary),
                n_confirmed_physical_plots=len({m['biological_plot_id'] for _, m in pairs}),
                confirmed_collection_dates=joined(m['collection_date'] for _, m in pairs),
                n_collection_dates=len({m['collection_date'] for _, m in pairs}),
                gate='METADATA_SUPPORTED_REVIEW_PENDING',
                interpretation='Exact recorded outcome/soil-to-domain identity support only; method/depth/design and integration eligibility unassessed',
                automatic_model_approval=False))
    return support


def requested_horizon_calendar(groups, micro_cohorts):
    """Calendar annotation only; a requested endpoint is not a valid extrapolation."""
    rows = []
    def append(cid, track, details, dates):
        dates = sorted(set(dates))
        origin = exact_date(dates[-1]) if dates else None
        if origin:
            try:
                endpoint = origin.replace(year=origin.year + 10)
                convention = 'Same month/day plus ten calendar years'
            except ValueError:
                endpoint = origin.replace(year=origin.year + 10, day=28)
                convention = 'Leap-day origin maps to 28 February in non-leap endpoint year'
        else:
            endpoint, convention = None, 'No confirmed origin assigned'
        rows.append(dict(details, candidate_id=cid, track=track,
            origin_date=origin.isoformat() if origin else '',
            requested_endpoint=endpoint.isoformat() if endpoint else '', requested_horizon_years=10,
            calendar_convention=convention,
            gate='CONCEPTUAL_ONLY' if origin else 'HELD_IDENTITY',
            interpretation='Requested calendar endpoint only; no fitted model, simulated trajectory, accuracy claim or forecast authorization',
            ten_year_forecast_approved=False))
    for key, rr in groups.items():
        append(identifier(key), 'measured_outcome', dict(zip(('farm', 'system', 'dataset', 'outcome', 'unit', 'assay'), key)),
               [r['event'] for r in rr if r['_date_confirmed']])
    for row in micro_cohorts:
        if row['scope'] == 'primary_positive_nonreference':
            key = (row['farm'], row['domain'], row['system'])
            append(identifier(('microbiome_origin',) + key), 'microbiome_scope',
                   dict(zip(('farm', 'domain', 'system'), key)), tokens(row['collection_events']))
    return sorted(rows, key=lambda row: row['candidate_id'])


def functional_catalog(snapshot, metadata):
    panel = read_csv(snapshot / 'comparisons/results/FEATURE_PANEL.csv', ('family', 'function', 'feature_name'))
    keys = {(row['family'], row['function']) for row in panel}
    require(len(keys) == len(panel), 'Duplicate prespecified functional identifier.')
    require({row['family'] for row in panel} <= {'EC', 'METACYC'}, 'Unexpected targeted family.')
    present = defaultdict(set)
    for row in read_csv(snapshot / 'comparisons/results/feature_summaries.csv',
                        ('domain', 'variant', 'farm', 'analytic_system', 'family', 'function', 'in_original_table'), project=True):
        key = (row['domain'], row['variant'], row['farm'], row['analytic_system'], row['family'], row['function'])
        require((row['family'], row['function']) in keys, 'Unprespecified targeted summary feature.')
        present[key].add(truth(row['in_original_table']))
    absent = set()
    # No p-value, effect, ranking, or significance field is used to choose features.
    for row in read_csv(snapshot / 'comparisons/results/targeted_tests.csv',
                        ('domain', 'variant', 'farm', 'analytic_system', 'family', 'function', 'status', 'reason'), project=True):
        if 'function absent' in row['reason'].lower():
            absent.add((row['domain'], row['variant'], row['farm'], row['analytic_system'], row['family'], row['function']))
    eligibility = read_csv(snapshot / 'comparisons/results/sample_eligibility.csv',
        ('domain', 'variant', 'family', 'analysis_sample', 'farm', 'analytic_system', 'collection_date',
         'biological_plot_id', 'treatment', 'primary_included', 'nonzero_profile', 'hold_reason'))
    meta_ids = {(row['microbial_domain'], row['analysis_sample']): row for row in metadata}
    cohorts = set()
    support = defaultdict(list)
    for row in eligibility:
        key = (row['domain'], row['analysis_sample'])
        require(key in meta_ids, 'Comparison library absent from locked source metadata: ' + repr(key))
        meta = meta_ids[key]
        require(all(row[field] == meta[mfield] for field, mfield in (
            ('farm', 'farm'), ('analytic_system', 'analytic_system'), ('collection_date', 'collection_date'),
            ('biological_plot_id', 'biological_plot_id'), ('treatment', 'treatment'))),
            'Comparison metadata identity changed: ' + repr(key))
        ck = (row['domain'], row['variant'], row['farm'], row['analytic_system'], row['family'])
        cohorts.add(ck)
        if truth(row['primary_included']) and truth(row['nonzero_profile']) and not row['hold_reason']:
            support[ck].append(row)
    catalog = []
    for ck in sorted(cohorts):
        for feature in panel:
            if feature['family'] != ck[-1]:
                continue
            key = ck + (feature['function'],)
            statuses = present.get(key, set())
            require(len(statuses) <= 1, 'Conflicting recorded feature-presence flags: ' + repr(key))
            if key in absent:
                require(statuses != {True}, 'Recorded absent-ID status contradicts presence flag: ' + repr(key))
                gate = 'ABSENT_PRESPECIFIED_ID_UNTESTED'
            elif statuses == {True}:
                gate = 'RECORDED_PRESENT_REVIEW_PENDING'
            elif statuses == {False}:
                gate = 'ABSENT_PRESPECIFIED_ID_UNTESTED'
            else:
                gate = 'PRESENCE_NOT_RECORDED_HELD'
            rows = support[ck]
            catalog.append(dict(zip(('domain', 'variant', 'farm', 'system', 'family'), ck),
                function=feature['function'], feature_name=feature['feature_name'],
                biological_topic=feature.get('biological_topic', ''), annotation_source=feature.get('annotation_source', ''),
                identifier_gate=gate, n_primary_profile_libraries=len(rows),
                n_primary_physical_plots=len({r['biological_plot_id'] for r in rows}),
                collection_dates=joined(r['collection_date'] for r in rows),
                selection_rule='Fixed source FEATURE_PANEL; no p-value or effect-based selection',
                scientific_role='Predicted functional potential; not measured activity or absolute abundance',
                individual_feature_response_values_audited=False,
                response_variance_status='NOT_ASSESSED', model_design_and_skill_status='NOT_ASSESSED',
                later_input_requirement='Individual predicted feature matrices and training-only preprocessing needed before any fit',
                modeling_gate='METADATA_SUPPORTED_REVIEW_PENDING' if gate == 'RECORDED_PRESENT_REVIEW_PENDING' else 'DESCRIPTIVE_ONLY',
                forecast_gate='VALIDATION_NOT_ESTABLISHED'))
    events = []
    event_groups = defaultdict(list)
    for ck, rows in support.items():
        for row in rows:
            event_groups[ck + (row['collection_date'], row['treatment'])].append(row)
    for key, rows in sorted(event_groups.items()):
        events.append(dict(zip(('domain', 'variant', 'farm', 'system', 'family', 'collection_date', 'treatment'), key),
            n_primary_profile_libraries=len(rows), n_primary_physical_plots=len({r['biological_plot_id'] for r in rows}),
            automatic_model_approval=False))
    return catalog, eligibility, events, support


def split_designs(outcome_groups, metadata, functional_support):
    """Enumerate actual-date cutoffs only; never fit or endorse a validation design."""
    groups = []
    for key, rows in outcome_groups.items():
        groups.append((identifier(key), 'measured_outcome', dict(zip(('farm', 'system', 'dataset', 'outcome', 'unit', 'assay'), key)),
            [dict(event=r['event'], plot=r['_physical_plot'], treatment=r['treatment'],
                  identity_confirmed=r['_date_confirmed']) for r in rows if r['_date_ok'] and r['_physical_plot']]))
    micro_groups = defaultdict(list)
    for row in metadata:
        if not truth(row['primary_included']) or truth(row['descriptive_reference']) or (number(row['target_read_count']) or 0) <= 0:
            continue
        if exact_date(row['collection_date']) and row['biological_plot_id']:
            micro_groups[(row['farm'], row['microbial_domain'], row['analytic_system'])].append(row)
    for key, rows in micro_groups.items():
        for outcome in ('Shannon', 'observed_ASVs', 'target_read_count', 'community_structure'):
            details = dict(zip(('farm', 'domain', 'system'), key), outcome=outcome,
                           outcome_role='QC_ONLY' if outcome == 'target_read_count' else 'PRESPECIFIED_CANDIDATE')
            groups.append((identifier(('microbiome',) + key + (outcome,)), 'microbiome', details,
                [dict(event=r['collection_date'], plot=r['biological_plot_id'], treatment=r['treatment'], identity_confirmed=True) for r in rows]))
    for key, rows in functional_support.items():
        if key[-1] not in {'EC', 'METACYC'}:
            continue  # KO profile metadata can be audited, but no prespecified KO target panel exists.
        details = dict(zip(('domain', 'variant', 'farm', 'system', 'family'), key), outcome='fixed_functional_panel')
        groups.append((identifier(('functional',) + key), 'functional_panel', details,
            [dict(event=r['collection_date'], plot=r['biological_plot_id'], treatment=r['treatment'], identity_confirmed=True) for r in rows]))
    designs = []
    for cid, track, details, rows in sorted(groups, key=lambda item: item[0]):
        dates = sorted({row['event'] for row in rows})
        if len(dates) < 2:
            reason = 'Fewer than two recorded exact collection events'
            basis = plot_count_basis(rows)
            if basis == 'INCLUDES_UNCONFIRMED_RECORDED_PLOT_IDENTITIES':
                reason += '; Plot counts include unconfirmed recorded plot identities and do not establish confirmed biological replication'
            elif basis == 'NO_COUNTABLE_PLOT_IDENTITIES':
                reason += '; No countable dated plot identities available for a time split'
            designs.append(dict(details, candidate_id=cid, track=track, design='NO_OBSERVED_TIME_SPLIT',
                training_dates='', held_out_date='', n_training_physical_plots=0,
                n_held_out_physical_plots=len({r['plot'] for r in rows}), n_shared_physical_plots=0,
                gate='DESCRIPTIVE_ONLY', reasons=reason,
                plot_count_basis=plot_count_basis(rows), plot_identity_basis=plot_count_basis(rows),
                numerical_model_approved=False, ten_year_forecast_approved=False))
            continue
        for cutoff in dates[1:]:
            train = [row for row in rows if row['event'] < cutoff]
            held = [row for row in rows if row['event'] == cutoff]
            training_plots = {row['plot'] for row in train}
            held_plots = {row['plot'] for row in held}
            train_arms = {arm: len({r['plot'] for r in train if r['treatment'] == arm}) for arm in sorted({r['treatment'] for r in train})}
            held_arms = {arm: len({r['plot'] for r in held if r['treatment'] == arm}) for arm in sorted({r['treatment'] for r in held})}
            reasons = ['Candidate dates only; no held-out predictions or validation performance computed',
                       'Fit preprocessing, depth adjustment, feature reduction and tuning on training occasions only',
                       'Same-plot future-occasion validation is not new-plot or new-farm validation',
                       'Method compatibility, treatment/date confounding and covariance design require review']
            if not all(row['identity_confirmed'] for row in train + held):
                reasons.append('Some recorded exact dates lack a confirmed sample/date join')
                reasons.append('Plot counts include unconfirmed recorded plot identities; apparent new plot counts are not confirmed new biological plots')
            if set(held_arms) - set(train_arms):
                reasons.append('Held-out treatment arm absent from training')
            if any(n < 2 for n in held_arms.values()):
                reasons.append('At least one held-out arm has fewer than two physical plots')
            if details.get('system') == 'OFR_2025_Field':
                reasons.append('Oatman 2025 treatment is confounded with field side')
            if details.get('outcome_role') == 'QC_ONLY':
                reasons.append('Read count is a sequencing-depth diagnostic only')
            designs.append(dict(details, candidate_id=cid, track=track, design='ROLLING_ORIGIN_NEXT_OBSERVED_OCCASION',
                training_dates=joined(r['event'] for r in train), held_out_date=cutoff,
                n_training_physical_plots=len(training_plots), n_held_out_physical_plots=len(held_plots),
                n_shared_physical_plots=len(training_plots & held_plots),
                n_new_held_out_physical_plots=len(held_plots - training_plots),
                plot_count_basis=plot_count_basis(train + held),
                plot_identity_basis=plot_count_basis(train + held),
                training_plot_count_basis=plot_count_basis(train), held_out_plot_count_basis=plot_count_basis(held),
                training_plots_by_arm=compact(train_arms), held_out_plots_by_arm=compact(held_arms),
                gate='VALIDATION_NOT_ESTABLISHED', reasons='; '.join(reasons),
                numerical_model_approved=False, ten_year_forecast_approved=False))
    return designs


def safeguards(metadata, observation_audit, confirmed_identities):
    checks = []
    for domain in ('bacteria', 'archaea'):
        rows = [r for r in metadata if r['microbial_domain'] == domain and r['farm'] == 'Page Springs'
                and r['analytic_system'] == 'Grape_PetiteSirah' and r['collection_date'] == '2025-10-10'
                and r['treatment'] == 'Control_fertigation_only']
        require(len(rows) == 1 and rows[0]['sample_key'] == 'PS_R39_T1|2025-10-10',
                'Petite Sirah 2025 T1 source must remain one merged R39 plot/library per domain.')
        checks.append(dict(check='Petite_Sirah_2025_T1_' + domain, status='PASS',
            detail='One recorded R39 library and physical plot; merged counts preserved; no technical-replicate splitting'))
    require(not any(r['farm'] == 'Page Springs' and r['analytic_system'] == 'Grape_Grenache'
                    and r['collection_date'].startswith('2025-') for r in metadata),
            'Unexpected Grenache 2025 metadata; fixed missing-data safeguard requires review.')
    checks.append(dict(check='Grenache_2025_absence', status='PASS', detail='No supplied 2025 microbiome row; no imputation or cross-cultivar borrowing'))
    old = {r['biological_plot_id'] for r in metadata if r['analytic_system'] == 'OFR_2023_2024_Field'}
    new = {r['biological_plot_id'] for r in metadata if r['analytic_system'] == 'OFR_2025_Field'}
    require(not old.intersection(new), 'Oatman 2025 field was stitched to older physical plots.')
    checks.append(dict(check='Oatman_separate_fields', status='PASS', detail='2023–2024 and 2025 physical plot identities do not overlap'))
    for row in metadata:
        if row['analytic_system'] == 'OFR_2025_Field':
            expected = {'Control': 'EAST', 'Cover_crop_only': 'WEST'}.get(row['treatment'])
            require(expected and row.get('source_plot_group_2025') == expected,
                    'Oatman 2025 recorded treatment/side membership disagreement.')
    checks.append(dict(check='Oatman_2025_side_confound', status='PASS', detail='Control/EAST and cover crop/WEST remain descriptive; treatment effect cannot be separated from side'))
    for excluded in ('R0206', 'R0220'):
        require(not any(excluded in tokens(r['record_id']) for r in observation_audit),
                'Excluded 2025 soil record re-entered used observations: ' + excluded)
        require(not any(r['record_id'] == excluded for r in confirmed_identities),
                'Excluded 2025 soil record re-entered confirmed joins: ' + excluded)
        checks.append(dict(check='Excluded_soil_' + excluded, status='PASS', detail='No used observation or confirmed join invented'))
    return checks


def gate_register(inventory, cohorts, catalog):
    register = []
    def add(cid, track, details, gate, status, reason):
        register.append(dict(details, candidate_id=cid, track=track, gate=gate, status=status, reason=reason,
                             numerical_model_approved=False, ten_year_forecast_approved=False))
    for row in inventory:
        details = {k: row[k] for k in ('farm', 'system', 'dataset', 'outcome', 'unit', 'assay')}
        add(row['outcome_id'], 'measured_outcome', details, 'identity_temporal_support', row['modeling_gate'],
            'Recorded exact dates and visits are inventoried; collection identity and physical sampling units remain explicit')
        add(row['outcome_id'], 'measured_outcome', details, 'assay_compatibility',
            'METADATA_SUPPORTED_REVIEW_PENDING' if row['assay_gate'].startswith('RECORDED_ASSAY') else 'HELD_IDENTITY',
            'Units and assays remain separate; no normalization or method-equivalence inference')
        add(row['outcome_id'], 'measured_outcome', details, 'near_term_validation', 'VALIDATION_NOT_ESTABLISHED',
            'No model fit or performance estimate; candidate time splits do not establish validity')
        add(row['outcome_id'], 'measured_outcome', details, 'ten_year_calibration', 'EXTERNAL_CALIBRATION_NOT_ESTABLISHED',
            'No external calibration, process drivers, measured starting state or validated ten-year scenario')
    for row in cohorts:
        if row['scope'] != 'primary_positive_nonreference':
            continue
        details = {k: row[k] for k in ('farm', 'domain', 'system')}
        for outcome in ('Shannon', 'observed_ASVs', 'target_read_count', 'community_structure'):
            cid = identifier(('microbiome', row['farm'], row['domain'], row['system'], outcome))
            dd = dict(details, outcome=outcome, outcome_role='QC_ONLY' if outcome == 'target_read_count' else 'PRESPECIFIED_CANDIDATE')
            gate = 'QC_ONLY' if outcome == 'target_read_count' else ('DESCRIPTIVE_ONLY' if row['system'] == 'OFR_2025_Field' else 'METADATA_SUPPORTED_REVIEW_PENDING')
            add(cid, 'microbiome', dd, 'repeated_plot_model', gate,
                'Visits do not establish replication, identifiability or power; unrarefied alpha outcomes require depth review')
            add(cid, 'microbiome', dd, 'near_term_validation', 'VALIDATION_NOT_ESTABLISHED',
                'No empirical held-out predictions; new occasions must remain out of training and tuning')
            add(cid, 'microbiome', dd, 'ten_year_calibration', 'EXTERNAL_CALIBRATION_NOT_ESTABLISHED',
                'Microbiome support alone does not validate a ten-year agricultural or ecosystem forecast')
    for row in catalog:
        details = {k: row[k] for k in ('domain', 'variant', 'farm', 'system', 'family', 'function', 'feature_name')}
        cid = identifier(('functional',) + tuple(details.values()))
        add(cid, 'functional_feature', details, 'prespecified_identifier', row['identifier_gate'],
            'Fixed panel retained in full; absent IDs explicitly untested; no p-value selection')
        add(cid, 'functional_feature', details, 'soil_function_integration',
            'DESCRIPTIVE_ONLY' if row['identifier_gate'] != 'RECORDED_PRESENT_REVIEW_PENDING' else 'METADATA_SUPPORTED_REVIEW_PENDING',
            'Requires exact confirmed soil/16S identity, compatible assay, read-depth and reference-quality review; predicted potential is not activity')
        add(cid, 'functional_feature', details, 'ten_year_forecast', 'EXTERNAL_CALIBRATION_NOT_ESTABLISHED',
            'Relative PICRUSt potential cannot supply measured rates or a calibrated process model')
    for gate, status, reason in (
        ('cross_farm_pooling', 'HELD_IDENTITY', 'Farm, field, crop, cultivar and domain remain separate unless a later protocol justifies pooling'),
        ('NIFA_NRCS_independent_replication', 'HELD_IDENTITY', 'Sample or sequence reuse remains unresolved; no independent-evidence claim'),
        ('Oatman_2025_treatment_effect', 'DESCRIPTIVE_ONLY', 'Treatment and EAST/WEST field side are confounded; 2025 is a distinct field'),
        ('Petite_Sirah_2025_T1_replication', 'HELD_REPLICATION', 'One merged R39 biological plot per domain; technical libraries do not supply replication'),
        ('Grenache_2025', 'HELD_IDENTITY', 'No supplied 2025 records; no interpolation from Petite Sirah'),
        ('composite_reference_records', 'DESCRIPTIVE_ONLY', 'One composite is one sample; reference records are not replicated treatment groups'),
        ('integration_model', 'CONCEPTUAL_ONLY', 'No integration fit, forecast, causal effect or automatic model eligibility is established')):
        add('GLOBAL_' + gate, 'global', {}, gate, status, reason)
    return register


def main():
    require(len(sys.argv) == 2, 'Usage: audit_feasibility.py RUN_PATH')
    run = Path(sys.argv[1]).resolve(strict=True)
    require(not (run / 'ANALYSES_COMPLETE.txt').exists(), 'Run already contains a completion sentinel.')
    config = read_json(run / 'CONFIG.json')
    require(config.get('package') == 'NRCS_ten_year_feasibility_HPC_v2', 'Unexpected feasibility package.')
    plan_path = run / config.get('outcome_plan', 'OUTCOME_PLAN.csv')
    require(plan_path.resolve().is_relative_to(run), 'Outcome plan escaped run directory.')
    plan = read_csv(plan_path, ('candidate_id', 'track', 'outcome_identifier', 'status'))
    require(plan, 'Prespecified outcome plan is empty.')
    snapshot, hashes, provenance = locked_sources(run, config)
    out = run / 'results'
    out.mkdir(exist_ok=False)
    metadata, by_key, sample_audit, micro_visits, micro_cohorts, micro_events, issues = metadata_audit(snapshot)
    held = read_csv(snapshot / 'corrected/payload/tables/held_records.csv', ('record_id', 'dataset', 'reason'))
    dispositions = read_csv(snapshot / 'corrected/payload/tables/record_disposition.csv', ('record_id', 'dataset', 'status', 'reason'))
    held_ids = {row['record_id'] for row in held}
    join_audit, identities, explicit, confirmed_by_key = joins_audit(snapshot, by_key, held_ids)
    groups, inventory, outcome_visits, outcome_events, observation_audit = outcome_audit(snapshot, by_key, confirmed_by_key, held_ids)
    source_measurements, laboratory_records, laboratory_measurements = source_catalogs(snapshot, observation_audit, dispositions, held)
    catalog, eligibility, functional_events, functional_support = functional_catalog(snapshot, metadata)
    checks = safeguards(metadata, observation_audit, identities)
    checks.extend(audit_label_integrity_checks(source_measurements, laboratory_records,
                                              laboratory_measurements, held, observation_audit))
    designs = split_designs(groups, metadata, functional_support)
    gates = gate_register(inventory, micro_cohorts, catalog)
    checklist = evidence_requirements()
    horizons = requested_horizon_calendar(groups, micro_cohorts)
    domain_joins = outcome_domain_join_support(groups, by_key)
    tables = {
        'source_provenance.csv': (provenance, ('source', 'source_job_id')),
        'source_hashes.csv': (hashes, ('source', 'relative_path', 'sha256')),
        'integrity_checks.csv': (checks, ('check', 'status', 'detail')),
        'outcome_plan_recorded.csv': (plan, ('candidate_id', 'track', 'outcome_identifier', 'status')),
        'microbiome_sample_audit.csv': (sample_audit, ('domain', 'analysis_sample')),
        'microbiome_plot_visit_support.csv': (micro_visits, ('farm', 'domain', 'system', 'scope', 'physical_plot')),
        'microbiome_cohort_support.csv': (micro_cohorts, ('farm', 'domain', 'system', 'scope')),
        'microbiome_event_treatment_support.csv': (micro_events, ('farm', 'domain', 'system', 'scope', 'collection_date', 'treatment')),
        'identity_issues.csv': (issues, ('kind', 'domain', 'analysis_sample', 'reason')),
        'held_records.csv': (held, ('record_id', 'dataset', 'reason')),
        'source_record_disposition.csv': (dispositions, ('record_id', 'dataset', 'status', 'reason')),
        'source_measurement_catalog.csv': (source_measurements, ('record_id', 'variable', 'unit', 'audit_source_row')),
        'laboratory_record_catalog.csv': (laboratory_records, ('source_record_key', 'legacy_record_id', 'audit_source_row')),
        'laboratory_measurement_catalog.csv': (laboratory_measurements, ('source_record_key', 'analyte', 'unit', 'audit_source_row')),
        'confirmed_join_audit.csv': (join_audit, ('record_id', 'farm', 'audit_gate', 'audit_reasons')),
        'confirmed_soil_microbiome_identities.csv': (identities, ('record_id', 'farm', 'domain', 'sample_key', 'physical_plot')),
        'recorded_soil_match_identities.csv': (explicit, ('farm', 'source_system', 'collection_date', 'domain', 'sample_key', 'analysis_sample', 'record_id')),
        'outcome_observation_audit.csv': (observation_audit, ('observation_row', 'outcome_id')),
        'outcome_inventory.csv': (inventory, ('outcome_id', 'farm', 'system', 'dataset', 'outcome', 'unit', 'assay')),
        'outcome_plot_visit_support.csv': (outcome_visits, ('outcome_id', 'physical_plot')),
        'outcome_event_treatment_support.csv': (outcome_events, ('outcome_id', 'event', 'treatment')),
        'outcome_domain_join_support.csv': (domain_joins, ('outcome_id', 'farm', 'domain', 'analytic_system', 'outcome', 'unit', 'assay')),
        'candidate_feature_catalog.csv': (catalog, ('domain', 'variant', 'farm', 'system', 'family', 'function', 'identifier_gate')),
        'functional_sample_eligibility_recorded.csv': (eligibility, ('domain', 'variant', 'family', 'analysis_sample')),
        'functional_event_treatment_support.csv': (functional_events, ('domain', 'variant', 'farm', 'system', 'family', 'collection_date', 'treatment')),
        'time_split_candidate_designs.csv': (designs, ('candidate_id', 'track', 'design', 'gate')),
        'integration_forecast_gate_register.csv': (gates, ('candidate_id', 'track', 'gate', 'status', 'reason')),
        'external_evidence_requirements.csv': (checklist, ('evidence_item', 'recorded_status', 'required_review_next')),
        'requested_horizon_calendar.csv': (horizons, ('candidate_id', 'track', 'origin_date', 'requested_endpoint', 'requested_horizon_years', 'gate'))}
    for filename, (rows, fields) in tables.items():
        write_csv(out / filename, rows, fields)
    # Preserve the earlier support audit verbatim for review, not as a new eligibility decision.
    for filename in ('microbiome_plot_visit_support.csv', 'microbiome_cohort_support.csv', 'measured_outcome_visit_support.csv'):
        shutil.copyfile(snapshot / ('functional/results/modeling_support/' + filename), out / ('prior_recorded_' + filename))
    postread = reverify_sources(snapshot, hashes)
    write_csv(out / 'source_integrity_after_audit.csv', postread,
              ('source', 'relative_path', 'expected_sha256', 'original_sha256_after_audit', 'snapshot_sha256_after_audit', 'verified_unchanged'))
    manifest = [dict(relative_path=str(path.relative_to(run)), sha256=digest(path), bytes=path.stat().st_size)
                for path in sorted(out.glob('*.csv'))]
    write_csv(out / 'output_manifest.csv', manifest, ('relative_path', 'sha256', 'bytes'))
    status = dict(status='FEASIBILITY_AUDIT_COMPLETED_REVIEW_PENDING', execution=execution,
        audit_revision='v2_catalog_hold_membership_and_missing_method_labels',
        correction_scope=['Authoritative immutable held-record membership with noninserting catalog lookups',
                          'Explicit missing method text is unknown/held; literal source method text preserved',
                          'Existing measured plot/split counts labeled when provisional recorded identities are included; no identity inference'],
        completed_utc=datetime.now(timezone.utc).isoformat(),
        source_jobs={name: item['job_id'] for name, item in config['source_runs'].items()},
        pinned_source_files=len(hashes), corrected_outcome_groups=len(inventory),
        numerical_models_fitted=False, models_fitted=False, statistical_tests_run=False,
        forecasts_generated=False, ten_year_forecast_generated=False, eligibility_review_pending=True,
        automatic_forecast_authorization=False, source_integrity_passed=True,
        source_files_changed=False, source_files_verified_unchanged_after_audit=True,
        scientific_scope='Descriptive temporal, assay, identity and candidate validation-design audit only',
        near_term_validation_status='VALIDATION_NOT_ESTABLISHED',
        external_calibration_status='EXTERNAL_CALIBRATION_NOT_ESTABLISHED',
        selection_policy='Prespecified source panel and all corrected recorded outcome variables; no p-value selection',
        limits=['Counts and candidate time splits do not authorize model fitting or a numerical forecast.',
                'A recorded exact event date is not automatically an independently confirmed field collection.',
                'Unknown methods and units are not pooled or converted; laboratory dates do not establish collection dates.',
                'Merged libraries, held records, composites, references, separate fields and crop systems retain source identities.',
                'PICRUSt predictions describe relative functional potential, not measured activity or process rates.',
                'NIFA/NRCS sample reuse and independent evidence remain unresolved.'])
    write_json(out / 'FEASIBILITY_STATUS.json', status)
    (out / 'RUN_SUMMARY.txt').write_text(
        'Puma feasibility audit completed; scientific review remains pending.\n'
        'Source jobs: ' + ', '.join(name + '=' + str(item['job_id']) for name, item in config['source_runs'].items()) + '\n'
        'Inventories preserve farm/system/domain, assay/unit, exact event and physical plot.\n'
        'All corrected recorded outcome variables and the fixed functional panel are retained without p-value selection.\n'
        'Absent prespecified functions are explicitly untested. Held/composite/reference identities remain held or descriptive.\n'
        'Petite Sirah 2025 T1 remains one merged R39 biological plot per domain. Grenache 2025 is absent.\n'
        'Oatman 2025 is a distinct field with treatment/side confounding. No new 2025 soil joins are invented.\n'
        'No numerical model fitted; no test run; no forecast or scenario generated.\n'
        'Candidate time splits require design review and actual held-out predictions before validation can be claimed.\n'
        'Ten-year forecasting remains blocked pending external calibration, measured starting states and drivers.\n', encoding='utf-8')
    # A successful sentinel is the last scientific write and follows every integrity check.
    (run / 'ANALYSES_COMPLETE.txt').write_text(
        'Feasibility audit completed on Puma job ' + execution['job_id'] + '; review pending; no models or forecasts.\n',
        encoding='utf-8')
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
