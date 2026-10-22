#!/usr/bin/env python3
"""Exploratory exact permutation tests of pre-specified bacterial genus log-ratios.

Run: python3 analyze.py
Requires Python 3.10+, numpy, pandas, scipy. No network or HPC submission.
This implements the enclosed protocol; it is not ANCOM-BC, ALDEx2, or LDM.
"""
from pathlib import Path
from itertools import combinations
from collections import Counter
import hashlib
import json
import math
import platform
import sys

import numpy as np
import pandas as pd
import scipy
from scipy.stats import t as student_t

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "tables"
PROTOCOL_SHA = "da4debd25475abe040a09a912809ab949946fd284ae30f57ba23dcd8e114be72"
CONTROL = "ControlFertigationOnly"
VARIANTS = [("primary", 0.5, "eligible"),
            ("pseudocount_0.1", 0.1, "eligible"),
            ("pseudocount_1", 1.0, "eligible"),
            ("all_positive_reference", 0.5, "positive")]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bh(p):
    p = np.asarray(p, float)
    assert np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all()
    order = np.argsort(p, kind="stable")
    ranked = p[order] * len(p) / np.arange(1, len(p) + 1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1].clip(0, 1)
    out = np.empty(len(p))
    out[order] = adjusted
    return out


def partitions(n, size, blocks=None):
    """Enumerate unlabelled equal-size groups, anchored at the first remaining row.

    With blocks, every group must contain the same number from each block.
    Each returned partition stands for k! equally weighted labelled allocations.
    """
    assert n % size == 0
    k = n // size
    if blocks is not None:
        blocks = np.asarray(blocks)
        totals = Counter(blocks)
        assert all(v % k == 0 for v in totals.values())
        required = {b: v // k for b, v in totals.items()}
    else:
        required = None

    def allowed(group):
        return required is None or Counter(blocks[list(group)]) == required

    def generate(remaining, chosen):
        if len(remaining) == size:
            if allowed(remaining):
                yield chosen + [remaining]
            return
        anchor = remaining[0]
        for rest in combinations(remaining[1:], size - 1):
            group = (anchor,) + rest
            if allowed(group):
                used = set(group)
                yield from generate(tuple(i for i in remaining if i not in used),
                                    chosen + [group])

    return np.asarray(list(generate(tuple(range(n)), [])), dtype=np.int16)


def model_matrix(treatments, dates, control=CONTROL):
    tr = np.asarray(treatments)
    dt = np.asarray(dates)
    treatment_levels = [control] + sorted(set(tr) - {control})
    assert control in set(tr)
    date_levels = sorted(set(dt))
    cols = [np.ones(len(tr))]
    names = ["Intercept"]
    for d in date_levels[1:]:
        cols.append((dt == d).astype(float))
        names.append("date:" + str(d))
    reduced_cols = len(cols)
    for t in treatment_levels[1:]:
        cols.append((tr == t).astype(float))
        names.append("treatment:" + str(t))
    return np.column_stack(cols), names, reduced_cols


def ols(X, Y):
    beta, _, rank, _ = np.linalg.lstsq(X, Y, rcond=None)
    assert rank == X.shape[1]
    resid = Y - X @ beta
    sse = np.sum(resid**2, axis=0)
    df = len(X) - rank
    assert df > 0
    covariance_base = np.linalg.inv(X.T @ X)
    se = np.sqrt(np.maximum(0, np.diag(covariance_base)[:, None] * sse[None, :] / df))
    return beta, se, sse, df


def direct_f(X, Y, reduced_cols):
    """Independent QR projection oracle; does not use group-sum shortcut."""
    Qf, _ = np.linalg.qr(X, mode="reduced")
    Qr, _ = np.linalg.qr(X[:, :reduced_cols], mode="reduced")
    full_sse = np.sum((Y - Qf @ (Qf.T @ Y))**2, axis=0)
    reduced_sse = np.sum((Y - Qr @ (Qr.T @ Y))**2, axis=0)
    term_ss = np.maximum(0, reduced_sse - full_sse)
    f = term_ss / (X.shape[1] - reduced_cols) / (full_sse / (len(X) - X.shape[1]))
    return f, term_ss, reduced_sse


def exact_test(Y, treatments, dates, allocations):
    tr = np.asarray(treatments)
    dt = np.asarray(dates)
    k = len(set(tr))
    size = len(tr) // k
    # Orthogonality requires identical block proportions in every group.
    for d in set(dt):
        values = [np.sum((tr == t) & (dt == d)) for t in set(tr)]
        assert len(set(values)) == 1
    centered = Y.copy()
    for d in set(dt):
        mask = dt == d
        centered[mask] -= centered[mask].mean(axis=0)
    reduced_ss = (centered**2).sum(axis=0)
    observed_ss = sum(centered[tr == t].sum(axis=0)**2 / size for t in set(tr))
    residual_df = len(Y) - len(set(dt)) - (k - 1)
    denominator = reduced_ss - observed_ss
    assert np.all(denominator > 1e-13)
    observed_f = observed_ss / (k - 1) / (denominator / residual_df)
    exceedances = np.zeros(Y.shape[1], dtype=np.int64)
    tol = 1e-12 * np.maximum(1, reduced_ss)
    # Precompute costs for distinct allowed groups instead of allocating a large tensor.
    unique_groups, ids = np.unique(allocations.reshape(-1, size), axis=0, return_inverse=True)
    ids = ids.reshape(len(allocations), k)
    costs = centered[unique_groups].sum(axis=1)**2 / size
    for start in range(0, len(ids), 1024):
        permuted_ss = costs[ids[start:start+1024]].sum(axis=1)
        exceedances += np.sum(permuted_ss >= observed_ss - tol, axis=0)
    assert np.all(exceedances >= 1)
    return observed_f, observed_ss / reduced_ss, exceedances / len(allocations), exceedances


def validate_algorithms():
    rng = np.random.default_rng(20260924)
    validations = []
    # Both toy designs have two equal groups. Enumerate every labelled allocation
    # independently, fit it by QR, and compare the exact tail with partition code.
    for name, dates in [("free", np.array(["one"] * 6)),
                        ("within_date", np.array(["a", "a", "b", "b", "c", "c"]))]:
        observed = np.array(["control", "treated", "control", "treated", "control", "treated"])
        Y = rng.normal(size=(6, 5))
        parts = partitions(6, 3, dates)
        f, _, p, _ = exact_test(Y, observed, dates, parts)
        X, _, nr = model_matrix(observed, dates, control="control")
        fq, _, _ = direct_f(X, Y, nr)
        np.testing.assert_allclose(f, fq, rtol=1e-10, atol=1e-10)
        brute = []
        for chosen in combinations(range(6), 3):
            if any(sum(dates[i] == d for i in chosen) != np.sum(dates == d) // 2
                   for d in set(dates)):
                continue
            labels = np.array(["treated"] * 6)
            labels[list(chosen)] = "control"
            xx, _, rr = model_matrix(labels, dates, control="control")
            ff, _, _ = direct_f(xx, Y, rr)
            brute.append(ff)
        p_brute = (np.array(brute) >= fq - 1e-10).mean(axis=0)
        np.testing.assert_allclose(p, p_brute, atol=1e-14)
        assert len(brute) == len(parts) * 2
        validations.append({"design": name, "labelled_allocations": len(brute),
                            "unlabelled_partitions": len(parts), "exact_p_matches_brute_force": True})
    np.testing.assert_allclose(bh([0.01, 0.04, 0.03, 0.8]), [0.04, 0.053333333333, 0.053333333333, 0.8])
    return validations


def main():
    OUT.mkdir(exist_ok=True)
    assert sha(ROOT / "ANALYSIS_PROTOCOL.json") == PROTOCOL_SHA, "Frozen protocol changed"
    protocol = json.loads((ROOT / "ANALYSIS_PROTOCOL.json").read_text())
    for entry in json.loads((ROOT / "INPUT_PROVENANCE.json").read_text()):
        assert sha(ROOT / entry["file"]) == entry["sha256"]
    validation = {"protocol_sha256": PROTOCOL_SHA, "input_hashes_verified": True,
                  "toy_tests": validate_algorithms(), "cohorts": []}
    long = pd.read_csv(ROOT / "inputs/genus_relative_abundance.tsv", sep="\t")
    metadata = pd.read_csv(ROOT / "inputs/sample_readdepth_alpha_and_design.tsv", sep="\t")
    assert not long.duplicated(["sample_name", "taxon"]).any()
    assert not metadata.sample_name.duplicated().any()
    assert (long.read_count >= 0).all() and np.equal(long.read_count, np.floor(long.read_count)).all()
    omnibus, contrasts, influence, features, summaries, clr_rows, samples = [], [], [], [], [], [], []
    all_variants_contrasts = []
    expected_features = dict(zip(protocol["cohorts"], [38, 63, 54]))
    partition_cache = {}
    for cohort in protocol["cohorts"]:
        md = metadata.loc[metadata.cohort == cohort].sort_values("sample_name").copy()
        assert len(md) == 12 and md.target_read_count.gt(0).all() and md.primary_included.all()
        assert md.plot_id.nunique() == 12
        treatments = md.treatment_label.to_numpy()
        dates = md.collection_date.to_numpy()
        assert sorted(Counter(treatments).values()) == [3, 3, 3, 3]
        source = long.loc[long.cohort == cohort]
        assert set(source.sample_name) == set(md.sample_name)
        for col in ["cohort", "collection_date", "treatment_label", "plot_id"]:
            assert source.groupby("sample_name")[col].nunique().eq(1).all()
            actual = source.groupby("sample_name")[col].first().reindex(md.sample_name).to_numpy()
            assert np.array_equal(actual, md[col].to_numpy())
        counts = source.pivot(index="sample_name", columns="taxon", values="read_count").reindex(md.sample_name)
        assert counts.notna().all().all()
        np.testing.assert_array_equal(counts.sum(axis=1), md.target_read_count)
        relative = counts.div(md.target_read_count.to_numpy(), axis=0)
        supplied_rel = source.pivot(index="sample_name", columns="taxon", values="relative_abundance").reindex(index=counts.index, columns=counts.columns)
        np.testing.assert_allclose(relative, supplied_rel, atol=2e-14, rtol=1e-11)
        prevalence = (counts >= 10).sum()
        mean_rel = relative.mean()
        eligible = (prevalence >= 6) & (mean_rel >= .001) & (counts.columns != "Unclassified_Genus")
        taxa = counts.columns[eligible].tolist()
        assert len(taxa) == expected_features[cohort]
        positive = counts.sum(axis=0) > 0
        for taxon in counts.columns:
            reasons = []
            if taxon == "Unclassified_Genus": reasons.append("unclassified_label")
            if prevalence[taxon] < 6: reasons.append("less_than_6_samples_with_10_reads")
            if mean_rel[taxon] < .001: reasons.append("mean_relative_abundance_below_0.1_percent")
            features.append({"cohort": cohort, "taxon": taxon, "eligible": bool(eligible[taxon]),
                             "samples_with_at_least_10_reads": int(prevalence[taxon]),
                             "mean_relative_abundance": float(mean_rel[taxon]),
                             "total_reads": int(counts[taxon].sum()),
                             "exclusion_reasons": ";".join(reasons)})
        count_export = counts.copy()
        count_export.insert(0, "cohort", cohort)
        count_export.to_csv(OUT / (cohort + "_genus_counts.tsv"), sep="\t")
        md["eligible_reference_read_fraction"] = relative[taxa].sum(axis=1).to_numpy()
        md["unclassified_genus_read_fraction"] = relative.get("Unclassified_Genus", 0).to_numpy()
        samples.append(md)
        for treatment in sorted(set(treatments)):
            mask = treatments == treatment
            for taxon in taxa:
                rr = relative.loc[mask, taxon]
                cc = counts.loc[mask, taxon]
                summaries.append({"cohort": cohort, "treatment": treatment, "taxon": taxon,
                    "n": int(mask.sum()), "mean_percent": 100 * rr.mean(),
                    "median_percent": 100 * rr.median(), "min_percent": 100 * rr.min(),
                    "max_percent": 100 * rr.max(), "samples_with_at_least_10_reads": int((cc >= 10).sum()),
                    "zero_count_samples": int((cc == 0).sum()), "total_reads": int(cc.sum())})
        X, names, n_reduced = model_matrix(treatments, dates)
        pd.DataFrame(X, index=md.sample_name, columns=names).to_csv(OUT / (cohort + "_design_matrix.tsv"), sep="\t")
        cache_key = tuple(dates)
        if cache_key not in partition_cache:
            partition_cache[cache_key] = partitions(len(md), 3, dates)
        parts = partition_cache[cache_key]
        expected_partitions = 15400 if len(set(dates)) == 1 else 2520
        assert len(parts) == expected_partitions
        # Confirm uniqueness, complete row use, block balance, and observed assignment inclusion.
        assert len(np.unique(parts.reshape(len(parts), -1), axis=0)) == len(parts)
        np.testing.assert_array_equal(np.sort(parts.reshape(len(parts), -1), axis=1),
                                      np.tile(np.arange(12), (len(parts), 1)))
        for d in set(dates):
            assert np.all((dates[parts] == d).sum(axis=2) == np.sum(dates == d) // 4)
        observed_partition = sorted(tuple(np.flatnonzero(treatments == t)) for t in set(treatments))
        assert np.any(np.all(parts == np.array(observed_partition), axis=(1, 2)))
        labelled = math.prod(math.factorial(int(np.sum(dates == d))) //
                             math.prod(math.factorial(int(np.sum((dates == d) & (treatments == t))))
                                       for t in set(treatments)) for d in set(dates))
        assert labelled == len(parts) * math.factorial(4)
        max_f_error = 0.
        for variant, pseudocount, reference in VARIANTS:
            logarithms = np.log2(counts.astype(float) + pseudocount)
            reference_taxa = taxa if reference == "eligible" else counts.columns[positive].tolist()
            Y = logarithms[taxa].to_numpy() - logarithms[reference_taxa].mean(axis=1).to_numpy()[:, None]
            if reference == "eligible":
                np.testing.assert_allclose(Y.sum(axis=1), 0, atol=2e-12)
            f, partial_r2, p, exceedances = exact_test(Y, treatments, dates, parts)
            fq, ssq, reducedq = direct_f(X, Y, n_reduced)
            np.testing.assert_allclose(f, fq, rtol=2e-10, atol=2e-10)
            np.testing.assert_allclose(partial_r2, ssq / reducedq, rtol=2e-10, atol=2e-10)
            max_f_error = max(max_f_error, float(np.max(np.abs(f - fq))))
            beta, se, _, df = ols(X, Y)
            crit = student_t.ppf(.975, df)
            loo = []
            for i in range(12):
                keep = np.arange(12) != i
                b_i, _, _, _ = ols(X[keep], Y[keep])
                loo.append(b_i)
            loo = np.asarray(loo)
            for j, taxon in enumerate(taxa):
                omnibus.append({"variant": variant, "cohort": cohort, "taxon": taxon,
                    "n_samples": 12, "n_reference_taxa": len(reference_taxa),
                    "treatment_df": 3, "residual_df": df, "treatment_F": f[j],
                    "partial_R2": partial_r2[j], "exact_p": p[j],
                    "tail_partitions": int(exceedances[j]), "total_partitions": len(parts),
                    "labelled_allocations": labelled})
                for c in range(n_reduced, len(names)):
                    treatment = names[c].split(":", 1)[1]
                    loo_values = loo[:, c, j]
                    max_i = int(np.argmax(np.abs(loo_values - beta[c, j])))
                    record = {"variant": variant, "cohort": cohort, "taxon": taxon,
                        "treatment": treatment, "reference_treatment": CONTROL,
                        "log2_ratio_effect": beta[c, j], "ratio_of_ratios": 2.**beta[c, j],
                        "standard_error": se[c, j], "nominal_95_lower": beta[c, j] - crit * se[c, j],
                        "nominal_95_upper": beta[c, j] + crit * se[c, j],
                        "residual_df": df, "loo_min_effect": loo_values.min(),
                        "loo_max_effect": loo_values.max(),
                        "loo_direction_stable": bool(np.all(np.sign(loo_values) == np.sign(beta[c, j]))),
                        "max_loo_change": float(np.max(np.abs(loo_values - beta[c, j]))),
                        "most_influential_sample": md.sample_name.iloc[max_i]}
                    all_variants_contrasts.append(record)
                    if variant == "primary":
                        contrasts.append(record)
                        for i in range(12):
                            influence.append({"cohort": cohort, "taxon": taxon, "treatment": treatment,
                                "omitted_sample": md.sample_name.iloc[i],
                                "omitted_sample_treatment": treatments[i],
                                "omitted_sample_date": dates[i], "full_effect": beta[c, j],
                                "omission_effect": loo_values[i], "change": loo_values[i] - beta[c, j]})
                if variant == "primary":
                    for i in range(12):
                        clr_rows.append({"cohort": cohort, "sample_name": md.sample_name.iloc[i],
                            "collection_date": dates[i], "treatment": treatments[i], "taxon": taxon,
                            "read_count": int(counts.iloc[i][taxon]),
                            "percent": 100 * relative.iloc[i][taxon], "primary_CLR_log2": Y[i, j]})
        validation["cohorts"].append({"cohort": cohort, "samples": 12, "distinct_plots": 12,
            "eligible_taxa": len(taxa), "positive_genus_labels": int(positive.sum()),
            "exact_unlabelled_partitions": len(parts), "exact_labelled_allocations": labelled,
            "all_partition_constraints_verified": True, "observed_assignment_in_enumeration": True,
            "max_absolute_F_difference_from_QR": max_f_error,
            "counts_sum_to_target_reads": True, "relative_abundances_match_counts": True})
        print(f"Completed {cohort}: {len(taxa)} eligible labels; {len(parts)} exact partitions per variant", flush=True)
    om = pd.DataFrame(omnibus)
    for variant in om.variant.unique():
        mask = om.variant == variant
        assert mask.sum() == 155
        om.loc[mask, "q_BH_joint_155"] = bh(om.loc[mask, "exact_p"])
        for cohort in protocol["cohorts"]:
            cmask = mask & (om.cohort == cohort)
            om.loc[cmask, "q_BH_within_cohort_secondary"] = bh(om.loc[cmask, "exact_p"])
    primary = om.loc[om.variant == "primary"].copy()
    primary["primary_q_below_0.05"] = primary.q_BH_joint_155 < .05
    q_wide = om.pivot(index=["cohort", "taxon"], columns="variant", values="q_BH_joint_155")
    q_wide = q_wide.rename(columns={c: "q_" + c for c in q_wide.columns}).reset_index()
    q_wide["q_below_0.05_in_all_four"] = q_wide.filter(like="q_").lt(.05).all(axis=1)
    primary = primary.merge(q_wide, on=["cohort", "taxon"], validate="one_to_one")
    primary = primary.sort_values(["q_BH_joint_155", "cohort", "taxon"])
    primary.to_csv(OUT / "primary_omnibus.csv", index=False)
    om.to_csv(OUT / "all_variants_omnibus.csv", index=False)
    effects = pd.DataFrame(all_variants_contrasts)
    wide_effect = effects.pivot(index=["cohort", "taxon", "treatment"], columns="variant", values="log2_ratio_effect")
    wide_effect = wide_effect.rename(columns={c: "effect_" + c for c in wide_effect.columns}).reset_index()
    signs = np.sign(wide_effect.filter(like="effect_").to_numpy())
    wide_effect["direction_stable_across_four_transforms"] = (signs == signs[:, :1]).all(axis=1)
    contrasts = pd.DataFrame(contrasts).merge(wide_effect, on=["cohort", "taxon", "treatment"], validate="one_to_one")
    contrasts = contrasts.merge(primary[["cohort", "taxon", "q_BH_joint_155", "primary_q_below_0.05", "q_below_0.05_in_all_four"]], on=["cohort", "taxon"], validate="many_to_one")
    effects.to_csv(OUT / "all_variants_contrasts.csv", index=False)
    contrasts.to_csv(OUT / "primary_contrasts_with_sensitivity.csv", index=False)
    pd.DataFrame(influence).to_csv(OUT / "primary_leave_one_sample_out.csv", index=False)
    pd.DataFrame(features).to_csv(OUT / "feature_eligibility.csv", index=False)
    pd.DataFrame(summaries).to_csv(OUT / "descriptive_treatment_abundances.csv", index=False)
    pd.DataFrame(clr_rows).to_csv(OUT / "primary_sample_logratios.csv", index=False)
    pd.concat(samples).to_csv(OUT / "sample_design_and_reference_coverage.csv", index=False)
    validation.update({"status": "PASS", "primary_tests": len(primary),
        "sensitivity_tests": len(om) - len(primary), "primary_contrasts": len(contrasts),
        "primary_leave_one_out_estimates": len(influence), "all_155_tests_adjusted_together_per_variant": True,
        "R_used": False, "R_note": "Rscript not available locally; independently verified with QR/SVD and exhaustive toy allocations",
        "limitations": "Numerical validation does not establish biological independence, field randomization, exchangeability, or causal effects"})
    (ROOT / "VALIDATION.json").write_text(json.dumps(validation, indent=2) + "\n")
    (ROOT / "ENVIRONMENT.json").write_text(json.dumps({"python": sys.version, "platform": platform.platform(),
        "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__}, indent=2) + "\n")
    print(primary.groupby("cohort")[["primary_q_below_0.05", "q_below_0.05_in_all_four"]].sum().to_string())


if __name__ == "__main__":
    main()
