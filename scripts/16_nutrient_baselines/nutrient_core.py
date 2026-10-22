"""Guarded reported-response source eligibility and training-only baseline mechanics."""
from hpc_guard import require_hpc
EXECUTION = require_hpc()

from collections import defaultdict
from datetime import date
from pathlib import Path
import hashlib
import importlib.metadata
import json
import math
import re
import sys
from common import require, sha, safe_file, truth

METHODS = ('PERSISTENCE', 'EQUAL_PLOT_TRAINING_MEAN')
DYNAMIC_FLAGS = ('models_fitted', 'baseline_models_fitted', 'baseline_parameters_computed',
                 'baseline_predictions_computed', 'training_baseline_means_computed')
FINITE = 'FINITE_RECORDED_NUMERIC_VALUE'
MAPPING = 'REPORTED_DEFINITION_COMPATIBLE_SOURCE_BACKED'


def cell(value):
    return '' if value is None else str(value)


def stable_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def current_gate(run, cfg, execution):
    """Verify actual runtime, stage, controls before fixtures or response reads."""
    env_path = safe_file(run, 'audit/software/environment.json')
    env = json.loads(env_path.read_text(encoding='utf-8'))
    require(env.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED'
            and all(env.get('execution', {}).get(k) == execution[k] for k in ('job_id', 'cluster', 'node')),
            'Current genuine Puma allocation has no verified pinned environment')
    require(env.get('versions') == cfg['runtime_expected_versions']
            and env.get('requirements_sha256') == cfg['runtime_requirements_sha256']
            and env.get('runtime_package') == cfg['runtime_package']
            and env.get('software_installed') is False, 'Pinned runtime scope or versions changed')
    require(sys.version == env.get('python') == cfg['runtime_expected_python'], 'Actual Python build differs')
    runtime = Path(cfg['runtime_package']).resolve(strict=True)
    require(Path(sys.executable).resolve(strict=True) == (runtime / 'runtime_env/bin/python').resolve(strict=True)
            == Path(env['executable']).resolve(strict=True), 'Actual runtime interpreter differs')
    require(sha(runtime / 'runtime_env/READY.json')
            == sha(safe_file(run, 'audit/software/original_runtime_READY.json'))
            == cfg['runtime_ready_sha256'] == env.get('runtime_ready_sha256'), 'Runtime READY bytes changed')
    require(sha(runtime / 'payload/requirements.txt') == cfg['runtime_requirements_sha256'],
            'Runtime requirements bytes changed')
    for name, version in cfg['runtime_expected_versions'].items():
        require(importlib.metadata.version(name) == version, 'Installed dependency changed: ' + name)
    stage_path = safe_file(run, 'audit/SOURCE_STAGE_STATUS.json')
    stage = json.loads(stage_path.read_text(encoding='utf-8'))
    require(stage.get('status') == cfg['stage_success_status']
            and all(stage.get('execution', {}).get(k) == execution[k] for k in ('job_id', 'cluster', 'node')),
            'Current genuine Puma allocation has no verified immutable staging receipt')
    require(stage.get('source_integrity_passed') is True and stage.get('source_files_changed') is False
            and stage.get('source_files_verified_unchanged_after_staging') is True,
            'Staging source integrity failed')
    require(stage.get('accepted_review_path') == cfg['accepted_review_path']
            and stage.get('accepted_review_sha256') == cfg['accepted_review_sha256']
            == sha(safe_file(run, cfg['accepted_review_path'])), 'Accepted inventory review linkage changed')
    for pfield, hfield, relative in (
        ('source_manifest_path', 'source_manifest_sha256', 'audit/source_manifest.csv'),
        ('review_lock_manifest_path', 'review_lock_manifest_sha256', 'audit/review_lock_manifest.csv'),
        ('validation_output_manifest_path', 'validation_output_manifest_sha256', 'audit/validation_output_manifest.csv'),
    ):
        require(stage.get(pfield) == relative and stage.get(hfield) == sha(safe_file(run, relative)),
                'Staging manifest pin changed: ' + relative)
    require(cfg.get('baseline_point_diagnostics_authorized') is True
            and cfg.get('reported_response_arithmetic_training_baseline_authorized') is True,
            'Frozen simple-baseline authorization missing')
    for flag in ('statistical_tests_authorized', 'confidence_or_prediction_intervals_authorized',
                 'ten_year_forecast_generated', 'numerical_ten_year_forecast',
                 'predictive_validation_established', 'numerical_forecast_execution_in_this_stage'):
        require(cfg.get(flag) is False, 'Unsupported response execution scope: ' + flag)
    require(cfg.get('preparation_evidence', {}).get('forecasts_generated') is False,
            'Preparation evidence may not imply a forecast')
    manifest_path = safe_file(run, 'PACKAGE_SHA256SUMS.txt')
    files = set()
    for line in manifest_path.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None, 'Malformed immutable package hash line')
        digest, relative = match.groups()
        require(relative not in files, 'Repeated immutable package hash path')
        files.add(relative)
        require(sha(safe_file(run, relative)) == digest, 'Immutable package changed: ' + relative)
    require({'CONFIG.json', 'RESPONSE_SPEC.json', 'OUTPUT_CONTRACT.json', 'PROTOCOL.txt',
             'nutrient_core.py', 'analyze_nutrients.py', 'nutrient_self_checks.py'}.issubset(files),
            'Scientific controls absent from immutable package manifest')
    return {'environment_sha256': sha(env_path), 'source_stage_status_sha256': sha(stage_path),
            'package_manifest_sha256': sha(manifest_path), 'current_runtime_and_stage_verified': True}


