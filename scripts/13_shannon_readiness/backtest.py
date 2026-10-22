#!/usr/bin/env python3
"""Compute one grouped 2024 backtest on Puma and preserve the original 2025 fold."""
from hpc_guard import require_hpc
execution = require_hpc()

from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
import importlib.util
import json
import math
import sys
from common import require, save, sha, table, readcsv
from baseline_core import (METHODS, ARM_SKIP, NO_SUPPORT, eligible, select_training,
                           fit_baselines, predict, score, metrics, compare_fixed_errors, stable_digest)

NEW_FOLD = 'TRAIN2023_TEST2024_ANNUAL_ROUND'
OLD_FOLD = 'TRAIN2023_2024_TEST2025'
DOMAINS = ('bacteria', 'archaea')
SCOPES = ('PRIMARY', 'DEPTH_SENSITIVITY')
ARMS = ('Blood_meal_and_Feather_meal', 'Blood_meal_and_Feather_meal_and_cover_crop',
        'Control', 'Cover_crop_only')
TRAIN = ('2023-11-16',)
TARGET = ('2024-07-06', '2024-08-14')
CUTOFF = '2024-01-01'
SUCCESS = 'TEMPORAL_BACKTEST_COMPLETED_REVIEW_PENDING'
OLD_ORIGIN = {'job_id': '24124251', 'cluster': 'puma', 'node': 'r7u03n1'}


def load(path):
    value = json.loads(path.read_text())
    require(isinstance(value, dict), 'Expected JSON object: ' + str(path))
    return value


def safe_file(base, relative):
    rel = Path(relative)
    require(not rel.is_absolute() and '..' not in rel.parts, 'Unsafe relative path.')
    result = (base / rel).resolve(strict=True)
    require(result.is_relative_to(base.resolve(strict=True)) and result.is_file(), 'Unsafe verified file.')
    return result


def same_execution(record, expected):
    actual = record.get('execution', {})
    require(all(str(actual.get(k)) == str(expected[k]) for k in ('job_id', 'cluster', 'node')),
            'Execution provenance disagrees.')


def protocol_gate(cfg):
    require(cfg.get('package') == 'NRCS_validation_forecast_readiness_HPC_v1', 'Wrong validation package.')
    intent = cfg['user_intent']
    require(intent.get('predictive_validation_and_ten_year_work_authorized') is True
            and intent.get('independent_information_available') == 'CURRENT_NRCS_FILES_ONLY', 'Unexpected current authorization/evidence scope.')
    p = cfg['backtest_protocol']
    expected = {'farm': 'Whipstone', 'system': 'Annual', 'domains': list(DOMAINS),
                'scopes': list(SCOPES), 'response': 'Shannon', 'methods': list(METHODS), 'treatment_arms': list(ARMS)}
    require(all(p.get(k) == v for k, v in expected.items()), 'New frozen backtest protocol disagreement.')
    require(p['new_fold'] == dict(fold_id=NEW_FOLD, training_dates=list(TRAIN), held_out_dates=list(TARGET),
                cutoff_date=CUTOFF, target_annual_round='2024', one_observation_per_physical_plot_in_round=True),
            'New grouped annual-round fold changed.')
    require(p['reused_fold'] == dict(fold_id=OLD_FOLD, training_dates=['2023-11-16', '2024-07-06', '2024-08-14'],
                held_out_dates=['2025-08-17'], cutoff_date='2025-01-01', origin_job_id='24124251',
                recompute_fits_predictions_errors=False), 'Original 2025 reuse protocol changed.')
    require(all(p.get(k) is False for k in ('future_assay_availability_verified', 'pool_rounds_as_independent_plot_samples',
                'select_winner', 'intervals_or_p_values', 'ten_year_numerical_values')), 'Unsupported protocol claim.')
    return p


