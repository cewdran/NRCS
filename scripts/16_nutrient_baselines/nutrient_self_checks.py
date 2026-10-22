#!/usr/bin/env python3
"""Separate-response synthetic design fixtures; genuine Puma execution only."""
from hpc_guard import require_hpc
execution = require_hpc()

from copy import deepcopy
from pathlib import Path
import json
import sys
from common import require, save, sha, safe_file, verify_staged_sources
from nutrient_core import (METHODS, DYNAMIC_FLAGS, RAW_MEASUREMENT_FIELDS, cell,
                           valid_date, current_gate, build_observation_audit,
                           select_training, compute_parameters, freeze_predictions,
                           score_targets, performance, paired_comparison,
                           observed_changes, stable_digest, parameter_support)


FIX_RESPONSES = {
    'W_OM': {'variable': 'FIX Organic Matter LOI, %', 'unit': '%', 'method': 'LOI',
             'definition': 'FIX reported LOI organic matter'},
    'W_NO3': {'variable': 'FIX Nitrate-N KCl, ppm N', 'unit': 'ppm N', 'method': 'KCl',
              'definition': 'FIX reported KCl nitrate-N'},
}


def synthetic_inputs(definitions):
    """Create typed source/CSV pairs only after genuine compute guards pass."""
    measurements, records, expected, rounds = [], [], {}, {}
    forms = {track: {} for track in FIX_RESPONSES}
    for index, (rid, plot, actual, om_value, no3_value) in enumerate(definitions, start=1):
        annual, report = actual[:4], 'FIX_PDF_' + actual[:4]
        record = {
            'record_id': rid, 'source_id': 'FIX_RECORD_SOURCE', 'farm': 'Whipstone',
            'system': 'Annual crops', 'dataset': 'FIX_SOIL', 'sample_name': 'SOURCE_' + rid,
            'collection_date': actual, 'confirmed_sample_key': plot + '|' + actual,
            'candidate_sample_key': 'UNAPPROVED_OTHER_LABEL', 'original_lab_id': 'LAB_' + rid,
            'original_sample_label': 'Original sample label ' + rid,
            'preferred_pdf_source_id': report, 'preferred_pdf_page': index,
            'intentional_exclusion': '',
        }
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=True)
        identity = {
            'record_id': rid, 'inventory_source': 'corrected_workbook/Sample records',
            'collection_date': actual, 'confirmed_sample_key': record['confirmed_sample_key'],
            'confirmed_physical_plot_id': plot, 'analytic_system': 'Annual',
            'record_identity_status': 'EXPLICIT_CONFIRMED_PLOT_EVENT_IDENTITY',
            'record_identity_confirmed': 'True', 'record_excluded': 'False', 'raw_record_json': encode(record),
        }
        records.append(identity)
        expected[rid] = {k: record[k] for k in ('source_id', 'preferred_pdf_source_id', 'preferred_pdf_page',
                                               'original_lab_id', 'collection_date', 'confirmed_sample_key')}
        rounds.setdefault(annual, {'round_id': annual, 'record_ids': [], 'actual_collection_dates': []})
        rounds[annual]['record_ids'].append(rid)
        if actual not in rounds[annual]['actual_collection_dates']:
            rounds[annual]['actual_collection_dates'].append(actual)
        for track, value in (('W_OM', om_value), ('W_NO3', no3_value)):
            definition = FIX_RESPONSES[track]
            raw = {
                'record_id': rid, 'farm': 'Whipstone', 'system': 'Annual crops', 'dataset': 'FIX_SOIL',
                'sample_name': 'SOURCE_' + rid, 'variable': definition['variable'], 'value': value,
                'source_value': cell(value), 'text_value': None, 'unit': definition['unit'],
                'method': definition['method'], 'qualifier': None, 'reporting_limit': None,
                'source_id': 'FIX_MEASUREMENT_SOURCE',
                'source_location': 'synthetic.csv row ' + str(index),
                'source_field': 'Obsolete unrelated header retained',
                'preferred_pdf_source_id': report, 'preferred_pdf_page': index,
                'value_basis': 'PDF preferred synthetic fixture', 'notes': 'Original source fixture note',
            }
            row = {
                **identity, 'inventory_row_id': 'WB_MEASUREMENT_' + str(index).zfill(6) + '_' + track,
                'source_row_number': str(index), 'inventory_source': 'corrected_workbook/Measurements',
                'source_role': 'CORRECTED_PRIMARY_MEASUREMENT_NOT_A_NEW_OBSERVATION',
                'scope_id': 'WHIPSTONE_ANNUAL', 'track_id': track,
                'mapping_status': 'REPORTED_DEFINITION_COMPATIBLE_SOURCE_BACKED',
                'raw_lab_id': record['original_lab_id'], 'raw_sample_label': record['original_sample_label'],
                'raw_measurement_json': encode(raw), 'raw_record_json': encode(record),
                **{column: cell(raw.get(field)) for column, field in RAW_MEASUREMENT_FIELDS.items()},
                'model_eligible': 'False', 'forecast_eligible': 'False',
                'reviewed_method': definition['method'],
                'depth_as_reported': 'source literal only', 'field_depth_protocol': 'separate field protocol',
            }
            measurements.append(row)
            form = forms[track].setdefault(report, {
                'preferred_pdf_source_id': report, 'raw_source_ids': ['FIX_MEASUREMENT_SOURCE'],
                'raw_variables': [definition['variable']], 'raw_units': [definition['unit']],
                'raw_methods_allowed': [definition['method']], 'record_ids': [],
            })
            form['record_ids'].append(rid)
    scope = {'WHIPSTONE_ANNUAL': {'farm_literal': 'Whipstone', 'system_literal': 'Annual crops',
                                 'dataset_literals': ['FIX_SOIL'], 'expected_analytic_system': 'Annual'}}
    responses = [{
        'track_id': track, 'scope_id': 'WHIPSTONE_ANNUAL', 'forms': list(forms[track].values()),
        'scopes': deepcopy(scope), 'reported_unit': definition['unit'],
        'reported_response_definition': definition['definition'],
        'authority_inventory_source': 'corrected_workbook/Measurements',
        'authority_source_role': 'CORRECTED_PRIMARY_MEASUREMENT_NOT_A_NEW_OBSERVATION',
        'authority_row_id_prefix': 'WB_MEASUREMENT_',
    } for track, definition in FIX_RESPONSES.items()]
    return measurements, records, {'responses': responses, 'expected_record_sources': expected,
                                    'rounds': list(rounds.values()), 'source_reports': {}}