def valid_date(value):
    if not isinstance(value, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def finite_source_value(raw):
    """Source JSON type controls eligibility; no text coercion or substitution."""
    for field in ('qualifier', 'reporting_limit'):
        if raw.get(field) not in (None, ''):
            return False, 'HELD_SOURCE_QUALIFIER_OR_REPORTING_LIMIT'
    if re.search(r'[<>≤≥]', cell(raw.get('source_value')) + ' ' + cell(raw.get('text_value'))):
        return False, 'HELD_CENSORED_SOURCE_VALUE'
    value = raw.get('value')
    if value is None or value == '' or isinstance(value, bool):
        return False, 'HELD_MISSING_OR_TEXT_VALUE'
    if not isinstance(value, (int, float)):
        return False, 'HELD_NONNUMERIC_VALUE_FIELD'
    if not math.isfinite(value):
        return False, 'HELD_NONFINITE_VALUE'
    if cell(raw.get('text_value')).strip():
        return False, 'HELD_TEXT_NUMERIC_CONFLICT'
    return True, FINITE


def mean(values):
    values = list(values)
    require(values and all(math.isfinite(v) for v in values), 'Invalid finite reported-response mean input')
    return math.fsum(values) / len(values)


RAW_MEASUREMENT_FIELDS = {
    'raw_variable': 'variable', 'raw_value': 'value', 'raw_source_value': 'source_value',
    'raw_text_value': 'text_value', 'raw_unit': 'unit', 'raw_method': 'method',
    'raw_qualifier': 'qualifier', 'raw_reporting_limit': 'reporting_limit',
    'raw_source_id': 'source_id', 'raw_source_location': 'source_location',
    'raw_source_field': 'source_field', 'raw_preferred_pdf_source_id': 'preferred_pdf_source_id',
    'raw_preferred_pdf_page': 'preferred_pdf_page', 'raw_value_basis': 'value_basis', 'raw_notes': 'notes',
}


def response_for(spec, track_id):
    matches = [r for r in spec['responses'] if r['track_id'] == track_id]
    require(len(matches) == 1, 'Unknown or repeated frozen response track: ' + track_id)
    return matches[0]


def response_context(response):
    return {'track_id': response['track_id'], 'unit': response['reported_unit'],
            'reported_response_definition': response['reported_response_definition']}


def fold_context(fold):
    return {k: fold[k] for k in ('track_id', 'unit', 'reported_response_definition')}


def check_spec(spec, accepted_tracks):
    require([r['track_id'] for r in spec['responses']] == ['W_OM', 'W_NO3'],
            'Frozen response panel or order changed')
    expected_ids = set(spec['expected_record_sources'])
    require(expected_ids, 'Frozen 2023/2024 record declaration missing')
    for response in spec['responses']:
        track = response['track_id']
        require(response['scope_id'] == 'WHIPSTONE_ANNUAL', 'Response scope changed')
        accepted = [r for r in accepted_tracks['tracks'] if r['track_id'] == track]
        require(len(accepted) == 1, 'Accepted source has ambiguous response track: ' + track)
        for field in ('track_id', 'scope_id', 'reported_response_definition', 'reported_method', 'reported_unit'):
            require(response[field] == accepted[0][field], 'Exact accepted source response changed: ' + field)
        require(response['forms'] == [f for f in accepted[0]['forms']
                                     if f['preferred_pdf_source_id'] in ('S33', 'S37')],
                'Exact accepted 2023/2024 response forms changed: ' + track)
        require(response['scopes'] == {'WHIPSTONE_ANNUAL': accepted_tracks['scope_records']['WHIPSTONE_ANNUAL']},
                'Accepted Annual record/scope declaration changed')
        scope_ids = response['scopes']['WHIPSTONE_ANNUAL']['record_ids']
        require(len(scope_ids) == len(set(scope_ids)) and expected_ids.issubset(scope_ids),
                'Frozen source records outside exact accepted Annual scope')
        require(response['laboratory_copies_are_observations'] is False
                and response['microbiome_availability_required'] is False
                and response['measurement_response_aggregation_permitted'] is False,
                'Response source/unit boundary changed')
    require(all(spec['expected_record_sources'][rid] == accepted_tracks['expected_record_sources'][rid]
                for rid in expected_ids), 'Frozen source/date/key record metadata changed')
    round_ids, round_records = set(), set()
    for annual in spec['rounds']:
        require(annual['round_id'] not in round_ids and all(valid_date(d) for d in annual['actual_collection_dates']),
                'Duplicate or malformed annual round')
        round_ids.add(annual['round_id'])
        require(len(annual['record_ids']) == len(set(annual['record_ids']))
                and not (round_records & set(annual['record_ids'])), 'Record appears repeatedly in annual rounds')
        round_records.update(annual['record_ids'])
        for rid in annual['record_ids']:
            require(spec['expected_record_sources'][rid]['collection_date'] in annual['actual_collection_dates']
                    and spec['expected_record_sources'][rid]['preferred_pdf_source_id'] == annual['preferred_pdf_source_id'],
                    'Frozen actual date/report is outside its annual round')
    require([r['round_id'] for r in spec['rounds']] == ['2023', '2024']
            and round_records == expected_ids, 'Annual rounds do not close exact frozen 2023/2024 record set')
    require([m['method_id'] for m in spec['methods']] == list(METHODS)
            and all(m['known_training_plot_required'] for m in spec['methods']), 'Baseline method applicability changed')
    require([(f['fold_id'], f['track_id'], f['training_round_ids'], f['target_round_id'], f['cutoff_date'])
             for f in spec['folds']] == [
        ('W_OM_2024_FROM_2023', 'W_OM', ['2023'], '2024', '2024-01-01'),
        ('W_NO3_2024_FROM_2023', 'W_NO3', ['2023'], '2024', '2024-01-01')], 'Fixed separate-response temporal folds changed')


def build_observation_audit(measurements, record_rows, spec):
    """Copy every source row once; assess only the two exact frozen responses."""
    response_by_track = {r['track_id']: r for r in spec['responses']}
    require(set(response_by_track) == {'W_OM', 'W_NO3'} and len(spec['responses']) == 2,
            'Separate-response audit panel changed')
    scope = spec['responses'][0]['scopes']['WHIPSTONE_ANNUAL']
    record_index = {}
    for row in record_rows:
        if row['inventory_source'] != 'corrected_workbook/Sample records':
            continue
        require(row['record_id'] not in record_index, 'Duplicate accepted workbook record identity')
        record_index[row['record_id']] = row
    annual_by_record = {}
    for annual in spec['rounds']:
        for rid in annual['record_ids']:
            require(rid not in annual_by_record, 'Record assigned multiple frozen annual rounds')
            annual_by_record[rid] = annual
    for rid, expected in spec['expected_record_sources'].items():
        require(rid in record_index, 'Declared source Sample record missing: ' + rid)
        raw = json.loads(record_index[rid]['raw_record_json'])
        require(raw['record_id'] == rid and all(raw.get(k) == v for k, v in expected.items()),
                'Declared source record/date/key/report differs from frozen metadata: ' + rid)
        require(raw.get('farm') == scope['farm_literal'] and raw.get('system') == scope['system_literal']
                and raw.get('dataset') in scope['dataset_literals'], 'Declared source farm/system/dataset changed')
    audit, seen_source_ids, response_record_rows = [], set(), defaultdict(list)
    variables = {track: {v for form in r['forms'] for v in form['raw_variables']}
                 for track, r in response_by_track.items()}
    for original in measurements:
        row = dict(original)
        oid, rid = row['inventory_row_id'], row['record_id']
        require(oid not in seen_source_ids, 'Duplicate accepted inventory row identifier')
        seen_source_ids.add(oid)
        annual = annual_by_record.get(rid)
        matches = [r for track, r in response_by_track.items()
                   if annual and (row['track_id'] == track or row['raw_variable'] in variables[track])]
        require(len(matches) <= 1, 'Conflicting accepted response track and original raw analyte; no response choice permitted')
        response = matches[0] if matches else None
        selected = response is not None
        authoritative = bool(response and row['inventory_source'] == response['authority_inventory_source'])
        row.update(observation_id=oid, annual_round_id=annual['round_id'] if annual else '',
                   inventory_track_id=row.get('track_id', ''),
                   track_id=response['track_id'] if response else row.get('track_id', ''),
                   reported_response_definition=response['reported_response_definition'] if response else '',
                   reported_value='', observation_eligible=False, candidate_in_scope=selected,
                   eligibility_status='OUTSIDE_FROZEN_WH_ANNUAL_OM_NO3_SCOPE',
                   eligibility_reason='Saved source retained; no response evaluation outside frozen 2023/2024 W_OM/W_NO3 scope',
                   authoritative_workbook_response=authoritative, source_value_unchanged_verified=False,
                   source_row_present=True, plot_event_source_rows='', plot_round_actual_events='',
                   treatment_source_label=row.get('raw_sample_label', ''),
                   treatment_used_for_model_or_eligibility=False)
        audit.append(row)
        if not selected:
            continue
        if not authoritative:
            row.update(eligibility_status='AUDIT_TRANSCRIPTION_COPY_ONLY',
                       eligibility_reason='Laboratory transcription is duplicate evidence; not another field observation')
            continue
        track = response['track_id']
        response_record_rows[(track, rid)].append(row)
        require(row['source_role'] == response['authority_source_role']
                and oid.startswith(response['authority_row_id_prefix']), 'Workbook response authority marker changed')
        require(rid in record_index, 'Workbook response has no accepted Sample record')
        accepted_record = record_index[rid]
        require(row['raw_record_json'] == accepted_record['raw_record_json'],
                'Measurement and accepted Sample-record original JSON disagree')
        raw, raw_record = json.loads(row['raw_measurement_json']), json.loads(row['raw_record_json'])
        require(raw.get('record_id') == rid and raw_record.get('record_id') == rid,
                'Original source measurement/record identity differs')
        require(all(cell(raw.get(field)) == row.get(column, '') for column, field in RAW_MEASUREMENT_FIELDS.items()),
                'Inventory projection changed an original source measurement cell')
        for key in ('collection_date', 'confirmed_sample_key'):
            require(cell(raw_record.get(key)) == row.get(key, '') == accepted_record.get(key, ''),
                    'Original record/date/key projection differs: ' + key)
        for key in ('confirmed_physical_plot_id', 'analytic_system', 'record_identity_status',
                    'record_identity_confirmed', 'record_excluded'):
            require(row.get(key, '') == accepted_record.get(key, ''), 'Accepted physical identity projection differs: ' + key)
        row['source_value_unchanged_verified'] = True
        forms = [form for form in response['forms'] if rid in form['record_ids']
                 and raw.get('variable') in form['raw_variables']
                 and raw.get('unit') in form['raw_units'] and raw.get('method') in form['raw_methods_allowed']
                 and raw.get('source_id') in form['raw_source_ids']
                 and raw.get('preferred_pdf_source_id') == form['preferred_pdf_source_id']
                 == raw_record.get('preferred_pdf_source_id')
                 and cell(raw.get('preferred_pdf_page')) == cell(raw_record.get('preferred_pdf_page'))
                 and raw.get('farm') == raw_record.get('farm') and raw.get('dataset') == raw_record.get('dataset')
                 and raw.get('sample_name') == raw_record.get('sample_name')
                 and isinstance(raw.get('value_basis'), str) and raw['value_basis'].startswith('PDF')]
        require(len(forms) <= 1, 'Multiple exact reviewed response forms match one original source')
        reasons = []
        if len(forms) != 1 or row['mapping_status'] != MAPPING or row['inventory_track_id'] != track:
            reasons.append('HELD_SOURCE_FORM_MISMATCH')
        if truth(row['record_excluded']) or raw_record.get('intentional_exclusion') not in (None, ''):
            reasons.append('HELD_SOURCE_EXCLUSION')
        physical = row['confirmed_physical_plot_id']
        if (not truth(row['record_identity_confirmed']) or not physical
                or row['record_identity_status'] != 'EXPLICIT_CONFIRMED_PLOT_EVENT_IDENTITY'
                or row['analytic_system'] != scope['expected_analytic_system']):
            reasons.append('HELD_NO_CONFIRMED_PHYSICAL_IDENTITY')
        actual, key = row['collection_date'], row['confirmed_sample_key']
        if (not valid_date(actual) or actual not in annual['actual_collection_dates']
                or not key or not key.endswith('|' + actual)):
            reasons.append('HELD_COLLECTION_DATE_OR_KEY_CONFLICT')
        valid, value_status = finite_source_value(raw)
        row['source_numeric_status'] = value_status
        if not valid:
            reasons.append({
                'HELD_SOURCE_QUALIFIER_OR_REPORTING_LIMIT': 'HELD_CENSORED_OR_QUALIFIED_RESPONSE',
                'HELD_CENSORED_SOURCE_VALUE': 'HELD_CENSORED_OR_QUALIFIED_RESPONSE',
                'HELD_MISSING_OR_TEXT_VALUE': 'HELD_MISSING_OR_NONNUMERIC_RESPONSE',
                'HELD_NONNUMERIC_VALUE_FIELD': 'HELD_MISSING_OR_NONNUMERIC_RESPONSE',
                'HELD_NONFINITE_VALUE': 'HELD_NONFINITE_RESPONSE',
                'HELD_TEXT_NUMERIC_CONFLICT': 'HELD_TEXT_NUMERIC_CONFLICT',
            }[value_status])
        row['_reasons'], row['_source_value'] = reasons, raw.get('value')
        row['_group_identity_valid'] = bool(physical and valid_date(actual)
                                           and actual in annual['actual_collection_dates']
                                           and truth(row['record_identity_confirmed']))
    for response in spec['responses']:
        for rid, annual in annual_by_record.items():
            track = response['track_id']
            if response_record_rows[(track, rid)]:
                continue
            record = record_index[rid]
            audit.append({
                'audit_id': 'EXPECTED_' + track + '_ABSENT_' + rid,
                'track_id': track, 'inventory_track_id': '',
                'reported_response_definition': response['reported_response_definition'],
                'observation_id': '', 'inventory_row_id': '', 'record_id': rid,
                'annual_round_id': annual['round_id'], 'collection_date': record['collection_date'],
                'confirmed_sample_key': record['confirmed_sample_key'],
                'confirmed_physical_plot_id': record['confirmed_physical_plot_id'], 'raw_value': '', 'raw_source_value': '',
                'raw_sample_label': '', 'raw_record_json': record['raw_record_json'], 'raw_measurement_json': '',
                'reported_value': '', 'observation_eligible': False, 'candidate_in_scope': True,
                'eligibility_status': 'EXPECTED_SOURCE_ROW_ABSENT',
                'eligibility_reason': 'Declared record has no authoritative response row; no value is invented',
                'authoritative_workbook_response': False, 'source_value_unchanged_verified': True,
                'source_row_present': False, 'plot_event_source_rows': 0, 'plot_round_actual_events': 0,
                'treatment_source_label': '', 'treatment_used_for_model_or_eligibility': False,
                'unit': response['reported_unit'],
            })
    events, annual_events = defaultdict(list), defaultdict(set)
    for row in audit:
        if row.get('_group_identity_valid'):
            key = row['track_id'], row['confirmed_physical_plot_id'], row['collection_date']
            events[key].append(row)
            annual_events[(row['track_id'], row['confirmed_physical_plot_id'], row['annual_round_id'])].add(row['collection_date'])
    for row in audit:
        if '_reasons' not in row:
            continue
        if row['_group_identity_valid']:
            source_rows = events[(row['track_id'], row['confirmed_physical_plot_id'], row['collection_date'])]
            dates = annual_events[(row['track_id'], row['confirmed_physical_plot_id'], row['annual_round_id'])]
            row['plot_event_source_rows'], row['plot_round_actual_events'] = len(source_rows), len(dates)
            if len(source_rows) != 1:
                row['_reasons'].append('HELD_MULTIPLE_PLOT_EVENT_VALUES')
            if len(dates) != 1:
                row['_reasons'].append('HELD_MULTIPLE_EVENTS_PER_PLOT_ROUND')
        if len(response_record_rows[(row['track_id'], row['record_id'])]) != 1:
            row['_reasons'].append('HELD_DUPLICATE_OR_CONFLICTING_SOURCE_ROWS')
        reasons = list(dict.fromkeys(row['_reasons']))
        row['observation_eligible'] = not reasons
        row['eligibility_status'] = ';'.join(reasons) if reasons else 'ELIGIBLE_RECORDED_RESPONSE'
        row['eligibility_reason'] = ('Exact unchanged authoritative workbook response; confirmed physical/date identity; one source/event/annual round; no microbial availability condition'
                                     if not reasons else '; '.join(reasons) + '; raw source retained without choice, averaging or substitution')
        row['reported_value'] = row['_source_value'] if not reasons else ''
        for key in ('_reasons', '_source_value', '_group_identity_valid'):
            row.pop(key)
    for row in audit:
        row.update(audit_id=row.get('audit_id', row['inventory_row_id']),
                   expected_source_row=bool(row['candidate_in_scope'] and
                                            (row['authoritative_workbook_response'] or not row['source_row_present'])),
                   source_row_kind=('EXPECTED_SOURCE_ROW_ABSENT' if not row['source_row_present'] else
                                    'AUTHORITATIVE_WORKBOOK_RESPONSE' if row['authoritative_workbook_response']
                                    and row['candidate_in_scope'] else 'AUDIT_SOURCE_COPY_OR_OUTSIDE_RESPONSE'),
                   eligible=row['observation_eligible'], eligibility_reason_codes=row['eligibility_status'],
                   duplicate_or_conflict_status=('HELD_AMBIGUOUS_SOURCE_OR_ANNUAL_EVENT' if any(
                       token in row['eligibility_status'] for token in ('MULTIPLE', 'DUPLICATE')) else
                       'NO_SELECTED_ROW_DEDUPLICATION_OR_AGGREGATION'),
                   source_id=row.get('raw_source_id', ''),
                   preferred_pdf_source_id=row.get('raw_preferred_pdf_source_id', ''),
                   preferred_pdf_page=row.get('raw_preferred_pdf_page', ''),
                   original_lab_id=row.get('raw_lab_id', ''), treatment='',
                   sample_context_label=row.get('raw_sample_label', ''),
                   treatment_definition_status='NO_EXPLICIT_ACCEPTED_TREATMENT_FIELD_USED',
                   unit=row.get('unit', row.get('raw_unit', '')),
                   source_field=row.get('raw_source_field', ''),
                   pdf_depth_literal=spec.get('source_reports', {}).get(row.get('raw_preferred_pdf_source_id'), {}).get('pdf_depth_literal', ''),
                   historical_assay_availability_status='NOT_VERIFIED')
    observations = [dict(row) for row in audit if row['observation_eligible']]
    return audit, observations


def observed_changes(observations, round_order):
    by_plot = defaultdict(list)
    positions = {annual: i for i, annual in enumerate(round_order)}
    for row in observations:
        by_plot[(row['track_id'], row['confirmed_physical_plot_id'])].append(row)
    changes = []
    for (track, plot), rows in sorted(by_plot.items()):
        rows.sort(key=lambda r: r['collection_date'])
        for left, right in zip(rows, rows[1:]):
            gap = positions[right['annual_round_id']] - positions[left['annual_round_id']]
            require(left['unit'] == right['unit'] and left['reported_response_definition'] == right['reported_response_definition'],
                    'Observed endpoints mix units or response definitions')
            changes.append({
                'track_id': track, 'unit': left['unit'],
                'reported_response_definition': left['reported_response_definition'],
                'confirmed_physical_plot_id': plot,
                'from_observation_id': left['observation_id'], 'to_observation_id': right['observation_id'],
                'from_date': left['collection_date'], 'to_date': right['collection_date'],
                'from_annual_round_id': left['annual_round_id'], 'to_annual_round_id': right['annual_round_id'],
                'from_reported_value': left['reported_value'], 'to_reported_value': right['reported_value'],
                'observed_value_change': right['reported_value'] - left['reported_value'],
                'change_kind': 'ADJACENT_ELIGIBLE_OBSERVED_VISITS',
                'actual_gap_days': (date.fromisoformat(right['collection_date']) - date.fromisoformat(left['collection_date'])).days,
                'adjacent_prespecified_rounds': gap == 1,
                'status': 'ADJACENT_OBSERVED_VISITS_REPORTED_RESPONSE_DIFFERENCE',
                'reason': 'Descriptive saved actual endpoints; no interpolation, annualization, trend or causal effect',
            })
    return changes


def verify_original_workbook_links(audit, workbook):
    """Check original row objects, never construct replacement response values."""
    tables = workbook['tables']
    originals = {}
    for name in ('Sample records', 'Measurements'):
        value = tables[name]
        columns, rows = value['columns'], value['rows']
        require(len(columns) == len(set(columns))
                and all(isinstance(r, list) and len(r) == len(columns) for r in rows),
                'Malformed original workbook table: ' + name)
        originals[name] = [dict(zip(columns, r)) for r in rows]
    record_index = {}
    for row in originals['Sample records']:
        require(row['record_id'] not in record_index, 'Duplicate original workbook record ID')
        record_index[row['record_id']] = row
    proofs = []
    for row in audit:
        if not row['candidate_in_scope'] or not row['authoritative_workbook_response']:
            continue
        require(row['source_row_present'] and row['source_row_number'].isdigit(), 'Original row ordinal missing')
        ordinal = int(row['source_row_number'])
        require(1 <= ordinal <= len(originals['Measurements']), 'Original row ordinal outside source table')
        original = originals['Measurements'][ordinal - 1]
        original_record = record_index[row['record_id']]
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=True)
        require(encode(original) == row['raw_measurement_json']
                and encode(original_record) == row['raw_record_json'],
                'Accepted inventory original row objects differ from pinned corrected workbook')
        proofs.append({'track_id': row['track_id'], 'observation_id': row['observation_id'], 'record_id': row['record_id'],
                       'source_row_number': ordinal, 'status': 'EXACT_ORIGINAL_TYPED_WORKBOOK_ROWS_VERIFIED'})
    return proofs


