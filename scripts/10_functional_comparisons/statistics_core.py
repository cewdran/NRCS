"""Conservative comparison helpers; scientific execution requires Puma/Slurm.

Response matrices have samples in rows. Metadata order must match response order.
No nominal F or t-test p-values are used: inference uses the restricted designs
described in each result. ``p_grid_resolution`` is the assignment-probability
grid; ties and statistic symmetries may make the minimum p-value larger.
"""
from hpc_guard import require_hpc

HPC_EXECUTION = require_hpc()

import hashlib
import itertools
import math
from collections import Counter, defaultdict

import numpy as np
from scipy import stats


def stable_seed(*parts):
    """Return an order-sensitive, process-independent 64-bit seed."""
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _finite(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _matrix(values, *, nonnegative=False):
    array = np.asarray(values, dtype=float)
    if array.ndim == 1:
        array = array[:, None]
    if array.ndim != 2 or not array.shape[0] or not array.shape[1]:
        raise ValueError("Expected a nonempty samples-by-features matrix.")
    if not np.isfinite(array).all():
        raise ValueError("Response values must all be finite.")
    if nonnegative and (array < 0).any():
        raise ValueError("Response values must be nonnegative.")
    return array


def hellinger_frame(values):
    """Row-normalize a finite nonnegative matrix and take its square root.

    Returns a NumPy array; labels and metadata remain the caller's responsibility.
    A zero-total sample is rejected instead of being silently removed.
    """
    array = _matrix(values, nonnegative=True)
    totals = array.sum(axis=1)
    if not np.isfinite(totals).all() or (totals <= 0).any():
        raise ValueError("Each sample must have a finite positive profile total.")
    return np.sqrt(array / totals[:, None])


def _metadata_records(metadata):
    if hasattr(metadata, "to_dict"):
        return metadata.to_dict(orient="records")
    return list(metadata)


def _clean_label(value, column):
    if value is None:
        raise ValueError("Missing " + column)
    if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
        raise ValueError("Missing " + column)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "<na>"}:
        raise ValueError("Missing " + column)
    return text


def _multinomial_space(labels):
    space = math.factorial(len(labels))
    for count in Counter(labels).values():
        space //= math.factorial(count)
    return space


def _multiset_assignments(labels):
    """Unique label arrangements without enumerating factorial duplicates."""
    counts = Counter(labels)
    keys = sorted(counts)
    arrangement = [None] * len(labels)

    def visit(position):
        if position == len(arrangement):
            yield tuple(arrangement)
            return
        for label in keys:
            if counts[label]:
                counts[label] -= 1
                arrangement[position] = label
                yield from visit(position + 1)
                counts[label] += 1

    yield from visit(0)


def _basis(design):
    left, singular, _ = np.linalg.svd(design, full_matrices=False)
    tolerance = (max(design.shape) * np.finfo(float).eps * singular[0]
                 if singular.size else 0.0)
    rank = int(np.count_nonzero(singular > tolerance))
    return left[:, :rank], rank


def _sse(gram, basis):
    total = float(np.trace(gram))
    fitted = float(np.sum(basis * (gram @ basis)))
    residual = total - fitted
    tolerance = 1e-10 * max(abs(total), abs(fitted), np.finfo(float).tiny)
    if residual < -tolerance:
        raise RuntimeError("Numerically invalid negative residual sum of squares.")
    return max(0.0, residual)


def _at_least(value, observed):
    if math.isinf(observed):
        return math.isinf(value) and value > 0
    tolerance = 1e-12 * max(abs(observed), 1.0)
    return value >= observed - tolerance


