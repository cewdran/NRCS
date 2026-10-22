#!/usr/bin/env python3
"""Extract source-preserving measured-response/identity support on real Puma."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
from datetime import datetime, timezone
import json
import sys
from common import require, sha, save, table, readcsv, verify_staged_sources
from inventory_core import (validate_workbook_table, unique_index, metadata_indexes,
                            scope_for_record, record_disposition, candidate_measurement,
                            confirmed_domain_joins, mark_repeated_lab_records,
                            support_inventory, track_availability, original_columns,
                            raw_json, cell, blank, require_current_runtime_and_stage)

RAW_FIELDS = ['inventory_row_id', 'inventory_source', 'source_row_number', 'source_role',
              'record_id', 'scope_id', 'candidate_response', 'mapping_status',
              'mapping_reason', 'raw_measurement_json']
MEASUREMENT_FIELDS = [
    'inventory_row_id', 'record_id', 'scope_id', 'track_id', 'inventory_source', 'source_role',
    'source_row_number', 'collection_date', 'confirmed_sample_key', 'confirmed_physical_plot_id',
    'candidate_sample_key_preserved_only', 'analytic_system', 'record_identity_status',
    'record_identity_reason', 'record_excluded', 'record_identity_confirmed',
    'raw_variable', 'raw_value', 'raw_source_value', 'raw_text_value', 'raw_unit', 'raw_method',
    'raw_qualifier', 'raw_reporting_limit', 'raw_source_id', 'raw_source_location', 'raw_source_field',
    'raw_preferred_pdf_source_id', 'raw_preferred_pdf_page', 'raw_lab_id', 'raw_sample_label',
    'raw_value_basis', 'raw_notes', 'laboratory_received', 'laboratory_reported',
    'field_depth_protocol', 'depth_as_reported', 'candidate_response', 'mapping_status',
    'mapping_reason', 'reviewed_response_definition', 'reviewed_method', 'reviewed_unit',
    'reviewed_report_heading', 'method_source_id', 'method_source_sha256', 'value_status', 'value_reason',
    'quantitative_value_inventory_eligible', 'confirmed_plot_event_inventory_eligible',
    'measurement_disposition', 'measurement_reason', 'repeated_plot_event_lab_rows',
    'plot_event_group_record_count', 'plot_event_group_measurement_count', 'aggregation_rule_status',
    'prospective_single_plot_event_value_eligible', 'prospective_value_status', 'model_eligible', 'forecast_eligible',
    'source_record_response_row_count', 'exact_source_row_duplicate_count',
    'duplicate_source_record_response_rows', 'response_conflict_status',
    'raw_measurement_json', 'raw_record_json',
]
RECORD_FIELDS = [
    'inventory_record_id', 'record_id', 'inventory_source', 'scope_id', 'collection_date',
    'confirmed_sample_key', 'candidate_sample_key_preserved_only', 'confirmed_physical_plot_id',
    'analytic_system', 'record_identity_status', 'record_identity_reason', 'record_excluded',
    'record_identity_confirmed', 'raw_record_json', 'methodology_source',
]
JOIN_FIELDS = [
    'record_id', 'scope_id', 'domain', 'collection_date', 'confirmed_sample_key',
    'confirmed_physical_plot_id', 'candidate_sample_key_preserved_only', 'join_status', 'join_reason',
    'microbial_sample_name', 'analysis_sample', 'microbial_primary_included',
    'microbial_depth_sensitivity_included', 'microbial_merge_status', 'microbial_identity_json',
    'confirmed_join', 'methodology_source', 'model_eligible', 'forecast_eligible',
]
SUPPORT_FIELDS = [
    'track_id', 'scope_id', 'collection_date', 'source_measurement_rows',
    'distinct_source_lab_record_ids', 'compatible_reported_definition_rows',
    'finite_uncensored_compatible_unexcluded_rows', 'held_source_rows',
    'distinct_confirmed_physical_plots', 'distinct_confirmed_plot_events',
    'finite_soil_only_or_unconfirmed_identity_rows', 'repeated_plot_event_lab_rows_present',
    'bacteria_distinct_confirmed_joined_plot_events', 'bacteria_distinct_preserved_specimen_ids',
    'archaea_distinct_confirmed_joined_plot_events', 'archaea_distinct_preserved_specimen_ids',
    'status', 'reason', 'methodology_source', 'model_eligible', 'forecast_eligible',
]
AVAILABILITY_FIELDS = [
    'track_id', 'scope_id', 'epoch_id', 'preferred_pdf_source_id', 'compatible_measurement_rows',
    'candidate_measurement_rows', 'actual_collection_dates', 'status', 'reason',
    'methodology_source', 'no_observation_created', 'model_eligible', 'forecast_eligible',
]


def check_tracks(tracks, record_index, run):
    require(tracks.get('scope_records') and tracks.get('tracks'), 'Frozen response track contract missing')
    ids = [track['track_id'] for track in tracks['tracks']]
    require(len(ids) == len(set(ids)), 'Duplicate response track identifier')
    for scope, definition in tracks['scope_records'].items():
        require(len(definition['record_ids']) == len(set(definition['record_ids'])), 'Repeated scope record identifier')
        for rid in definition['record_ids']:
            require(rid in record_index, 'Frozen selected record absent in corrected source: ' + rid)
            expected = tracks['expected_record_sources'][rid]
            for field, value in expected.items():
                require(record_index[rid].get(field) == value,
                        'Exact selected source record differs from frozen report/date/identity metadata: ' + rid + ' ' + field)
            require(record_index[rid]['farm'] == definition['farm_literal']
                    and record_index[rid]['system'] == definition['system_literal'],
                    'Frozen selected farm/system mismatch: ' + rid)
            datasets = definition.get('dataset_literals', [definition.get('dataset_literal')])
            require(record_index[rid]['dataset'] in datasets, 'Frozen selected dataset mismatch: ' + rid)
        for epoch in definition['epochs']:
            require(set(epoch['record_ids']).issubset(definition['record_ids']), 'Epoch record outside scope')
    for track in tracks['tracks']:
        require(track['scope_id'] in tracks['scope_records'], 'Track scope absent')
        for form in track['forms']:
            require(set(form['record_ids']).issubset(tracks['scope_records'][track['scope_id']]['record_ids']),
                    'Track form records outside its frozen scope')
    for source_id, source in tracks['source_reports'].items():
        report = run / 'review/source_reports' / (source_id + '.pdf')
        require(report.is_file() and sha(report) == source['sha256'], 'Frozen method PDF changed: ' + source_id)


def main():
    run = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
    require(run == Path(__file__).resolve().parent, 'Execute the scientific engine from its immutable run copy')
    cfg = json.loads((run / 'CONFIG.json').read_text(encoding='utf-8'))
    entry_gate = require_current_runtime_and_stage(run, cfg, execution)
    source_before = verify_staged_sources(run, cfg)
    controls = ['RESPONSE_TRACKS.json', 'PROTOCOL.txt', 'inventory_core.py', 'inventory_outcomes.py',
                'inventory_self_checks.py']
    control_before = {name: sha(run / name) for name in controls}
    self_checks = json.loads((run / 'audit/inventory_self_checks.json').read_text(encoding='utf-8'))
    require(self_checks.get('status') == 'PASS' and self_checks.get('passed') is True,
            'Puma scientific fixture checks did not pass')
    require(self_checks.get('execution', {}).get('job_id') == execution['job_id']
            and self_checks.get('execution', {}).get('node') == execution['node'],
            'Scientific fixtures do not belong to this genuine main Puma execution')
    root = run / 'source_snapshot/corrected'
    workbook_path = root / 'payload/inputs/workbook_data.json'
    workbook = json.loads(workbook_path.read_text(encoding='utf-8'))
    tables = workbook['tables']
    records = validate_workbook_table(tables['Sample records'],
        ['record_id', 'farm', 'dataset', 'collection_date', 'confirmed_sample_key',
         'candidate_sample_key', 'preferred_pdf_source_id', 'preferred_pdf_page', 'intentional_exclusion'],
        'Sample records')
    measurements = validate_workbook_table(tables['Measurements'],
        ['record_id', 'variable', 'value', 'unit', 'method', 'source_id', 'source_field',
         'preferred_pdf_source_id', 'preferred_pdf_page', 'qualifier', 'reporting_limit'], 'Measurements')
    metadata = validate_workbook_table(tables['16S samples'],
        ['sample_key', 'microbial_domain', 'sample_name', 'analysis_sample', 'collection_date',
         'farm', 'biological_plot_id', 'analytic_system', 'merge_status'], '16S samples')
    sources = validate_workbook_table(tables['Sources'], ['source_id', 'filename', 'sha256'], 'Sources')
    source_index = unique_index(sources, 'source_id', 'Corrected Sources')
    record_index = unique_index(records, 'record_id', 'Corrected Sample records')
    functional = {domain: readcsv(run / 'source_snapshot/functional/validated' / domain / 'sample_metadata.csv')
                  for domain in ('bacteria', 'archaea')}
    metadata_index = metadata_indexes(metadata, functional)
    tracks = json.loads((run / 'RESPONSE_TRACKS.json').read_text(encoding='utf-8'))
    check_tracks(tracks, record_index, run)
    # Where a preferred source is indexed in the full corrected Sources sheet,
    # require the existing source hash to agree with the separately frozen PDF.
    for source_id, report in tracks['source_reports'].items():
        if source_id in source_index:
            require(source_index[source_id]['sha256'] == report['sha256'],
                    'Corrected source index and reviewed method PDF hash disagree')
    identities = {}
    record_rows = []
    joins = []
    for ordinal, record in enumerate(records, start=1):
        scope = scope_for_record(record['record_id'], tracks)
        identity = record_disposition(record, scope, metadata_index)
        identities[record['record_id']] = identity
        record_rows.append({
            'inventory_record_id': 'WB_RECORD_' + str(ordinal).zfill(6),
            'inventory_source': 'corrected_workbook/Sample records', **identity,
            'raw_record_json': raw_json(record), **original_columns(record, 'original_record__'),
            'methodology_source': 'Exact corrected Sample records; collection_date/confirmed key preserved; report dates separate',
        })
        if scope:
            joins.extend(confirmed_domain_joins(record, identity, metadata_index))
    all_rows = []
    candidates = []
    for ordinal, measurement in enumerate(measurements, start=1):
        rid = measurement['record_id']
        require(rid in record_index, 'Source measurement references an absent record: ' + cell(rid))
        row = candidate_measurement(measurement, record_index[rid], identities[rid], tracks, ordinal)
        all_rows.append(row)
        if row['candidate_response']:
            candidates.append(row)
    mark_repeated_lab_records(candidates)
    # Laboratory transcriptions remain an explicit audit copy of the original
    # evidence, never a second biological observation or a second support row.
    labs = readcsv(root / 'payload/corrections/laboratory_records.csv')
    lab_measurements = readcsv(root / 'payload/corrections/laboratory_measurements.csv')
    crosswalk = readcsv(root / 'payload/corrections/legacy_record_crosswalk.csv')
    lab_index = unique_index(labs, 'source_record_key', 'Laboratory source records')
    legacy_index = {}
    for row in crosswalk:
        source_key = row.get('source_record_key')
        if blank(source_key):
            continue
        require(source_key not in legacy_index, 'Duplicate laboratory-to-legacy crosswalk key')
        legacy_index[source_key] = row
    transcription_reconciliation = []
    for ordinal, lab in enumerate(labs, start=1):
        link = legacy_index.get(lab['source_record_key'])
        rid = link.get('legacy_record_id') if link else ''
        scope = scope_for_record(rid, tracks) if rid else ''
        record_rows.append({
            'inventory_record_id': 'LAB_RECORD_' + str(ordinal).zfill(6),
            'record_id': rid, 'inventory_source': 'corrections/laboratory_records.csv',
            'scope_id': scope, 'collection_date': lab.get('collection_date'),
            'confirmed_sample_key': lab.get('matched_16S_sample_key'),
            'candidate_sample_key_preserved_only': '', 'confirmed_physical_plot_id': '', 'analytic_system': '',
            'record_identity_status': 'AUDIT_TRANSCRIPTION_COPY_ONLY',
            'record_identity_reason': 'Original transcription/crosswalk preserved; never an additional specimen, plot or eligibility source',
            'record_excluded': identities[rid]['record_excluded'] if rid in identities else '',
            'record_identity_confirmed': False,
            'raw_record_json': raw_json(lab), **original_columns(lab, 'original_laboratory_record__'),
            'methodology_source': 'Source transcription and exact legacy_record_crosswalk; no new label matching',
        })
    for ordinal, measurement in enumerate(lab_measurements, start=1):
        key = measurement['source_record_key']
        require(key in lab_index, 'Laboratory measurement references an absent transcription record')
        lab = lab_index[key]
        link = legacy_index.get(key)
        rid = link.get('legacy_record_id') if link else ''
        scope = scope_for_record(rid, tracks) if rid else ''
        matched = [row for row in all_rows if row['inventory_source'] == 'corrected_workbook/Measurements'
                   and row['record_id'] == rid and row['raw_variable'] == measurement['report_heading']
                   and row['raw_preferred_pdf_source_id'] == lab['source_id']
                   and cell(row['raw_preferred_pdf_page']) == measurement['pdf_page']]
        is_candidate = bool(scope and any(row['candidate_response'] for row in matched))
        row = {
            'inventory_row_id': 'LAB_MEASUREMENT_' + str(ordinal).zfill(6),
            'inventory_source': 'corrections/laboratory_measurements.csv', 'source_row_number': ordinal,
            'source_role': 'AUDIT_TRANSCRIPTION_COPY_NOT_INDEPENDENT_SUPPORT', 'record_id': rid,
            'scope_id': scope, 'candidate_response': is_candidate,
            'mapping_status': 'AUDIT_TRANSCRIPTION_COPY_ONLY',
            'mapping_reason': 'Exact source_record_key/crosswalk preserved; workbook inventory is the sole support-count source',
            'raw_measurement_json': raw_json(measurement), 'raw_record_json': raw_json(lab),
            'raw_variable': measurement['report_heading'], 'raw_source_value': measurement['value_as_reported'],
            'raw_unit': measurement['unit'], 'raw_method': measurement['method'],
            'raw_source_id': lab['source_id'], 'raw_source_location': measurement['source_file'],
            'raw_preferred_pdf_source_id': lab['source_id'], 'raw_preferred_pdf_page': measurement['pdf_page'],
            'raw_lab_id': measurement['lab_id'], 'raw_sample_label': measurement['report_label'],
            'collection_date': lab.get('collection_date'), 'confirmed_sample_key': lab.get('matched_16S_sample_key'),
            'quantitative_value_inventory_eligible': False, 'confirmed_plot_event_inventory_eligible': False,
            'model_eligible': False, 'forecast_eligible': False,
            **original_columns(measurement, 'original_laboratory_measurement__'),
        }
        all_rows.append(row)
        if scope:
            require(len(matched) <= 1, 'Transcription maps to ambiguous corrected source rows')
            if matched:
                require(measurement['unit'] == matched[0]['raw_unit']
                        and measurement['method'] == cell(matched[0]['raw_method'])
                        and measurement['value_as_reported'] == cell(matched[0]['raw_source_value']),
                        'Exact laboratory transcription and corrected workbook source strings disagree')
            transcription_reconciliation.append({
                'source_record_key': key, 'legacy_record_id': rid, 'scope_id': scope,
                'report_heading': measurement['report_heading'],
                'corrected_inventory_row_id': matched[0]['inventory_row_id'] if matched else '',
                'status': 'EXACT_CORRECTED_TRANSCRIPTION_STRINGS_AGREE' if matched else 'SOURCE_TRANSCRIPTION_WITHOUT_DECLARED_CORRECTED_ROW',
                'reason': 'Audit duplicate evidence only; no additional biological observation or value aggregation',
            })
    out = run / 'results'
    out.mkdir(exist_ok=True)
    table(out / 'all_raw_measurements.csv', all_rows, RAW_FIELDS)
    table(out / 'measurement_inventory.csv', candidates, MEASUREMENT_FIELDS)
    table(out / 'record_inventory.csv', record_rows, RECORD_FIELDS)
    table(out / 'confirmed_microbiome_joins.csv', joins, JOIN_FIELDS)
    table(out / 'support_inventory.csv', support_inventory(candidates, joins, tracks), SUPPORT_FIELDS)
    table(out / 'track_availability.csv', track_availability(candidates, tracks), AVAILABILITY_FIELDS)
    table(out / 'source_index_inventory.csv', sources, tables['Sources']['columns'])
    table(out / 'transcription_reconciliation.csv', transcription_reconciliation,
          ['source_record_key', 'legacy_record_id', 'scope_id', 'report_heading',
           'corrected_inventory_row_id', 'status', 'reason'])
    source_after = verify_staged_sources(run, cfg)
    require(source_before == source_after, 'Original/snapshot/review source hashes changed during scientific inventory')
    require(control_before == {name: sha(run / name) for name in controls}, 'Frozen engine/protocol changed during inventory')
    preservation = {
        'status': 'ALL_STAGED_ORIGINALS_SNAPSHOTS_AND_REVIEW_LOCKS_UNCHANGED',
        'execution': execution, 'source_files_changed': False, 'source_integrity_passed': True,
        'source_files_verified_unchanged_after_audit': True,
        'before': source_before, 'after': source_after, 'control_sha256_before_after': control_before,
        'entry_gate': entry_gate,
        'source_manifest': 'audit/source_manifest.csv', 'source_manifest_sha256': sha(run / 'audit/source_manifest.csv'),
    }
    save(run / 'audit/INVENTORY_SOURCE_PRESERVATION.json', preservation)
    schema = {
        'status': 'MEASURED_INVENTORY_OUTPUT_SCHEMA', 'execution': execution,
        'candidate_measurement_schema': MEASUREMENT_FIELDS, 'record_schema': RECORD_FIELDS,
        'join_schema': JOIN_FIELDS, 'support_schema': SUPPORT_FIELDS, 'availability_schema': AVAILABILITY_FIELDS,
        'all_raw_source_rows_preserved': True, 'out_of_scope_values_not_scientifically_evaluated': True,
        'raw_json_retains_types_nulls_and_exact_strings': True,
        'nonfinite_raw_numeric_tokens_if_any_are_archival_only_never_eligible': True,
        'original_fields_never_overwritten_by_reviewed_mappings': True,
        'laboratory_transcriptions_are_audit_copies_not_independent_support': True,
    }
    save(out / 'INVENTORY_SCHEMA.json', schema)
    artifacts = [out / name for name in (
        'all_raw_measurements.csv', 'measurement_inventory.csv', 'record_inventory.csv',
        'confirmed_microbiome_joins.csv', 'support_inventory.csv', 'track_availability.csv',
        'source_index_inventory.csv', 'transcription_reconciliation.csv', 'INVENTORY_SCHEMA.json')]
    artifacts += [run / 'audit/inventory_self_checks.json', run / 'audit/INVENTORY_SOURCE_PRESERVATION.json']
    manifest_rows = [{'relative_path': str(path.relative_to(run)), 'sha256': sha(path),
                      'size_bytes': path.stat().st_size} for path in artifacts]
    table(out / 'ENGINE_OUTPUT_MANIFEST.csv', manifest_rows, ['relative_path', 'sha256', 'size_bytes'])
    status = {
        'status': 'HPC_MEASURED_OUTCOME_INVENTORY_COMPLETED_REVIEW_PENDING',
        'execution': execution, 'completed_utc': datetime.now(timezone.utc).isoformat(),
        'source_corrected_job': '24067160', 'source_functional_job': '24067682',
        'inventory_computed': True, 'scientific_inventory_completed': True,
        'source_files_changed': False, 'source_integrity_passed': True,
        'source_files_verified_unchanged_after_audit': True,
        'package_integrity_passed': True,
        'measurement_values_transformed': False, 'measurement_values_aggregated': False,
        'new_plot_or_specimen_aliases_created': False, 'microbial_response_values_joined': False,
        'technical_replicates_split': False, 'models_fitted': False,
        'statistical_tests_performed': False, 'statistical_tests_run': False, 'predictive_validation_established': False,
        'forecasts_generated': False, 'ten_year_forecast_generated': False, 'numerical_ten_year_forecast': False,
        'model_eligibility_established': False, 'calibration_established': False,
        'sampling_equivalence_established': False,
        'scientific_review_pending': True, 'visual_review_pending': True,
        'output_manifest': 'results/ENGINE_OUTPUT_MANIFEST.csv',
        'output_manifest_path': 'results/ENGINE_OUTPUT_MANIFEST.csv',
        'output_manifest_sha256': sha(out / 'ENGINE_OUTPUT_MANIFEST.csv'),
        'source_preservation': 'audit/INVENTORY_SOURCE_PRESERVATION.json',
        'source_preservation_sha256': sha(run / 'audit/INVENTORY_SOURCE_PRESERVATION.json'),
        'response_tracks_sha256': control_before['RESPONSE_TRACKS.json'],
        'protocol_sha256': control_before['PROTOCOL.txt'],
        'self_checks_sha256': sha(run / 'audit/inventory_self_checks.json'),
        'entry_gate': entry_gate,
        'reported_output_row_counts': {
            'all_raw_measurements': len(all_rows), 'candidate_measurement_inventory': len(candidates),
            'record_inventory': len(record_rows), 'confirmed_microbiome_join_inventory': len(joins),
        },
        'review': 'Source-preserving response/identity support inventory only; reported definition compatibility is not calibration, sampling equivalence, model eligibility, causal evidence or forecast support.',
    }
    save(out / 'INVENTORY_STATUS.json', status)
    print(json.dumps(status, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