def select_training(observations, fold):
    """Select dates/rounds first; no future presence or outcome is consulted."""
    rounds = set(fold['training_round_ids'])
    cutoff = fold['cutoff_date']
    require(valid_date(cutoff) and rounds and fold['target_round_id'] not in rounds,
            'Invalid frozen training/target fold')
    rows = []
    for row in observations:
        if row['track_id'] != fold['track_id'] or row['annual_round_id'] not in rounds or row['collection_date'] >= cutoff:
            continue
        require(valid_date(row['collection_date']), 'Training actual date invalid')
        rows.append({k: row[k] for k in (
            'track_id', 'unit', 'reported_response_definition',
            'observation_id', 'inventory_row_id', 'record_id', 'annual_round_id', 'confirmed_physical_plot_id',
            'collection_date', 'reported_value', 'raw_value', 'raw_source_value', 'raw_sample_label',
            'confirmed_sample_key', 'raw_preferred_pdf_source_id', 'raw_preferred_pdf_page')})
    return sorted(rows, key=lambda r: (r['confirmed_physical_plot_id'], r['collection_date'], r['inventory_row_id']))


def compute_parameters(training, fold):
    """Only eligible pre-cutoff observations enter the two frozen rules."""
    by_plot = defaultdict(list)
    seen_events, seen_rounds = set(), set()
    for row in training:
        require(all(row[k] == fold[k] for k in ('track_id', 'unit', 'reported_response_definition')),
                'Foreign response or unit entered baseline parameter computation')
        require(row['annual_round_id'] in fold['training_round_ids'] and row['collection_date'] < fold['cutoff_date']
                and valid_date(row['collection_date']), 'Future or nonallowlisted source entered training')
        plot = row['confirmed_physical_plot_id']
        require(plot and math.isfinite(row['reported_value']), 'Malformed prior plot/response')
        event, annual = (plot, row['collection_date']), (plot, row['annual_round_id'])
        require(event not in seen_events and annual not in seen_rounds,
                'Ambiguous prior plot event/annual round; never select or average source observations')
        seen_events.add(event)
        seen_rounds.add(annual)
        by_plot[plot].append(dict(row))
    summaries = []
    for plot, rows in sorted(by_plot.items()):
        rows.sort(key=lambda r: r['collection_date'])
        last = rows[-1]
        summaries.append({
            **fold_context(fold),
            'confirmed_physical_plot_id': plot, 'n_training_visits': len(rows),
            'training_dates': ';'.join(r['collection_date'] for r in rows),
            'training_round_ids': ';'.join(r['annual_round_id'] for r in rows),
            'training_inventory_row_ids': ';'.join(r['inventory_row_id'] for r in rows),
            'latest_prior_date': last['collection_date'], 'latest_prior_inventory_row_id': last['inventory_row_id'],
            'latest_prior_record_id': last['record_id'], 'latest_training_value': last['reported_value'],
            'latest_observation_id': last['observation_id'],
            'training_plot_mean_value': mean(r['reported_value'] for r in rows),
            'raw_sample_label': last['raw_sample_label'],
        })
    return {
        **fold_context(fold),
        'fold_id': fold['fold_id'], 'cutoff_date': fold['cutoff_date'],
        'target_round_id': fold['target_round_id'], 'training_round_ids': list(fold['training_round_ids']),
        'training_membership': training, 'training_fingerprint': stable_digest(training),
        'plot_summaries': summaries,
        'equal_plot_training_mean_value': mean(r['training_plot_mean_value'] for r in summaries) if summaries else None,
        'status': 'TRAINING_ONLY_BASELINE_PARAMETERS_COMPUTED' if summaries else 'NOT_ESTIMATED_NO_ELIGIBLE_TRAINING',
        'arithmetic_scale': 'Own original reported response units; no conversion or cross-response averaging',
    }