def change_raw(row, **updates):
    raw = json.loads(row['raw_measurement_json'])
    raw.update(updates)
    row['raw_measurement_json'] = json.dumps(raw, ensure_ascii=False, separators=(',', ':'), allow_nan=True)
    row.update({column: cell(raw.get(field)) for column, field in RAW_MEASUREMENT_FIELDS.items()})


def fold(track):
    definition = FIX_RESPONSES[track]
    return {'fold_id': track + '_2024_FROM_2023', 'track_id': track,
            'unit': definition['unit'], 'reported_response_definition': definition['definition'],
            'training_round_ids': ['2023'], 'target_round_id': '2024', 'cutoff_date': '2024-01-01'}


def main():
    run = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
    require(run == Path(__file__).resolve().parent, 'Execute scientific fixtures only from immutable run copy')
    cfg = json.loads(safe_file(run, 'CONFIG.json').read_text(encoding='utf-8'))
    gate = current_gate(run, cfg, execution)
    before = verify_staged_sources(run, cfg)
    spec_hash, contract_hash = sha(run / 'RESPONSE_SPEC.json'), sha(run / 'OUTPUT_CONTRACT.json')
    checks = []

    def check(name, condition):
        require(condition, 'Puma synthetic nutrient fixture failed: ' + name)
        checks.append({'check': name, 'passed': True})

    def refuses(name, action):
        refused = False
        try:
            action()
        except RuntimeError:
            refused = True
        check(name, refused)

    source, records, spec = synthetic_inputs([
        ('A23', 'PLOT_A', '2023-11-16', 4.0, 20.0),
        ('B23', 'PLOT_B', '2023-11-16', 10.0, 100.0),
        ('A24', 'PLOT_A', '2024-07-06', 6.0, 30.0),
        ('B24', 'PLOT_B', '2024-08-14', None, 50.0),
    ])
    source_before = deepcopy(source)
    audit, observed = build_observation_audit(source, records, spec)
    check('Each exact response retains finite values despite inherited inventory model/forecast false',
          len(observed) == 7 and all(r['model_eligible'] == 'False' for r in observed))
    check('OM and nitrate on the same field event are distinct response units, not duplicate values',
          all(r['plot_event_source_rows'] == 1 for r in observed)
          and {r['unit'] for r in observed} == {'%', 'ppm N'})
    check('No microbial counterpart required; source physical plot and original source rows retained',
          all('confirmed_join' not in r for r in source) and source == source_before)
    check('Legacy source_field remains provenance rather than an inferred analyte',
          all(r['source_field'] == 'Obsolete unrelated header retained' for r in observed))
    check('No treatment inferred from original sample labels', all(r['treatment'] == '' for r in observed))
    check('Missing organic matter cannot become zero or remove the same nitrate event',
          any(r['record_id'] == 'B24' and r['track_id'] == 'W_OM' and not r['eligible']
              and r['reported_value'] == '' for r in audit)
          and any(r['record_id'] == 'B24' and r['track_id'] == 'W_NO3' for r in observed))
    fitted, frozen = {}, {}
    for track in FIX_RESPONSES:
        f = fold(track)
        fitted[track] = compute_parameters(select_training(observed, f), f)
        frozen[track] = freeze_predictions(fitted[track], f)
        check(track + ' whole July/August target round uses only2023 training and every prior plot',
              {r['annual_round_id'] for r in fitted[track]['training_membership']} == {'2023'}
              and len(frozen[track]) == 4
              and {r['confirmed_physical_plot_id'] for r in frozen[track]} == {'PLOT_A', 'PLOT_B'})
        check(track + ' equal plot mean uses only its own reported response units',
              fitted[track]['equal_plot_training_mean_value'] == (7.0 if track == 'W_OM' else 60.0))
        edited = deepcopy(source)
        for row in edited:
            if row['collection_date'].startswith('2024'):
                change_raw(row, value=100000.0, source_value='100000.0')
        _, changed_observed = build_observation_audit(edited, records, spec)
        changed_fit = compute_parameters(select_training(changed_observed, f), f)
        check(track + ' future numerical outcomes cannot alter training, parameters or frozen predictions',
              changed_fit == fitted[track] and freeze_predictions(changed_fit, f) == frozen[track])
        removed = [r for r in source if not r['collection_date'].startswith('2024')]
        _, removed_observed = build_observation_audit(removed, records, spec)
        check(track + ' future response absence cannot alter prior training or predictions',
              compute_parameters(select_training(removed_observed, f), f) == fitted[track])
    refuses('OM parameters reject a nitrate training row rather than mix response units',
            lambda: compute_parameters([next(r for r in observed if r['track_id'] == 'W_NO3')], fold('W_OM')))
    refuses('Future target row cannot enter prior parameter computation',
            lambda: compute_parameters([next(r for r in observed if r['record_id'] == 'A24')], fold('W_OM')))
    duplicate_prior = fitted['W_OM']['training_membership'] + [deepcopy(fitted['W_OM']['training_membership'][0])]
    refuses('Duplicate prior event cannot be silently deduplicated or averaged',
            lambda: compute_parameters(duplicate_prior, fold('W_OM')))
    unconfirmed, unconfirmed_records = deepcopy(source), deepcopy(records)
    for row in unconfirmed + unconfirmed_records:
        if row['record_id'] == 'A24':
            row.update(confirmed_physical_plot_id='', record_identity_confirmed='False',
                       record_identity_status='HELD_NO_CONFIRMED_IDENTITY')
    unconfirmed_audit, unconfirmed_observed = build_observation_audit(unconfirmed, unconfirmed_records, spec)
    check('Candidate label or specimen key does not invent missing physical plot',
          all(not r['eligible'] for r in unconfirmed_audit if r['record_id'] == 'A24'))
    excluded, excluded_records = deepcopy(source), deepcopy(records)
    for row in excluded + excluded_records:
        if row['record_id'] == 'A24':
            row['record_excluded'] = 'True'
    excluded_audit, _ = build_observation_audit(excluded, excluded_records, spec)
    check('Accepted source exclusion stays visible and held for both response tracks',
          all('HELD_SOURCE_EXCLUSION' in r['eligibility_status'] for r in excluded_audit if r['record_id'] == 'A24'))
    for label, updates in [
        ('Censored response', {'source_value': '<6.0'}),
        ('Qualifier', {'qualifier': 'review required'}),
        ('Reporting limit', {'reporting_limit': 1.0}),
        ('Nonfinite response', {'value': float('inf')}),
        ('Boolean response', {'value': True}),
        ('Numeric text is not coerced', {'value': '6.0'}),
        ('Numeric/text conflict', {'text_value': 'incompatible source text'}),
        ('Different reported unit is not converted', {'unit': 'mg/kg'}),
        ('Different original method', {'method': 'unreviewed'}),
        ('Fuzzy whitespace variable repair is forbidden', {'variable': 'FIX Organic Matter LOI, % '}),
        ('Unreviewed preferred report', {'preferred_pdf_source_id': 'OTHER_PDF'}),
        ('Wrong original source', {'source_id': 'OTHER_SOURCE'}),
        ('Measurement farm mismatch', {'farm': 'OTHER_FARM'}),
        ('Measurement dataset mismatch', {'dataset': 'OTHER_DATASET'}),
        ('Source sample label alias', {'sample_name': 'UNAPPROVED_ALIAS'}),
    ]:
        edited = deepcopy(source)
        edited_row = next(r for r in edited if r['record_id'] == 'A24' and r['track_id'] == 'W_OM')
        change_raw(edited_row, **updates)
        edited_audit, edited_observed = build_observation_audit(edited, records, spec)
        check(label + ' is held unchanged without affecting nitrate eligibility',
              not next(r for r in edited_audit if r['record_id'] == 'A24' and r['track_id'] == 'W_OM')['eligible']
              and any(r['record_id'] == 'A24' and r['track_id'] == 'W_NO3' for r in edited_observed))
    conflict = deepcopy(source)
    change_raw(conflict[0], variable=FIX_RESPONSES['W_NO3']['variable'])
    refuses('Conflicting declared response and original raw analyte fails closed without track choice',
            lambda: build_observation_audit(conflict, records, spec))
    impossible, impossible_records, impossible_spec = synthetic_inputs([
        ('IMPOSSIBLE', 'PLOT_A', '2024-02-31', 6.0, 30.0)])
    impossible_audit, impossible_observed = build_observation_audit(impossible, impossible_records, impossible_spec)
    check('Impossible calendar date stays preserved and held for both responses',
          not valid_date('2024-02-31') and not impossible_observed
          and all('HELD_COLLECTION_DATE_OR_KEY_CONFLICT' in r['eligibility_status']
                  and r['collection_date'] == '2024-02-31' for r in impossible_audit))
    zero_source = deepcopy(source)
    change_raw(next(r for r in zero_source if r['record_id'] == 'A24' and r['track_id'] == 'W_OM'),
               value=0.0, source_value='0.0')
    _, zero_observed = build_observation_audit(zero_source, records, spec)
    check('An actual recorded finite zero is retained as zero rather than missing',
          next(r for r in zero_observed if r['record_id'] == 'A24' and r['track_id'] == 'W_OM')['reported_value'] == 0.0)
    altered = deepcopy(source)
    altered[0]['raw_value'] = '999'
    refuses('Source projection/value mismatch fails closed', lambda: build_observation_audit(altered, records, spec))
    lab_copy = deepcopy(source[0])
    lab_copy.update(inventory_row_id='LAB_AUDIT_COPY', inventory_source='corrections/laboratory_measurements.csv',
                    source_role='AUDIT_TRANSCRIPTION_COPY_NOT_INDEPENDENT_SUPPORT')
    copy_audit, copy_observed = build_observation_audit(source + [lab_copy], records, spec)
    check('Laboratory copy remains visible without adding field support',
          len(copy_observed) == len(observed) and copy_audit[-1]['eligibility_status'] == 'AUDIT_TRANSCRIPTION_COPY_ONLY')
    extra = deepcopy(next(r for r in source if r['record_id'] == 'A24' and r['track_id'] == 'W_OM'))
    extra['inventory_row_id'] = 'WB_MEASUREMENT_EXTRA'
    change_raw(extra, value=None, source_value='<6.0')
    repeated_audit, repeated_observed = build_observation_audit(source + [extra], records, spec)
    check('Finite and censored duplicate OM event rows all held; nitrate remains independent',
          all(not r['eligible'] and 'HELD_MULTIPLE_PLOT_EVENT_VALUES' in r['eligibility_status']
              for r in repeated_audit if r['record_id'] == 'A24' and r['track_id'] == 'W_OM')
          and any(r['record_id'] == 'A24' and r['track_id'] == 'W_NO3' for r in repeated_observed))
    multiple, multiple_records, multiple_spec = synthetic_inputs([
        ('A23', 'PLOT_A', '2023-11-16', 4.0, 20.0),
        ('A24J', 'PLOT_A', '2024-07-06', 6.0, 30.0),
        ('A24A', 'PLOT_A', '2024-08-14', None, 50.0)])
    annual_audit, annual_observed = build_observation_audit(multiple, multiple_records, multiple_spec)
    check('Multiple actual target visits are all held before any finite-value selection',
          all('HELD_MULTIPLE_EVENTS_PER_PLOT_ROUND' in r['eligibility_status']
              for r in annual_audit if r['annual_round_id'] == '2024') and len(annual_observed) == 2)
    missing_om = [r for r in source if not (r['record_id'] == 'A24' and r['track_id'] == 'W_OM')]
    absent_audit, _ = build_observation_audit(missing_om, records, spec)
    placeholder = next(r for r in absent_audit if r['record_id'] == 'A24' and r['track_id'] == 'W_OM')
    check('Missing expected response gets track-specific audit ID, blank observation ID and no fabricated value',
          placeholder['audit_id'] and placeholder['observation_id'] == '' and placeholder['reported_value'] == '')
    outside = deepcopy(source[-2:])
    for row in outside:
        row.update(inventory_row_id=row['inventory_row_id'] + '_OUTSIDE', record_id='OUTSIDE2025',
                   collection_date='2025-08-17')
        change_raw(row, value=float('inf'), source_value='infinite outside evidence')
    outside_audit, outside_observed = build_observation_audit(source + outside, records, spec)
    check('2025 evidence remains copied audit-only without new value eligibility or predictions',
          len(outside_observed) == len(observed)
          and all(r['eligibility_status'] == 'OUTSIDE_FROZEN_WH_ANNUAL_OM_NO3_SCOPE'
                  and not r['eligible'] for r in outside_audit if r['record_id'] == 'OUTSIDE2025'))
    cold, cold_records, cold_spec = synthetic_inputs([
        ('A23', 'PLOT_A', '2023-11-16', 4.0, 20.0), ('C24', 'COLD_PLOT', '2024-07-06', 7.0, 30.0)])
    cold_audit, cold_observed = build_observation_audit(cold, cold_records, cold_spec)
    cf = fold('W_OM')
    cold_fit = compute_parameters(select_training(cold_observed, cf), cf)
    cold_errors = score_targets(freeze_predictions(cold_fit, cf),
                               [r for r in cold_audit if r['track_id'] == 'W_OM' and r['annual_round_id'] == '2024'], cf)
    check('Both methods hold cold-start physical plots and leave errors unestimated',
          all(r['status'] == 'COLD_START_NO_PRIOR_RESPONSE' for r in cold_errors)
          and all(r['MAE'] == '' and r['RMSE'] == '' for r in performance(cold_errors, cf)))
    empty_fit = compute_parameters([], fold('W_NO3'))
    empty_preds = freeze_predictions(empty_fit, fold('W_NO3'))
    empty_support = parameter_support(empty_fit, empty_preds, fold('W_NO3'))
    supported = parameter_support(fitted['W_OM'], frozen['W_OM'], fold('W_OM'))
    check('No eligible training emits null parameters, no predictions and every computed flagFalse',
          empty_fit['equal_plot_training_mean_value'] is None and empty_preds == []
          and all(empty_support[key] is False for key in DYNAMIC_FLAGS))
    check('Mixed response support keeps no-support flagsFalse rather than claiming both tracks fitted',
          all(supported[key] is True and empty_support[key] is False for key in DYNAMIC_FLAGS))
    fingerprints = {track: stable_digest(preds) for track, preds in frozen.items()}
    scored = {}
    for track in FIX_RESPONSES:
        f = fold(track)
        targets = [r for r in audit if r['track_id'] == track and r['annual_round_id'] == '2024'
                   and r['expected_source_row']]
        scored[track] = score_targets(frozen[track], targets, f)
        check(track + ' full target ledger keeps both methods including held values',
              len(scored[track]) == 4 and {r['method'] for r in scored[track]} == set(METHODS))
        check(track + ' target scoring cannot change either response prediction freeze',
              all(stable_digest(frozen[t]) == fingerprints[t] for t in FIX_RESPONSES))
    check('Signed error uses frozen persistence prediction minus source OM observation',
          next(r for r in scored['W_OM'] if r['method'] == 'PERSISTENCE' and r['scored'])['signed_error'] == -2.0)
    pairs, paired = paired_comparison(scored['W_OM'], fold('W_OM'))
    check('Paired methods retain exact same source outcome and physical plot',
          len(pairs) == 1 and all(r['n_common_plots'] == 1 for r in paired))
    one_method_extra = deepcopy(scored['W_OM'])
    extra_error = deepcopy(next(r for r in scored['W_OM'] if r['method'] == 'PERSISTENCE' and r['scored']))
    extra_error.update(confirmed_physical_plot_id='ONLY_METHOD_A', inventory_row_id='SOURCE_OTHER')
    one_method_extra.append(extra_error)
    intersection, intersection_metrics = paired_comparison(one_method_extra, fold('W_OM'))
    check('Paired metrics use exact common cohort rather than different method support sets',
          len(intersection) == 1 and all(r['n_common_plots'] == 1 for r in intersection_metrics)
          and performance(one_method_extra, fold('W_OM'))[0]['n_scored_plots'] == 2)
    refuses('Paired methods scoring different original source values fail closed', lambda: paired_comparison([
        {**r, 'observed_value': r['observed_value'] + 1 if r['method'] == METHODS[1] and r['scored'] else r['observed_value']}
        for r in scored['W_OM']], fold('W_OM')))
    refuses('Cross-response errors cannot be pooled into common performance',
            lambda: performance(scored['W_OM'] + scored['W_NO3'], fold('W_OM')))
    changes = observed_changes(observed, ['2023', '2024'])
    om_change = next(r for r in changes if r['track_id'] == 'W_OM')
    check('Observed differences keep same-response endpoints and actual dates without rates',
          om_change['observed_value_change'] == 2.0 and om_change['from_date'] == '2023-11-16'
          and om_change['to_date'] == '2024-07-06' and 'annualized_rate' not in om_change
          and all(r['unit'] == FIX_RESPONSES[r['track_id']]['unit'] for r in changes))
    require(verify_staged_sources(run, cfg) == before and sha(run / 'RESPONSE_SPEC.json') == spec_hash
            and sha(run / 'OUTPUT_CONTRACT.json') == contract_hash, 'Fixture stage source/control bytes changed')
    save(run / 'audit/nutrient_self_checks.json', {
        'status': 'PASS', 'passed': True, 'checks': checks, 'execution': execution,
        'fixture_kind': 'SCIENTIFIC_SYNTHETIC_EDGE_CASES_HPC_ONLY', 'scientific_source_data_read': False,
        'response_spec_sha256': spec_hash, 'output_contract_sha256': contract_hash, 'runtime_and_stage': gate,
        'source_integrity_passed': True, 'source_files_changed': False,
        'source_files_verified_unchanged_after_audit': True,
        'predictive_validation_established': False, 'ten_year_forecast_generated': False,
    })
    print('NUTRIENT_SCIENTIFIC_SELF_CHECKS=PASS genuine Puma job ' + execution['job_id'])


if __name__ == '__main__':
    main()
