"""Prespecified independent-unit numeric associations; execute only on Puma.

Inputs are already source-approved, exactly matched, and transformed by the
adapter. This module never joins, aggregates, selects source samples, or pools
years. Core functions are deterministic except for a locally seeded permutation
generator. Importing the module does not execute numerical checks.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import socket
from collections import OrderedDict

from hpc_guard import require_hpc

# A scheduler-backed check precedes every scientific import. Thousands of
# features share this verified process context rather than each feature causing
# another three scheduler queries. Every worker/combine process imports afresh.
_EXECUTION = require_hpc()

import numpy as np
from scipy.stats import rankdata


CORE_VERSION = "NRCS_ASSOCIATION_CORE_20261009_V1"
MIN_INFERENTIAL_N = 6
MIN_DESCRIPTIVE_N = 3
MIN_UNIQUE_VALUES = 3
DEFAULT_PERMUTATIONS = 19_999
EXACT_MAX_N = 8
PERMUTATION_TOLERANCE = 1e-12


def _require_compute_context():
    if (_EXECUTION.get("cluster") != "puma" or
            _EXECUTION.get("job_id") != os.environ.get("SLURM_JOB_ID") or
            _EXECUTION.get("node") != socket.gethostname().split(".")[0]):
        raise SystemExit("HPC_ONLY: Verified allocation context changed")


class PermutationCache:
    """A bounded endpoint cache shared across features with identical rows."""

    def __init__(self, max_entries=4):
        if not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        self.max_entries = max_entries
        self._items = OrderedDict()

    def get(self, key):
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, key, value):
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)


def _normalized_ranks(values):
    ranks = rankdata(values, method="average").astype(np.float64)
    ranks -= ranks.mean()
    length = float(np.linalg.norm(ranks))
    if length == 0 or not math.isfinite(length):
        return None
    return ranks / length


def _rank_rho(x, y):
    rx, ry = _normalized_ranks(x), _normalized_ranks(y)
    if rx is None or ry is None:
        return None
    return float(np.clip(np.dot(rx, ry), -1.0, 1.0))


def _seed_and_key(plot_ids, y, seed_context, permutations):
    payload = {
        "version": CORE_VERSION,
        "units": [str(unit) for unit in plot_ids],
        "endpoint_values": [float(value).hex() for value in y],
        "seed_context": str(seed_context),
        "permutations": int(permutations),
    }
    key = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()
    return int(key[:16], 16), key


def _endpoint_permutations(y_ranks, plot_ids, y, seed_context,
                           permutations, cache):
    n = len(y_ranks)
    seed, key = _seed_and_key(plot_ids, y, seed_context, permutations)
    cached = cache.get(key) if cache is not None else None
    if cached is not None:
        return cached
    if n <= EXACT_MAX_N:
        count = math.factorial(n)
        indices = np.fromiter(
            (index for permutation in itertools.permutations(range(n))
             for index in permutation), dtype=np.int32, count=count*n,
        ).reshape(count, n)
        endpoint_matrix = y_ranks[indices]
        result = (endpoint_matrix, "EXACT_ALL_INDEX_PERMUTATIONS", count,
                  1.0/count, None)
    else:
        rng = np.random.default_rng(seed)
        indices = np.empty((permutations, n), dtype=np.int32)
        for index in range(permutations):
            indices[index] = rng.permutation(n)
        endpoint_matrix = y_ranks[indices]
        result = (endpoint_matrix, "MONTE_CARLO_PLUS_ONE", permutations,
                  1.0/(permutations+1), seed)
    if cache is not None:
        cache.put(key, result)
    return result


def _depth_diagnostic(x, y, log_depth):
    result = {"status": "NOT_PROVIDED", "rho": None, "p": None,
              "method": "PARTIAL_RANK_CORRELATION_DIAGNOSTIC_ONLY"}
    if log_depth is None:
        return result
    depth = np.asarray(log_depth, dtype=np.float64)
    if depth.ndim != 1 or len(depth) != len(x):
        result["status"] = "INVALID_LENGTH"
        return result
    if not np.isfinite(depth).all():
        result["status"] = "NONFINITE_DEPTH"
        return result
    if len(x) < 4 or len(np.unique(depth)) < 2:
        result["status"] = "INSUFFICIENT_DEPTH_VARIATION_OR_UNITS"
        return result
    design = np.column_stack((np.ones(len(x)), rankdata(depth,
                                                     method="average")))
    rx = rankdata(x, method="average").astype(np.float64)
    ry = rankdata(y, method="average").astype(np.float64)
    residual_x = rx-design @ np.linalg.lstsq(design, rx, rcond=None)[0]
    residual_y = ry-design @ np.linalg.lstsq(design, ry, rcond=None)[0]
    norm_x, norm_y = np.linalg.norm(residual_x), np.linalg.norm(residual_y)
    residual_tolerance = 1e-10
    if norm_x <= residual_tolerance or norm_y <= residual_tolerance:
        result["status"] = "ZERO_RESIDUAL_VARIATION"
        return result
    result.update(status="DESCRIPTIVE_DIAGNOSTIC",
                  rho=float(np.clip(np.dot(residual_x, residual_y)/
                                    (norm_x*norm_y), -1.0, 1.0)))
    return result


def _treatment_diagnostics(x, y, treatment):
    if treatment is None:
        return {"status": "NOT_PROVIDED", "groups": []}
    labels = np.asarray(treatment, dtype=object)
    if labels.ndim != 1 or len(labels) != len(x):
        return {"status": "INVALID_LENGTH", "groups": []}
    normalized = [None if value is None or str(value).strip() in
                  ("", "nan", "None", "<NA>") else str(value)
                  for value in labels]
    groups = []
    for label in sorted(set(value for value in normalized
                            if value is not None)):
        selected = np.asarray([value == label for value in normalized])
        tx, ty = x[selected], y[selected]
        row = {"treatment": label, "n": int(selected.sum()), "rho": None,
               "p": None, "status": "TOO_FEW_UNITS"}
        if len(tx) >= MIN_DESCRIPTIVE_N:
            value = _rank_rho(tx, ty)
            if value is None:
                row["status"] = "CONSTANT_VECTOR"
            else:
                row.update(status="DESCRIPTIVE_DIAGNOSTIC", rho=value)
        groups.append(row)
    return {"status": "DESCRIPTIVE_DIAGNOSTIC", "groups": groups,
            "missing_treatment_n": sum(value is None for value in normalized)}


def analyze_association(x, y, plot_ids, *, treatment=None, log_depth=None,
                        positive=None, test_id="", seed_context="",
                        permutations=DEFAULT_PERMUTATIONS,
                        permutation_cache=None):
    """Return one primary test plus non-inferential sensitivity diagnostics.

    x/y are complete one-dimensional aligned feature-CLR/endpoint vectors;
    plot_ids are independently identified physical biological-unit IDs.
    positive optionally marks pre-transformation observed feature values >0.
    Caller must enforce exact-event/crop/matrix/method strata and source flags.
    No samples are silently removed. ``p`` is the canonical raw-p field.
    """
    _require_compute_context()
    result = {
        "core_version": CORE_VERSION, "test_id": str(test_id),
        "status": "SKIPPED", "rho": None, "p": None, "n": 0,
        "n_unique_x": None, "n_unique_y": None,
        "positive_n": None, "positive_fraction": None,
        "correction_eligible": False, "skip_reason": None,
        "permutation_mode": None, "permutation_count": None,
        "minimum_attainable_p": None, "seed": None,
        "estimand": "MARGINAL_WITHIN_DECLARED_STRATUM_ACROSS_TREATMENTS",
        "depth_diagnostic": None, "treatment_diagnostics": None,
    }
    try:
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        units = np.asarray(plot_ids, dtype=object)
    except (TypeError, ValueError):
        result["skip_reason"] = "INVALID_INPUT_VECTOR"
        return result
    if any(values.ndim != 1 for values in (x, y, units)) or not (
            len(x) == len(y) == len(units)):
        result["skip_reason"] = "INPUT_LENGTH_OR_DIMENSION_MISMATCH"
        return result
    result["n"] = len(x)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        result["skip_reason"] = "NONFINITE_INPUT_NO_SILENT_DROPPING"
        return result
    unit_strings = [str(unit) for unit in units]
    if any(unit is None or label.strip() in ("", "nan", "None", "<NA>")
           for unit, label in zip(units, unit_strings)):
        result["skip_reason"] = "MISSING_BIOLOGICAL_UNIT_ID"
        return result
    if len(set(unit_strings)) != len(unit_strings):
        result["skip_reason"] = "REPEATED_BIOLOGICAL_UNIT_WITHIN_STRATUM"
        return result
    result["n_unique_x"] = int(len(np.unique(x)))
    result["n_unique_y"] = int(len(np.unique(y)))
    if len(x) < MIN_DESCRIPTIVE_N:
        result["skip_reason"] = "FEWER_THAN_THREE_INDEPENDENT_UNITS"
        return result
    if positive is not None:
        observed = np.asarray(positive)
        if observed.ndim != 1 or len(observed) != len(x) or not all(
                isinstance(value, (bool, np.bool_)) for value in observed):
            result["skip_reason"] = "INVALID_FEATURE_POSITIVE_MASK"
            return result
        observed = observed.astype(bool)
        result["positive_n"] = int(observed.sum())
        result["positive_fraction"] = float(observed.mean())
        if result["positive_n"] < 3 or result["positive_fraction"] < 0.5:
            result["skip_reason"] = "FEATURE_PREVALENCE_GATE"
            return result
    # Once an independently matched cohort passes the prespecified count and
    # detection screens, a degenerate transformed vector must not silently
    # reduce the global discovery family. Its reported p stays missing and the
    # merger conservatively contributes one to multiplicity correction.
    result["correction_eligible"] = len(x) >= MIN_INFERENTIAL_N
    if min(result["n_unique_x"], result["n_unique_y"]) < MIN_UNIQUE_VALUES:
        result["skip_reason"] = "FEWER_THAN_THREE_DISTINCT_VALUES"
        if result["correction_eligible"]:
            result["status"] = "FAILED_ELIGIBLE"
        return result
    rho = _rank_rho(x, y)
    if rho is None:
        result["skip_reason"] = "CONSTANT_RANK_VECTOR"
        return result
    result["rho"] = rho
    result["depth_diagnostic"] = _depth_diagnostic(x, y, log_depth)
    result["treatment_diagnostics"] = _treatment_diagnostics(x, y, treatment)
    if len(x) < MIN_INFERENTIAL_N:
        result.update(status="DESCRIPTIVE_ONLY",
                      skip_reason="FEWER_THAN_SIX_INDEPENDENT_UNITS")
        return result
    if not isinstance(permutations, int) or permutations < DEFAULT_PERMUTATIONS:
        result.update(status="FAILED_ELIGIBLE",
                      skip_reason="INVALID_OR_INSUFFICIENT_MONTE_CARLO_BUDGET")
        return result
    try:
        rx, ry = _normalized_ranks(x), _normalized_ranks(y)
        matrix, mode, count, minimum, seed = _endpoint_permutations(
            ry, units, y, seed_context, permutations, permutation_cache)
        null_rhos = matrix @ rx
        extreme = int(np.count_nonzero(np.abs(null_rhos) >=
                                       abs(rho)-PERMUTATION_TOLERANCE))
        p_value = (extreme/count if mode == "EXACT_ALL_INDEX_PERMUTATIONS"
                   else (extreme+1)/(count+1))
        if not math.isfinite(p_value) or p_value <= 0 or p_value > 1:
            raise ValueError("Invalid permutation p-value")
        result.update(status="INFERENTIAL", p=float(p_value),
                      permutation_mode=mode, permutation_count=int(count),
                      minimum_attainable_p=float(minimum), seed=seed)
    except (ValueError, FloatingPointError, ArithmeticError, MemoryError) as error:
        result.update(status="FAILED_ELIGIBLE", p=None,
                      skip_reason="PERMUTATION_COMPUTATION_FAILED:"+
                      type(error).__name__)
    return result


def adjust_global_pvalues(p_values, correction_eligible):
    """Global BH/BY with failed eligible tests conservatively included as p=1.

    Reported raw p-values remain untouched. Null p with eligibility False does
    not enter the family; null/invalid p with eligibility True contributes 1.
    The caller must first verify every expected worker's completion/manifest.
    """
    _require_compute_context()
    if len(p_values) != len(correction_eligible):
        raise ValueError("Multiplicity inputs have unequal length")
    eligible = [bool(value) for value in correction_eligible]
    indices = [index for index, included in enumerate(eligible) if included]
    q_bh, q_by = [None]*len(eligible), [None]*len(eligible)
    if not indices:
        return {"q_bh": q_bh, "q_by": q_by, "family_size": 0,
                "failed_eligible_p_one": 0}
    used = []
    failed = 0
    for index in indices:
        value = p_values[index]
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = math.nan
        if not math.isfinite(value) or value <= 0 or value > 1:
            value = 1.0
            failed += 1
        used.append(value)
    values = np.asarray(used, dtype=np.float64)
    order = np.argsort(values, kind="stable")
    m = len(values)
    ranks = np.arange(1, m+1, dtype=np.float64)
    harmonic = float(np.sum(1.0/ranks))
    bh_sorted = np.minimum.accumulate((values[order]*m/ranks)[::-1])[::-1]
    by_sorted = np.minimum.accumulate((values[order]*m*harmonic/
                                       ranks)[::-1])[::-1]
    bh_sorted, by_sorted = np.minimum(1.0, bh_sorted), np.minimum(1.0, by_sorted)
    for position, original in enumerate(order):
        q_bh[indices[int(original)]] = float(bh_sorted[position])
        q_by[indices[int(original)]] = float(by_sorted[position])
    return {"q_bh": q_bh, "q_by": q_by, "family_size": m,
            "failed_eligible_p_one": failed}