def freeze_predictions(parameters, fold):
    """Create predictions for ALL prior plots without receiving target rows."""
    require(parameters['fold_id'] == fold['fold_id'] and parameters['cutoff_date'] == fold['cutoff_date']
            and all(parameters[k] == fold[k] for k in ('track_id', 'unit', 'reported_response_definition')),
            'Parameter/fold identity differs')
    predictions = []
    for prior in parameters['plot_summaries']:
        for method in METHODS:
            prediction = prior['latest_training_value'] if method == 'PERSISTENCE' else parameters['equal_plot_training_mean_value']
            require(math.isfinite(prediction), 'Nonfinite frozen baseline prediction')
            predictions.append({
                **fold_context(fold),
                'fold_id': fold['fold_id'], 'target_round_id': fold['target_round_id'],
                'cutoff_date': fold['cutoff_date'], 'method': method,
                'confirmed_physical_plot_id': prior['confirmed_physical_plot_id'],
                'predicted_value': prediction, 'latest_prior_date': prior['latest_prior_date'],
                'latest_prior_inventory_row_id': prior['latest_prior_inventory_row_id'],
                'n_training_visits': prior['n_training_visits'], 'training_dates': prior['training_dates'],
                'training_inventory_row_ids': prior['training_inventory_row_ids'],
                'training_fingerprint': parameters['training_fingerprint'],
                'prediction_status': 'FROZEN_TRAINING_ONLY_KNOWN_PLOT_PREDICTION',
                'n_training_observations_for_plot': prior['n_training_visits'],
                'training_plot_mean_value': prior['training_plot_mean_value'],
                'latest_training_date': prior['latest_prior_date'],
                'baseline_status': 'ESTIMATED_TRAINING_ONLY', 'skip_reason': '',
            })
    return predictions


