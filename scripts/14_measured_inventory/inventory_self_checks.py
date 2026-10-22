#!/usr/bin/env python3
"""Scientific synthetic edge-case fixtures; execute only on genuine Puma."""
from hpc_guard import require_hpc
execution = require_hpc()

from copy import deepcopy
from pathlib import Path
import json
import sys
from common import require, save
from inventory_core import (value_disposition, match_track, candidate_measurement,
                            record_disposition, confirmed_domain_joins,
                            mark_repeated_lab_records, support_inventory,
                            track_availability, metadata_indexes, raw_json,
                            require_current_runtime_and_stage)


def fixtures():
    record = {
        'record_id': 'FIX_A', 'sample_name': 'fixed_label', 'farm': 'Whipstone',
        'system': 'Annual crops', 'dataset': 'fixed_soil', 'collection_date': '2024-08-14',
        'confirmed_sample_key': 'fixed_plot|2024-08-14', 'candidate_sample_key': 'unapproved_other_key',
        'preferred_pdf_source_id': 'FIX_PDF', 'preferred_pdf_page': 1,
        'original_lab_id': 'fixed_lab', 'intentional_exclusion': '',
    }
    measurement = {
        'record_id': 'FIX_A', 'sample_name': 'fixed_label', 'farm': 'Whipstone',
        'dataset': 'fixed_soil', 'variable': 'fixed_soil_pH ', 'unit': 'pH units',
        'method': None, 'source_id': 'FIX_CSV', 'preferred_pdf_source_id': 'FIX_PDF',
        'preferred_pdf_page': 1, 'value': 8.4, 'source_value': '8.4',
        'text_value': None, 'qualifier': None, 'reporting_limit': None,
        'source_field': 'obsolete unrelated header', 'notes': 'Original header remains provenance',
        'value_basis': 'PDF preferred',
    }
    track = {
        'track_id': 'FIX_PH', 'scope_id': 'WHIPSTONE_ANNUAL',
        'reported_response_definition': '1:1 soil pH', 'reported_method': '1:1',
        'reported_unit': 'pH units', 'forms': [{
            'preferred_pdf_source_id': 'FIX_PDF', 'raw_source_ids': ['FIX_CSV'],
            'raw_variables': ['fixed_soil_pH '], 'raw_units': ['pH units'],
            'raw_methods_allowed': [None], 'record_ids': ['FIX_A', 'FIX_B'],
            'reported_heading': 'Soil pH 1:1',
        }],
    }
    tracks = {
        'tracks': [track],
        'scope_records': {'WHIPSTONE_ANNUAL': {
            'record_ids': ['FIX_A', 'FIX_B'], 'comparator_variables': ['Organic Carbon % C'],
            'epochs': [
                {'epoch_id': 'FIX_2024', 'preferred_pdf_source_id': 'FIX_PDF', 'record_ids': ['FIX_A', 'FIX_B']},
                {'epoch_id': 'FIX_ABSENT', 'preferred_pdf_source_id': 'FIX_OTHER_PDF', 'record_ids': []},
            ],
        }},
    }
    metadata = {}
    workbook_metadata = []
    functional = {}
    for domain, sample in [('bacteria', 'FIX_D'), ('archaea', 'FIX_C')]:
        row = {
            'sample_key': 'fixed_plot|2024-08-14', 'sample_name': sample, 'analysis_sample': sample,
            'farm': 'Whipstone', 'collection_date': '2024-08-14', 'biological_plot_id': 'fixed_physical_plot',
            'analytic_system': 'Annual', 'merge_status': 'merged_technical_replicates_preserved',
            'microbial_domain': domain, 'primary_included': True, 'depth_sensitivity_included': False,
        }
        workbook_metadata.append(row)
        functional[domain] = [deepcopy(row)]
    metadata = metadata_indexes(workbook_metadata, functional)
    return record, measurement, tracks, metadata, workbook_metadata, functional