def verify_sources(run, cfg):
    stage = load(run / 'audit/ENGINE_SOURCE_STATUS.json')
    same_execution(stage, execution)
    require(stage.get('status') == 'IMMUTABLE_ENGINE_SOURCE_VERIFIED_AND_STAGED'
            and stage.get('engine_origin_execution') == OLD_ORIGIN and stage.get('source_integrity_passed') is True
            and stage.get('source_files_changed') is False, 'Original engine staging gate did not pass.')
    engine = run / 'engine_source'
    manifest = run / 'audit/engine_source_manifest.csv'
    require(stage.get('engine_source_manifest_sha256') == sha(manifest), 'Staging manifest pin changed.')
    audit = []
    seen = set()
    original = Path(cfg['engine_source']['path']).resolve(strict=True)
    for row in readcsv(manifest):
        relative = row['relative_path']
        require(relative not in seen, 'Duplicate staged source path.')
        seen.add(relative)
        old, copied = safe_file(original, relative), safe_file(engine, relative)
        require(sha(old) == sha(copied) == row['sha256'] and copied.stat().st_size == int(row['size_bytes']),
                'Original/staged engine file changed: ' + relative)
        audit.append(dict(source='engine', relative_path=relative, expected_sha256=row['sha256'],
                          original_sha256=sha(old), snapshot_sha256=sha(copied), status='UNCHANGED'))
    require(len(seen) == stage['files_staged'], 'Staged source file count changed.')
    for alias, pins in cfg['sources'].items():
        source = Path(cfg['source_runs'][alias]['path']).resolve(strict=True)
        for relative, expected in pins.items():
            old = safe_file(source, relative)
            copied = safe_file(engine, 'source_snapshot/' + alias + '/' + relative)
            require(sha(old) == sha(copied) == expected, 'Upstream source or snapshot changed.')
            audit.append(dict(source=alias, relative_path=relative, expected_sha256=expected,
                              original_sha256=sha(old), snapshot_sha256=sha(copied), status='UNCHANGED'))
    repair_path = run / 'audit/REPAIR_SOURCE_STATUS.json'
    repair = load(repair_path)
    same_execution(repair, execution)
    require(stage.get('repair_source_status_sha256') == sha(repair_path)
            and stage.get('successful_report_source_verified') is True
            and repair.get('status') == 'SUCCESSFUL_REPORT_REPAIR_SOURCE_VERIFIED_AND_STAGED'
            and repair.get('source_integrity_passed') is True and repair.get('source_files_changed') is False,
            'Successful repair staging linkage or source preservation failed.')
    repair_manifest = safe_file(run, repair['repair_source_manifest_path'])
    require(sha(repair_manifest) == repair['repair_source_manifest_sha256'], 'Repair source manifest changed.')
    for row in readcsv(repair_manifest):
        old = safe_file(Path(cfg['repair_source']['path']).resolve(strict=True), row['relative_path'])
        copied = safe_file(run / 'repair_source', row['relative_path'])
        require(sha(old) == sha(copied) == row['sha256'] and copied.stat().st_size == int(row['size_bytes']),
                'Successful repair source/snapshot changed.')
    lineage = safe_file(run, repair['repair_engine_lineage_checks_path'])
    require(sha(lineage) == repair['repair_engine_lineage_checks_sha256'], 'Repair engine lineage receipt changed.')
    for row in readcsv(lineage):
        copied = safe_file(engine, row['relative_path'])
        middle = safe_file(Path(cfg['repair_source']['path']).resolve(strict=True), 'engine_source/' + row['relative_path'])
        require(sha(middle) == sha(copied) == row['sha256'], 'Successful repair intermediate engine bytes changed.')
    for relative, expected in cfg['repair_source']['files'].items():
        old = safe_file(Path(cfg['repair_source']['path']).resolve(strict=True), relative)
        copied = safe_file(run / 'repair_source', relative)
        require(sha(old) == sha(copied) == expected, 'Successful report-repair source changed.')
    for relative, expected in cfg['review_locks'].items():
        require(sha(safe_file(run, relative)) == expected, 'Accepted source review byte pin changed.')
    return engine, stage, audit


def assert_round_unique(rows, held_out_dates=TARGET):
    """A split calendar round may never count a physical plot twice."""
    seen, samples = set(), set()
    for row in rows:
        require(date.fromisoformat(row['date']).isoformat() == row['date'], 'Noncanonical actual collection date.')
        if row['date'] not in held_out_dates:
            continue
        require(row['plot'] and row['plot'] not in seen, 'Repeated physical plot within target annual round.')
        require(row['analysis_sample'] and row['analysis_sample'] not in samples, 'Repeated library within target annual round.')
        seen.add(row['plot']); samples.add(row['analysis_sample'])