def parameter_support(parameters, predictions, fold):
    """Report actual training computation separately for each declared response."""
    nplots = len(parameters['plot_summaries'])
    supported = bool(nplots)
    require(len(predictions) == len(METHODS) * nplots,
            'Frozen response predictions do not close exact prior plot/method map')
    require(parameters['fold_id'] == fold['fold_id'] and parameters['track_id'] == fold['track_id']
            and all(p['fold_id'] == fold['fold_id'] and p['track_id'] == fold['track_id'] for p in predictions),
            'Support receipt contains foreign response/fold parameters or predictions')
    require(supported == (parameters['equal_plot_training_mean_value'] is not None),
            'No-support parameter estimate must remain null')
    return {
        **fold_context(fold), 'fold_id': fold['fold_id'],
        'status': 'TRAINING_SUPPORT_PRESENT' if supported else 'NO_ELIGIBLE_TRAINING_RESPONSE',
        'reason': ('Actual own-response prior parameters and all prior-plot predictions computed'
                   if supported else 'No eligible prior response; no baseline parameter, fit or prediction is claimed'),
        'n_training_observations': len(parameters['training_membership']), 'n_training_plots': nplots,
        'n_frozen_predictions': len(predictions),
        **{key: supported for key in DYNAMIC_FLAGS},
    }


