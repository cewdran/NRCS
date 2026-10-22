"""Whole-plot label tests of treatment differences in fixed-date changes.

The response may contain one scalar endpoint or every coordinate of a full
Hellinger-profile change. Permutations preserve declared treatment-arm sizes.
The tested statistic is the between-centroid sum of squares divided by total
centered sum of squares. This is a difference in mean-change vectors, including
their directions; it is not a test solely of turnover magnitudes.

All numerical imports and execution are restricted to verified Puma compute
allocations. Canonical physical-plot row order and declared arm order belong to
the worker. Sharing a seed across identically ordered cohorts gives the same
label-allocation schedule across endpoints.
"""

from hpc_guard import require_hpc

execution = require_hpc()

import hashlib
import itertools
import json
import math

import numpy as np


def stable_seed(*parts):
    """Create a reproducible unsigned 64-bit seed from JSON-safe cohort facts."""
    encoded = json.dumps(parts, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], 'big')


def holm_adjust(p_values):
    """Holm-adjust one complete planned family, preserving missing outputs.

Every sequence position counts as one planned hypothesis. A skipped/missing
test is conservatively assigned p=1 for adjustment, but remains None in the
returned output. Never pass only the successful subset of a planned family.
"""
    values = list(p_values)
    tested = []
    for position, value in enumerate(values):
        if value is None:
            continue
        value = float(value)
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError('Holm p-values must be finite in [0, 1] or None.')
        tested.append((value, position))
    adjusted = [None] * len(values)
    running = 0.0
    for rank, (value, position) in enumerate(sorted(tested)):
        running = max(running, min(1.0, (len(values) - rank) * value))
        adjusted[position] = float(running)
    return adjusted


def _allocation_space(counts):
    remaining = sum(counts)
    answer = 1
    for count in counts:
        answer *= math.comb(remaining, count)
        remaining -= count
    return answer


def _iter_unique_labels(counts):
    """Enumerate each allocation to labeled arms once, without duplicates."""
    n = sum(counts)
    allocation = np.empty(n, dtype=np.int64)

    def visit(arm, available):
        if arm == len(counts) - 1:
            allocation[list(available)] = arm
            yield allocation.copy()
            return
        for selected in itertools.combinations(available, counts[arm]):
            allocation[list(selected)] = arm
            chosen = set(selected)
            rest = tuple(index for index in available if index not in chosen)
            yield from visit(arm + 1, rest)

    yield from visit(0, tuple(range(n)))


def _batch_between_ss(gram, allocations, counts):
    """Use n-by-n Gram matrices, independent of the number of features."""
    codes = np.asarray(allocations, dtype=np.int64)
    if codes.ndim == 1:
        codes = codes[None, :]
    one_hot = np.eye(len(counts), dtype=np.float64)[codes]
    membership = one_hot / np.sqrt(np.asarray(counts, dtype=np.float64))
    return np.einsum('bng,nm,bmg->b', membership, gram, membership,
                     optimize=True)