def treatment_test(Y, metadata, seed=0, n_perm=4999, exact_limit=5000):
    """Nested multivariate partial-F test with whole-plot permutations.

    The reduced design contains one indicator per actual collection event. The
    full design adds treatment indicators (one reference arm omitted). Each plot
    has total weight one, spread equally over its visits. Treatment labels move
    only between plots with identical ordered actual-date visit patterns.

    Exact enumeration includes the observed assignment. Monte Carlo draws may
    repeat assignments and use (1 + number >= observed) / (n_perm + 1).
    Inference requires exchangeability of plot treatment labels within patterns;
    its validity is conditional on that assumption and the observed visit design.
    """
    response = _matrix(Y)
    rows = _metadata_records(metadata)
    if len(rows) != len(response):
        raise ValueError("Metadata and response row counts differ.")
    if int(n_perm) < 1 or int(exact_limit) < 1:
        raise ValueError("Permutation counts and exact limit must be positive.")
    plots = defaultdict(list)
    dates, labels, row_plots = [], [], []
    for index, row in enumerate(rows):
        plot = _clean_label(row.get("biological_plot_id"), "biological_plot_id")
        date = _clean_label(row.get("collection_date"), "collection_date")
        label = _clean_label(row.get("treatment"), "treatment")
        plots[plot].append(index)
        dates.append(date)
        labels.append(label)
        row_plots.append(plot)
    plot_labels, patterns = {}, defaultdict(list)
    weights = np.empty(len(response), dtype=float)
    for plot in sorted(plots):
        indices = plots[plot]
        plot_treatments = {labels[i] for i in indices}
        if len(plot_treatments) != 1:
            raise ValueError("Treatment changed within biological plot " + plot)
        visit_dates = sorted(dates[i] for i in indices)
        if len(set(visit_dates)) != len(visit_dates):
            raise ValueError("Duplicate collection event within biological plot " + plot)
        plot_labels[plot] = next(iter(plot_treatments))
        patterns[tuple(visit_dates)].append(plot)
        weights[indices] = 1.0 / len(indices)
    arms = sorted(set(labels))
    independent_n = dict(sorted(Counter(plot_labels.values()).items()))
    groups = [patterns[key] for key in sorted(patterns)]
    group_labels = [[plot_labels[plot] for plot in group] for group in groups]
    spaces = [_multinomial_space(group) for group in group_labels]
    assignment_space = math.prod(spaces)
    exchangeable_n = sum(len(group) for group, values in zip(groups, group_labels)
                         if len(set(values)) > 1)
    base = {
        "status": "SKIPPED", "reason": None, "p_value": None,
        "statistic": None, "statistic_name": "multivariate_partial_F",
        "partial_r2": None, "partial_r2_definition": "(SSE_reduced-SSE_full)/SSE_reduced",
        "treatment_independent_n": independent_n, "n_samples": len(rows),
        "n_independent_plots": len(plots), "exchangeable_plot_count": exchangeable_n,
        "assignment_space": assignment_space, "df_treatment": None,
        "df_residual": None, "rank_reduced": None, "rank_full": None,
        "permutation_method": None, "permutations": 0,
        "p_grid_resolution": None, "seed": int(seed),
        "fixed_label_plot_count": len(plots) - exchangeable_n,
        "movable_label_plot_count": exchangeable_n,
        "plot_weighting": "Each plot totals one; each visit weight is 1/n_visits.",
        "restriction": "Whole-plot labels within identical ordered actual-date visit patterns.",
        "inference_assumption": "Treatment-label exchangeability between plots within visit patterns.",
        "visit_patterns": [
            {"collection_dates": list(pattern), "n_plots": len(patterns[pattern]),
             "treatment_independent_n": dict(sorted(Counter(plot_labels[p]
                 for p in patterns[pattern]).items())),
             "plot_labels": {p: plot_labels[p] for p in patterns[pattern]},
             "labels_movable": len(set(plot_labels[p] for p in patterns[pattern])) > 1}
            for pattern in sorted(patterns)
        ],
    }
    if len(arms) < 2:
        base["reason"] = "Fewer than two treatment arms."
        return base
    if any(count < 2 for count in independent_n.values()):
        base["reason"] = "Every treatment arm requires at least two independent plots."
        return base
    square_weight = np.sqrt(weights)
    event_levels = sorted(set(dates))
    reduced = np.column_stack([[date == event for date in dates]
                               for event in event_levels]).astype(float)
    reduced *= square_weight[:, None]
    weighted_response = response * square_weight[:, None]
    gram = weighted_response @ weighted_response.T
    if not np.isfinite(gram).all():
        raise ValueError("Response magnitudes overflow the Gram matrix.")
    reduced_basis, reduced_rank = _basis(reduced)
    reduced_sse = _sse(gram, reduced_basis)
    response_scale = max(float(np.trace(gram)), np.finfo(float).tiny)

    def full_design(assignment):
        sample_labels = [assignment[plot] for plot in row_plots]
        additions = np.column_stack([[label == arm for label in sample_labels]
                                     for arm in arms[1:]]).astype(float)
        additions *= square_weight[:, None]
        return np.column_stack((reduced, additions))

    observed_basis, full_rank = _basis(full_design(plot_labels))
    full_sse = _sse(gram, observed_basis)
    effect_df, residual_df = full_rank - reduced_rank, len(rows) - full_rank
    base.update(rank_reduced=reduced_rank, rank_full=full_rank,
                df_treatment=effect_df, df_residual=residual_df,
                sse_reduced=_finite(reduced_sse), sse_full=_finite(full_sse))
    if effect_df != len(arms) - 1:
        base["reason"] = "Treatment is aliased with collection events in this design."
        return base
    if residual_df <= 0:
        base["reason"] = "The full model has no residual degrees of freedom."
        return base
    if reduced_sse <= 1e-12 * response_scale:
        base["reason"] = "No response variation remains after collection-event adjustment."
        return base
    if assignment_space < 20:
        base["reason"] = "Fewer than 20 distinct restricted treatment assignments."
        return base

    def statistic(basis):
        residual = _sse(gram, basis)
        explained = max(0.0, reduced_sse - residual)
        if residual <= 1e-12 * response_scale:
            return math.inf if explained > 1e-12 * response_scale else 0.0
        return (explained / effect_df) / (residual / residual_df)

    observed = statistic(observed_basis)
    base.update(statistic=_finite(observed), statistic_infinite=math.isinf(observed),
                partial_r2=_finite(max(0.0, min(1.0, (reduced_sse - full_sse) / reduced_sse))))
    exact = assignment_space <= int(exact_limit)
    if exact:
        choices = [list(_multiset_assignments(values)) for values in group_labels]
        assignments = itertools.product(*choices)
        n_draws = assignment_space
    else:
        generator = np.random.default_rng(int(seed))
        assignments = (tuple(tuple(generator.permutation(values)) for values in group_labels)
                       for _ in range(int(n_perm)))
        n_draws = int(n_perm)
    exceedances = 0
    for values in assignments:
        assignment = {plot: label for group, permuted in zip(groups, values)
                      for plot, label in zip(group, permuted)}
        basis, rank = _basis(full_design(assignment))
        if rank != full_rank:
            raise RuntimeError("A restricted treatment assignment changed design rank.")
        exceedances += int(_at_least(statistic(basis), observed))
    base.update(status="TESTED", reason=None,
                p_value=float(exceedances / n_draws if exact else
                              (1 + exceedances) / (1 + n_draws)),
                permutation_method="exact" if exact else "monte_carlo_plus_one",
                permutations=n_draws, exceedances=exceedances,
                p_grid_resolution=float(1 / n_draws if exact else 1 / (1 + n_draws)))
    return base


