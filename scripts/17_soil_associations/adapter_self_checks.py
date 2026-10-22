"""Small numerical interface checks, executed only within a Puma allocation."""
from __future__ import annotations

import json
from pathlib import Path


def run_adapter_self_checks(output_path):
    from hpc_guard import require_hpc
    execution = require_hpc()
    import numpy as np
    import pandas as pd
    from feature_adapters import (_clr, _feature_sets, _taxon_value,
                                  _endpoint_provenance, _censored)

    checks = []

    def record(name, condition):
        checks.append({"check": name, "passed": bool(condition)})
        if not condition:
            raise RuntimeError("Adapter self-check failed: " + name)

    raw = pd.DataFrame([[0., 2., 6., 0.], [1., 3., 5., 7.],
                        [0., 0., 1., 2.]],
                       index=["s1", "s2", "s3"], columns=["f1", "f2", "f3", "f4"])
    clr, quality = _clr(raw, 1e-6, pd, np)
    scaled, _ = _clr(raw.multiply([11., 0.25, 99.], axis=0), 1e-6, pd, np)
    record("Multiplicative closure is invariant to sample total scale",
           np.allclose(clr, scaled, rtol=0, atol=1e-12))
    composition = np.exp(clr.to_numpy())
    composition /= composition.sum(axis=1, keepdims=True)
    record("Replacement reconstructed compositions are positive and sum to one",
           (composition > 0).all() and
           np.allclose(composition.sum(axis=1), 1, rtol=0, atol=1e-12))
    record("Zero replacement preserves all nonzero component ratios",
           np.isclose(composition[0, 1] / composition[0, 2], 2./6.) and
           np.isclose(composition[2, 2] / composition[2, 3], 1./2.))
    record("CLR is finite and every row has zero mean",
           np.isfinite(clr.to_numpy()).all() and
           np.allclose(clr.mean(axis=1), 0, rtol=0, atol=1e-12))
    record("Replacement burden is bounded and recorded",
           (quality.replaced_mass <= 0.5).all() and
           quality.loc["s2", "zero_n"] == 0 and
           quality.loc["s2", "applied_delta"] == 0)
    for delta in (1e-7, 1e-6, 1e-5):
        _, diagnostics = _clr(raw, delta, pd, np)
        record("Frozen replacement rule accepts delta " + str(delta),
               (diagnostics.requested_delta == delta).all())
    failed = False
    try:
        _clr(pd.DataFrame([[0., 0.]], columns=["a", "b"]), 1e-6, pd, np)
    except RuntimeError:
        failed = True
    record("Zero-total profiles cannot be imputed into compositions", failed)
    failed = False
    try:
        _clr(pd.DataFrame([[1., -1.]], columns=["a", "b"]), 1e-6, pd, np)
    except RuntimeError:
        failed = True
    record("Negative profiles fail the compositional gate", failed)

    # Distinct complete upstream lineage keys keep two missing genus labels
    # separate rather than inventing a single shared unnamed genus.
    rank = _taxon_value("g__", "Genus")
    first = json.dumps([(name, value) for name, value in
                        (("Kingdom", "Bacteria"), ("Family", "Family_A"),
                         ("Genus", rank))])
    second = json.dumps([(name, value) for name, value in
                         (("Kingdom", "Bacteria"), ("Family", "Family_B"),
                          ("Genus", rank))])
    record("Unclassified lineage-specific genus bins have distinct keys",
           rank == "[unclassified]" and first != second)
    metadata = pd.DataFrame({"primary_included": [True, True, True],
                             "depth_sensitivity_included": [True, False, True]},
                            index=raw.index)
    base = {"class": "Genus", "variant": "PRIMARY",
            "source_variant": "SYNTHETIC_CHECK_ONLY", "raw": raw,
            "feature_metadata": {name: {"testable": name != "f4"}
                                 for name in raw.columns},
            "quality": pd.DataFrame(index=raw.index)}
    audit = []
    sets = _feature_sets([base], metadata, pd, np, audit)
    reference = list(raw.columns)
    record("Primary, depth and zero scenarios use the same fixed denominator",
           len(sets) == 6 and all(item["reference"] == reference for item in sets))
    candidate_clr = sets[0]["clr"][["f2", "f3"]]
    record("Testing a subset leaves the already-computed CLR denominator intact",
           np.array_equal(candidate_clr.to_numpy(), clr[["f2", "f3"]].to_numpy())
           and sets[0]["raw"].shape[1] == 4)
    record("Unclassified bins remain in reference but are not named taxon tests",
           "f4" in sets[0]["reference"] and
           sets[0]["feature_metadata"]["f4"]["testable"] is False)

    source = pd.DataFrame([{"record_id": "R0001", "farm": "Synthetic",
                            "confirmed_sample_key": "plot1|2024-01-01",
                            "collection_date": "2024-01-01", "intentional_exclusion": ""}]).set_index("record_id", drop=False)
    measurement = pd.DataFrame([{"record_id": "R0001", "variable": "Trait", "unit": "u",
                                 "value": 2., "method": "", "qualifier": "",
                                 "source_id": "REPORT_ONE", "source_location": "p1"}])
    disposition = pd.DataFrame([{"record_id": "R0001", "status": "selected_plot_measurements"}])
    observation = {"record_id": "R0001", "farm": "Synthetic", "dataset": "Dataset",
                   "variable": "Trait", "unit": "u", "value": 2.,
                   "sample_key": "plot1|2024-01-01", "event": "2024-01-01"}
    provenance, reason = _endpoint_provenance(observation, {}, measurement, source,
                                              disposition, pd)
    record("Unreported assay identity stays isolated by its exact source",
           reason is None and provenance["method"] == "NOT_REPORTED" and
           provenance["assay_source_group"] == "REPORT_ONE" and
           provenance["method_verified"] is False)
    changed = dict(observation, event="2024-01-02")
    _, reason = _endpoint_provenance(changed, {}, measurement, source, disposition, pd)
    record("A one-day event mismatch cannot enter the endpoint cohort",
           reason == "SOURCE_RECORD_EXACT_COLLECTION_IDENTITY_DIFFERS")
    record("Below-limit and ND qualifier tokens are censored",
           _censored({"qualifier": "<0.1"}) and _censored({"text_value": "ND"}))
    record("Explicit at-or-above MDL numeric observations are not false nondetects",
           not _censored({"qualifier": "At or above MDL", "source_value": 2.}))
    result = {"status": "HPC_ADAPTER_SELF_CHECKS_PASSED", "execution": execution,
              "passed": True, "checks": checks,
              "local_execution": False, "scientific_source_files_changed": False}
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    import sys
    run_adapter_self_checks(sys.argv[1])
