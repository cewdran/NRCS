#!/usr/bin/env python3
"""Observed Whipstone Annual OM/NO3 and frozen retrospective baselines, Puma only."""
from hpc_guard import require_hpc
execution = require_hpc()

from datetime import datetime, timezone
from pathlib import Path
import json
import sys
from common import require, sha, save, table, readcsv, safe_file, verify_staged_sources
from nutrient_core import (METHODS, DYNAMIC_FLAGS, parameter_support, current_gate, check_spec, build_observation_audit,
                     verify_original_workbook_links, observed_changes, select_training,
                     compute_parameters, freeze_predictions, score_targets,
                     performance, paired_comparison, stable_digest, response_for, response_context, fold_context)


def main():
    run = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
    require(run == Path(__file__).resolve().parent, 'Execute science only from its immutable run copy')
    cfg = json.loads(safe_file(run, 'CONFIG.json').read_text(encoding='utf-8'))
    gate = current_gate(run, cfg, execution)
    source_before = verify_staged_sources(run, cfg)
    controls = ('CONFIG.json', 'RESPONSE_SPEC.json', 'OUTPUT_CONTRACT.json', 'PROTOCOL.txt',
                'nutrient_core.py', 'analyze_nutrients.py', 'nutrient_self_checks.py')
    control_before = {name: sha(safe_file(run, name)) for name in controls}
    spec = json.loads(safe_file(run, cfg['response_spec_path']).read_text(encoding='utf-8'))
    contract = json.loads(safe_file(run, cfg['output_contract_path']).read_text(encoding='utf-8'))
    require(tuple(contract['dynamic_engine_flags']) == DYNAMIC_FLAGS, 'Dynamic baseline computation flags changed')
    checks_path = safe_file(run, contract['self_check_receipt_path'])
    checks = json.loads(checks_path.read_text(encoding='utf-8'))
    require(checks.get('status') == 'PASS' and checks.get('passed') is True and checks.get('checks')
            and all(row.get('passed') is True for row in checks['checks']), 'Puma scientific fixtures missing or failed')
    require(all(checks.get('execution', {}).get(k) == execution[k] for k in ('job_id', 'cluster', 'node'))
            and checks.get('fixture_kind') == 'SCIENTIFIC_SYNTHETIC_EDGE_CASES_HPC_ONLY'
            and checks.get('scientific_source_data_read') is False
            and checks.get('response_spec_sha256') == control_before['RESPONSE_SPEC.json']
            and checks.get('output_contract_sha256') == control_before['OUTPUT_CONTRACT.json'],
            'Scientific fixtures do not belong to current genuine main/control freeze')
    accepted = spec['accepted_source']
    for key in ('measurement_inventory', 'record_inventory'):
        require(sha(safe_file(run, accepted[key + '_path'])) == accepted[key + '_sha256'], 'Accepted OM/NO3 input changed')
    tracks_path = safe_file(run, 'source_snapshot/measured_inventory/RESPONSE_TRACKS.json')
    require(sha(tracks_path) == accepted['response_tracks_sha256'], 'Accepted response-form source changed')
    tracks = json.loads(tracks_path.read_text(encoding='utf-8'))
    check_spec(spec, tracks)
    for source in spec['source_reports'].values():
        require(sha(safe_file(run, source['local_path'])) == source['sha256'], 'Reported method PDF changed')
    audit, observations = build_observation_audit(
        readcsv(safe_file(run, accepted['measurement_inventory_path'])),
        readcsv(safe_file(run, accepted['record_inventory_path'])), spec)
    original = json.loads(safe_file(run, 'source_snapshot/corrected/payload/inputs/workbook_data.json').read_text(encoding='utf-8'))
    original_row_proofs = verify_original_workbook_links(audit, original)
    changes = observed_changes(observations, [r['round_id'] for r in spec['rounds']])
    folds = [{**f, **response_context(response_for(spec, f['track_id']))} for f in spec['folds']]
    require(len(folds) == 2 and {f['track_id'] for f in folds} == {'W_OM', 'W_NO3'},
            'Exact independent response fold panel missing')
    csv_rows = {name: [] for name in contract['csv_schemas']}
    csv_rows['results/observation_audit.csv'] = audit
    csv_rows['results/eligible_observations.csv'] = observations
    csv_rows['results/plot_changes.csv'] = changes
    fits, frozen, per_track_status = [], {}, []
    # Compute both declared parameter sets and all known-plot predictions BEFORE
    # any target scoring. In 2024, July and August share this single freeze.
    for fold in folds:
        training = select_training(observations, fold)
        parameters = compute_parameters(training, fold)
        fits.append(parameters)
        predictions = freeze_predictions(parameters, fold)
        frozen[fold['fold_id']] = predictions
        csv_rows['results/predictions.csv'].extend(predictions)
        csv_rows['results/training_membership.csv'].extend([
            {**row, 'fold_id': fold['fold_id'], 'target_round_id': fold['target_round_id'],
             'cutoff_date': fold['cutoff_date'], 'future_target_eligibility_used': False}
            for row in training])
        nplots = len(parameters['plot_summaries'])
        csv_rows['results/training_plot_means.csv'].extend([
            {**row, 'fold_id': fold['fold_id'], 'n_training_observations': row['n_training_visits'],
             'arithmetic_temporal_mean_value': row['training_plot_mean_value'],
             'latest_training_date': row['latest_prior_date'], 'latest_training_value': row['latest_training_value'],
             'equal_plot_weight': 1 / nplots}
            for row in parameters['plot_summaries']])
        csv_rows['results/baseline_support.csv'].extend([
            {**fold_context(fold), 'fold_id': fold['fold_id'], 'method': method, 'n_training_observations': len(training),
             'n_training_plots': nplots, 'support_gate_status': 'TRAINING_SUPPORT_PRESENT' if nplots else 'NO_ELIGIBLE_TRAINING_RESPONSE',
             'known_training_plot_required': True, 'minimum_prior_observations_for_plot': 1,
             'minimum_training_plots': 1, 'power_or_validation_established': False,
             'reason': 'Existence gate only; all prior eligible plots retained independently of future targets'}
            for method in METHODS])
        per_track_status.append(parameter_support(parameters, predictions, fold))
    dynamic_flags = {key: any(row[key] for row in per_track_status) for key in contract['dynamic_engine_flags']}
    for relative in ('results/training_membership.csv', 'results/training_plot_means.csv', 'results/predictions.csv'):
        table(run / relative, csv_rows[relative], contract['csv_schemas'][relative]['required_columns'])
    save(run / 'results/fits.json', {
        'status': 'TRAINING_ONLY_BASELINE_PARAMETERS_FROZEN', 'folds': fits, 'methods': list(METHODS),
        'reported_scale': 'OWN_ORIGINAL_REPORTED_RESPONSE_UNITS', 'future_target_membership_or_value_used_for_training': False,
        'simple_training_parameters_estimated': dynamic_flags['baseline_parameters_computed'],
        'per_track_status': per_track_status, 'complex_models_fitted': False,
    })
    training_files = {name: sha(run / name) for name in ('results/training_membership.csv', 'results/training_plot_means.csv')}
    freeze = {
        'status': 'ALL_DECLARED_ROUND_PREDICTIONS_FROZEN_BEFORE_TARGET_SCORING',
        'frozen_utc': datetime.now(timezone.utc).isoformat(), 'execution': execution,
        'folds': [{**fold_context(f), 'fold_id': f['fold_id'], 'target_round_id': f['target_round_id'],
                   'training_round_ids': f['training_round_ids'], 'cutoff_date': f['cutoff_date'],
                   'training_fingerprint': fit['training_fingerprint'],
                   'prediction_fingerprint': stable_digest(frozen[f['fold_id']])}
                  for f, fit in zip(folds, fits)],
        'training_files': training_files, 'predictions_sha256': sha(run / 'results/predictions.csv'),
        'fits_sha256': sha(run / 'results/fits.json'), 'freeze_before_any_target_scoring': True,
        'target_outcome_used_for_training': False, 'retrospective_execution_order_only': True,
        'source_outcomes_previously_viewed': spec['source_outcomes_previously_viewed'],
    }
    save(run / 'results/prediction_freeze.json', freeze)
    freeze_hash = sha(run / 'results/prediction_freeze.json')
    for fold, fit in zip(folds, fits):
        targets = [r for r in audit if r['track_id'] == fold['track_id'] and r['annual_round_id'] == fold['target_round_id']
                   and r['expected_source_row']]
        errors = score_targets(frozen[fold['fold_id']], targets, fold)
        pairs, pair_metrics = paired_comparison(errors, fold)
        metric = performance(errors, fold)
        csv_rows['results/plot_errors.csv'].extend(errors)
        csv_rows['results/performance.csv'].extend(metric)
        csv_rows['results/paired_errors.csv'].extend(pairs)
        csv_rows['results/paired_performance.csv'].extend(pair_metrics)
        target_round = next(r for r in spec['rounds'] if r['round_id'] == fold['target_round_id'])
        eligible_plots = {r['confirmed_physical_plot_id'] for r in targets if r['eligible']}
        known = {r['confirmed_physical_plot_id'] for r in fit['plot_summaries']}
        csv_rows['results/cohort_support.csv'].append({
            **fold_context(fold),
            'fold_id': fold['fold_id'], 'training_round_ids': ';'.join(fold['training_round_ids']),
            'target_round_id': fold['target_round_id'], 'cutoff_date': fold['cutoff_date'],
            'training_actual_dates': ';'.join(sorted({r['collection_date'] for r in fit['training_membership']})),
            'target_actual_dates': ';'.join(target_round['actual_collection_dates']),
            'n_training_observations': len(fit['training_membership']), 'n_training_plots': len(known),
            'n_declared_target_records': len(target_round['record_ids']), 'n_target_eligible_plots': len(eligible_plots),
            'n_target_known_plots': len(eligible_plots & known),
            'n_scored_persistence': metric[0]['n_scored_plots'],
            'n_scored_equal_plot_training_mean': metric[1]['n_scored_plots'], 'n_common_paired_plots': len(pairs),
            'status': 'LIMITED_RETROSPECTIVE_KNOWN_PLOT_DIAGNOSTIC',
            'reason': 'One annual target round; actual dates preserved; existence is not power or external validation',
        })
    # Frozen files may never be refitted or rewritten after scoring starts.
    require(all(sha(run / name) == digest for name, digest in training_files.items())
            and sha(run / 'results/predictions.csv') == freeze['predictions_sha256']
            and sha(run / 'results/fits.json') == freeze['fits_sha256']
            and sha(run / 'results/prediction_freeze.json') == freeze_hash, 'Training/prediction freeze changed during scoring')
    for relative, rows in csv_rows.items():
        if relative not in ('results/training_membership.csv', 'results/training_plot_means.csv', 'results/predictions.csv'):
            table(run / relative, rows, contract['csv_schemas'][relative]['required_columns'])
    source_after = verify_staged_sources(run, cfg)
    require(source_after == source_before and all(sha(run / name) == digest for name, digest in control_before.items()),
            'Immutable scientific source or control changed during OM/NO3 baseline diagnostics')
    preservation = {
        'status': 'ALL_SOURCE_BYTES_AND_ORIGINAL_RESPONSE_ROWS_VERIFIED_UNCHANGED', 'execution': execution,
        'source_integrity_passed': True, 'source_files_verified_unchanged': True,
        'source_files_verified_unchanged_after_audit': True, 'source_files_changed': False,
        'source_measurement_values_modified': False, 'prior_receipts_changed': False,
        'before': source_before, 'after': source_after, 'original_workbook_row_proofs': original_row_proofs,
        'control_sha256': control_before, 'runtime_and_stage': gate,
    }
    save(run / contract['source_preservation_path'], preservation)
    outputs = contract['engine_output_files']
    require(len(outputs) == len(set(outputs)) and set(csv_rows) == set(contract['csv_schemas']), 'Engine output contract malformed')
    manifest = []
    for name in outputs:
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts
                and all(not (run / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)),
                'Engine output contract path is linked or unsafe')
        output = safe_file(run, name)
        manifest.append({'relative_path': name, 'sha256': sha(output), 'size_bytes': output.stat().st_size})
    table(run / contract['engine_manifest_path'], manifest, contract['manifest_columns'])
    status = {
        'status': contract['engine_success_status'], 'execution': execution,
        'engine_output_manifest_path': contract['engine_manifest_path'],
        'engine_output_manifest_sha256': sha(run / contract['engine_manifest_path']),
        'source_preservation_path': contract['source_preservation_path'],
        'source_preservation_sha256': sha(run / contract['source_preservation_path']),
        'response_spec_sha256': control_before['RESPONSE_SPEC.json'], 'output_contract_sha256': control_before['OUTPUT_CONTRACT.json'],
        **contract['successful_flags'], **dynamic_flags, 'per_track_status': per_track_status,
        'source_integrity_passed': True,
        'source_files_verified_unchanged_after_audit': True, 'source_files_changed': False,
        'statistical_tests_performed': False, 'numerical_ten_year_forecast': False, 'forecasts_generated': False,
        'prediction_freeze_sha256': freeze_hash, 'fixture_receipt_sha256': sha(checks_path),
        'scope': cfg['scientific_scope'], 'completion_utc': datetime.now(timezone.utc).isoformat(),
        'interpretation': ('Own-response simple baseline parameters computed for supported tracks; limited retrospective known-plot point diagnostics'
                           if dynamic_flags['baseline_parameters_computed'] else
                           'No eligible prior response support; explicit unestimated baselines and skipped target ledger')
                          + '; no tuned/regression/process model, prospective validation or ten-year forecast',
    }
    require(all(key in status for key in contract['engine_status_required_keys']), 'Engine status required key missing')
    save(run / contract['engine_status_path'], status)
    print(json.dumps(status, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