def main():
    run = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
    require(run == Path(__file__).resolve().parent, 'Execute fixtures from the immutable run copy')
    cfg = json.loads((run / 'CONFIG.json').read_text(encoding='utf-8'))
    entry_gate = require_current_runtime_and_stage(run, cfg, execution)
    record, measurement, tracks, metadata, wb_meta, functional = fixtures()
    checks = []

    def check(name, condition):
        require(condition, 'Scientific inventory fixture failed: ' + name)
        checks.append({'check': name, 'passed': True})

    check('Original finite numeric value preserved', value_disposition(measurement)[0] == 'FINITE_RECORDED_NUMERIC_VALUE')
    for name, updates, expected in [
        ('Missing numeric value held without zero', {'value': None, 'source_value': '--', 'text_value': '--'}, 'HELD_MISSING_OR_TEXT_VALUE'),
        ('Censored text held without limit substitution', {'source_value': '<0.1'}, 'HELD_CENSORED_SOURCE_VALUE'),
        ('Reporting-limit metadata held', {'reporting_limit': 0.1}, 'HELD_SOURCE_QUALIFIER_OR_REPORTING_LIMIT'),
        ('Unknown source qualifier held', {'qualifier': 'unknown source qualification'}, 'HELD_SOURCE_QUALIFIER_OR_REPORTING_LIMIT'),
        ('Nonfinite numeric value held', {'value': float('inf')}, 'HELD_NONFINITE_VALUE'),
        ('Numeric-looking text field not coerced', {'value': '8.4'}, 'HELD_NONNUMERIC_VALUE_FIELD'),
        ('Boolean is not numeric measurement', {'value': True}, 'HELD_MISSING_OR_TEXT_VALUE'),
        ('Numeric and text source conflict held', {'text_value': 'different source text'}, 'HELD_TEXT_NUMERIC_CONFLICT'),
    ]:
        edited = deepcopy(measurement)
        edited.update(updates)
        check(name, value_disposition(edited)[0] == expected)
    check('Exact trailing-space variable and blank original method map separately',
          match_track(measurement, record, tracks)['reviewed_method'] == '1:1'
          and measurement['method'] is None and measurement['variable'].endswith(' '))
    check('Legacy unrelated source_field preserved rather than remapped as analyte',
          match_track(measurement, record, tracks)['mapping_status'] == 'REPORTED_DEFINITION_COMPATIBLE_SOURCE_BACKED'
          and measurement['source_field'] == 'obsolete unrelated header')
    for name, updates in [
        ('Unit mismatch held', {'unit': 'different_units'}),
        ('Unreviewed method held', {'method': 'unrecorded substitute'}),
        ('Unreviewed preferred PDF held', {'preferred_pdf_source_id': 'OTHER_PDF'}),
        ('PDF page mismatch held', {'preferred_pdf_page': 2}),
        ('Wrong measurement source ID held', {'source_id': 'OTHER_CSV'}),
        ('Non-PDF value basis cannot claim preferred report value', {'value_basis': 'unreviewed worksheet'}),
    ]:
        edited = deepcopy(measurement)
        edited.update(updates)
        check(name, match_track(edited, record, tracks)['mapping_status'] == 'HELD_EXACT_SOURCE_FORM_MISMATCH')
    no_space = deepcopy(measurement)
    no_space['variable'] = no_space['variable'].rstrip()
    check('No fuzzy whitespace identifier repair', not match_track(no_space, record, tracks)['candidate_response'])
    organic_carbon = deepcopy(measurement)
    organic_carbon['variable'] = 'Organic Carbon % C'
    check('Organic carbon cannot extend LOI track',
          match_track(organic_carbon, record, tracks)['mapping_status'] == 'HELD_NON_EQUIVALENT_RESPONSE_COMPARATOR')
    identity = record_disposition(record, 'WHIPSTONE_ANNUAL', metadata)
    check('Confirmed exact plot/event identity uses no provisional alias',
          identity['record_identity_confirmed'] and identity['confirmed_physical_plot_id'] == 'fixed_physical_plot')
    candidates = [candidate_measurement(measurement, record, identity, tracks, 1)]
    original_before = raw_json(measurement)
    mark_repeated_lab_records(candidates)
    check('Single finite source row remains a protocol-review candidate only',
          candidates[0]['prospective_single_plot_event_value_eligible']
          and candidates[0]['model_eligible'] is False and candidates[0]['forecast_eligible'] is False)
    second_record = deepcopy(record)
    second_record['record_id'] = 'FIX_B'
    second_record['original_lab_id'] = 'different_lab'
    second_measurement = deepcopy(measurement)
    second_measurement['record_id'] = 'FIX_B'
    second_identity = record_disposition(second_record, 'WHIPSTONE_ANNUAL', metadata)
    repeated = [candidate_measurement(measurement, record, identity, tracks, 1),
                candidate_measurement(second_measurement, second_record, second_identity, tracks, 2)]
    mark_repeated_lab_records(repeated)
    check('Repeated lab records remain two rows but prospective plot-event value held',
          len(repeated) == 2 and all(row['repeated_plot_event_lab_rows'] for row in repeated)
          and all(row['prospective_single_plot_event_value_eligible'] is False for row in repeated))
    joins = confirmed_domain_joins(record, identity, metadata) + confirmed_domain_joins(second_record, second_identity, metadata)
    support = support_inventory(repeated, joins, tracks)
    check('Repeated lab rows count one confirmed field plot/event per domain',
          support[0]['source_measurement_rows'] == 2
          and support[0]['distinct_confirmed_physical_plots'] == 1
          and support[0]['bacteria_distinct_confirmed_joined_plot_events'] == 1
          and support[0]['archaea_distinct_preserved_specimen_ids'] == 1)
    within_record = [deepcopy(repeated[0]), deepcopy(repeated[0])]
    within_record[1]['inventory_row_id'] = 'different_measurement_row'
    mark_repeated_lab_records(within_record)
    check('Duplicate measurements within one lab record are visible and prospectively held',
          all(row['plot_event_group_record_count'] == 1 for row in within_record)
          and all(row['plot_event_group_measurement_count'] == 2 for row in within_record)
          and all(row['prospective_single_plot_event_value_eligible'] is False for row in within_record))
    conflict = deepcopy(within_record)
    conflict[1]['raw_value'] = 9.1
    conflict[1]['raw_measurement_json'] = '{"synthetic_conflicting_source_row":true}'
    mark_repeated_lab_records(conflict)
    check('Conflicting duplicate record-response values retain explicit conflict hold',
          all(row['response_conflict_status'] == 'DUPLICATE_RECORD_RESPONSE_VALUES_OR_DEFINITIONS_DIFFER' for row in conflict)
          and all(row['prospective_single_plot_event_value_eligible'] is False for row in conflict))
    unconfirmed_duplicate = deepcopy(within_record)
    for row in unconfirmed_duplicate:
        row['record_identity_confirmed'] = False
        row['confirmed_physical_plot_id'] = ''
    mark_repeated_lab_records(unconfirmed_duplicate)
    check('Unconfirmed duplicate source rows are flagged without inventing physical identity',
          all(row['duplicate_source_record_response_rows'] for row in unconfirmed_duplicate)
          and all(row['exact_source_row_duplicate_count'] == 2 for row in unconfirmed_duplicate)
          and all(row['prospective_single_plot_event_value_eligible'] is False for row in unconfirmed_duplicate))
    check('Source values never averaged or overwritten', raw_json(measurement) == original_before
          and repeated[0]['raw_value'] == measurement['value']
          and repeated[1]['raw_value'] == second_measurement['value'])
    missing_key = deepcopy(record)
    missing_key['confirmed_sample_key'] = None
    unconfirmed = record_disposition(missing_key, 'WHIPSTONE_ANNUAL', metadata)
    check('Candidate-only alias never creates physical or microbial identity',
          not unconfirmed['record_identity_confirmed']
          and all(not row['confirmed_join'] for row in confirmed_domain_joins(missing_key, unconfirmed, metadata)))
    malformed_date = deepcopy(record)
    malformed_date['collection_date'] = '2024-02-31'
    malformed_date['confirmed_sample_key'] = 'fixed_plot|2024-02-31'
    check('Impossible calendar date held unchanged',
          record_disposition(malformed_date, 'WHIPSTONE_ANNUAL', metadata)['record_identity_status'] == 'HELD_CONFIRMED_KEY_DATE_CONFLICT'
          and malformed_date['collection_date'] == '2024-02-31')
    excluded = deepcopy(record)
    excluded['intentional_exclusion'] = 'Researcher exclusion retained'
    excluded_identity = record_disposition(excluded, 'WHIPSTONE_ANNUAL', metadata)
    excluded_row = candidate_measurement(measurement, excluded, excluded_identity, tracks, 3)
    check('Intentional exclusion remains visible but ineligible',
          excluded_row['raw_value'] == measurement['value']
          and excluded_row['quantitative_value_inventory_eligible'] is False)
    unavailable_domain = deepcopy(metadata)
    unavailable_domain['archaea']['functional'] = {}
    domain_joins = confirmed_domain_joins(record, identity, unavailable_domain)
    check('Missing domain counterpart never fabricated', domain_joins[0]['confirmed_join']
          and domain_joins[1]['confirmed_join'] is False and domain_joins[1]['analysis_sample'] == '')
    check('Technical library merge identity retained unsplit',
          all(row['microbial_merge_status'] == 'merged_technical_replicates_preserved' for row in confirmed_domain_joins(record, identity, metadata)))
    conflicting = deepcopy(functional)
    conflicting['bacteria'][0]['biological_plot_id'] = 'invented_plot'
    refused = False
    try:
        metadata_indexes(wb_meta, conflicting)
    except RuntimeError:
        refused = True
    check('Conflicting corrected/functional physical identities refuse join', refused)
    availability = track_availability(candidates, tracks)
    check('Absent report epoch creates audit disposition without a measurement',
          availability[1]['compatible_measurement_rows'] == 0
          and availability[1]['no_observation_created'] is True
          and len(candidates) == 1)
    soil_only = {
        'record_id': 'R0025', 'original_lab_id': '34082', 'source_id': 'S04',
        'preferred_pdf_source_id': 'S25', 'preferred_pdf_page': 10, 'collection_date': '2024-06-06',
        'original_sample_label': '50:50 SHEEP 1 EAST', 'confirmed_sample_key': None,
        'candidate_sample_key': None, 'intentional_exclusion': '',
    }
    soil_only_identity = record_disposition(soil_only, 'OATMAN_OLDER', metadata)
    check('Documented June soil-only remains without a physical key or 16S join',
          soil_only_identity['record_identity_confirmed'] is False
          and all(not row['confirmed_join'] for row in confirmed_domain_joins(soil_only, soil_only_identity, metadata)))
    save(run / 'audit/inventory_self_checks.json', {
        'status': 'PASS', 'passed': True, 'execution': execution,
        'entry_gate': entry_gate,
        'fixture_kind': 'SYNTHETIC_SOURCE_PRESERVATION_AND_IDENTITY_EDGE_CASES',
        'real_source_data_read_by_fixtures': False,
        'measurement_value_aggregation_performed': False,
        'models_fitted': False, 'statistical_tests_performed': False,
        'predictive_validation_established': False, 'ten_year_forecast_generated': False,
        'checks': checks,
    })
    print('PUMA_INVENTORY_SELF_CHECKS=PASS')


if __name__ == '__main__':
    main()