def paired_change_test(delta_samples, seed=0, n_perm=4999, exact_limit=5000):
    """Squared norm of mean paired change, tested by whole-row sign flips.

    Each row must be one independent plot's change between the same actual date
    pair. The caller establishes that pairing. The test is conditional on joint
    sign symmetry of the entire multivariate change vector for each plot.
    """
    delta = _matrix(delta_samples)
    n = len(delta)
    if int(n_perm) < 1 or int(exact_limit) < 1:
        raise ValueError("Permutation counts and exact limit must be positive.")
    space = 2 ** n
    mean_delta = delta.mean(axis=0)
    gram = delta @ delta.T
    if not np.isfinite(gram).all():
        raise ValueError("Change magnitudes overflow the Gram matrix.")
    observed = max(0.0, float(gram.sum()) / (n * n))
    all_zero = bool((delta == 0).all())
    base = {
        "status": "SKIPPED", "reason": None, "p_value": None,
        "statistic": _finite(observed), "statistic_name": "squared_norm_of_mean_paired_change",
        "n_independent_pairs": n, "mean_delta": [_finite(v) for v in mean_delta],
        "all_deltas_zero": all_zero, "mean_delta_zero": bool((mean_delta == 0).all()),
        "assignment_space": space, "permutations": 0, "permutation_method": None,
        "p_grid_resolution": None, "min_attainable_p_bound": None, "seed": int(seed),
        "inference_assumption": "Independent plots with joint sign symmetry of each multivariate paired change.",
    }
    if n < 3:
        base["reason"] = "At least three independent paired plots are required."
        return base
    if all_zero:
        base["reason"] = "All paired changes are zero."
        return base
    exact = space <= int(exact_limit)
    if exact:
        assignments = itertools.product((-1, 1), repeat=n)
        draws = space
    else:
        generator = np.random.default_rng(int(seed))
        assignments = (generator.choice((-1, 1), size=n) for _ in range(int(n_perm)))
        draws = int(n_perm)
    exceedances = 0
    for assignment in assignments:
        signs = np.asarray(assignment, dtype=float)
        value = max(0.0, float(signs @ gram @ signs) / (n * n))
        exceedances += int(_at_least(value, observed))
    base.update(status="TESTED", reason=None,
                p_value=float(exceedances / draws if exact else (1 + exceedances) / (1 + draws)),
                permutations=draws, exceedances=exceedances,
                permutation_method="exact" if exact else "monte_carlo_plus_one",
                p_grid_resolution=float(1 / draws if exact else 1 / (1 + draws)),
                min_attainable_p_bound=float(2 / draws) if exact else None)
    return base


