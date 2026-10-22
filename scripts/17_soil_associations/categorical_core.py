"""Independent-plot categorical feature associations; Puma allocation only.

The caller supplies one complete, source-approved feature vector per declared
farm/system/crop/event stratum. Labels are categories: their spelling or numeric
appearance never determines an ordered correlation. No rows are dropped here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import socket
from collections import Counter

from hpc_guard import require_hpc

_EXECUTION = require_hpc()

# Scientific imports occur only after the scheduler-backed allocation guard.
import numpy as np
from scipy.stats import rankdata


CORE_VERSION = "NRCS_CATEGORICAL_CORE_20261009_V1"
MIN_INFERENTIAL_N = 6
MIN_DESCRIPTIVE_N = 3
MIN_GROUP_N = 2
DEFAULT_PERMUTATIONS = 19_999
EXACT_MAX_N = 8
PERMUTATION_TOLERANCE = 1e-12


def _require_compute_context():
    if (_EXECUTION.get("cluster") != "puma" or
            _EXECUTION.get("job_id") != os.environ.get("SLURM_JOB_ID") or
            _EXECUTION.get("node") != socket.gethostname().split(".")[0]):
        raise SystemExit("HPC_ONLY: Verified allocation context changed")


def _multiset_allocations(counts, length):
    """Yield every unique labelled allocation once, including the identity."""
    remaining = list(counts)
    row = [0]*length

    def visit(position):
        if position == length:
            yield tuple(row)
            return
        for level in range(len(remaining)):
            if remaining[level] > 0:
                remaining[level] -= 1
                row[position] = level
                yield from visit(position+1)
                remaining[level] += 1

    yield from visit(0)


def _label_assignments(codes, labels, units, seed_context, permutations, cache):
    payload = {"version": CORE_VERSION, "units": units, "groups": labels,
               "seed_context": str(seed_context),
               "permutations": int(permutations)}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()
    cached = cache.get(key) if cache is not None else None
    if cached is not None:
        return cached
    counts = Counter(int(value) for value in codes)
    n = len(codes)
    space = math.factorial(n)//math.prod(math.factorial(count)
                                        for count in counts.values())
    if n <= EXACT_MAX_N:
        matrix = np.asarray(list(_multiset_allocations(
            [counts[level] for level in range(len(counts))], n)),
            dtype=np.int16)
        if len(matrix) != space:
            raise ValueError("Exact categorical assignment count differs")
        result = (matrix, "EXACT_UNIQUE_LABEL_ASSIGNMENTS", space, space, None)
    else:
        seed = int(key[:16], 16)
        rng = np.random.default_rng(seed)
        matrix = np.empty((permutations, n), dtype=np.int16)
        for index in range(permutations):
            matrix[index] = rng.permutation(codes)
        result = (matrix, "MONTE_CARLO_PLUS_ONE", permutations, space, seed)
    if cache is not None:
        cache.put(key, result)
    return result


def _eta_squared(values, codes, group_counts):
    centered = values-values.mean()
    total = float(np.dot(centered, centered))
    if total <= 0 or not math.isfinite(total):
        return None, total
    between = sum(float(centered[codes == level].sum())**2/count
                  for level, count in enumerate(group_counts))
    return float(np.clip(between/total, 0.0, 1.0)), total


def analyze_categorical(x, groups, plot_ids, *, positive=None, test_id="",
                        permutations=DEFAULT_PERMUTATIONS, seed_context="",
                        permutation_cache=None, descriptive_only=False):
    """Return a categorical rank eta-squared label-permutation association.

    x is the complete aligned feature vector (normally CLR abundance). Primary
    observed detection masks can be reused by the adapter for all prespecified
    robustness scenarios. At least six independently identified plots, two
    groups with at least two plots each, and three detections in at least half
    the cohort are required for an inferential candidate. Three to five plots
    can have descriptive group summaries and effect sizes, never a p-value.

    group_* dictionaries/lists support JSON outputs; explicit *_json strings
    are supplied for CSV adapters. Canonical effect is rank_eta_squared. CLR_R2
    is a separate untested descriptive raw-vector association. The permutation
    probability resolution is only a grid bound; ties and label symmetry can
    increase the actual smallest attainable p-value.
    """
    _require_compute_context()
    result = {"core_version": CORE_VERSION, "test_id": str(test_id),
              "status": "SKIPPED", "p": None, "n": 0,
              "effect": None, "effect_statistic": "rank_eta_squared",
              "rank_eta_squared": None, "CLR_R2": None,
              "group_levels": [], "group_sizes": {}, "group_medians": {},
              "group_levels_json": "[]", "group_sizes_json": "{}",
              "group_medians_json": "{}", "n_unique_x": None,
              "positive_n": None, "positive_fraction": None,
              "correction_eligible": False, "skip_reason": None,
              "permutation_mode": None, "permutation_count": None,
              "unique_label_assignment_space": None,
              "permutation_probability_resolution": None,
              "minimum_attainable_p": None, "seed": None,
              "estimand": "WITHIN_DECLARED_STRATUM_CATEGORICAL_OMNIBUS",
              "interpretation": "Observational association; no causal claim"}
    try:
        values = np.asarray(x, dtype=np.float64)
        category_values = np.asarray(groups, dtype=object)
        units = np.asarray(plot_ids, dtype=object)
    except (TypeError, ValueError):
        result["skip_reason"] = "INVALID_INPUT_VECTOR"
        return result
    if any(vector.ndim != 1 for vector in (values, category_values, units)) or not (
            len(values) == len(category_values) == len(units)):
        result["skip_reason"] = "INPUT_LENGTH_OR_DIMENSION_MISMATCH"
        return result
    n = len(values)
    result["n"] = n
    if not np.isfinite(values).all():
        result["skip_reason"] = "NONFINITE_INPUT_NO_SILENT_DROPPING"
        return result
    unit_strings = [str(unit) for unit in units]
    absent = ("", "nan", "None", "<NA>", "NA", "NaN")
    if any(unit is None or label.strip() in absent
           for unit, label in zip(units, unit_strings)):
        result["skip_reason"] = "MISSING_BIOLOGICAL_UNIT_ID"
        return result
    if len(set(unit_strings)) != n:
        result["skip_reason"] = "REPEATED_BIOLOGICAL_UNIT_WITHIN_STRATUM"
        return result
    labels = [str(label) for label in category_values]
    if any(value is None or label.strip() in absent
           for value, label in zip(category_values, labels)):
        result["skip_reason"] = "MISSING_CATEGORICAL_LABEL"
        return result
    levels = sorted(set(labels))
    level_codes = {label: index for index, label in enumerate(levels)}
    codes = np.asarray([level_codes[label] for label in labels], dtype=np.int16)
    sizes = [labels.count(label) for label in levels]
    medians = {label: float(np.median(values[codes == index]))
               for index, label in enumerate(levels)}
    result.update(group_levels=levels,
                  group_sizes=dict(zip(levels, sizes)), group_medians=medians,
                  group_levels_json=json.dumps(levels, ensure_ascii=False),
                  group_sizes_json=json.dumps(dict(zip(levels, sizes)),
                                              ensure_ascii=False),
                  group_medians_json=json.dumps(medians, ensure_ascii=False,
                                                allow_nan=False),
                  n_unique_x=int(len(np.unique(values))))
    if n < MIN_DESCRIPTIVE_N:
        result["skip_reason"] = "FEWER_THAN_THREE_INDEPENDENT_UNITS"
        return result
    if len(levels) < 2:
        result["skip_reason"] = "FEWER_THAN_TWO_GROUPS"
        return result
    if positive is not None:
        observed = np.asarray(positive)
        if observed.ndim != 1 or len(observed) != n or not all(
                isinstance(value, (bool, np.bool_)) for value in observed):
            result["skip_reason"] = "INVALID_FEATURE_POSITIVE_MASK"
            return result
        observed = observed.astype(bool)
        result["positive_n"] = int(observed.sum())
        result["positive_fraction"] = float(observed.mean())
        if result["positive_n"] < 3 or result["positive_fraction"] < 0.5:
            result["skip_reason"] = "FEATURE_PREVALENCE_GATE"
            return result
    # Count/detection gates, not transformed variance or observed p-values,
    # define the frozen discovery family. Failed eligible constants count as1
    # in the global merger while retaining their reported p as missing.
    result["correction_eligible"] = (n >= MIN_INFERENTIAL_N and
                                     min(sizes) >= MIN_GROUP_N)
    ranks = rankdata(values, method="average").astype(np.float64)
    effect, total = _eta_squared(ranks, codes, sizes)
    raw_effect, _ = _eta_squared(values, codes, sizes)
    result.update(effect=effect, rank_eta_squared=effect, CLR_R2=raw_effect)
    if descriptive_only:
        result.update(status="DESCRIPTIVE_ONLY", correction_eligible=False,
                      skip_reason="PRESPECIFIED_DESCRIPTIVE_DESIGN_HOLD")
        return result
    if effect is None:
        result.update(status=("FAILED_ELIGIBLE" if
                              result["correction_eligible"] else "SKIPPED"),
                      skip_reason="CONSTANT_RANK_VECTOR")
        return result
    if n < MIN_INFERENTIAL_N:
        result.update(status="DESCRIPTIVE_ONLY",
                      skip_reason="FEWER_THAN_SIX_INDEPENDENT_UNITS")
        return result
    if min(sizes) < MIN_GROUP_N:
        result["skip_reason"] = "GROUP_HAS_FEWER_THAN_TWO_INDEPENDENT_UNITS"
        return result
    if not isinstance(permutations, int) or permutations < DEFAULT_PERMUTATIONS:
        result.update(status="FAILED_ELIGIBLE",
                      skip_reason="INVALID_OR_INSUFFICIENT_MONTE_CARLO_BUDGET")
        return result
    try:
        matrix, mode, count, space, seed = _label_assignments(
            codes, labels, unit_strings, seed_context, permutations,
            permutation_cache)
        centered = ranks-ranks.mean()
        between = np.zeros(count, dtype=np.float64)
        for level, group_n in enumerate(sizes):
            sums = np.sum((matrix == level)*centered[None, :], axis=1)
            between += sums*sums/group_n
        null_effects = np.clip(between/total, 0.0, 1.0)
        extreme = int(np.count_nonzero(null_effects >=
                                       effect-PERMUTATION_TOLERANCE))
        exact = mode == "EXACT_UNIQUE_LABEL_ASSIGNMENTS"
        p_value = extreme/count if exact else (extreme+1)/(count+1)
        resolution = 1.0/count if exact else 1.0/(count+1)
        if not math.isfinite(p_value) or p_value <= 0 or p_value > 1:
            raise ValueError("Invalid categorical permutation p-value")
        result.update(status="INFERENTIAL", p=float(p_value),
                      permutation_mode=mode, permutation_count=int(count),
                      unique_label_assignment_space=str(space),
                      permutation_probability_resolution=resolution,
                      minimum_attainable_p=resolution, seed=seed)
    except (ValueError, FloatingPointError, ArithmeticError, MemoryError) as error:
        result.update(status="FAILED_ELIGIBLE", p=None,
                      skip_reason="PERMUTATION_COMPUTATION_FAILED:"+
                      type(error).__name__)
    return result