def score_targets(predictions, target_rows, fold):
    """Attach held-out actual outcomes only after the prediction map is frozen."""
    lookup = {}
    for prediction in predictions:
        require(prediction['fold_id'] == fold['fold_id'] and prediction['method'] in METHODS
                and all(prediction[k] == fold[k] for k in ('track_id', 'unit', 'reported_response_definition')),
                'Foreign prediction entered target scoring')
        key = prediction['method'], prediction['confirmed_physical_plot_id']
        require(key not in lookup, 'Duplicate frozen method/plot prediction')
        lookup[key] = prediction
    errors = []
    seen_eligible = set()
    for target in target_rows:
        require(target['annual_round_id'] == fold['target_round_id']
                and target['track_id'] == fold['track_id'], 'Foreign target round entered scoring')
        eligible = truth(target['observation_eligible'])
        plot = target['confirmed_physical_plot_id']
        if eligible:
            require(all(target[k] == fold[k] for k in ('unit', 'reported_response_definition')),
                    'Eligible target response unit or definition differs')
            require(plot and valid_date(target['collection_date']) and target['collection_date'] >= fold['cutoff_date'],
                    'Eligible target identity/date invalid')
            require(plot not in seen_eligible, 'Multiple eligible target events for one annual plot')
            seen_eligible.add(plot)
        for method in METHODS:
            prediction = lookup.get((method, plot))
            status = target['eligibility_status'] if not eligible else 'COLD_START_NO_PRIOR_RESPONSE' if prediction is None else 'SCORED_KNOWN_PLOT_POINT'
            row = {
                **fold_context(fold),
                'fold_id': fold['fold_id'], 'target_round_id': fold['target_round_id'], 'cutoff_date': fold['cutoff_date'],
                'method': method, 'inventory_row_id': target['inventory_row_id'], 'record_id': target['record_id'],
                'target_record_id': target['record_id'], 'target_observation_id': target['observation_id'],
                'confirmed_physical_plot_id': plot, 'collection_date': target['collection_date'],
                'confirmed_sample_key': target['confirmed_sample_key'], 'raw_sample_label': target['raw_sample_label'],
                'raw_value': target['raw_value'], 'raw_source_value': target['raw_source_value'],
                'raw_unit': target.get('raw_unit', ''), 'raw_method': target.get('raw_method', ''),
                'observed_value': target['reported_value'] if eligible else '',
                'predicted_value': prediction['predicted_value'] if prediction else '',
                'signed_error': '', 'absolute_error': '', 'squared_error': '',
                'latest_prior_date': prediction['latest_prior_date'] if prediction else '',
                'prior_to_target_gap_days': '', 'n_training_visits': prediction['n_training_visits'] if prediction else 0,
                'training_fingerprint': prediction['training_fingerprint'] if prediction else '',
                'status': status, 'reason_codes': status,
                'score_reason': ('Eligible known plot actual target scored against pre-frozen prediction' if status == 'SCORED_KNOWN_PLOT_POINT'
                                 else 'No eligible pre-cutoff measured response for this exact physical plot' if status == 'COLD_START_NO_PRIOR_RESPONSE'
                                 else target['eligibility_reason']),
                'source_observation_eligible': eligible, 'target_eligible': eligible,
                'known_training_plot': prediction is not None, 'prediction_available': prediction is not None,
                'scored': status == 'SCORED_KNOWN_PLOT_POINT',
            }
            if status == 'SCORED_KNOWN_PLOT_POINT':
                error = row['predicted_value'] - row['observed_value']
                row.update(signed_error=error, absolute_error=abs(error), squared_error=error * error,
                           prior_to_target_gap_days=(date.fromisoformat(row['collection_date'])
                                                     - date.fromisoformat(row['latest_prior_date'])).days)
            errors.append(row)
    return errors