def observed_map(rows):
    observed = {}
    for row in rows:
        require(row['plot'] not in observed, 'Observed map would silently overwrite a physical plot.')
        observed[row['plot']] = dict(date=row['date'], Shannon=row['Shannon'], sample_key=row['sample_key'],
            analysis_sample=row['analysis_sample'], treatment=row['treatment'])
    return observed


def validate_prediction_identity(predictions, observed):
    for row in predictions:
        actual = observed[row['biological_plot_id']]
        require(all(row[k] == actual[v] for k, v in (('held_out_date', 'date'), ('sample_key', 'sample_key'),
                    ('analysis_sample', 'analysis_sample'), ('treatment', 'treatment'))), 'Full frozen target identity disagrees.')


def paired_identity(left, right):
    a, b = {r['biological_plot_id']: r for r in left}, {r['biological_plot_id']: r for r in right}
    require(len(a) == len(left) and len(b) == len(right), 'Duplicate plot in paired errors.')
    for plot in set(a) & set(b):
        require(all(a[plot][k] == b[plot][k] for k in ('domain', 'fold_id', 'held_out_date', 'sample_key',
                    'analysis_sample', 'treatment', 'observed_Shannon')), 'Paired fold/domain/observed identity disagrees.')


def tagged(row, fold, origin, kind):
    require(not any(k in row for k in ('fold_id', 'origin_job_id', 'origin_kind')), 'Source row already has fold tags.')
    return dict(row, fold_id=fold, origin_job_id=origin, origin_kind=kind)


