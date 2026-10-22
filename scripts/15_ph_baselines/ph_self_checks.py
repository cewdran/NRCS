#!/usr/bin/env python3
"""Meaningful synthetic pH design fixtures; genuine Puma execution only."""
from hpc_guard import require_hpc
execution = require_hpc()

from copy import deepcopy
from pathlib import Path
import json
import math
import sys
from common import require, save, sha, safe_file, verify_staged_sources
from ph_core import (METHODS, RAW_MEASUREMENT_FIELDS, cell, valid_date, current_gate,
                     build_observation_audit, finite_source_value, select_training,
                     compute_parameters, freeze_predictions, score_targets,
                     performance, paired_comparison, observed_changes, stable_digest)


def synthetic_inputs(definitions):
    """Create typed source/CSV pairs only after the genuine compute guard."""
    measurements, records, expected, rounds = [], [], {}, {}
    forms = {}
    for index, (rid, plot, actual, value) in enumerate(definitions, start=1):
        annual = actual[:4]
        report = 'FIX_PDF_' + annual
        record = {
            'record_id': rid, 'source_id': 'FIX_RECORD_SOURCE', 'farm': 'Whipstone',
            'system': 'Annual crops', 'dataset': 'FIX_SOIL', 'sample_name': 'SOURCE_' + rid,
            'collection_date': actual, 'confirmed_sample_key': plot + '|' + actual,
            'candidate_sample_key': 'UNAPPROVED_OTHER_LABEL', 'original_lab_id': 'LAB_' + rid,
            'original_sample_label': 'Original sample label ' + rid,
            'preferred_pdf_source_id': report, 'preferred_pdf_page': index,
            'intentional_exclusion': '',
        }
        raw = {
            'record_id': rid, 'farm': 'Whipstone', 'system': 'Annual crops', 'dataset': 'FIX_SOIL',
            'sample_name': 'SOURCE_' + rid, 'variable': 'FIX pH 1:1 ', 'value': value,
            'source_value': cell(value), 'text_value': None, 'unit': 'pH units', 'method': None,
            'qualifier': None, 'reporting_limit': None, 'source_id': 'FIX_MEASUREMENT_SOURCE',
            'source_location': 'synthetic.csv row ' + str(index), 'source_field': 'Obsolete unrelated header retained',
            'preferred_pdf_source_id': report, 'preferred_pdf_page': index,
            'value_basis': 'PDF preferred synthetic fixture', 'notes': 'Original source fixture note',
        }
        encode = lambda v: json.dumps(v, ensure_ascii=False, separators=(',', ':'), allow_nan=True)
        identity = {
            'record_id': rid, 'inventory_source': 'corrected_workbook/Sample records',
            'collection_date': actual, 'confirmed_sample_key': record['confirmed_sample_key'],
            'confirmed_physical_plot_id': plot, 'analytic_system': 'Annual',
            'record_identity_status': 'EXPLICIT_CONFIRMED_PLOT_EVENT_IDENTITY',
            'record_identity_confirmed': 'True', 'record_excluded': 'False', 'raw_record_json': encode(record),
        }
        row = {
            **identity, 'inventory_row_id': 'WB_MEASUREMENT_' + str(index).zfill(6),
            'source_row_number': str(index), 'inventory_source': 'corrected_workbook/Measurements',
            'source_role': 'CORRECTED_PRIMARY_MEASUREMENT_NOT_A_NEW_OBSERVATION',
            'scope_id': 'WHIPSTONE_ANNUAL', 'track_id': 'W_PH', 'mapping_status': 'REPORTED_DEFINITION_COMPATIBLE_SOURCE_BACKED',
            'raw_lab_id': record['original_lab_id'], 'raw_sample_label': record['original_sample_label'],
            'raw_measurement_json': encode(raw), 'raw_record_json': encode(record),
            **{column: cell(raw.get(field)) for column, field in RAW_MEASUREMENT_FIELDS.items()},
            'model_eligible': 'False', 'forecast_eligible': 'False', 'reviewed_method': '1:1',
            'depth_as_reported': 'source literal only', 'field_depth_protocol': 'separate field protocol',
        }
        measurements.append(row)
        records.append(identity)
        expected[rid] = {k: record[k] for k in ('source_id', 'preferred_pdf_source_id', 'preferred_pdf_page',
                                               'original_lab_id', 'collection_date', 'confirmed_sample_key')}
        rounds.setdefault(annual, {'round_id': annual, 'record_ids': [], 'actual_collection_dates': []})
        rounds[annual]['record_ids'].append(rid)
        if actual not in rounds[annual]['actual_collection_dates']:
            rounds[annual]['actual_collection_dates'].append(actual)
        form = forms.setdefault(report, {
            'preferred_pdf_source_id': report, 'raw_source_ids': ['FIX_MEASUREMENT_SOURCE'],
            'raw_variables': ['FIX pH 1:1 '], 'raw_units': ['pH units'], 'raw_methods_allowed': [None], 'record_ids': [],
        })
        form['record_ids'].append(rid)
    spec = {
        'response': {'track_id': 'W_PH', 'scope_id': 'WHIPSTONE_ANNUAL', 'forms': list(forms.values()),
                     'scopes': {'WHIPSTONE_ANNUAL': {'farm_literal': 'Whipstone', 'system_literal': 'Annual crops',
                                                   'dataset_literals': ['FIX_SOIL'], 'expected_analytic_system': 'Annual'}},
                     'authority_inventory_source': 'corrected_workbook/Measurements',
                     'authority_source_role': 'CORRECTED_PRIMARY_MEASUREMENT_NOT_A_NEW_OBSERVATION',
                     'authority_row_id_prefix': 'WB_MEASUREMENT_'},
        'expected_record_sources': expected, 'rounds': list(rounds.values()), 'source_reports': {},
    }
    return measurements, records, spec