def error_metrics(errors):
    require(errors and all(r['status'] == 'SCORED_KNOWN_PLOT_POINT' for r in errors), 'Metrics need scored saved errors')
    require(len({(r['track_id'], r['fold_id'], r['unit'], r['reported_response_definition']) for r in errors}) == 1,
            'Metrics cannot pool folds or different response units')
    plots = [r['confirmed_physical_plot_id'] for r in errors]
    require(len(plots) == len(set(plots)), 'Metrics would repeatedly weight a physical target plot')
    return {'n_scored_plots': len(plots), 'evaluation_plot_ids': ';'.join(sorted(plots)),
            'MAE': mean(r['absolute_error'] for r in errors),
            'RMSE': math.sqrt(mean(r['squared_error'] for r in errors))}


def performance(errors, fold):
    require(all(r['fold_id'] == fold['fold_id'] and r['track_id'] == fold['track_id'] for r in errors),
            'Foreign response/fold in method performance')
    output = []
    for method in METHODS:
        rows = [r for r in errors if r['method'] == method and r['status'] == 'SCORED_KNOWN_PLOT_POINT']
        output.append({
            **fold_context(fold),
            'fold_id': fold['fold_id'], 'target_round_id': fold['target_round_id'], 'method': method,
            'status': 'ESTIMATED_POINT_DIAGNOSTIC' if rows else 'NOT_ESTIMATED',
            'n_scored_plots': 0, 'evaluation_plot_ids': '', 'MAE': '', 'RMSE': '',
            **(error_metrics(rows) if rows else {}),
            'reason': 'Method-specific eligible exact known-plot cohort; no external/prospective validation claim',
            'evaluation_scope': 'KNOWN_PLOT_RETROSPECTIVE_POINT_DIAGNOSTIC', 'folds_independent': False,
        })
    return output


