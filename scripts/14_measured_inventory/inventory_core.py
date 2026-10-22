"""Puma-only, source-preserving measured-response inventory mechanics.

This module intentionally guards import: even fixtures and support counts are
scientific work. It never fits models, converts units, averages source values,
constructs missing observations, or invents physical/specimen identities.
"""
from hpc_guard import require_hpc
EXECUTION = require_hpc()

from collections import defaultdict
from datetime import date as calendar_date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import importlib.metadata
import json
import re
import sys
from common import require, truth, sha, safe_file


def require_current_runtime_and_stage(run, cfg, execution):
    """Fail closed before source parsing/fixtures, including standalone entry."""
    environment_path = safe_file(run, 'audit/software/environment.json')
    environment = json.loads(environment_path.read_text(encoding='utf-8'))
    require(environment.get('status') == 'PINNED_CORRECTED_ENVIRONMENT_VERIFIED'
            and all(environment.get('execution', {}).get(key) == execution[key]
                    for key in ('job_id', 'cluster', 'node')),
            'Current main Puma execution has no verified pinned environment receipt')
    require(environment.get('versions') == cfg['runtime_expected_versions']
            and environment.get('requirements_sha256') == cfg['runtime_requirements_sha256']
            and environment.get('runtime_package') == cfg['runtime_package']
            and environment.get('software_installed') is False,
            'Existing verified runtime/version/requirements scope changed')
    require(sys.version == cfg['runtime_expected_python'] == environment.get('python'),
            'Actual interpreter build differs from the pinned corrected runtime')
    runtime = Path(cfg['runtime_package']).resolve(strict=True)
    interpreter = runtime / 'runtime_env/bin/python'
    require(Path(sys.executable).resolve(strict=True) == interpreter.resolve(strict=True)
            == Path(environment['executable']).resolve(strict=True), 'Actual interpreter path changed')
    ready = runtime / 'runtime_env/READY.json'
    ready_snapshot = safe_file(run, 'audit/software/original_runtime_READY.json')
    require(sha(ready) == sha(ready_snapshot) == cfg['runtime_ready_sha256']
            == environment.get('runtime_ready_sha256'), 'Actual/snapshotted READY record differs')
    require(sha(runtime / 'payload/requirements.txt') == cfg['runtime_requirements_sha256'],
            'Existing corrected requirements file changed')
    for name, expected in cfg['runtime_expected_versions'].items():
        require(importlib.metadata.version(name) == expected, 'Installed runtime dependency changed: ' + name)
    stage_path = safe_file(run, 'audit/SOURCE_STAGE_STATUS.json')
    stage = json.loads(stage_path.read_text(encoding='utf-8'))
    require(stage.get('status') == 'IMMUTABLE_MEASURED_SOURCES_VERIFIED_AND_STAGED'
            and all(stage.get('execution', {}).get(key) == execution[key]
                    for key in ('job_id', 'cluster', 'node')),
            'Current main Puma execution has no verified immutable-source staging receipt')
    require(stage.get('source_integrity_passed') is True
            and stage.get('source_files_changed') is False
            and stage.get('source_files_verified_unchanged_after_staging') is True,
            'Stage source-integrity flags failed')
    require(stage.get('accepted_review_path') == cfg['accepted_review_path']
            and stage.get('accepted_review_sha256') == cfg['accepted_review_sha256']
            == sha(safe_file(run, cfg['accepted_review_path'])), 'Accepted review stage linkage changed')
    for path_field, hash_field, expected_path in (
        ('source_manifest_path', 'source_manifest_sha256', 'audit/source_manifest.csv'),
        ('review_lock_manifest_path', 'review_lock_manifest_sha256', 'audit/review_lock_manifest.csv'),
        ('validation_output_manifest_path', 'validation_output_manifest_sha256', 'audit/validation_output_manifest.csv'),
    ):
        require(stage.get(path_field) == expected_path
                and stage.get(hash_field) == sha(safe_file(run, expected_path)), 'Stage manifest receipt changed')
    for flag in ('models_fitted', 'statistical_tests_performed', 'forecasts_generated',
                 'measurement_values_aggregated', 'ten_year_forecast_generated',
                 'numerical_ten_year_forecast', 'predictive_validation_established',
                 'numerical_forecast_execution_in_this_stage'):
        require(cfg.get(flag) is False, 'Frozen measured-inventory-only scope changed: ' + flag)
    # Verify the entire immutable package rather than assuming RUN.sh was used.
    package_manifest = safe_file(run, 'PACKAGE_SHA256SUMS.txt')
    package_paths = set()
    for line in package_manifest.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None, 'Malformed immutable package hash manifest')
        digest, relative = match.groups()
        require(relative not in package_paths, 'Duplicate immutable package path')
        package_paths.add(relative)
        require(sha(safe_file(run, relative)) == digest, 'Immutable package file changed: ' + relative)
    require('CONFIG.json' in package_paths and 'RESPONSE_TRACKS.json' in package_paths
            and 'inventory_core.py' in package_paths and 'inventory_self_checks.py' in package_paths
            and 'inventory_outcomes.py' in package_paths, 'Package manifest does not pin scientific controls')
    return {'environment_sha256': sha(environment_path), 'source_stage_status_sha256': sha(stage_path),
            'package_manifest_sha256': sha(package_manifest), 'current_runtime_and_stage_verified': True}