def change_raw(row, **updates):
    raw = json.loads(row['raw_measurement_json'])
    raw.update(updates)
    row['raw_measurement_json'] = json.dumps(raw, ensure_ascii=False, separators=(',', ':'), allow_nan=True)
    row.update({column: cell(raw.get(field)) for column, field in RAW_MEASUREMENT_FIELDS.items()})


def fold(target):
    return {'fold_id': 'PH_2024_FROM_2023' if target == '2024' else 'PH_2025_FROM_2023_2024',
            'training_round_ids': ['2023'] if target == '2024' else ['2023', '2024'],
            'target_round_id': target, 'cutoff_date': target + '-01-01'}


def main():
    run = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
    require(run == Path(__file__).resolve().parent, 'Execute scientific fixtures only from immutable run copy')
    cfg = json.loads(safe_file(run, 'CONFIG.json').read_text(encoding='utf-8'))
    gate = current_gate(run, cfg, execution)
    before = verify_staged_sources(run, cfg)
    spec_hash, contract_hash = sha(run / 'RESPONSE_SPEC.json'), sha(run / 'OUTPUT_CONTRACT.json')
    checks = []

    def check(name, condition):
        require(condition, 'Puma synthetic pH fixture failed: ' + name)
        checks.append({'check': name, 'passed': True})

    def refuses(name, action):
        refused = False
        try:
            action()
        except RuntimeError:
            refused = True
        check(name, refused)

    definitions = [('A23', 'PLOT_A', '2023-11-16', 4.0), ('B23', 'PLOT_B', '2023-11-16', 10.0),
                   ('A24', 'PLOT_A', '2024-07-06', 6.0), ('B24', 'PLOT_B', '2024-08-14', None),
                   ('A25', 'PLOT_A', '2025-08-17', 7.0)]
    source, records, spec = synthetic_inputs(definitions)
    source_before = deepcopy(source)
    audit, observed = build_observation_audit(source, records, spec)
    check('Authoritative finite pH retained despite inherited inventory model/forecast false', len(observed) == 4)
    check('No 16S or functional counterpart is required to retain confirmed measured soil pH',
          len(observed) == 4 and all('confirmed_join' not in r for r in source))
    check('Trailing-space variable and blank original method retained with separate reviewed method',
          observed[0]['raw_variable'].endswith(' ') and observed[0]['raw_method'] == '' and observed[0]['reviewed_method'] == '1:1')
    check('Unrelated legacy source field preserved, not inferred as response analyte',
          observed[0]['source_field'] == 'Obsolete unrelated header retained')
    check('No treatment inferred from original source sample label', all(r['treatment'] == '' for r in observed))
    unconfirmed_source, unconfirmed_records = deepcopy(source), deepcopy(records)
    for identity in (unconfirmed_source[2], unconfirmed_records[2]):
        identity.update(confirmed_physical_plot_id='', record_identity_confirmed='False',
                        record_identity_status='HELD_NO_CONFIRMED_IDENTITY')
    unconfirmed_audit, unconfirmed_obs = build_observation_audit(unconfirmed_source, unconfirmed_records, spec)
    check('Candidate label/confirmed specimen key never invents a missing physical plot',
          not next(r for r in unconfirmed_audit if r['record_id'] == 'A24')['eligible']
          and all(r['record_id'] != 'A24' for r in unconfirmed_obs))
    excluded_source, excluded_records = deepcopy(source), deepcopy(records)
    for identity in (excluded_source[2], excluded_records[2]):
        identity['record_excluded'] = 'True'
    excluded_audit, excluded_obs = build_observation_audit(excluded_source, excluded_records, spec)
    check('Accepted source exclusion stays visible and out of response cohort',
          'HELD_SOURCE_EXCLUSION' in next(r for r in excluded_audit if r['record_id'] == 'A24')['eligibility_status']
          and all(r['record_id'] != 'A24' for r in excluded_obs))
    impossible, impossible_records, impossible_spec = synthetic_inputs([('IMPOSSIBLE', 'PLOT_A', '2024-02-31', 7.0)])
    impossible_audit, impossible_obs = build_observation_audit(impossible, impossible_records, impossible_spec)
    check('Impossible calendar date remains preserved and held', not valid_date('2024-02-31')
          and not impossible_obs and impossible_audit[0]['collection_date'] == '2024-02-31'
          and 'HELD_COLLECTION_DATE_OR_KEY_CONFLICT' in impossible_audit[0]['eligibility_status'])
    check('Missing response remains held and is not a zero observation',
          any(r['record_id'] == 'B24' and not r['eligible'] and r['reported_pH'] == '' for r in audit))
    check('All source rows remain byte-equivalent dictionaries after eligibility', source == source_before)
    train25 = select_training(observed, fold('2025'))
    fit25 = compute_parameters(train25, fold('2025'))
    check('Training includes prior plot lacking any finite future target',
          {r['confirmed_physical_plot_id'] for r in train25} == {'PLOT_A', 'PLOT_B'})
    check('Within-plot temporal mean then equal plot weight differs from row-weighted mean',
          fit25['equal_plot_training_mean_pH'] == 7.5 and fit25['equal_plot_training_mean_pH'] != 20 / 3)
    predictions25 = freeze_predictions(fit25, fold('2025'))
    check('Prediction maps include all prior plots, not only future target membership',
          len(predictions25) == 4 and {r['confirmed_physical_plot_id'] for r in predictions25} == {'PLOT_A', 'PLOT_B'})
    check('Persistence uses latest eligible actual prior date and original value',
          next(r for r in predictions25 if r['method'] == 'PERSISTENCE' and r['confirmed_physical_plot_id'] == 'PLOT_A')['predicted_pH'] == 6.0)
    future_changed = deepcopy(source)
    change_raw(future_changed[-1], value=100.0, source_value='100.0')
    _, observed_changed = build_observation_audit(future_changed, records, spec)
    changed_fit = compute_parameters(select_training(observed_changed, fold('2025')), fold('2025'))
    check('Future numerical outcome cannot alter training membership/parameters/predictions',
          changed_fit == fit25 and freeze_predictions(changed_fit, fold('2025')) == predictions25)
    future_removed = source[:-1]
    _, observed_removed = build_observation_audit(future_removed, records, spec)
    removed_fit = compute_parameters(select_training(observed_removed, fold('2025')), fold('2025'))
    check('Future response absence cannot alter prior training pool or predictions', removed_fit == fit25)
    train24 = select_training(observed, fold('2024'))
    fit24 = compute_parameters(train24, fold('2024'))
    pred24 = freeze_predictions(fit24, fold('2024'))
    check('Whole July/August target round shares 2023-only training',
          {r['annual_round_id'] for r in train24} == {'2023'} and len(pred24) == 4)
    targets24 = [r for r in audit if r['annual_round_id'] == '2024' and r['expected_source_row']]
    fingerprint = stable_digest(pred24)
    scored24 = score_targets(pred24, targets24, fold('2024'))
    check('Scoring either target subset cannot update the frozen prediction map',
          fingerprint == stable_digest(pred24) and all(r['training_fingerprint'] == fit24['training_fingerprint'] for r in scored24))
    refuses('Target round observation cannot enter prior parameter computation',
            lambda: compute_parameters([observed[2]], fold('2024')))
    duplicate_training = train24 + [deepcopy(train24[0])]
    refuses('Duplicate prior plot/event cannot be silently deduplicated or averaged',
            lambda: compute_parameters(duplicate_training, fold('2024')))
    for label, updates in [
        ('Censored response', {'source_value': '<6.0'}),
        ('Qualification', {'qualifier': 'review required'}),
        ('Reporting limit', {'reporting_limit': 1.0}),
        ('Nonfinite response', {'value': float('inf')}),
        ('Boolean response', {'value': True}),
        ('Numeric text is not coerced into source number', {'value': '6.0'}),
        ('Numeric/text conflict', {'text_value': 'incompatible source text'}),
        ('Unknown unit', {'unit': 'unreviewed pH scale'}),
        ('Unknown original method', {'method': 'new method'}),
        ('Fuzzy whitespace variable repair forbidden', {'variable': 'FIX pH 1:1'}),
        ('Unreviewed preferred report', {'preferred_pdf_source_id': 'OTHER_PDF'}),
        ('Wrong original measurement source', {'source_id': 'OTHER_SOURCE'}),
        ('Measurement farm differs from exact accepted record', {'farm': 'OTHER_FARM'}),
        ('Measurement dataset differs from exact accepted record', {'dataset': 'OTHER_DATASET'}),
        ('Measurement source label differs from exact accepted record', {'sample_name': 'UNAPPROVED_ALIAS'}),
    ]:
        edited = deepcopy(source)
        change_raw(edited[2], **updates)
        test_audit, test_obs = build_observation_audit(edited, records, spec)
        check(label + ' is held unchanged without imputation or conversion',
              not next(r for r in test_audit if r['record_id'] == 'A24')['eligible']
              and all(r['record_id'] != 'A24' for r in test_obs))
    altered_projection = deepcopy(source)
    altered_projection[0]['raw_value'] = '999'
    refuses('Source projection/value mismatch fails closed', lambda: build_observation_audit(altered_projection, records, spec))
    lab_copy = deepcopy(source[0])
    lab_copy.update(inventory_row_id='LAB_AUDIT_COPY', inventory_source='corrections/laboratory_measurements.csv',
                    source_role='AUDIT_TRANSCRIPTION_COPY_NOT_INDEPENDENT_SUPPORT')
    copy_audit, copy_obs = build_observation_audit(source + [lab_copy], records, spec)
    check('Laboratory representation remains visible without additional observation',
          len(copy_obs) == len(observed) and copy_audit[-1]['eligibility_status'] == 'AUDIT_TRANSCRIPTION_COPY_ONLY')
    repeated = deepcopy(source)
    extra = deepcopy(repeated[2])
    extra['inventory_row_id'] = 'WB_MEASUREMENT_EXTRA'
    change_raw(extra, value=None, source_value='<6.0')
    repeated.append(extra)
    repeated_audit, repeated_obs = build_observation_audit(repeated, records, spec)
    check('Finite and censored duplicate event rows are all held before value inclusion',
          all(not r['eligible'] and 'HELD_MULTIPLE_PLOT_EVENT_VALUES' in r['eligibility_status']
              for r in repeated_audit if r['record_id'] == 'A24')
          and all(r['record_id'] != 'A24' for r in repeated_obs))
    multiple, multiple_records, multiple_spec = synthetic_inputs([
        ('A23', 'PLOT_A', '2023-11-16', 4.0), ('A24J', 'PLOT_A', '2024-07-06', 6.0),
        ('A24A', 'PLOT_A', '2024-08-14', None)])
    annual_audit, annual_obs = build_observation_audit(multiple, multiple_records, multiple_spec)
    check('Multiple actual target visits are all held even with one finite response',
          all('HELD_MULTIPLE_EVENTS_PER_PLOT_ROUND' in r['eligibility_status']
              for r in annual_audit if r['annual_round_id'] == '2024') and len(annual_obs) == 1)
    absent_audit, _ = build_observation_audit(source[:-1], records, spec)
    placeholder = next(r for r in absent_audit if r['record_id'] == 'A25')
    check('Expected missing source gets an audit ID, blank observation ID and no fabricated value',
          placeholder['audit_id'] and placeholder['observation_id'] == '' and placeholder['reported_pH'] == '')
    cold_source, cold_records, cold_spec = synthetic_inputs([
        ('A23', 'PLOT_A', '2023-11-16', 4.0), ('C24', 'COLD_PLOT', '2024-07-06', 7.0)])
    cold_audit, cold_obs = build_observation_audit(cold_source, cold_records, cold_spec)
    cold_fit = compute_parameters(select_training(cold_obs, fold('2024')), fold('2024'))
    cold_errors = score_targets(freeze_predictions(cold_fit, fold('2024')),
                               [r for r in cold_audit if r['annual_round_id'] == '2024'], fold('2024'))
    check('Both methods hold cold-start exact physical plot', all(r['status'] == 'COLD_START_NO_PRIOR_PH' for r in cold_errors))
    empty_metrics = performance(cold_errors, fold('2024'))
    check('Zero scorable plots have empty metrics, never zero error',
          all(r['status'] == 'NOT_ESTIMATED' and r['MAE'] == '' and r['RMSE'] == '' for r in empty_metrics))
    empty_fit = compute_parameters([], fold('2024'))
    check('No eligible prior training yields no parameter estimate or prediction map',
          empty_fit['status'] == 'NOT_ESTIMATED_NO_ELIGIBLE_TRAINING'
          and empty_fit['equal_plot_training_mean_pH'] is None
          and freeze_predictions(empty_fit, fold('2024')) == [])
    check('Signed error and absolute/squared errors use frozen prediction minus observed source',
          next(r for r in scored24 if r['method'] == 'PERSISTENCE' and r['scored'])['signed_error'] == -2.0)
    pairs, pair_metric = paired_comparison(scored24, fold('2024'))
    check('Common paired comparison retains exact same source outcome and plot',
          len(pairs) == 1 and all(r['n_common_plots'] == 1 for r in pair_metric))
    one_method_extra = deepcopy(scored24)
    extra_error = deepcopy(next(r for r in scored24 if r['method'] == 'PERSISTENCE' and r['scored']))
    extra_error['confirmed_physical_plot_id'] = 'ONLY_METHOD_A'
    extra_error['inventory_row_id'] = 'SOURCE_OTHER'
    one_method_extra.append(extra_error)
    intersection, intersection_metrics = paired_comparison(one_method_extra, fold('2024'))
    check('Paired metrics use intersection rather than different method support sets',
          len(intersection) == 1 and all(r['n_common_plots'] == 1 for r in intersection_metrics)
          and performance(one_method_extra, fold('2024'))[0]['n_scored_plots'] == 2)
    refuses('Paired methods scoring different source outcomes fail closed', lambda: paired_comparison([
        {**r, 'reported_pH': r['reported_pH'] + 1 if r['method'] == METHODS[1] and r['scored'] else r['reported_pH']}
        for r in scored24], fold('2024')))
    changes = observed_changes(observed, ['2023', '2024', '2025'])
    check('Observed differences preserve actual endpoint dates without annualized rates',
          changes[0]['observed_pH_change'] == 2.0 and changes[0]['from_date'] == '2023-11-16'
          and changes[0]['to_date'] == '2024-07-06' and 'annualized_rate' not in changes[0])
    require(verify_staged_sources(run, cfg) == before and sha(run / 'RESPONSE_SPEC.json') == spec_hash
            and sha(run / 'OUTPUT_CONTRACT.json') == contract_hash, 'Fixture stage source/control bytes changed')
    save(run / 'audit/ph_self_checks.json', {
        'status': 'PASS', 'passed': True, 'checks': checks, 'execution': execution,
        'fixture_kind': 'SCIENTIFIC_SYNTHETIC_EDGE_CASES_HPC_ONLY', 'scientific_source_data_read': False,
        'response_spec_sha256': spec_hash, 'output_contract_sha256': contract_hash, 'runtime_and_stage': gate,
        'source_integrity_passed': True, 'source_files_changed': False,
        'source_files_verified_unchanged_after_audit': True,
        'predictive_validation_established': False, 'ten_year_forecast_generated': False,
    })
    print('PH_SCIENTIFIC_SELF_CHECKS=PASS genuine Puma job ' + execution['job_id'])


if __name__ == '__main__':
    main()