def paired_comparison(errors, fold):
    require(all(r['fold_id'] == fold['fold_id'] and r['track_id'] == fold['track_id'] for r in errors),
            'Foreign response/fold in paired method comparison')
    sets = {}
    for method in METHODS:
        rows = [r for r in errors if r['method'] == method and r['status'] == 'SCORED_KNOWN_PLOT_POINT']
        sets[method] = {r['confirmed_physical_plot_id']: r for r in rows}
        require(len(rows) == len(sets[method]), 'Duplicate physical plot in paired method cohort')
    common = sorted(set(sets[METHODS[0]]) & set(sets[METHODS[1]]))
    pairs = []
    for plot in common:
        a, b = (sets[m][plot] for m in METHODS)
        require(all(a[k] == b[k] for k in ('track_id', 'unit', 'reported_response_definition', 'fold_id', 'target_round_id', 'inventory_row_id', 'collection_date', 'observed_value')),
                'Paired methods do not score the same source observation')
        pairs.append({
            **fold_context(fold),
            'fold_id': fold['fold_id'], 'target_round_id': fold['target_round_id'],
            'inventory_row_id': a['inventory_row_id'], 'record_id': a['record_id'],
            'confirmed_physical_plot_id': plot, 'collection_date': a['collection_date'],
            'target_record_id': a['target_record_id'], 'target_observation_id': a['target_observation_id'],
            'raw_sample_label': a['raw_sample_label'], 'observed_value': a['observed_value'],
            'persistence_prediction': a['predicted_value'], 'equal_plot_training_mean_prediction': b['predicted_value'],
            'persistence_signed_error': a['signed_error'], 'equal_plot_training_mean_signed_error': b['signed_error'],
            'persistence_absolute_error': a['absolute_error'], 'equal_plot_training_mean_absolute_error': b['absolute_error'],
            'persistence_squared_error': a['squared_error'], 'equal_plot_training_mean_squared_error': b['squared_error'],
            'common_eligible': True,
            'absolute_error_PERSISTENCE': a['absolute_error'],
            'absolute_error_EQUAL_PLOT_TRAINING_MEAN': b['absolute_error'],
            'mean_minus_persistence_absolute_error': b['absolute_error'] - a['absolute_error'],
            'status': 'EXACT_COMMON_KNOWN_PLOT_TARGET_COHORT',
        })
    results = []
    for method in METHODS:
        metric = error_metrics([sets[method][p] for p in common]) if common else {'MAE': '', 'RMSE': ''}
        results.append({**fold_context(fold), 'fold_id': fold['fold_id'], 'target_round_id': fold['target_round_id'],
                        'method': method, 'n_common_plots': len(common), 'common_plot_ids': ';'.join(common),
                        'MAE': metric['MAE'], 'RMSE': metric['RMSE'],
                        'status': 'ESTIMATED_POINT_DIAGNOSTIC' if common else 'NOT_ESTIMATED',
                        'reason': 'Same source outcomes/plots and frozen original predictions; no pooled-fold test or winner',
                        'pairing_scope': 'EXACT_COMMON_ELIGIBLE_KNOWN_PLOT_TARGETS'})
    return pairs, results