def feature_treatment_contrast(values, treatmentlabels, control, treatment,
                               seed=0, n_perm=4999, exact_limit=5000):
    """Two-sided difference in means for one event and independent plots.

    Values are relative abundances in [0,1]. Rows from arms other than the two
    specified are ignored. The caller ensures one actual event and one row per
    biological plot. The Welch 95% interval is approximate and especially fragile
    for the small group sizes here; it is not the source of the p-value.
    """
    array = np.asarray(values, dtype=float)
    labels = np.asarray([_clean_label(v, "treatment") for v in treatmentlabels], dtype=object)
    if array.ndim != 1 or len(array) != len(labels):
        raise ValueError("Feature values and treatment labels must be matching vectors.")
    if not np.isfinite(array).all() or (array < 0).any() or (array > 1 + 1e-12).any():
        raise ValueError("Feature values must be finite relative abundances in [0,1].")
    if str(control) == str(treatment):
        raise ValueError("The control and treatment arms must differ.")
    if int(n_perm) < 1 or int(exact_limit) < 1:
        raise ValueError("Permutation counts and exact limit must be positive.")
    control, treatment = str(control), str(treatment)
    selected = (labels == control) | (labels == treatment)
    ignored = int((~selected).sum())
    array, labels = array[selected], labels[selected]
    controls, treated = array[labels == control], array[labels == treatment]
    nc, nt = len(controls), len(treated)
    base = {
        "status": "SKIPPED", "reason": None, "p_value": None,
        "statistic": None, "statistic_name": "absolute_difference_in_mean_relative_abundance",
        "mean_difference": None, "mean_control": _finite(controls.mean()) if nc else None,
        "mean_treatment": _finite(treated.mean()) if nt else None,
        "control": control, "treatment": treatment,
        "n_control": nc, "n_treatment": nt, "ignored_other_arm_rows": ignored,
        "ci_low": None, "ci_high": None, "welch_df": None,
        "ci_method": "Approximate Welch 95% interval; fragile with small n and distribution assumptions.",
        "assignment_space": math.comb(nc + nt, nt) if nc + nt else 0,
        "permutations": 0, "permutation_method": None, "p_grid_resolution": None,
        "seed": int(seed),
        "inference_assumption": "Treatment labels exchangeable between independent plots at this one actual event.",
    }
    if min(nc, nt) < 2:
        base["reason"] = "Both arms require at least two independent plots."
        return base
    difference = float(treated.mean() - controls.mean())
    observed = abs(difference)
    variance_control = float(np.var(controls, ddof=1)) / nc
    variance_treatment = float(np.var(treated, ddof=1)) / nt
    se_squared = variance_control + variance_treatment
    if se_squared > 0:
        denominator = variance_control ** 2 / (nc - 1) + variance_treatment ** 2 / (nt - 1)
        welch_df = se_squared ** 2 / denominator
        margin = float(stats.t.ppf(0.975, welch_df)) * math.sqrt(se_squared)
    else:
        welch_df, margin = None, 0.0
    base.update(statistic=_finite(observed), mean_difference=_finite(difference),
                ci_low=_finite(difference - margin), ci_high=_finite(difference + margin),
                welch_df=_finite(welch_df) if welch_df is not None else None,
                degenerate_sample_variances=bool(se_squared == 0))
    if se_squared == 0:
        base.update(ci_low=None, ci_high=None,
                    uncertainty_note="No within-arm variability; Welch interval degenerate.")
        if difference == 0:
            base["reason"] = "No feature response variation across the two arms."
            return base
    space = base["assignment_space"]
    exact = space <= int(exact_limit)
    n = nc + nt
    if exact:
        assignments = itertools.combinations(range(n), nt)
        draws = space
    else:
        generator = np.random.default_rng(int(seed))
        assignments = (generator.choice(n, size=nt, replace=False) for _ in range(int(n_perm)))
        draws = int(n_perm)
    total = float(array.sum())
    exceedances = 0
    for assignment in assignments:
        treatment_total = float(array[list(assignment)].sum())
        value = abs(treatment_total / nt - (total - treatment_total) / nc)
        exceedances += int(_at_least(value, observed))
    base.update(status="TESTED", reason=None,
                p_value=float(exceedances / draws if exact else (1 + exceedances) / (1 + draws)),
                permutations=draws, exceedances=exceedances,
                permutation_method="exact" if exact else "monte_carlo_plus_one",
                p_grid_resolution=float(1 / draws if exact else 1 / (1 + draws)))
    return base


