#!/usr/bin/env python3
"""Guarded point-baseline arithmetic for the frozen known-plot diagnostic."""
from hpc_guard import require_hpc
execution = require_hpc()  # Before arithmetic, fixtures, or scientific imports.

from collections import defaultdict
from datetime import date
import hashlib
import json
import math


METHODS = ('PERSISTENCE', 'EQUAL_PLOT_TRAINING_MEAN', 'EQUAL_PLOT_TRAINING_ARM_MEAN')
ARM_SKIP = 'NOT_ESTIMATED_UNBALANCED_OR_THIN_TRAINING_ARMS'
NO_SUPPORT = 'NOT_ESTIMATED_NO_ELIGIBLE_TRAINING_OR_KNOWN_PLOT_TEST_SUPPORT'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stable_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()


def mean(values):
    values = list(values)
    require(bool(values) and all(math.isfinite(v) for v in values), 'Invalid finite mean inputs.')
    return math.fsum(values) / len(values)


def eligible(row, scope):
    """Only this row's frozen flags enter its inclusion decision."""
    require(scope in ('PRIMARY', 'DEPTH_SENSITIVITY'), 'Unknown scope.')
    primary = row['primary_included'] and not row['descriptive_reference']
    primary = primary and row['target_read_count'] > 0 and math.isfinite(row['Shannon']) and row['Shannon'] >= 0
    return primary and (scope == 'PRIMARY' or row['depth_sensitivity_included'])


def select_training(rows, scope, training_dates, cutoff_date):
    """Filter dates FIRST so future outcome/depth cannot select historical rows."""
    allowed = set(training_dates)
    require(all(d < cutoff_date for d in allowed), 'Training allowlist crosses cutoff.')
    selected = []
    for row in rows:
        if row['date'] not in allowed or row['date'] >= cutoff_date:
            continue
        if eligible(row, scope):
            selected.append({key: row[key] for key in
                ('plot', 'treatment', 'date', 'Shannon', 'analysis_sample', 'sample_key')})
    return selected


def fit_baselines(training, arms, training_dates, cutoff_date):
    """Accept training rows only; held-out identity/outcome/flags are absent."""
    allowed = set(training_dates)
    require(allowed and all(d < cutoff_date for d in allowed), 'Training allowlist crosses cutoff.')
    by_plot = defaultdict(list)
    seen = set()
    for row in training:
        require(row['date'] in allowed and row['date'] < cutoff_date,
                'Whole held-out event or nonallowlisted event entered training.')
        require(row['plot'] and row['treatment'] in arms and math.isfinite(row['Shannon']),
                'Malformed baseline training identity or response.')
        key = (row['plot'], row['date'])
        require(key not in seen, 'Duplicate plot/date in training.')
        seen.add(key)
        by_plot[row['plot']].append(dict(row))
    summaries = {}
    for plot, rows in sorted(by_plot.items()):
        rows.sort(key=lambda row: row['date'])
        require(len({row['treatment'] for row in rows}) == 1, 'Training plot changed treatment.')
        summaries[plot] = dict(plot=plot, treatment=rows[0]['treatment'],
            n_training_visits=len(rows), training_dates=';'.join(row['date'] for row in rows),
            last_prior_date=rows[-1]['date'], last_prior_Shannon=rows[-1]['Shannon'],
            plot_training_mean=mean(row['Shannon'] for row in rows))
    arm_plots = {arm: [p for p, row in summaries.items() if row['treatment'] == arm] for arm in arms}
    arm_counts = {arm: len(plots) for arm, plots in arm_plots.items()}
    balanced = bool(summaries) and len(set(arm_counts.values())) == 1 and min(arm_counts.values()) >= 2
    global_mean = mean(row['plot_training_mean'] for row in summaries.values()) if summaries else None
    arm_means = {arm: mean(summaries[p]['plot_training_mean'] for p in plots)
                 for arm, plots in arm_plots.items()} if balanced else {}
    status = {method: ('ESTIMATED_TRAINING_ONLY' if summaries else NO_SUPPORT) for method in METHODS}
    status['EQUAL_PLOT_TRAINING_ARM_MEAN'] = ('ESTIMATED_TRAINING_ONLY' if balanced else
                                          ARM_SKIP if summaries else NO_SUPPORT)
    training_membership = sorted([dict(row) for row in training], key=lambda r: (r['plot'], r['date']))
    return dict(plot_summaries=summaries, arm_plot_counts=arm_counts,
                global_training_mean=global_mean, arm_training_means=arm_means,
                method_status=status, training_membership=training_membership,
                training_digest=stable_digest(training_membership))