def compute_2024(rows, out):
    out.mkdir()
    fits, memberships, means, supports = {}, [], [], []
    for domain in DOMAINS:
        source = [r for r in rows if r['domain'] == domain]
        assert_round_unique(source)
        for scope in SCOPES:
            training = select_training(source, scope, TRAIN, CUTOFF)
            require(all(r['date'] in TRAIN and r['date'] < CUTOFF and r['treatment'] in ARMS
                        and math.isfinite(r['Shannon']) and r['Shannon'] >= 0 for r in training), 'Invalid 2023-only training.')
            fit = fit_baselines(training, ARMS, TRAIN, CUTOFF)
            fits[(domain, scope)] = fit
            memberships.extend(dict(r, domain=domain, scope=scope, biological_plot_id=r['plot'], collection_date=r['date']) for r in training)
            means.extend(dict(r, domain=domain, scope=scope, biological_plot_id=r['plot']) for r in fit['plot_summaries'].values())
            for method in METHODS:
                supports.append(dict(domain=domain, scope=scope, method=method, status=fit['method_status'][method],
                    n_training_plots=len(fit['plot_summaries']), n_training_rows=len(training),
                    training_arm_plot_counts=json.dumps(fit['arm_plot_counts'], sort_keys=True),
                    training_digest=fit['training_digest'], reason='' if fit['method_status'][method] == 'ESTIMATED_TRAINING_ONLY'
                        else 'All four training arms require equal plot counts >=2' if fit['method_status'][method] == ARM_SKIP
                        else 'No eligible 2023 training plots', fit_uses_held_out_response=False,
                    fit_conditioned_on_held_out_availability=False))
    save(out / 'training_fits.json', {d + '__' + s: f for (d, s), f in fits.items()})
    table(out / 'training_membership.csv', memberships, ['domain', 'scope', 'biological_plot_id', 'collection_date', 'Shannon'])
    table(out / 'training_plot_means.csv', means, ['domain', 'scope', 'biological_plot_id', 'plot_training_mean'])
    table(out / 'baseline_support.csv', supports, ['domain', 'scope', 'method', 'status'])
    fits_hash = sha(out / 'training_fits.json')
    predictions, qc, cohorts, observed_sets, scope_digests = [], [], [], {}, {}
    for domain in DOMAINS:
        source = [r for r in rows if r['domain'] == domain]
        for scope in SCOPES:
            fit = fits[(domain, scope)]
            target_rows = [r for r in source if r['date'] in TARGET and eligible(r, scope)
                           and r['plot'] in fit['plot_summaries']]
            assert_round_unique(target_rows)
            observed_sets[(domain, scope)] = observed_map(target_rows)
            scoped_predictions = []
            for actual_date in TARGET:
                targets = [dict(plot=r['plot'], treatment=r['treatment'], sample_key=r['sample_key'],
                                analysis_sample=r['analysis_sample']) for r in target_rows if r['date'] == actual_date]
                scoped_predictions.extend(predict(fit, targets, actual_date, CUTOFF))
            require(len({(r['method'], r['biological_plot_id']) for r in scoped_predictions}) == len(scoped_predictions),
                    'A method counts a plot twice across the grouped 2024 annual round.')
            digest = stable_digest(scoped_predictions)
            scope_digests[domain + '__' + scope] = digest
            predictions.extend(dict(r, domain=domain, scope=scope, prediction_digest=digest,
                fold_id=NEW_FOLD, origin_job_id=execution['job_id'], origin_kind='NEW_2024_BACKTEST') for r in scoped_predictions)
            cohorts.append(dict(domain=domain, scope=scope, n_training_rows=len(fit['training_membership']),
                n_training_plots=len(fit['plot_summaries']), n_known_eligible_test_plots=len(target_rows),
                training_plot_ids=';'.join(sorted(fit['plot_summaries'])),
                known_test_plot_ids=';'.join(sorted(r['plot'] for r in target_rows)),
                training_digest=fit['training_digest'], prediction_digest=digest,
                training_selection_uses_future_flags=False, evaluation_unit='physical_plot',
                fold_id=NEW_FOLD, origin_job_id=execution['job_id'], origin_kind='NEW_2024_BACKTEST'))
            for r in source:
                partition = 'TRAINING_ALLOWED' if r['date'] in TRAIN else 'HELD_OUT_ANNUAL_ROUND' if r['date'] in TARGET else 'OUTSIDE_FOLD'
                included = eligible(r, scope)
                disposition = ('OUTSIDE_FOLD' if partition == 'OUTSIDE_FOLD' else
                    'EXCLUDED_SOURCE_FLAGS' if not included else 'TRAINING_ELIGIBLE' if r['date'] in TRAIN else
                    'TEST_EVALUATED' if r['plot'] in fit['plot_summaries'] else 'HELD_NO_ELIGIBLE_PRIOR')
                failed_flags = []
                if not r['primary_included']: failed_flags.append('source_primary_included=False')
                if r['descriptive_reference']: failed_flags.append('descriptive_reference=True')
                if r['target_read_count'] <= 0: failed_flags.append('nonpositive target reads')
                if scope == 'DEPTH_SENSITIVITY' and not r['depth_sensitivity_included']:
                    failed_flags.append('source_depth_sensitivity_included=False')
                qc.append(dict(domain=domain, scope=scope, biological_plot_id=r['plot'], collection_date=r['date'],
                    analysis_sample=r['analysis_sample'], sample_key=r['sample_key'], treatment=r['treatment'], partition=partition,
                    disposition=disposition, reason={'OUTSIDE_FOLD': 'Date not in either frozen fold partition',
                    'EXCLUDED_SOURCE_FLAGS': '; '.join(failed_flags),
                    'TRAINING_ELIGIBLE': 'Eligible 2023 training row selected before future flags/outcomes',
                    'TEST_EVALUATED': 'Eligible target with independently fitted 2023 plot history',
                    'HELD_NO_ELIGIBLE_PRIOR': 'No eligible 2023 history; no history borrowed'}[disposition],
                    eligible_in_scope=included, primary_included=r['primary_included'],
                    descriptive_reference=r['descriptive_reference'],
                    depth_sensitivity_included=r['depth_sensitivity_included'], target_read_count=r['target_read_count']))
    prediction_fields = ['domain', 'scope', 'method', 'biological_plot_id', 'treatment', 'held_out_date',
                        'predicted_Shannon', 'last_prior_date', 'sample_key', 'analysis_sample',
                        'training_digest', 'prediction_digest', 'fold_id', 'origin_job_id', 'origin_kind']
    table(out / 'predictions.csv', predictions, prediction_fields)
    prediction_hash = sha(out / 'predictions.csv')
    save(out / 'prediction_freeze.json', dict(status='FROZEN_BEFORE_SCORING', execution=execution, fold_id=NEW_FOLD,
        training_dates=list(TRAIN), held_out_actual_dates=list(TARGET), cutoff_date=CUTOFF,
        grouped_annual_round='2024', same_fit_used_for_both_actual_dates=True,
        training_response_partition='2023_ONLY', future_availability_used_for_training=False,
        all_scope_predictions_frozen_before_any_scoring=True, predictions_csv_sha256=prediction_hash,
        training_fits_sha256=fits_hash, scope_prediction_digests=scope_digests))
    errors, performance, groups = [], [], {}
    for domain in DOMAINS:
        for scope in SCOPES:
            selected = [p for p in predictions if p['domain'] == domain and p['scope'] == scope]
            validate_prediction_identity(selected, observed_sets[(domain, scope)])
            scored = score(selected, observed_sets[(domain, scope)])
            errors.extend(scored)
            for method in METHODS:
                group = [r for r in scored if r['method'] == method]
                groups[(domain, scope, method)] = group
                record = dict(domain=domain, scope=scope, method=method, fold_id=NEW_FOLD,
                    origin_job_id=execution['job_id'], origin_kind='NEW_2024_BACKTEST')
                if group:
                    record.update(metrics(group), status='DESCRIPTIVE_POINT_DIAGNOSTIC', reason='')
                else:
                    status = fits[(domain, scope)]['method_status'][method]
                    record.update(status=status if status != 'ESTIMATED_TRAINING_ONLY' else NO_SUPPORT,
                        n_evaluated_plots=0, evaluation_plot_ids='', MAE='', RMSE='',
                        reason='All four declared training arms must have equal plot counts >=2' if status == ARM_SKIP
                               else 'No eligible known-plot target in this frozen annual round')
                performance.append(record)
    paired_errors, paired_performance = [], []
    comparisons = [(d, s, s, a, b, 'WITHIN_SCOPE_METHODS') for d in DOMAINS for s in SCOPES
        for i, a in enumerate(METHODS) for b in METHODS[i + 1:]]
    comparisons += [(d, 'PRIMARY', 'DEPTH_SENSITIVITY', m, m, 'MATCHED_TEST_COHORT_DIFFERENT_TRAINING_SCOPES')
                    for d in DOMAINS for m in METHODS]
    for domain, sa, sb, ma, mb, kind in comparisons:
        left, right = groups[(domain, sa, ma)], groups[(domain, sb, mb)]
        paired_identity(left, right)
        paired, summary = compare_fixed_errors(left, right)
        base = dict(comparison_type=kind, domain=domain, scope=sa if sa == sb else 'PRIMARY_VS_DEPTH_SENSITIVITY',
            scope_A=sa, scope_B=sb, method_A=ma, method_B=mb, fold_id=NEW_FOLD,
            origin_job_id=execution['job_id'], origin_kind='NEW_2024_BACKTEST')
        if not paired:
            summary.update(reason='No exact common evaluated plots; skipped baselines remain unestimated',
                MAE_A='', MAE_B='', RMSE_A='', RMSE_B='', mean_delta_abs_error='', delta_RMSE='')
        else:
            summary['reason'] = 'Fixed 2023 fits compared on identical common annual-round plots; differences are descriptive'
        paired_performance.append(dict(base, **summary))
        paired_errors.extend(dict(base, **r) for r in paired)
    require(sha(out / 'predictions.csv') == prediction_hash and sha(out / 'training_fits.json') == fits_hash,
            'Frozen 2024 predictions or fits changed while scoring.')
    table(out / 'plot_errors.csv', errors, prediction_fields + ['observed_Shannon', 'signed_error', 'absolute_error', 'squared_error'])
    table(out / 'performance.csv', performance, ['domain', 'scope', 'method', 'status', 'n_evaluated_plots', 'evaluation_plot_ids', 'MAE', 'RMSE', 'reason'])
    table(out / 'paired_errors.csv', paired_errors, ['comparison_type', 'domain', 'scope', 'method_A', 'method_B', 'biological_plot_id'])
    table(out / 'paired_performance.csv', paired_performance, ['comparison_type', 'domain', 'scope', 'method_A', 'method_B', 'scope_A', 'scope_B', 'n_common_plots', 'common_plot_ids', 'MAE_A', 'MAE_B', 'RMSE_A', 'RMSE_B', 'mean_delta_abs_error', 'delta_RMSE', 'status', 'reason'])
    table(out / 'cohort_qc.csv', qc, ['domain', 'scope', 'biological_plot_id', 'collection_date', 'partition', 'disposition', 'reason'])
    table(out / 'cohort_support.csv', cohorts, ['domain', 'scope', 'n_training_rows', 'n_training_plots', 'n_known_eligible_test_plots'])
    return dict(plot_errors=errors, performance=performance, paired_errors=paired_errors,
                paired_performance=paired_performance, cohort_support=cohorts), prediction_hash