def paired_feature_contrast(delta1d, seed=0, n_perm=4999, exact_limit=5000):
    """One-feature paired mean change and two-sided whole-plot sign test.

    The caller passes changes from independent plots between one fixed actual
    date pair. The t-based 95% interval is approximate and fragile at small n.
    Its distribution assumptions do not provide the permutation p-value.
    """
    delta = np.asarray(delta1d, dtype=float)
    if delta.ndim != 1 or not len(delta) or not np.isfinite(delta).all():
        raise ValueError("Paired feature changes must be a nonempty finite vector.")
    if int(n_perm) < 1 or int(exact_limit) < 1:
        raise ValueError("Permutation counts and exact limit must be positive.")
    n = len(delta)
    mean_change = float(delta.mean())
    space = 2 ** n
    base = {
        "status": "SKIPPED", "reason": None, "p_value": None,
        "statistic": abs(mean_change), "statistic_name": "absolute_mean_paired_change",
        "mean_change": _finite(mean_change), "n_independent_pairs": n,
        "ci_low": None, "ci_high": None, "t_df": n - 1,
        "ci_method": "Approximate paired t 95% interval; fragile at small n and distribution assumptions.",
        "assignment_space": space, "permutations": 0, "permutation_method": None,
        "p_grid_resolution": None, "min_attainable_p_bound": None, "seed": int(seed),
        "inference_assumption": "Independent plots with symmetric scalar changes under the null for this fixed date pair.",
    }
    if n < 3:
        base["reason"] = "At least three independent paired plots are required."
        return base
    variance = float(np.var(delta, ddof=1))
    if not math.isfinite(variance):
        raise ValueError("Paired-change magnitudes overflow the variance calculation.")
    base["zero_sample_variance"] = variance == 0
    if variance == 0:
        base["uncertainty_note"] = "No within-pair variability; t interval degenerate."
        if mean_change == 0:
            base["reason"] = "All paired changes are zero."
            return base
    else:
        margin = float(stats.t.ppf(0.975, n - 1)) * math.sqrt(variance / n)
        base.update(ci_low=_finite(mean_change - margin), ci_high=_finite(mean_change + margin))
    exact = space <= int(exact_limit)
    if exact:
        assignments = itertools.product((-1, 1), repeat=n)
        draws = space
    else:
        generator = np.random.default_rng(int(seed))
        assignments = (generator.choice((-1, 1), size=n) for _ in range(int(n_perm)))
        draws = int(n_perm)
    exceedances = 0
    for assignment in assignments:
        value = abs(float(np.asarray(assignment, dtype=float) @ delta) / n)
        exceedances += int(_at_least(value, abs(mean_change)))
    base.update(status="TESTED", reason=None,
                p_value=float(exceedances / draws if exact else (1 + exceedances) / (1 + draws)),
                permutations=draws, exceedances=exceedances,
                permutation_method="exact" if exact else "monte_carlo_plus_one",
                p_grid_resolution=float(1 / draws if exact else 1 / (1 + draws)),
                min_attainable_p_bound=float(2 / draws) if exact else None)
    return base


