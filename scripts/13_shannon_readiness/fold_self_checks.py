#!/usr/bin/env python3
"""Scientific grouping/leakage fixtures, executed only in a genuine Puma allocation."""
from hpc_guard import require_hpc
execution = require_hpc()

from pathlib import Path
import sys
from common import require, save
from baseline_core import (select_training, fit_baselines, predict, score, metrics,
                           compare_fixed_errors, stable_digest, ARM_SKIP)
from backtest import (ARMS, TRAIN, TARGET, CUTOFF, NEW_FOLD, assert_round_unique,
                      observed_map, validate_prediction_identity, paired_identity)


def main():
    require(len(sys.argv) == 2, 'Usage: fold_self_checks.py RUN_PATH')
    run = Path(sys.argv[1]).resolve(strict=True)
    checks = []
    def passed(name, detail):
        checks.append(dict(check=name, status='PASS', detail=detail))
    def refused(action):
        try:
            action()
        except (RuntimeError, ValueError):
            return
        raise RuntimeError('A deliberately invalid fold input was accepted.')
    def row(i, when, value, depth=True):
        return dict(plot='P' + str(i), treatment=ARMS[(i - 1) // 2], date=when, Shannon=value,
            sample_key='P' + str(i) + '|' + when, analysis_sample='P' + str(i) + '_' + when,
            primary_included=True, descriptive_reference=False, depth_sensitivity_included=depth,
            target_read_count=100)
    historical = [row(i, TRAIN[0], float(i)) for i in range(1, 9)]
    targets = [row(i, TARGET[0] if i <= 4 else TARGET[1], float(i + 1)) for i in range(1, 9)]
    future = [row(i, '2025-08-17', float(100 + i)) for i in range(1, 9)]
    fit = fit_baselines(select_training(historical + targets + future, 'PRIMARY', TRAIN, CUTOFF), ARMS, TRAIN, CUTOFF)
    fit_hash = stable_digest(fit)
    assert_round_unique(targets)
    predictions = []
    for actual_date in TARGET:
        identity = [dict(plot=r['plot'], treatment=r['treatment'], sample_key=r['sample_key'],
                         analysis_sample=r['analysis_sample']) for r in targets if r['date'] == actual_date]
        predictions.extend(predict(fit, identity, actual_date, CUTOFF))
    require(stable_digest(fit) == fit_hash and len(predictions) == 24
            and {r['held_out_date'] for r in predictions} == set(TARGET)
            and all(r['last_prior_date'] == TRAIN[0] for r in predictions)
            and all(r['predicted_Shannon'] == 4.5 for r in predictions if r['method'] == 'EQUAL_PLOT_TRAINING_MEAN'),
            'Split-round prediction used separate fits or leaked July into August.')
    passed('one_frozen_2023_fit_for_both_actual_2024_dates', 'Eight plots split 4/4 between dates; same training digest/global mean and 2023 origin for every prediction.')
    frozen = stable_digest(predictions)
    observed = observed_map(targets); validate_prediction_identity(predictions, observed)
    errors = score(predictions, observed)
    persistence = [r for r in errors if r['method'] == 'PERSISTENCE']
    result = metrics(persistence)
    require(result['n_evaluated_plots'] == 8 and result['MAE'] == 1.0 and result['RMSE'] == 1.0
            and stable_digest(predictions) == frozen, 'Grouped round metric counted dates or mutated predictions.')
    passed('grouped_round_one_weight_per_physical_plot', 'Persistence error is exactly one for each of eight plots; one grouped-round metric, frozen predictions unchanged.')
    changed = [dict(r, Shannon=9999.0, depth_sensitivity_included=False) for r in targets + future]
    original_selection = select_training(historical + targets + future, 'DEPTH_SENSITIVITY', TRAIN, CUTOFF)
    changed_selection = select_training(historical + changed, 'DEPTH_SENSITIVITY', TRAIN, CUTOFF)
    require(original_selection == changed_selection and fit_baselines(changed_selection, ARMS, TRAIN, CUTOFF) == fit,
            'Future outcome/depth flag selected training history.')
    refused(lambda: fit_baselines([dict(plot='P1', treatment=ARMS[0], date=TARGET[0], Shannon=5.0)], ARMS, TRAIN, CUTOFF))
    refused(lambda: select_training(historical, 'PRIMARY', TARGET, CUTOFF))
    passed('future_flags_and_dates_cannot_enter_2023_training', 'Changing all 2024/2025 outcomes/depth flags leaves fit unchanged; crossed allowlist and target-as-training are refused.')
    duplicate = [targets[0], dict(targets[0], date=TARGET[1], analysis_sample='OTHER', sample_key='OTHER')]
    refused(lambda: assert_round_unique(duplicate))
    refused(lambda: observed_map(duplicate))
    passed('duplicate_round_plot_and_observed_overwrite_refused', 'One physical plot on both actual target dates fails; duplicate observed dictionaries cannot overwrite a target.')
    altered = {p: dict(r) for p, r in observed.items()}
    altered['P1']['analysis_sample'] = 'WRONG_LIBRARY'
    refused(lambda: validate_prediction_identity(predictions, altered))
    altered = {p: dict(r) for p, r in observed.items()}
    altered['P1']['date'] = TARGET[1]
    refused(lambda: validate_prediction_identity(predictions, altered))
    passed('full_frozen_library_and_actual_date_identity_checked', 'Wrong analysis_sample or substituted annual-round date is rejected even when a plot label matches.')
    thin = fit_baselines(select_training(historical[:-1], 'PRIMARY', TRAIN, CUTOFF), ARMS, TRAIN, CUTOFF)
    require(thin['method_status']['EQUAL_PLOT_TRAINING_ARM_MEAN'] == ARM_SKIP,
            'Unequal four-arm support estimated an arm baseline.')
    passed('all_four_training_arms_required_without_subset_rebalance', 'Removing one of eight training plots yields unequal arm support; whole arm-mean method remains unestimated.')
    base = dict(domain='bacteria', fold_id=NEW_FOLD, biological_plot_id='P1', held_out_date=TARGET[0],
        sample_key='P1|' + TARGET[0], analysis_sample='P1_' + TARGET[0], treatment=ARMS[0],
        observed_Shannon=4.0, last_prior_date=TRAIN[0])
    left = [dict(base, predicted_Shannon=3.0, absolute_error=1.0, squared_error=1.0),
            dict(base, biological_plot_id='P2', sample_key='P2|' + TARGET[0], analysis_sample='P2_' + TARGET[0],
                 predicted_Shannon=104.0, absolute_error=100.0, squared_error=10000.0)]
    right = [dict(base, predicted_Shannon=2.0, absolute_error=2.0, squared_error=4.0)]
    paired_identity(left, right)
    paired, summary = compare_fixed_errors(left, right)
    require(summary['n_common_plots'] == 1 and summary['MAE_A'] == 1.0 and summary['MAE_B'] == 2.0
            and summary['mean_delta_abs_error'] == 1.0 and summary['delta_RMSE'] == 1.0,
            'Paired comparison used noncommon plots or reversed B-minus-A.')
    refused(lambda: paired_identity(left, [dict(right[0], fold_id='OTHER_FOLD')]))
    refused(lambda: paired_identity(left, [dict(right[0], sample_key='OTHER_SAMPLE')]))
    passed('paired_same_fold_full_identity_and_B_minus_A', 'Only exact common target identity is compared; noncommon plot excluded and difference sign plus fold/sample mismatches verified.')
    save(run / 'audit/fold_self_checks.json', dict(status='PASS', execution=execution, checks=checks,
        scientific_fixtures_executed_on_puma=True, fixture_data_are_synthetic=True,
        real_source_data_used=False, checks_do_not_establish_predictive_validation=True))


if __name__ == '__main__':
    main()
