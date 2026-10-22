"""Numerical statistical checks: execute only on an allocated Puma node."""

from hpc_guard import require_hpc

execution = require_hpc()

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np

from statistics_core import (omnibus, holm_adjust, stable_seed,
                             _allocation_space, _iter_unique_labels,
                             _batch_between_ss)


def main():
    parser = argparse.ArgumentParser()
    destination_args = parser.add_mutually_exclusive_group(required=True)
    destination_args.add_argument('--run-root')
    destination_args.add_argument('--output')
    args = parser.parse_args()
    checks = []

    def check(name, condition):
        passed = bool(condition)
        checks.append({'check': name, 'passed': passed})
        if not passed:
            raise AssertionError(name)

    # Six observations with complete separation. Only A=000/B=111 and its
    # complement are as extreme among C(6,3)=20 labeled allocations: p=2/20.
    labels = ['A'] * 3 + ['B'] * 3
    exact = omnibus([0, 0, 0, 1, 1, 1], labels, ['A', 'B'], seed=11)
    check('Analytic six-plot exact test has twenty unique allocations',
          exact['assignment_space'] == 20 and exact['permutations_evaluated'] == 20)
    check('Analytic exact p-value is two of twenty and R2 is one',
          math.isclose(exact['p_value'], 0.1, abs_tol=1e-14)
          and math.isclose(exact['r_squared'], 1.0, abs_tol=1e-14)
          and exact['permutation_tail_count'] == 2)

    y = np.asarray([[0., 1.], [1., 2.], [2., 0.],
                    [3., 2.], [4., 1.], [5., 3.]])
    base = omnibus(y, labels, ['A', 'B'], seed=33)
    translated = omnibus(y + np.asarray([100., -21.]), labels, ['A', 'B'], seed=33)
    check('Translation preserves centered test statistic and exact p-value',
          math.isclose(base['r_squared'], translated['r_squared'], abs_tol=1e-12)
          and base['p_value'] == translated['p_value'])
    tiny = omnibus(y * 1e-12, labels, ['A', 'B'], seed=33)
    check('Small finite centered SS is retained without an absolute constant cutoff',
          tiny['testable'] and tiny['total_ss'] > 0.0
          and math.isclose(base['r2'], tiny['r2'], abs_tol=1e-12)
          and base['p_value'] == tiny['p_value'])
    high_dimensional = omnibus(np.tile(y, (1, 5000)) + 1e9,
                               labels, ['A', 'B'], seed=33)
    check('Ten-thousand-coordinate large-offset test preserves centered effect and exact p',
          high_dimensional['dimensions'] == 10000
          and math.isclose(base['r2'], high_dimensional['r2'], abs_tol=1e-11)
          and base['p_value'] == high_dimensional['p_value'])
    grand_mean = y.mean(axis=0)
    direct_total = float(np.square(y - grand_mean).sum())
    direct_between = sum(float(3 * np.square(y[np.asarray(labels) == arm].mean(axis=0)
                                             - grand_mean).sum()) for arm in ['A', 'B'])
    check('Gram observed SS agrees with direct coordinate centroids',
          math.isclose(base['total_ss'], direct_total, abs_tol=1e-12)
          and math.isclose(base['between_ss'], direct_between, abs_tol=1e-12))
    order = [5, 0, 4, 1, 3, 2]
    reordered = omnibus(y[order], [labels[index] for index in order],
                         ['B', 'A'], seed=33)
    check('Exact test is invariant to cohort row order and declared arm order',
          base['p_value'] == reordered['p_value']
          and math.isclose(base['r_squared'], reordered['r_squared'], abs_tol=1e-12))

    centered = y - grand_mean
    gram = centered @ centered.T
    matches = True
    for assignment in _iter_unique_labels([3, 3]):
        direct = sum(float(3 * np.square(centered[assignment == group].mean(axis=0)).sum())
                     for group in range(2))
        via_gram = float(_batch_between_ss(gram, assignment, [3, 3])[0])
        matches = matches and math.isclose(direct, via_gram, abs_tol=1e-12)
    check('Every exact Gram allocation agrees with direct centroid SS', matches)

    constant = omnibus(np.ones((6, 3)), labels, ['A', 'B'], seed=5)
    check('Constant response is an explicit missing-p skip',
          constant['skip_reason'] == 'constant_response' and constant['p_value'] is None)
    nonfinite_y = y.copy()
    nonfinite_y[0, 0] = np.nan
    nonfinite = omnibus(nonfinite_y, labels, ['A', 'B'], seed=5)
    check('Non-finite response is rejected as an explicit skip',
          nonfinite['skip_reason'] == 'nonfinite_response' and nonfinite['p_value'] is None)
    null = omnibus([0, 1, 2, 0, 1, 2], labels, ['A', 'B'], seed=5)
    check('Nonconstant zero between-centroid effect has exact p one',
          null['testable'] and math.isclose(null['r_squared'], 0.0, abs_tol=1e-12)
          and null['p_value'] == 1.0)
    singleton = omnibus([0, 1, 2, 3, 4, 5, 6], ['A'] + ['B'] * 6,
                         ['A', 'B'], seed=5)
    absent = omnibus(y, labels, ['A', 'B', 'C'], seed=5)
    check('Singleton and absent declared arms cannot enter formal tests',
          singleton['skip_reason'] == 'fewer_than_minimum_complete_pairs_in_a_declared_arm'
          and absent['skip_reason'] == 'fewer_than_minimum_complete_pairs_in_a_declared_arm')
    too_few = omnibus([0, 1, 2, 3], ['A', 'A', 'B', 'B'], ['A', 'B'], seed=5)
    check('Fewer than twenty unique allocations cannot enter formal tests',
          too_few['skip_reason'] == 'fewer_than_twenty_unique_label_allocations')
    unbalanced = omnibus([0, 0, 2, 2, 3, 3, 4], ['A', 'A', 'B', 'B', 'C', 'C', 'C'],
                          ['A', 'B', 'C'], seed=77)
    allocations = list(_iter_unique_labels([2, 2, 3]))
    unique_allocations = {tuple(allocation.tolist()) for allocation in allocations}
    check('Unequal arm sizes preserve all allocations without duplicates',
          unbalanced['testable'] and unbalanced['assignment_space'] == 210
          and len(allocations) == len(unique_allocations) == _allocation_space([2, 2, 3])
          and all(np.bincount(allocation, minlength=3).tolist() == [2, 2, 3]
                  for allocation in allocations))
    mc1 = omnibus(y, labels, ['A', 'B'], seed=101, exact_limit=1, permutations=99)
    mc2 = omnibus(y, labels, ['A', 'B'], seed=101, exact_limit=1, permutations=99)
    check('Monte Carlo p uses plus-one rule and reproducible seeded replacement draws',
          mc1['permutation_mode'] == 'monte_carlo_with_replacement_plus_one'
          and mc1['permutations_evaluated'] == 99
          and mc1['p_value'] == (1 + mc1['permutation_tail_count']) / 100
          and mc1 == mc2 and 0.01 <= mc1['p_value'] <= 1.0)
    check('Exact and Monte Carlo p-values stay within bounds',
          all(0.0 <= test['p_value'] <= 1.0 for test in [exact, base, unbalanced, mc1, null]))
    adjusted = holm_adjust([0.01, None, 0.04])
    check('Holm includes skipped hypotheses as p one and preserves missing output',
          adjusted[1] is None and math.isclose(adjusted[0], 0.03, abs_tol=1e-14)
          and math.isclose(adjusted[2], 0.08, abs_tol=1e-14))
    check('Holm retains monotonic order, clips to one and accepts all missing',
          holm_adjust([0.5, 0.5, 0.9]) == [1.0, 1.0, 1.0]
          and holm_adjust([None, None]) == [None, None]
          and holm_adjust([]) == [])
    check('Cohort seeds are stable and change when source cohort facts change',
          stable_seed(['plot1', 'plot2'], {'A': 1, 'B': 1})
          == stable_seed(['plot1', 'plot2'], {'B': 1, 'A': 1})
          and stable_seed(['plot1', 'plot2'], {'A': 1, 'B': 1})
          != stable_seed(['plot1', 'plot3'], {'A': 1, 'B': 1}))
    # This checks finite JSON serialization, not only individual scalar fields.
    json.dumps([exact, base, constant, nonfinite, singleton, absent, unbalanced, mc1],
               allow_nan=False)
    check('Test results serialize as strict finite JSON', True)
    result = {'passed': True, 'execution': execution, 'checks': checks,
              'completed_utc': datetime.now(timezone.utc).isoformat(),
              'numerical_checks_executed_on_puma': True}
    destination = (Path(args.output) if args.output else
                   Path(args.run_root) / 'audit' / 'stage' / 'NUMERIC_SELF_CHECKS.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