def raw_json(row):
    # Preserve nulls, types, whitespace inside strings and original field names.
    # A source nonfinite numeric token, if present, remains visible in this
    # archival text companion (and is never eligible). Status JSON remains
    # strict/no-NaN via common.save. Do not erase a nonfinite source by coercion.
    return json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=True)


def cell(value):
    return '' if value is None else str(value)


def blank(value):
    return value is None or value == ''


def original_columns(row, prefix):
    return {prefix + key: value for key, value in row.items()}


def validate_workbook_table(value, required, name):
    require(isinstance(value, dict), name + ': table is not an object')
    columns = value.get('columns')
    rows = value.get('rows')
    require(isinstance(columns, list) and isinstance(rows, list), name + ': invalid columns/rows')
    require(len(columns) == len(set(columns)), name + ': duplicate column')
    require(set(required).issubset(columns), name + ': missing required column')
    require(all(isinstance(row, list) and len(row) == len(columns) for row in rows),
            name + ': malformed row; do not silently truncate source fields')
    return [dict(zip(columns, row)) for row in rows]


def unique_index(rows, field, label):
    output = {}
    for row in rows:
        key = row.get(field)
        require(not blank(key), label + ': blank ' + field)
        require(key not in output, label + ': duplicate ' + field + ' ' + cell(key))
        output[key] = row
    return output


def value_disposition(measurement):
    """Flag a source value without transforming it or using a detection limit."""
    for field in ('qualifier', 'reporting_limit'):
        if not blank(measurement.get(field)):
            return 'HELD_SOURCE_QUALIFIER_OR_REPORTING_LIMIT', field + ' is recorded; no censoring substitution'
    source = cell(measurement.get('source_value'))
    text = cell(measurement.get('text_value'))
    if re.search(r'[<>≤≥]', source + ' ' + text):
        return 'HELD_CENSORED_SOURCE_VALUE', 'Censoring symbol retained; no zero, limit or midpoint substitution'
    value = measurement.get('value')
    if blank(value) or isinstance(value, bool):
        return 'HELD_MISSING_OR_TEXT_VALUE', 'Original numeric value absent; original text retained'
    # The source JSON's numeric field is authoritative, not a newly parsed text.
    if not isinstance(value, (int, float, Decimal)):
        return 'HELD_NONNUMERIC_VALUE_FIELD', 'Original value field is not a numeric source type'
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return 'HELD_NONNUMERIC_VALUE_FIELD', 'Original value field is not a finite number'
    if not number.is_finite():
        return 'HELD_NONFINITE_VALUE', 'Nonfinite source value retained; no imputation'
    if text.strip() not in ('',):
        return 'HELD_TEXT_NUMERIC_CONFLICT', 'A source text_value accompanies the numeric value; review required'
    return 'FINITE_RECORDED_NUMERIC_VALUE', 'Finite original numeric value retained without conversion'


IDENTITY_FIELDS = ('sample_key', 'sample_name', 'analysis_sample', 'farm',
                   'collection_date', 'biological_plot_id', 'analytic_system', 'merge_status')