def readiness(results):
    run = results.parent
    feasibility = 'engine_source/source_snapshot/feasibility_v2/results/FEASIBILITY_STATUS.json'
    accepted = 'review/ACCEPTED_FEASIBILITY_REVIEW_24110649.json'
    definitions = [
        ('work_authorization', 'AUTHORIZED', 'User explicitly authorizes predictive-validation and ten-year work with current NRCS files.', False, 'CONFIG.json#/user_intent'),
        ('response_and_identity', 'VERIFIED_CURRENT_SCOPE', 'Pinned natural-log target-count Shannon and physical library/plot identities verified on Puma.', False,
         'results/RAW_COUNT_AUDIT.json;engine_source/source_snapshot/corrected/audit/derived_inputs.json'),
        ('temporal_validation', 'LIMITED_RETROSPECTIVE_EVIDENCE', 'Two observed target annual rounds; repeated known plots, already inspected outcomes, no independent interval/skill calibration.', True,
         'results/performance.csv;results/paired_performance.csv;CONFIG.json#/backtest_protocol'),
        ('historical_assay_availability', 'NOT_ESTABLISHED', 'Assay availability at the historical cutoffs is NOT_VERIFIED. Collection-date ordering does not verify result availability at a prediction origin.', True,
         'CONFIG.json#/protocol/historical_assay_availability;review/ACCEPTED_RETURNED_REVIEW_24126557.json'),
        ('independent_long_horizon_or_process_calibration', 'NOT_ESTABLISHED', 'Pinned feasibility evidence reports external calibration not established; no new independent calibration input is supplied.', True,
         feasibility + ';CONFIG.json#/user_intent'),
        ('measured_starting_states', 'NOT_ESTABLISHED_FOR_TEN_YEAR_MODEL', 'Shannon library values are retained. Soil stocks require measured depth/bulk density/coarse fragments and units; crop harvest states and process starting states are not established by this Shannon backtest.', True, accepted),
        ('assay_and_outcome_harmonization', 'NOT_ESTABLISHED', 'Year/assay-specific soil/crop groups remain separate; unknown methods remain unknown. PICRUSt relative functional potential is not measured activity or a calibrated process rate.', True, accepted + ';' + feasibility),
        ('scenario_and_outcome_harmonization', 'NOT_ESTABLISHED', 'Scenario-to-outcome mappings, ecological process meaning and comparable horizon/management conditions require explicit evidence.', True, accepted + ';engine_source/PROTOCOL.txt'),
        ('historical_and_future_driver_series', 'NOT_PROVIDED_TO_PACKAGE', 'No pinned future weather/management scenario inputs are supplied to this package; historical driver completeness is not established.', True,
         'CONFIG.json#/user_intent;' + accepted),
        ('parameter_driver_and_structural_uncertainty', 'NOT_ESTABLISHED', 'No independently calibrated uncertainty model or ten-year interval-performance evidence is supplied.', True, accepted),
        ('independent_sample_reuse_audit', 'NOT_ESTABLISHED', 'Prior feasibility evidence retains unresolved NIFA/NRCS reuse and independent-calibration boundaries.', True, feasibility)]
    rows = [dict(requirement=n, status=s, evidence=e, blocking_for_numerical_forecast=b, source_reference=ref) for n, s, e, b, ref in definitions]
    for row in rows:
        refs = []
        for value in row['source_reference'].split(';'):
            relative, separator, pointer = value.partition('#')
            refs.append(dict(relative_path=relative, sha256=sha(safe_file(run, relative)),
                             json_pointer=pointer if separator else ''))
        row['source_reference_hashes'] = json.dumps(refs, sort_keys=True)
    table(results / 'forecast_requirements.csv', rows, ['requirement', 'status', 'evidence', 'blocking_for_numerical_forecast', 'source_reference', 'source_reference_hashes'])
    result = dict(status='NUMERICAL_TEN_YEAR_FORECAST_WITHHELD_NOT_SUPPORTED_CURRENT_DATA', execution=execution,
        readiness_audit_completed=True, user_authorizes_work=True, current_files_only=True,
        evidence_scope='CURRENT_NRCS_FILES_ONLY', numerical_forecast_decision='WITHHELD',
        reason='NOT_SUPPORTED_CURRENT_DATA', blocking_requirements=[r['requirement'] for r in rows if r['blocking_for_numerical_forecast']],
        requirements=rows, historical_assay_availability_status='NOT_VERIFIED',
        scenario_outcome_harmonization_status='NOT_ESTABLISHED',
        forecast_requirements_path='results/forecast_requirements.csv', forecast_requirements_sha256=sha(results / 'forecast_requirements.csv'),
        near_term_validation_status='LIMITED_RETROSPECTIVE_TWO_ANNUAL_ROUND_POINT_DIAGNOSTICS',
        predictive_validation_established=False, ten_year_forecast_generated=False,
        numerical_ten_year_forecast_generated=False, scientific_review_pending=True,
        unsupported_numerical_forecast_invented=False, forecasts_not_generated_because_of_evidence=True)
    save(results / 'FORECAST_READINESS.json', result)
    return result


