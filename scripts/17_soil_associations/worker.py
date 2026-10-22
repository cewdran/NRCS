#!/usr/bin/env python3
"""One independent farm/domain/feature worker; scientific execution on Puma."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback

from hpc_guard import require_hpc
from orchestration_common import (json_file, load_shards, require, save, sha,
                                  utc, verify_manifest)

SCOPES = ["analytic_system", "collection_date", "plant_type", "cultivar"]
TRAITS = ["dataset", "variable", "unit", "method", "assay_source_group"]
FIELDS = ["test_id", "shard_id", "farm", "domain", "feature_class", "feature_id",
          "feature_label", "feature_lineage", "interpretation", "association_type",
          "cohort_id", "scenario", "source_scope", "zero_rule", "variant",
          "source_variant", "analytic_system", "event", "plant_type", "cultivar",
          "dataset", "variable", "unit", "method", "assay_source_group",
          "endpoint_origin", "coupling_caution", "treatment_stratum", "n",
          "status", "p", "correction_eligible", "skip_reason", "rho", "effect",
          "rank_eta_squared", "CLR_R2", "n_unique_x", "n_unique_y", "positive_n",
          "positive_fraction", "primary_screen_n", "primary_screen_positive_n",
          "primary_screen_positive_fraction", "primary_feature_nominated",
          "variant_positive_n", "variant_positive_fraction",
          "permutation_mode", "permutation_count", "minimum_attainable_p", "seed",
          "unique_label_assignment_space", "group_levels_json", "group_sizes_json",
          "group_medians_json", "estimand"]


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def identifier(*parts):
    return hashlib.sha256(packed(parts).encode()).hexdigest()[:24]


def sink(path, fields):
    handle = gzip.open(path, "wt", newline="") if str(path).endswith(".gz") else path.open("w", newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    return handle, writer


def cohort_specs(prepared):
    """Declare exact-event numeric and genuinely categorical cohorts."""
    import pandas as pd
    endpoints, metadata = prepared["endpoints"], prepared["metadata"]
    cohorts = []
    if len(endpoints):
        for key, rows in endpoints.groupby(SCOPES + TRAITS, sort=True, dropna=False):
            traits = dict(zip(SCOPES + TRAITS, key))
            traits["event"] = traits.pop("collection_date")
            traits["endpoint_origin"] = str(rows.iloc[0].get("endpoint_origin", "CONFIRMED_LAB_MEASUREMENT"))
            traits["coupling_caution"] = ("Exploratory association: endpoint and features derive from the same microbial counts"
                                            if rows.iloc[0].get("mathematically_coupled", False) else "")
            cohorts.append(dict(kind="NUMERIC", traits=traits, rows=rows.set_index("analysis_sample", drop=False)))
    for key, rows in metadata.groupby(SCOPES, sort=True, dropna=False):
        traits = dict(zip(SCOPES, key))
        traits["event"] = traits.pop("collection_date")
        traits.update(dataset="FIELD_DESIGN", variable="treatment", unit="CATEGORY",
                      method="CATEGORICAL_LABEL_PERMUTATION", assay_source_group="CORRECTED_METADATA_24067160")
        cohorts.append(dict(kind="TREATMENT", traits=traits, rows=rows))
    # Crop labels are observational. Do not assign a crop to control/reference
    # libraries or silently turn a missing treatment into an ordered number.
    crops = metadata.copy()
    def crop_label(row):
        plant, cultivar = str(row.plant_type).strip(), str(row.cultivar).strip()
        if not plant or plant.lower() in ("none", "nan", "<na>", "unknown") or "control" in plant.lower():
            return ""
        return plant + (" / " + cultivar if cultivar else "")
    crops["crop_label"] = crops.apply(crop_label, axis=1) if len(crops) else pd.Series(dtype=str)
    crops = crops.loc[crops.crop_label != ""]
    for key, rows in crops.groupby(["analytic_system", "collection_date", "treatment"], sort=True, dropna=False):
        system, event, treatment = key
        traits = dict(analytic_system=system, event=event, plant_type="CROP_COMPARISON",
                      cultivar="", dataset="FIELD_DESIGN", variable="crop_within_treatment",
                      unit="CATEGORY", method="CATEGORICAL_LABEL_PERMUTATION",
                      assay_source_group="CORRECTED_METADATA_24067160", treatment_stratum=treatment)
        cohorts.append(dict(kind="CROP", traits=traits, rows=rows))
    for cohort in cohorts:
        cohort["id"] = identifier(cohort["kind"], cohort["traits"])
    require(len({c["id"] for c in cohorts}) == len(cohorts), "Duplicate declared cohort")
    return cohorts


def run_worker(run, shard, execution):
    verify_manifest(run)
    config, sources = json_file(run / "CONFIG.json"), json_file(run / "SOURCES.json")
    from feature_adapters import prepare_shard, verify_sources_unchanged
    # Frozen original source status/hash checks precede scientific imports and
    # actual numerical self-check execution in this worker.
    from recovery_validation import verify_recovery_stages
    verify_recovery_stages(run, require_join=False)
    prepared = prepare_shard(run, shard, config, sources)
    prepared["feature_sets"] = [item for item in prepared["feature_sets"] if item["class"] == shard["feature_class"]]
    import numpy as np
    import pandas as pd
    from association_core import analyze_association, PermutationCache
    from categorical_core import analyze_categorical
    from adapter_self_checks import run_adapter_self_checks
    out = run / "workers" / "chunks" / shard["output_id"]
    run_adapter_self_checks(out / "ADAPTER_SELF_CHECKS.json")
    for script, name in [("association_self_checks.py", "ASSOCIATION_SELF_CHECKS.json"),
                         ("categorical_self_checks.py", "CATEGORICAL_SELF_CHECKS.json")]:
        subprocess.run([sys.executable, str(run / script), "--output", str(out / name)], check=True)
    cohorts = cohort_specs(prepared)
    references = {}
    primary = {}
    primary_profiles_by_scope = {}
    for item in prepared["feature_sets"]:
        if item["variant"] == "PRIMARY" and item["zero_rule"] == "PRIMARY":
            primary_profiles_by_scope[(item["class"], item["source_scope"])] = item
        if item["scenario"] == "PRIMARY__PRIMARY__PRIMARY":
            primary[item["class"]] = item
            references[item["class"]] = dict(features=item["reference"], sha256=item["reference_sha256"],
                                              feature_metadata=item["feature_metadata"],
                                              source_variant=item["source_variant"])
            item["raw"].to_csv(out / (item["class"] + "_primary_raw.csv.gz"), index_label="analysis_sample")
            item["clr"].to_csv(out / (item["class"] + "_primary_CLR.csv.gz"), index_label="analysis_sample")
    save(out / "FEATURE_REFERENCE.json", references)
    prepared["metadata"].to_csv(out / "sample_metadata.csv.gz", index=False)
    prepared["endpoints"].to_csv(out / "matched_endpoints.csv.gz", index=False)
    # An empty audit must still be an interpretable file with a header.
    audit = prepared["match_audit"]
    if not len(audit.columns):
        audit = pd.DataFrame(columns=["stage", "status", "reason"])
    audit.to_csv(out / "match_audit.csv.gz", index=False)
    screens = {}
    sf, sw = sink(out / "feature_screen.csv.gz", ["feature_class", "feature_id", "cohort_id", "association_type",
                "n", "positive_n", "positive_fraction", "nominated", "testable", "reason"])
    cf, cw = sink(out / "cohorts.csv", ["cohort_id", "association_type", "traits_json", "all_sample_ids_json",
                                           "all_plot_ids_json", "primary_sample_ids_json", "primary_n"])
    for cohort in cohorts:
        all_rows = cohort["rows"]
        cw.writerow(dict(cohort_id=cohort["id"], association_type=cohort["kind"], traits_json=packed(cohort["traits"]),
                         all_sample_ids_json=packed(sorted(all_rows.index)),
                         all_plot_ids_json=packed(sorted(all_rows.biological_plot_id.astype(str))),
                         primary_sample_ids_json=packed(sorted(all_rows.loc[all_rows.primary_included].index)),
                         primary_n=int(all_rows.primary_included.sum())))
        for klass, item in primary.items():
            ids = sorted(set(all_rows.index) & set(item["raw"].index))
            observed = item["positive"].reindex(index=ids)
            for feature in item["reference"]:
                testable = bool(item["feature_metadata"][feature].get("testable", True))
                n, positive_n = len(ids), int(observed[feature].sum())
                fraction = positive_n/n if n else 0.0
                nominated = testable and n >= 3 and positive_n >= 3 and fraction >= 0.5
                reason = ("NAMED_FEATURE_AND_PRIMARY_DETECTION_SCREEN" if nominated else
                          "DENOMINATOR_ONLY_UNCLASSIFIED_OR_SUMMARY" if not testable else "PRIMARY_COHORT_DETECTION_GATE")
                screen = dict(feature_class=klass, feature_id=feature, cohort_id=cohort["id"], association_type=cohort["kind"],
                              n=n, positive_n=positive_n, positive_fraction=fraction, nominated=nominated,
                              testable=testable, reason=reason)
                screens[(klass, cohort["id"], feature)] = screen
                sw.writerow(screen)
    sf.close(); cf.close()
    af, aw = sink(out / "associations.csv.gz", FIELDS)
    df, dw = sink(out / "diagnostics.csv.gz", ["test_id", "feature_class", "diagnostic_type", "diagnostic_json", "p"])
    uf, uw = sink(out / "scenario_cohorts.csv", ["feature_class", "scenario", "source_variant", "cohort_id",
                                               "association_type", "n", "sample_ids_json", "plot_ids_json",
                                               "primary_sample_ids_json", "same_population_as_primary",
                                               "variant_only_sample_ids_excluded_json"])
    counters = dict(tests_rows=0, inferential_rows=0, failed_eligible_rows=0, descriptive_rows=0, skipped_rows=0)
    for item in prepared["feature_sets"]:
        prefix = item["class"] + "_" + item["scenario"]
        item["quality"].to_csv(out / (prefix + "_quality.csv.gz"), index_label="analysis_sample")
        item["transform_audit"].to_csv(out / (prefix + "_zero_replacement.csv.gz"), index_label="analysis_sample")
        for cohort in cohorts:
            original_scope_profile = primary_profiles_by_scope[(item["class"], item["source_scope"])]
            ids = sorted(set(cohort["rows"].index) & set(item["clr"].index) & set(original_scope_profile["raw"].index))
            rows = cohort["rows"].reindex(ids)
            primary_ids = sorted(set(cohort["rows"].index) & set(primary[item["class"]]["raw"].index))
            variant_only = sorted((set(cohort["rows"].index) & set(item["clr"].index)) - set(ids))
            uw.writerow(dict(feature_class=item["class"], scenario=item["scenario"], source_variant=item["source_variant"],
                             cohort_id=cohort["id"], association_type=cohort["kind"], n=len(ids),
                             sample_ids_json=packed(ids), plot_ids_json=packed(rows.biological_plot_id.astype(str).tolist()),
                             primary_sample_ids_json=packed(primary_ids), same_population_as_primary=ids == primary_ids,
                             variant_only_sample_ids_excluded_json=packed(variant_only)))
            cache = PermutationCache(max_entries=2)
            context = identifier(shard["id"], item["class"], cohort["id"], item["scenario"])
            for feature in item["reference"]:
                screen = screens[(item["class"], cohort["id"], feature)]
                meta = item["feature_metadata"][feature]
                if not screen["testable"] or not screen["nominated"]:
                    continue  # Exclusions are fully recorded in the primary feature/cohort screen.
                test_id = identifier(shard["id"], item["class"], cohort["id"], feature, item["scenario"])
                base = dict(test_id=test_id, shard_id=shard["id"], farm=shard["farm"], domain=shard["domain"],
                            feature_class=item["class"], feature_id=feature, feature_label=meta.get("display_label", feature),
                            feature_lineage=packed(meta.get("lineage", {})), interpretation=meta.get("interpretation", ""),
                            association_type=cohort["kind"], cohort_id=cohort["id"], scenario=item["scenario"],
                            source_scope=item["source_scope"], zero_rule=item["zero_rule"], variant=item["variant"],
                            source_variant=item["source_variant"], primary_screen_n=screen["n"],
                            primary_screen_positive_n=screen["positive_n"], primary_screen_positive_fraction=screen["positive_fraction"],
                            primary_feature_nominated=screen["nominated"], **cohort["traits"])
                variant_positive_n = int(item["positive"].loc[ids, feature].sum())
                base.update(variant_positive_n=variant_positive_n,
                            variant_positive_fraction=variant_positive_n/len(ids) if ids else 0.0)
                x = item["clr"].loc[ids, feature].to_numpy(float)
                origin_mask = primary_profiles_by_scope[(item["class"], item["source_scope"])]["positive"].reindex(ids)[feature]
                require(not origin_mask.isna().any(), "Sensitivity row lacks primary-source detection identity")
                positive = origin_mask.to_numpy(bool)
                kwargs = dict(positive=positive, test_id=test_id, seed_context=context,
                              permutations=int(config["analysis"]["monte_carlo_permutations"]), permutation_cache=cache)
                if cohort["kind"] == "NUMERIC":
                    result = analyze_association(x, rows.value.to_numpy(float), rows.biological_plot_id.astype(str).tolist(),
                                                 treatment=rows.treatment.tolist(), log_depth=rows.log10_target_reads.to_numpy(float), **kwargs)
                else:
                    group = rows.treatment if cohort["kind"] == "TREATMENT" else rows.crop_label
                    field_hold = (cohort["kind"] == "TREATMENT" and shard["farm"] == "Oatman Flats"
                                  and cohort["traits"]["analytic_system"] == "OFR_2025_Field")
                    result = analyze_categorical(x, group.tolist(), rows.biological_plot_id.astype(str).tolist(),
                                                 descriptive_only=field_hold, **kwargs)
                    if field_hold:
                        result.update(p=None, correction_eligible=False, status="DESCRIPTIVE_ONLY",
                                      skip_reason="FIELD_SIDE_TREATMENT_CONFOUNDED", permutation_mode=None,
                                      permutation_count=None, minimum_attainable_p=None)
                for key in ("depth_diagnostic", "treatment_diagnostics"):
                    if result.get(key) is not None:
                        dw.writerow(dict(test_id=test_id, feature_class=item["class"], diagnostic_type=key, diagnostic_json=packed(result[key]), p=""))
                for key in ("group_levels", "group_sizes", "group_medians"):
                    if key in result:
                        result[key + "_json"] = packed(result[key])
                if "permutation_resolution" in result:
                    result["minimum_attainable_p"] = result["permutation_resolution"]
                output_row = dict(base, **result)
                output_row["interpretation"] = base["interpretation"]
                aw.writerow(output_row)
                counters["tests_rows"] += 1
                name = {"INFERENTIAL": "inferential_rows", "FAILED_ELIGIBLE": "failed_eligible_rows",
                        "DESCRIPTIVE_ONLY": "descriptive_rows"}.get(result["status"], "skipped_rows")
                counters[name] += 1
    af.close(); df.close(); uf.close()
    receipts = verify_sources_unchanged(prepared["source_hash_receipts"])
    files = []
    for receipt in receipts:
        for path_key, before_key, after_key in [("path", "source_sha256_before", "source_sha256_after"),
                                                ("snapshot", "snapshot_sha256", "snapshot_sha256_after")]:
            files.append(dict(path=receipt[path_key], expected_sha256=receipt["sha256"],
                              before_sha256=receipt[before_key], after_sha256=receipt[after_key]))
    save(out / "SOURCE_VERIFICATION.json", dict(passed=True, source_files_changed=False, execution=execution, files=files))
    verify_manifest(run)
    outputs = sorted(p for p in out.rglob("*") if p.is_file() and "source_snapshot" not in p.parts
                     and p.name not in ("WORKER_STATUS.json", "OUTPUT_SHA256SUMS.txt"))
    (out / "OUTPUT_SHA256SUMS.txt").write_text("".join(sha(p) + "  " + str(p.relative_to(out)) + "\n" for p in outputs))
    save(out / "WORKER_STATUS.json", dict(completed=True, source_files_changed=False, package_integrity_passed=True,
         scientific_review_pending=True, visual_review_pending=True, execution=execution, shard={k: shard[k] for k in ("id", "farm", "domain", "feature_type")}, chunk_id=shard["output_id"], feature_class=shard["feature_class"],
         completed_utc=utc(), numerical_self_checks_passed=True, cohort_count=len(cohorts),
         source_file_count=len(files), output_manifest_sha256=sha(out / "OUTPUT_SHA256SUMS.txt"),
         predictive_validation_established=False, ten_year_forecast_generated=False, **counters))
    print("CHUNK_COMPLETED=" + shard["output_id"] + " " + packed(counters), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--shard-id", required=True)
    parser.add_argument("--output-id", required=True)
    parser.add_argument("--feature-class", choices=["EC", "KO", "METACYC"], required=True)
    args = parser.parse_args()
    run = Path(args.run_root).resolve()
    execution = require_hpc()
    shard = next((s for s in load_shards(run) if s["id"] == args.shard_id), None)
    require(shard is not None, "Unknown worker shard")
    receipt = json_file(run / "submission.json")
    specs = json_file(run / "CONFIG.json")["recovery"]["retry_chunks"]
    spec = next((v for v in specs if v["id"] == args.output_id), None)
    require(spec is not None and spec["shard_id"] == args.shard_id and spec["feature_class"] == args.feature_class, "Unexpected retry chunk declaration")
    shard = dict(shard, output_id=args.output_id, feature_class=args.feature_class)
    require(execution["job_id"] == receipt["jobs"].get("chunk:" + shard["output_id"]),
            "Live chunk allocation differs from the submitted retry job")
    out = run / "workers" / "chunks" / shard["output_id"]
    require(out.is_dir(), "Worker output directory not staged")
    try:
        run_worker(run, shard, execution)
    except Exception as error:
        save(out / "WORKER_STATUS.json", dict(completed=False, execution=execution, shard={k: shard[k] for k in ("id", "farm", "domain", "feature_type")}, chunk_id=shard["output_id"], feature_class=shard["feature_class"], failed_utc=utc(),
             source_files_changed=None, package_integrity_passed=None, scientific_review_pending=True,
             visual_review_pending=True, exception_type=type(error).__name__, error=str(error),
             ten_year_forecast_generated=False, predictive_validation_established=False))
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