def metadata_indexes(workbook_metadata, functional_metadata):
    output = {}
    for domain in ('bacteria', 'archaea'):
        wb = unique_index([row for row in workbook_metadata
                           if row.get('microbial_domain') == domain], 'sample_key', 'Corrected ' + domain)
        fn = unique_index(functional_metadata[domain], 'sample_key', 'Functional ' + domain)
        for key, row in fn.items():
            require(row.get('microbial_domain') == domain, 'Functional domain mismatch: ' + domain)
            require(key in wb, 'Functional metadata contains an identity absent from corrected metadata: ' + key)
            for field in IDENTITY_FIELDS:
                require(cell(row.get(field)) == cell(wb[key].get(field)),
                        'Corrected/functional identity conflict: ' + domain + ' ' + key + ' ' + field)
        output[domain] = {'corrected': wb, 'functional': fn}
    return output


def scope_for_record(record_id, response_tracks):
    matches = [scope for scope, definition in response_tracks['scope_records'].items()
               if record_id in definition['record_ids']]
    require(len(matches) <= 1, 'Record belongs to multiple prespecified systems: ' + cell(record_id))
    return matches[0] if matches else ''


def record_disposition(record, scope, metadata):
    key = record.get('confirmed_sample_key')
    date = record.get('collection_date')
    result = {
        'scope_id': scope, 'record_id': record['record_id'],
        'collection_date': date, 'confirmed_sample_key': key,
        'candidate_sample_key_preserved_only': record.get('candidate_sample_key'),
        'confirmed_physical_plot_id': '', 'analytic_system': '',
        'record_identity_status': '', 'record_identity_reason': '',
        'record_excluded': bool(record.get('intentional_exclusion')),
        'record_identity_confirmed': False,
    }
    if not scope:
        result.update(record_identity_status='OUTSIDE_PRESPECIFIED_RESPONSE_SCOPE',
                      record_identity_reason='Original record retained; no measured-response analysis outside selected systems')
        return result
    if record.get('intentional_exclusion'):
        result.update(record_identity_status='INTENTIONAL_EXCLUSION_PRESERVED',
                      record_identity_reason=record['intentional_exclusion'])
        return result
    if record['record_id'] == 'R0025':
        require(record.get('original_lab_id') == '34082'
                and record.get('source_id') == 'S04'
                and record.get('preferred_pdf_source_id') == 'S25'
                and cell(record.get('preferred_pdf_page')) == '10'
                and date == '2024-06-06'
                and record.get('original_sample_label') == '50:50 SHEEP 1 EAST'
                and blank(key) and blank(record.get('candidate_sample_key')),
                'Documented June soil-only record identity changed')
        result.update(record_identity_status='SOIL_ONLY_NO_CONFIRMED_MICROBIOME_OR_PHYSICAL_KEY',
                      record_identity_reason='R0025/lab34082 remains source-preserved soil-only; no provisional alias or 16S join')
        return result
    if blank(key):
        result.update(record_identity_status='HELD_NO_CONFIRMED_IDENTITY',
                      record_identity_reason='No confirmed sample_key; candidate/label/neighbor dates are not join keys')
        return result
    calendar_valid = False
    if isinstance(date, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        try:
            calendar_valid = calendar_date.fromisoformat(date).isoformat() == date
        except ValueError:
            calendar_valid = False
    if not calendar_valid or not isinstance(key, str) or not key.endswith('|' + date):
        result.update(record_identity_status='HELD_CONFIRMED_KEY_DATE_CONFLICT',
                      record_identity_reason='Actual collection_date and explicit confirmed key must agree; report dates never substituted')
        return result
    matches = [metadata[domain]['corrected'][key] for domain in ('bacteria', 'archaea')
               if key in metadata[domain]['corrected']]
    if not matches:
        result.update(record_identity_status='HELD_CONFIRMED_KEY_ABSENT_FROM_CORRECTED_METADATA',
                      record_identity_reason='No corrected specimen identity for explicit key; no inferred physical plot')
        return result
    for row in matches:
        require(row.get('collection_date') == date and row.get('farm') == record.get('farm'),
                'Record/confirmed specimen date or farm conflict: ' + record['record_id'])
        require(not blank(row.get('biological_plot_id')), 'Confirmed specimen has no physical plot identity')
    plots = {row['biological_plot_id'] for row in matches}
    systems = {row.get('analytic_system') for row in matches}
    require(len(plots) == 1 and len(systems) == 1, 'Domain identities disagree on physical plot/system')
    system = next(iter(systems))
    expected_system = 'OFR_2023_2024_Field' if scope == 'OATMAN_OLDER' else 'Annual'
    if system != expected_system:
        result.update(record_identity_status='HELD_CONFIRMED_SYSTEM_CONFLICT',
                      record_identity_reason='Confirmed domain system is not the prespecified older Oatman/Annual system')
        return result
    result.update(record_identity_status='EXPLICIT_CONFIRMED_PLOT_EVENT_IDENTITY',
                  record_identity_reason='Exact corrected sample_key/date and domain physical identities agree',
                  confirmed_physical_plot_id=next(iter(plots)), analytic_system=system,
                  record_identity_confirmed=True)
    return result


def match_track(measurement, record, tracks):
    scope = scope_for_record(record['record_id'], tracks)
    matches = []
    potential = []
    for track in tracks['tracks']:
        if track['scope_id'] != scope:
            continue
        for form in track['forms']:
            if measurement.get('variable') in form['raw_variables']:
                potential.append((track, form))
                checks = (
                    record['record_id'] in form['record_ids'],
                    measurement.get('preferred_pdf_source_id') == form['preferred_pdf_source_id'],
                    record.get('preferred_pdf_source_id') == form['preferred_pdf_source_id'],
                    cell(measurement.get('preferred_pdf_page')) == cell(record.get('preferred_pdf_page')),
                    measurement.get('source_id') in form['raw_source_ids'],
                    measurement.get('unit') in form['raw_units'],
                    measurement.get('method') in form['raw_methods_allowed'],
                    measurement.get('farm') == record.get('farm'),
                    measurement.get('dataset') == record.get('dataset'),
                    measurement.get('sample_name') == record.get('sample_name'),
                    isinstance(measurement.get('value_basis'), str) and measurement['value_basis'].startswith('PDF'),
                )
                if all(checks):
                    matches.append((track, form))
    require(len(matches) <= 1, 'Ambiguous reviewed response mapping: ' + record['record_id'])
    if matches:
        track, form = matches[0]
        return {
            'track_id': track['track_id'], 'scope_id': scope, 'candidate_response': True,
            'mapping_status': 'REPORTED_DEFINITION_COMPATIBLE_SOURCE_BACKED',
            'mapping_reason': 'Exact corrected variable/unit/method/record/PDF pointer matches frozen form; original source_field preserved',
            'reviewed_response_definition': track['reported_response_definition'],
            'reviewed_method': track['reported_method'], 'reviewed_unit': track['reported_unit'],
            'reviewed_report_heading': form['reported_heading'],
            'method_source_id': form['preferred_pdf_source_id'],
            'method_source_sha256': tracks.get('source_reports', {}).get(form['preferred_pdf_source_id'], {}).get('sha256', ''),
        }
    if potential:
        track = potential[0][0]
        return {
            'track_id': track['track_id'], 'scope_id': scope, 'candidate_response': True,
            'mapping_status': 'HELD_EXACT_SOURCE_FORM_MISMATCH',
            'mapping_reason': 'Response-like identifier present but exact record/PDF/unit/method/source identity checks failed; no global unknown-method relaxation',
            'reviewed_response_definition': '', 'reviewed_method': '', 'reviewed_unit': '',
            'reviewed_report_heading': '', 'method_source_id': '',
            'method_source_sha256': '',
        }
    comparator = scope and measurement.get('variable') in tracks['scope_records'][scope].get('comparator_variables', [])
    return {
        'track_id': '', 'scope_id': scope, 'candidate_response': bool(comparator),
        'mapping_status': 'HELD_NON_EQUIVALENT_RESPONSE_COMPARATOR' if comparator else 'OUTSIDE_DECLARED_RESPONSE_PANEL',
        'mapping_reason': 'Organic carbon, buffer pH, nitrate stock or another definition cannot substitute for the selected response' if comparator
                          else 'Original row preserved; no harmonization or scientific evaluation outside the declared response panel',
        'reviewed_response_definition': '', 'reviewed_method': '', 'reviewed_unit': '',
        'reviewed_report_heading': '', 'method_source_id': '',
        'method_source_sha256': '',
    }


def candidate_measurement(measurement, record, identity, response_tracks, ordinal):
    mapping = match_track(measurement, record, response_tracks)
    result = {
        'inventory_row_id': 'WB_MEASUREMENT_' + str(ordinal).zfill(6),
        'source_row_number': ordinal, 'inventory_source': 'corrected_workbook/Measurements',
        'source_role': 'CORRECTED_PRIMARY_MEASUREMENT_NOT_A_NEW_OBSERVATION',
        **mapping, **identity,
        'raw_value': measurement.get('value'), 'raw_source_value': measurement.get('source_value'),
        'raw_text_value': measurement.get('text_value'), 'raw_variable': measurement.get('variable'),
        'raw_unit': measurement.get('unit'), 'raw_method': measurement.get('method'),
        'raw_qualifier': measurement.get('qualifier'), 'raw_reporting_limit': measurement.get('reporting_limit'),
        'raw_source_id': measurement.get('source_id'), 'raw_source_location': measurement.get('source_location'),
        'raw_source_field': measurement.get('source_field'),
        'raw_preferred_pdf_source_id': measurement.get('preferred_pdf_source_id'),
        'raw_preferred_pdf_page': measurement.get('preferred_pdf_page'),
        'raw_lab_id': record.get('original_lab_id'), 'raw_sample_label': record.get('original_sample_label'),
        'raw_value_basis': measurement.get('value_basis'), 'raw_notes': measurement.get('notes'),
        'laboratory_received': record.get('laboratory_received'),
        'laboratory_reported': record.get('laboratory_reported'),
        'field_depth_protocol': record.get('field_depth_protocol'),
        'depth_as_reported': record.get('depth_as_reported'),
        'raw_measurement_json': raw_json(measurement),
        'raw_record_json': raw_json(record),
        'quantitative_value_inventory_eligible': False,
        'confirmed_plot_event_inventory_eligible': False,
        'model_eligible': False, 'forecast_eligible': False,
        'value_status': 'NOT_EVALUATED_OUTSIDE_PANEL',
        'value_reason': 'Raw source retained without scientific value eligibility evaluation',
        'measurement_disposition': 'PRESERVED_OUTSIDE_PANEL',
        'measurement_reason': mapping['mapping_reason'],
        'repeated_plot_event_lab_rows': False,
        'plot_event_group_record_count': '',
        'plot_event_group_measurement_count': '',
        'aggregation_rule_status': 'NO_MEASUREMENT_VALUES_AGGREGATED',
        'prospective_single_plot_event_value_eligible': False,
        'prospective_value_status': 'HELD_PENDING_FROZEN_RESPONSE_PROTOCOL',
        'source_record_response_row_count': '',
        'exact_source_row_duplicate_count': '',
        'duplicate_source_record_response_rows': False,
        'response_conflict_status': 'NOT_EVALUATED_OUTSIDE_PANEL',
    }
    result.update(original_columns(measurement, 'original_measurement__'))
    if not mapping['candidate_response']:
        return result
    value_status, value_reason = value_disposition(measurement)
    result.update(value_status=value_status, value_reason=value_reason)
    reasons = []
    if mapping['mapping_status'] != 'REPORTED_DEFINITION_COMPATIBLE_SOURCE_BACKED':
        reasons.append(mapping['mapping_status'])
    if identity['record_excluded']:
        reasons.append('INTENTIONAL_SOURCE_EXCLUSION')
    if value_status != 'FINITE_RECORDED_NUMERIC_VALUE':
        reasons.append(value_status)
    eligible = not reasons
    result['quantitative_value_inventory_eligible'] = eligible
    result['confirmed_plot_event_inventory_eligible'] = eligible and identity['record_identity_confirmed']
    if eligible:
        result['measurement_disposition'] = 'REPORTED_DEFINITION_VALUE_PRESERVED_FOR_REVIEW'
        result['measurement_reason'] = ('Method/unit-compatible source value inventory only; statistical eligibility, calibration, sampling equivalence and dependence remain unestablished')
    else:
        result['measurement_disposition'] = 'HELD_FROM_QUANTITATIVE_RESPONSE_TRACK'
        result['measurement_reason'] = '; '.join(reasons)
    return result


def confirmed_domain_joins(record, identity, metadata):
    rows = []
    key = record.get('confirmed_sample_key')
    for domain in ('bacteria', 'archaea'):
        row = {
            'record_id': record['record_id'], 'scope_id': identity['scope_id'],
            'domain': domain, 'collection_date': record.get('collection_date'),
            'confirmed_sample_key': key,
            'confirmed_physical_plot_id': identity['confirmed_physical_plot_id'],
            'candidate_sample_key_preserved_only': record.get('candidate_sample_key'),
            'join_status': 'HELD', 'join_reason': identity['record_identity_reason'],
            'microbial_sample_name': '', 'analysis_sample': '',
            'microbial_primary_included': '', 'microbial_depth_sensitivity_included': '',
            'microbial_merge_status': '', 'microbial_identity_json': '',
            'confirmed_join': False, 'model_eligible': False, 'forecast_eligible': False,
            'methodology_source': 'Exact corrected confirmed key + functional validated/' + domain + '/sample_metadata.csv; no response values joined',
        }
        if identity['record_identity_confirmed']:
            wb = metadata[domain]['corrected'].get(key)
            fn = metadata[domain]['functional'].get(key)
            if wb is None:
                row['join_reason'] = 'Explicit confirmed key absent in this corrected microbial domain'
            elif fn is None:
                row['join_reason'] = 'Exact corrected identity absent from this functional prediction metadata; no counterpart fabricated'
            else:
                row.update(join_status='EXACT_SOURCE_CONFIRMED_SPECIMEN_IDENTITY',
                           join_reason='Exact corrected and functional domain identity agree; one preserved established/merged library',
                           microbial_sample_name=fn['sample_name'], analysis_sample=fn['analysis_sample'],
                           microbial_primary_included=fn.get('primary_included'),
                           microbial_depth_sensitivity_included=fn.get('depth_sensitivity_included'),
                           microbial_merge_status=fn.get('merge_status'),
                           microbial_identity_json=raw_json({field: fn.get(field) for field in
                               IDENTITY_FIELDS + ('microbial_domain', 'primary_included', 'depth_sensitivity_included')}),
                           confirmed_join=True)
        rows.append(row)
    return rows


def mark_repeated_lab_records(candidates):
    groups = defaultdict(set)
    measurement_groups = defaultdict(list)
    source_groups = defaultdict(list)
    exact_groups = defaultdict(list)
    for row in candidates:
        source_groups[(row['record_id'], row['raw_variable'])].append(row)
        exact_groups[row['raw_measurement_json']].append(row)
    # Source-level duplicates and conflicts remain visible even where no
    # confirmed physical identity exists (including the June soil-only row).
    for rows in source_groups.values():
        definitions = {raw_json({field: row.get(field) for field in (
            'raw_value', 'raw_text_value', 'raw_unit', 'raw_method',
            'raw_qualifier', 'raw_reporting_limit')}) for row in rows}
        status = ('DUPLICATE_RECORD_RESPONSE_VALUES_OR_DEFINITIONS_DIFFER' if len(definitions) > 1
                  else 'DUPLICATE_RECORD_RESPONSE_SAME_RECORDED_VALUE' if len(rows) > 1
                  else 'ONE_SOURCE_RECORD_RESPONSE_ROW')
        for row in rows:
            row['source_record_response_row_count'] = len(rows)
            row['exact_source_row_duplicate_count'] = len(exact_groups[row['raw_measurement_json']])
            row['duplicate_source_record_response_rows'] = len(rows) > 1
            row['response_conflict_status'] = status
            if len(rows) > 1:
                row['prospective_single_plot_event_value_eligible'] = False
                row['prospective_value_status'] = 'HELD_DUPLICATE_OR_CONFLICTING_RECORD_RESPONSE_ROWS'
                row['measurement_reason'] += '; duplicate record/response source rows retained without resolution'
    for row in candidates:
        if row['track_id'] and row['record_identity_confirmed']:
            key = (row['track_id'], row['confirmed_physical_plot_id'], row['collection_date'])
            groups[key].add(row['record_id'])
            measurement_groups[key].append(row)
    for row in candidates:
        if row['track_id'] and row['record_identity_confirmed']:
            key = (row['track_id'], row['confirmed_physical_plot_id'], row['collection_date'])
            count = len(groups[key])
            measurement_count = len(measurement_groups[key])
            row['plot_event_group_record_count'] = count
            row['plot_event_group_measurement_count'] = measurement_count
            row['repeated_plot_event_lab_rows'] = measurement_count > 1
            if measurement_count > 1:
                row['aggregation_rule_status'] = 'REPEATED_SOURCE_LAB_ROWS_REMAIN_VISIBLE_AGGREGATION_RULE_REQUIRED'
                row['measurement_reason'] += '; repeated laboratory records are not additional biological plots; no mean computed'
                row['prospective_single_plot_event_value_eligible'] = False
                row['prospective_value_status'] = 'HELD_DUPLICATE_PLOT_EVENT_SOURCE_ROWS_NO_FROZEN_AGGREGATION_RULE'
            else:
                # This is only a source-row gate. It does not grant model or
                # prediction eligibility, calibration or sampling equivalence.
                row['prospective_single_plot_event_value_eligible'] = (row['confirmed_plot_event_inventory_eligible']
                                                                     and not row['duplicate_source_record_response_rows'])
                row['prospective_value_status'] = ('SINGLE_SOURCE_VALUE_CANDIDATE_REQUIRES_FROZEN_PROTOCOL_REVIEW'
                                                    if row['confirmed_plot_event_inventory_eligible']
                                                    else 'HELD_SOURCE_VALUE_OR_REPORTED_DEFINITION')
    return candidates


def support_inventory(candidates, joins, tracks):
    """Count support identities only; never aggregate measurement values."""
    grouped = defaultdict(list)
    for row in candidates:
        if row['track_id']:
            grouped[(row['track_id'], cell(row['collection_date']))].append(row)
    join_index = {(row['record_id'], row['domain']): row for row in joins}
    output = []
    for (track_id, date), rows in sorted(grouped.items()):
        compatible = [row for row in rows if row['mapping_status'] == 'REPORTED_DEFINITION_COMPATIBLE_SOURCE_BACKED']
        eligible = [row for row in rows if row['quantitative_value_inventory_eligible']]
        confirmed = [row for row in rows if row['confirmed_plot_event_inventory_eligible']]
        plots = {row['confirmed_physical_plot_id'] for row in confirmed}
        events = {(row['confirmed_physical_plot_id'], row['collection_date']) for row in confirmed}
        result = {
            'track_id': track_id, 'scope_id': rows[0]['scope_id'], 'collection_date': date,
            'source_measurement_rows': len(rows),
            'distinct_source_lab_record_ids': len({row['record_id'] for row in rows}),
            'compatible_reported_definition_rows': len(compatible),
            'finite_uncensored_compatible_unexcluded_rows': len(eligible),
            'held_source_rows': len(rows) - len(eligible),
            'distinct_confirmed_physical_plots': len(plots),
            'distinct_confirmed_plot_events': len(events),
            'finite_soil_only_or_unconfirmed_identity_rows': len([row for row in eligible if not row['record_identity_confirmed']]),
            'repeated_plot_event_lab_rows_present': any(row['repeated_plot_event_lab_rows'] for row in rows),
            'status': 'SOURCE_SUPPORT_INVENTORY_ONLY',
            'reason': 'Laboratory row counts and confirmed plot/event counts are different; no value mean or model/power eligibility follows',
            'methodology_source': 'Frozen RESPONSE_TRACKS.json plus exact source record/domain identity; no provisional aliases',
            'model_eligible': False, 'forecast_eligible': False,
        }
        for domain in ('bacteria', 'archaea'):
            matched = [row for row in confirmed if join_index[(row['record_id'], domain)]['confirmed_join']]
            result[domain + '_distinct_confirmed_joined_plot_events'] = len({(row['confirmed_physical_plot_id'], row['collection_date']) for row in matched})
            result[domain + '_distinct_preserved_specimen_ids'] = len({join_index[(row['record_id'], domain)]['analysis_sample'] for row in matched})
        output.append(result)
    return output


def track_availability(candidates, tracks):
    output = []
    for track in tracks['tracks']:
        scope = tracks['scope_records'][track['scope_id']]
        for epoch in scope['epochs']:
            present = [row for row in candidates if row['track_id'] == track['track_id']
                       and row['record_id'] in epoch['record_ids']]
            compatible = [row for row in present if row['mapping_status'] == 'REPORTED_DEFINITION_COMPATIBLE_SOURCE_BACKED']
            output.append({
                'track_id': track['track_id'], 'scope_id': track['scope_id'], 'epoch_id': epoch['epoch_id'],
                'preferred_pdf_source_id': epoch['preferred_pdf_source_id'],
                'compatible_measurement_rows': len(compatible), 'candidate_measurement_rows': len(present),
                'actual_collection_dates': ';'.join(sorted({cell(row['collection_date']) for row in present})),
                'status': 'REPORTED_DEFINITION_PRESENT' if compatible else 'NO_COMPATIBLE_MEASUREMENT_IN_PINNED_SOURCE_EPOCH',
                'reason': epoch.get('availability_note', '') + '; absence row is an audit disposition, not a fabricated observation',
                'methodology_source': 'Frozen source report epoch/record allowlist; exact source rows preserved in all_raw_measurements.csv',
                'no_observation_created': True, 'model_eligible': False, 'forecast_eligible': False,
            })
    return output