def main():
    require(len(sys.argv) == 2, 'Usage: backtest.py RUN_PATH')
    run = Path(sys.argv[1]).resolve(strict=True)
    cfg = load(run / 'CONFIG.json'); protocol_gate(cfg)
    for name in ('self_checks.json', 'fold_self_checks.json'):
        checks = load(run / 'audit' / name); same_execution(checks, execution)
        require(checks.get('status') == 'PASS' and checks.get('scientific_fixtures_executed_on_puma') is True
                and bool(checks.get('checks')) and all(r['status'] == 'PASS' for r in checks['checks']), 'Current Puma scientific fixtures did not pass.')
    engine, stage, source_before = verify_sources(run, cfg)
    require(sha(run / 'baseline_core.py') == cfg['source_core_sha256'] == sha(engine / 'baseline_core.py'), 'Original core byte pin changed.')
    require(sha(run / 'common.py') == sha(engine / 'common.py'), 'Original I/O helper changed.')
    original_pilot = safe_file(engine, 'pilot.py')
    code = load(engine / 'results/code_protocol_hashes.json')
    require(code['pilot.py'] == sha(original_pilot), 'Original pilot code provenance pin changed.')
    spec = importlib.util.spec_from_file_location('pinned_original_shannon_pilot', original_pilot)
    require(spec is not None and spec.loader is not None, 'Cannot load pinned original verifier definitions.')
    original = importlib.util.module_from_spec(spec); spec.loader.exec_module(original)
    results = run / 'results'; results.mkdir(exist_ok=False)
    snapshot = engine / 'source_snapshot'
    rows = original.metadata_gate(snapshot); original.expected_primary_gate(snapshot, rows)
    audits = original.count_response_audit(snapshot, rows, cfg['protocol']['Shannon_count_audit_abs_tolerance'],
                                            cfg['protocol']['Shannon_count_audit_rel_tolerance'])
    table(results / 'response_count_verification.csv', audits, ['domain', 'analysis_sample', 'sample_key', 'status', 'source_metric_overwritten'])
    save(results / 'RAW_COUNT_AUDIT.json', dict(status='PASS', execution=execution, verifier='pinned_original_pilot.count_response_audit',
        verifier_sha256=sha(original_pilot), n_verified_scoped_libraries=len(audits), source_metric_overwritten=False,
        observed_response_retained=True, response_definition='H=-sum(p*ln(p)); positive-ASV counts / total; unrarefied target counts',
        audit_table='results/response_count_verification.csv', audit_table_sha256=sha(results / 'response_count_verification.csv')))
    reused_files = ('training_fits.json', 'training_membership.csv', 'predictions.csv', 'prediction_freeze.json',
                    'plot_errors.csv', 'performance.csv', 'paired_errors.csv', 'paired_performance.csv', 'cohort_support.csv')
    reuse_before = {n: sha(engine / 'results' / n) for n in reused_files}
    old_status = load(engine / 'results/PILOT_STATUS.json'); same_execution(old_status, OLD_ORIGIN)
    require(old_status['status'] == 'PILOT_POINT_DIAGNOSTICS_COMPLETED_REVIEW_PENDING'
            and old_status['output_manifest_sha256'] == stage['engine_output_manifest_sha256'] == sha(engine / 'results/output_manifest.csv')
            and old_status['predictions_csv_sha256'] == reuse_before['predictions.csv'], 'Original 2025 frozen output pin changed.')
    computed, prediction_hash = compute_2024(rows, results / 'new_2024')
    for name, new_rows in computed.items():
        old_rows = readcsv(engine / 'results' / (name + '.csv'))
        old_tagged = [tagged(r, OLD_FOLD, OLD_ORIGIN['job_id'], 'REUSED_2025_ENGINE') for r in old_rows]
        combined = new_rows + old_tagged
        fields = list(old_rows[0]) if old_rows else ['domain', 'scope']
        table(results / (name + '.csv'), combined, fields + ['fold_id', 'origin_job_id', 'origin_kind'])
        serialized = readcsv(results / (name + '.csv'))
        actual_old = [{k: r[k] for k in fields} for r in serialized if r['fold_id'] == OLD_FOLD]
        require(actual_old == old_rows, 'Combined table changed an original 2025 byte-string row field.')
    readiness_result = readiness(results)
    engine_after, stage_after, source_after = verify_sources(run, cfg)
    require(engine_after == engine and stage_after == stage and source_after == source_before,
            'Original inputs/staging provenance changed during backtest.')
    require({n: sha(engine / 'results' / n) for n in reused_files} == reuse_before, 'Original 2025 fits/predictions/errors were modified.')
    table(results / 'source_integrity_after_backtest.csv', source_after, ['source', 'relative_path', 'expected_sha256', 'original_sha256', 'snapshot_sha256', 'status'])
    save(results / 'REUSED_2025_OUTPUTS.json', dict(status='VERIFIED_BYTE_IDENTICAL_REUSE_WITHOUT_RESCORING', execution=execution,
        engine_origin_execution=OLD_ORIGIN, fold_id=OLD_FOLD, source_output_manifest_sha256=old_status['output_manifest_sha256'],
        file_hashes=reuse_before, fits_predictions_errors_recomputed=False, combined_rows_preserve_original_string_fields=True))
    table(results / 'output_manifest.csv', [dict(relative_path=str(p.relative_to(run)), sha256=sha(p), size_bytes=p.stat().st_size)
        for p in sorted(results.rglob('*')) if p.is_file()], ['relative_path', 'sha256', 'size_bytes'])
    save(results / 'BACKTEST_STATUS.json', dict(status=SUCCESS, execution=execution, completed_utc=datetime.now(timezone.utc).isoformat(),
        source_integrity_passed=True, source_preservation_passed=True,
        source_files_verified_unchanged_after_backtest=True, source_files_changed=False,
        new_2024_fold_id=NEW_FOLD, reused_2025_fold_id=OLD_FOLD, new_2024_baseline_fits_computed=True,
        new_2024_predictions_computed=True, new2024_baseline_fits_computed=True,
        reused_2025_fits_predictions_errors_recomputed=False, reused2025_fits_recomputed=False,
        reused_2025_outputs_byte_identical=True, engine_origin_execution=OLD_ORIGIN,
        engine_source_job_id=OLD_ORIGIN['job_id'], source_staging_job_id=execution['job_id'],
        engine_output_manifest_sha256=old_status['output_manifest_sha256'], reused_2025_file_hashes=reuse_before,
        new_2024_prediction_freeze_sha256=sha(results / 'new_2024/prediction_freeze.json'), new_2024_predictions_csv_sha256=prediction_hash,
        output_manifest='results/output_manifest.csv', output_manifest_path='results/output_manifest.csv',
        output_manifest_sha256=sha(results / 'output_manifest.csv'),
        raw_count_audit='results/RAW_COUNT_AUDIT.json', raw_count_audit_sha256=sha(results / 'RAW_COUNT_AUDIT.json'),
        forecast_readiness='results/FORECAST_READINESS.json', forecast_readiness_sha256=sha(results / 'FORECAST_READINESS.json'),
        user_authorizes_work=True, current_files_only=True, predictive_validation_established=False,
        ten_year_forecast_generated=False, numerical_ten_year_forecast_generated=False,
        readiness_audit_completed=readiness_result['readiness_audit_completed'],
        intervals_generated=False, p_values_computed=False, winner_selected=False, rounds_pooled_as_independent_plots=False,
        scientific_review_pending=True, historical_assay_availability_verified=False,
        validation_scope='RETROSPECTIVE_KNOWN_PLOT_TWO_TARGET_ANNUAL_ROUND_POINT_DIAGNOSTICS'))


if __name__ == '__main__':
    main()