def predict(fit, targets, held_out_date, cutoff_date):
    """Targets contain identity only; no test response or depth enters prediction."""
    predictions = []
    require(held_out_date >= cutoff_date, 'Held-out event is before administrative cutoff.')
    seen = set()
    for target in sorted(targets, key=lambda row: row['plot']):
        require(set(target) == {'plot', 'treatment', 'sample_key', 'analysis_sample'},
                'Prediction target contains response or future measured covariate.')
        plot = target['plot']
        require(plot not in seen, 'Duplicate held-out prediction target.')
        seen.add(plot)
        require(plot in fit['plot_summaries'], 'No eligible training history for a target.')
        prior = fit['plot_summaries'][plot]
        require(target['treatment'] == prior['treatment'], 'Held-out plot changed treatment.')
        for method in METHODS:
            if fit['method_status'][method] != 'ESTIMATED_TRAINING_ONLY':
                continue
            if method == 'PERSISTENCE':
                value = prior['last_prior_Shannon']
            elif method == 'EQUAL_PLOT_TRAINING_MEAN':
                value = fit['global_training_mean']
            else:
                value = fit['arm_training_means'][target['treatment']]
            require(math.isfinite(value), 'Nonfinite point prediction.')
            predictions.append(dict(method=method, biological_plot_id=plot,
                treatment=target['treatment'], sample_key=target['sample_key'],
                analysis_sample=target['analysis_sample'], held_out_date=held_out_date,
                predicted_Shannon=value, cutoff_date=cutoff_date,
                cutoff_to_test_horizon_days=(date.fromisoformat(held_out_date)-date.fromisoformat(cutoff_date)).days,
                last_prior_date=prior['last_prior_date'],
                prior_to_test_gap_days=(date.fromisoformat(held_out_date)-date.fromisoformat(prior['last_prior_date'])).days,
                n_training_visits=prior['n_training_visits'], training_dates=prior['training_dates'],
                training_digest=fit['training_digest']))
    return predictions


def score(predictions, observed):
    """Evaluate the already-frozen point predictions; do not refit."""
    errors = []
    for prediction in predictions:
        plot = prediction['biological_plot_id']
        require(plot in observed, 'Frozen prediction has no held-out observation.')
        actual = observed[plot]
        require(actual['date'] == prediction['held_out_date'] and math.isfinite(actual['Shannon']),
                'Invalid paired held-out response.')
        require(actual['sample_key'] == prediction['sample_key'], 'Held-out sample key differs from frozen target.')
        error = prediction['predicted_Shannon'] - actual['Shannon']
        errors.append(dict(prediction, observed_Shannon=actual['Shannon'], signed_error=error,
                           absolute_error=abs(error), squared_error=error * error))
    return errors


def metrics(errors):
    require(bool(errors), 'No errors for a metric.')
    plots = [row['biological_plot_id'] for row in errors]
    require(len(plots) == len(set(plots)), 'Metric would weight a physical plot more than once.')
    return dict(n_evaluated_plots=len(plots), evaluation_plot_ids=';'.join(sorted(plots)),
                MAE=mean(row['absolute_error'] for row in errors),
                RMSE=math.sqrt(mean(row['squared_error'] for row in errors)))


def compare_fixed_errors(errors_A, errors_B):
    """Re-score two ORIGINAL fit/prediction sets on their exact plot intersection."""
    a = {row['biological_plot_id']: row for row in errors_A}
    b = {row['biological_plot_id']: row for row in errors_B}
    require(len(a) == len(errors_A) and len(b) == len(errors_B), 'Duplicate plots in paired comparison.')
    common = sorted(set(a) & set(b))
    paired = []
    for plot in common:
        left, right = a[plot], b[plot]
        require(left['held_out_date'] == right['held_out_date'] and
                left['observed_Shannon'] == right['observed_Shannon'], 'Paired observations/date differ.')
        paired.append(dict(biological_plot_id=plot, held_out_date=left['held_out_date'],
            observed_Shannon=left['observed_Shannon'], prediction_A=left['predicted_Shannon'],
            prediction_B=right['predicted_Shannon'], absolute_error_A=left['absolute_error'],
            absolute_error_B=right['absolute_error'],
            delta_abs_error=right['absolute_error'] - left['absolute_error'],
            origin_A=left['last_prior_date'], origin_B=right['last_prior_date']))
    if not common:
        return paired, dict(status='NO_COMMON_EVALUATION_PLOTS', n_common_plots=0, common_plot_ids='')
    ma, mb = metrics([a[p] for p in common]), metrics([b[p] for p in common])
    return paired, dict(status='DESCRIPTIVE_PAIRED_DIAGNOSTIC', n_common_plots=len(common),
        common_plot_ids=';'.join(common), MAE_A=ma['MAE'], MAE_B=mb['MAE'],
        RMSE_A=ma['RMSE'], RMSE_B=mb['RMSE'],
        mean_delta_abs_error=mean(row['delta_abs_error'] for row in paired),
        delta_RMSE=mb['RMSE'] - ma['RMSE'])