def omnibus(y, labels, arms, seed, exact_limit=5000, permutations=19999,
            min_per_arm=2):
    """Return JSON-safe results for one prespecified scalar/profile endpoint.

    y is n-by-d in canonical physical-plot row order (a 1-D input is accepted).
    labels identify the one treatment label per physical plot. arms lists all
    declared arms, including any absent from the complete paired cohort.

    At least two complete paired plots are required in EVERY declared arm and
    at least 20 unique size-preserving allocations must be possible. Constants,
    non-finite responses, and ineligible designs are explicit skipped tests.
    Skips retain p_value=None and must still count as p=1 in planned families.

    Small allocation spaces are fully enumerated, with exact p=tail/space.
    Larger spaces use uniform random label allocations WITH replacement and
    p=(1+tail)/(1+permutations). The observed assignment is additionally counted
    through the plus-one correction; duplicate random draws are not discarded.
    """
    if not isinstance(seed, (int, np.integer)) or int(seed) < 0:
        raise ValueError('seed must be a non-negative integer.')
    if not isinstance(exact_limit, int) or exact_limit < 1:
        raise ValueError('exact_limit must be a positive integer.')
    if not isinstance(permutations, int) or permutations < 1:
        raise ValueError('permutations must be a positive integer.')
    if not isinstance(min_per_arm, int) or min_per_arm < 2:
        raise ValueError('min_per_arm cannot be less than two.')
    arms = list(arms)
    labels = list(labels)
    if any(not isinstance(arm, str) for arm in arms + labels):
        raise ValueError('Arm names and labels must be strings.')
    if len(set(arms)) != len(arms) or len(arms) < 2:
        raise ValueError('Declare at least two unique treatment arms.')
    if any(label not in arms for label in labels):
        raise ValueError('Observed treatment label is not a declared arm.')
    response = np.asarray(y, dtype=np.float64)
    if response.ndim == 1:
        response = response[:, None]
    if response.ndim != 2 or len(response) != len(labels) or response.shape[1] < 1:
        raise ValueError('Response must have shape (number of labels, d>=1).')
    n, dimensions = response.shape
    counts = [labels.count(arm) for arm in arms]
    space = _allocation_space(counts)
    result = {
        'status': 'skipped', 'testable': False, 'skip_reason': None,
        'statistic': 'between_centroid_SS_over_total_centered_SS',
        'n': int(n), 'dimensions': int(dimensions),
        'arms': arms, 'group_n': dict(zip(arms, counts)),
        'group_mean': {arm: None for arm in arms},
        'mean_change_vector_norm': {arm: None for arm in arms},
        'between_ss': None, 'within_ss': None, 'total_ss': None,
        'r_squared': None, 'r2': None, 'p_value': None,
        'assignment_space': int(space), 'permutation_mode': None,
        'permutations_evaluated': 0, 'permutation_tail_count': None,
        'seed': int(seed),
        'design_rank': {
            'group_rank': int(sum(count > 0 for count in counts)),
            'between_df': int(max(sum(count > 0 for count in counts) - 1, 0)),
            'residual_df': int(n - sum(count > 0 for count in counts)),
        },
        'gates': {
            'every_declared_arm_min_n': bool(all(count >= min_per_arm for count in counts)),
            'at_least_twenty_allocations': bool(space >= 20),
            'finite_response': bool(np.isfinite(response).all()),
            'nonconstant_response': None,
        },
        'minimum_per_declared_arm': int(min_per_arm),
        'inference_scope': 'exploratory conditional whole-plot label exchangeability; no randomized causal inference',
    }

    def skip(reason):
        result['skip_reason'] = reason
        return result

    if not result['gates']['finite_response']:
        return skip('nonfinite_response')
    for arm, count in zip(arms, counts):
        if not count:
            continue
        with np.errstate(over='ignore', invalid='ignore'):
            mean = response[np.asarray(labels) == arm].mean(axis=0)
            norm = float(np.linalg.norm(mean))
        if not np.isfinite(mean).all() or not math.isfinite(norm):
            return skip('nonfinite_descriptive_mean_or_norm')
        result['group_mean'][arm] = [float(value) for value in mean]
        result['mean_change_vector_norm'][arm] = norm
    if not result['gates']['every_declared_arm_min_n']:
        return skip('fewer_than_minimum_complete_pairs_in_a_declared_arm')
    if not result['gates']['at_least_twenty_allocations']:
        return skip('fewer_than_twenty_unique_label_allocations')
    nonconstant = bool(np.any(response != response[0]))
    result['gates']['nonconstant_response'] = nonconstant
    if not nonconstant:
        result.update({'between_ss': 0.0, 'within_ss': 0.0, 'total_ss': 0.0})
        return skip('constant_response')
    with np.errstate(over='ignore', invalid='ignore', under='ignore'):
        centered = response - response.mean(axis=0)
        gram = centered @ centered.T
        total = float(np.trace(gram))
    if not np.isfinite(gram).all() or not math.isfinite(total) or total <= 0.0:
        return skip('nonpositive_or_nonfinite_centered_sum_of_squares')
    tolerance = 64.0 * np.finfo(np.float64).eps * max(n, len(arms)) * total
    arm_codes = {arm: position for position, arm in enumerate(arms)}
    observed_codes = np.asarray([arm_codes[label] for label in labels], dtype=np.int64)
    between = float(_batch_between_ss(gram, observed_codes, counts)[0])

    def checked_between(values):
        values = np.asarray(values, dtype=np.float64)
        if (not np.isfinite(values).all()
                or np.any(values < -tolerance)
                or np.any(values > total + tolerance)):
            raise ArithmeticError('Allocation sum of squares exceeded numerical bounds.')
        return np.clip(values, 0.0, total)

    between = float(checked_between([between])[0])
    within = max(0.0, total - between)
    result.update({'between_ss': between, 'within_ss': within,
                   'total_ss': total, 'r_squared': float(between / total),
                   'r2': float(between / total)})
    if space <= exact_limit:
        allocations = _iter_unique_labels(counts)
        mode = 'exact_unique_allocations'
        evaluated = space
    else:
        rng = np.random.default_rng(int(seed))
        template = np.repeat(np.arange(len(arms), dtype=np.int64), counts)
        allocations = (rng.permutation(template) for _ in range(permutations))
        mode = 'monte_carlo_with_replacement_plus_one'
        evaluated = permutations
    tail = 0
    processed = 0
    while True:
        batch = list(itertools.islice(allocations, 512))
        if not batch:
            break
        values = checked_between(_batch_between_ss(gram, batch, counts))
        tail += int(np.count_nonzero(values >= between - tolerance))
        processed += len(batch)
    if processed != evaluated:
        raise RuntimeError('Permutation enumeration did not match declared size.')
    p_value = (tail / evaluated if space <= exact_limit
               else (tail + 1) / (evaluated + 1))
    if not math.isfinite(p_value) or p_value < 0.0 or p_value > 1.0:
        raise ArithmeticError('Permutation p-value is outside [0, 1].')
    result.update({'status': 'tested', 'testable': True,
                   'permutation_mode': mode, 'permutations_evaluated': int(processed),
                   'permutation_tail_count': int(tail), 'p_value': float(p_value)})
    return result