def holm_adjust(p_values):
    """Holm family-wise adjustment, retaining None for untested entries."""
    result = [None] * len(p_values)
    finite = []
    for index, value in enumerate(p_values):
        if value is None:
            continue
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("P-values must be finite values in [0,1], or None.")
        finite.append((value, index))
    finite.sort()
    count, running = len(finite), 0.0
    for rank, (value, index) in enumerate(finite):
        running = max(running, min(1.0, value * (count - rank)))
        result[index] = float(running)
    return result


def bh_adjust(p_values):
    """Benjamini-Hochberg adjustment, retaining None for untested entries."""
    result = [None] * len(p_values)
    finite = []
    for index, value in enumerate(p_values):
        if value is None:
            continue
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("P-values must be finite values in [0,1], or None.")
        finite.append((value, index))
    finite.sort()
    count, running = len(finite), 1.0
    for rank in range(count, 0, -1):
        value, index = finite[rank - 1]
        running = min(running, value * count / rank)
        result[index] = float(running)
    return result


def hellinger_pca(hellinger_values):
    """Descriptive two-axis PCA of an already transformed Hellinger matrix."""
    array = _matrix(hellinger_values, nonnegative=True)
    centered = array - array.mean(axis=0)
    gram = centered @ centered.T
    if not np.isfinite(gram).all():
        raise ValueError("Profile magnitudes overflow the ordination Gram matrix.")
    eigenvalues, eigenvectors = np.linalg.eigh(gram)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    eigenvectors = eigenvectors[:, order]
    for column in range(eigenvectors.shape[1]):
        anchor = int(np.argmax(np.abs(eigenvectors[:, column])))
        if eigenvectors[anchor, column] < 0:
            eigenvectors[:, column] *= -1
    coordinates = np.zeros((len(array), 2), dtype=float)
    dimensions = min(2, len(eigenvalues))
    coordinates[:, :dimensions] = eigenvectors[:, :dimensions] * np.sqrt(eigenvalues[:dimensions])
    total = float(eigenvalues.sum())
    fractions = [float(eigenvalues[i] / total) if total > 0 and i < len(eigenvalues) else 0.0
                 for i in range(2)]
    return {"coordinates": coordinates.tolist(), "variance_fractions": fractions,
            "total_inertia": _finite(total), "zero_inertia": total == 0,
            "method": "Feature-centered PCA of Hellinger profiles; descriptive only."}
