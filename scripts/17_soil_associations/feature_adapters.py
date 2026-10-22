"""Frozen NRCS feature and exact endpoint interfaces, for Puma execution only.

No numerical packages are imported until the live allocation, immutable source
hashes and expected successful source receipts have passed. Importing this file
does not read data or execute scientific work. Selected observations are the
sole measured-value eligibility source; workbook measurements supply identity,
method and censoring provenance only. Previous engines are never executed.
"""
from __future__ import annotations

import csv
import datetime
import gzip
import hashlib
import json
import math
import re
import shutil
from pathlib import Path


ADAPTER_VERSION = "NRCS_EXACT_FEATURE_ADAPTER_20261009_V1"
RANKS = ("Kingdom", "Phylum", "Class", "Order", "Family", "Genus")
ZERO_RULES = (("PRIMARY", 1e-6), ("ZERO_LOW", 1e-7),
              ("ZERO_HIGH", 1e-5))
ENDPOINT_COLUMNS = (
    "farm", "domain", "analysis_sample", "sample_key", "biological_plot_id",
    "analytic_system", "plant_type", "cultivar", "collection_date", "event",
    "dataset", "variable", "unit", "method", "method_verified",
    "assay_source_group", "value", "record_id", "measurement_record_ids",
    "record_key", "source_id", "measured_system", "treatment",
    "target_read_count", "log10_target_reads", "primary_included",
    "depth_sensitivity_included", "field_location_id", "source_location",
    "endpoint_origin", "mathematically_coupled", "coupling_caution",
)


def _require(condition, message):
    if not condition:
        raise RuntimeError("NRCS adapter: " + str(message))


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text(value):
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result.lower() in ("nan", "none", "<na>", "null") else result


def _truth(value):
    if isinstance(value, bool):
        return value
    result = _text(value).lower()
    if result in ("true", "1", "yes"):
        return True
    if result in ("false", "0", "no", ""):
        return False
    raise RuntimeError("Unrecognized source inclusion boolean: " + repr(value))


def _tokens(value):
    return tuple(sorted(set(part.strip() for part in _text(value).split(";")
                            if part.strip())))


def _has_exclusion(value):
    """Source uses blank/reason text; tolerate an explicit false boolean."""
    return _text(value).lower() not in ("", "false", "0", "no")


def _date(value):
    value = _text(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _header(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="") as handle:
        row = next(csv.reader(handle, delimiter="\t"), None)
    _require(row and len(row) > 1, "Empty feature table: " + str(path))
    _require(len(row) == len(set(row)), "Duplicate original feature header")
    _require(all(_text(value) for value in row), "Empty feature header label")
    return row


def _stage_sources(run_root, shard, source_specs):
    domain = shard["domain"]
    kind = shard.get("feature_type", shard.get("kind", shard.get("type")))
    needed = {"corrected_source_status", "corrected_workbook",
              "selected_observations", "record_disposition"}
    if kind == "taxa":
        needed.update({"corrected_counts_" + domain, "taxonomy_" + domain})
    else:
        needed.add("functional_source_status")
        needed.add("functional_metadata_" + domain)
        needed.add("functional_qc_" + domain)
        needed.update("functional_" + domain + "_" + family
                      for family in ("EC", "KO", "METACYC"))
        if domain == "archaea":
            needed.update({"sensitivity_source_status", "archaeal_sensitivity_qc",
                           "sensitivity_pipeline_status"})
            needed.update("archaeal_filtered_" + family
                          for family in ("EC", "KO", "METACYC"))
    snapshot = Path(run_root) / "workers" / "chunks" / shard["output_id"] / "source_snapshot"
    snapshot.mkdir(parents=True, exist_ok=False)
    paths, receipts = {}, []
    for role in sorted(needed):
        _require(role in source_specs, "Missing frozen source role " + role)
        spec = source_specs[role]
        source = Path(spec["path"])
        expected = spec["sha256"]
        _require(source.is_absolute(), "Source path must be absolute")
        _require(re.fullmatch(r"[0-9a-f]{64}", expected), "Invalid source digest")
        before = _sha(source)
        _require(before == expected, "Source hash changed: " + role)
        if spec.get("bytes") is not None:
            _require(source.stat().st_size == int(spec["bytes"]),
                     "Source byte length changed: " + role)
        destination = snapshot / (role + "".join(source.suffixes))
        shutil.copyfile(source, destination)
        _require(_sha(destination) == expected, "Snapshot hash changed: " + role)
        destination.chmod(0o444)
        paths[role] = destination
        receipts.append({"role": role, "path": str(source),
                         "snapshot": str(destination), "sha256": expected,
                         "source_sha256_before": before,
                         "snapshot_sha256": expected,
                         "bytes": destination.stat().st_size})
    for role, expected_job in (("corrected_source_status", "24067160"),
                               ("functional_source_status", "24067682"),
                               ("sensitivity_source_status", "24073133")):
        if role not in paths:
            continue
        state = json.loads(paths[role].read_text())
        _require(str(state.get("analysis_job_id")) == expected_job,
                 "Unexpected source job for " + role)
        _require(state.get("scheduler_state") == "COMPLETED"
                 and state.get("pipeline_exit_code") == 0
                 and state.get("scheduler_exit_code") == "0:0"
                 and state.get("scientific_scripts_completed") is True,
                 "Expected successful Slurm source receipt missing: " + role)
    if "sensitivity_pipeline_status" in paths:
        state = json.loads(paths["sensitivity_pipeline_status"].read_text())
        _require(state.get("baseline_reproduced") is True
                 and state.get("original_scientific_data_changed") is False,
                 "Original archaeal reproduction or integrity receipt failed")
    return paths, receipts


def verify_sources_unchanged(receipts):
    """Byte-integrity end check; never edits or recalculates source science."""
    for receipt in receipts:
        after = _sha(receipt["path"])
        snapshot_after = _sha(receipt["snapshot"])
        _require(after == receipt["sha256"] == snapshot_after,
                 "Source or private snapshot changed during worker execution")
        receipt["source_sha256_after"] = after
        receipt["snapshot_sha256_after"] = snapshot_after
    return receipts


def _wb_frame(workbook, name, pd):
    table = workbook["tables"][name]
    _require(len(table["columns"]) == len(set(table["columns"])),
             "Duplicate workbook columns: " + name)
    return pd.DataFrame(table["rows"], columns=table["columns"])


def _matrix(path, first_label, pd, np, integer=False):
    columns = _header(path)
    _require(columns[0] == first_label, "Feature orientation/header changed")
    frame = pd.read_csv(path, sep="\t", index_col=0, keep_default_na=False)
    frame.index = frame.index.map(str)
    frame.columns = frame.columns.map(str)
    _require(not frame.index.has_duplicates and not frame.columns.has_duplicates,
             "Duplicate feature/sample identities")
    _require(all(_text(value) for value in frame.index), "Blank feature/sample ID")
    values = frame.to_numpy(dtype=float)
    _require(np.isfinite(values).all() and (values >= 0).all(),
             "Nonfinite or negative source abundances")
    if integer:
        _require(np.equal(values, np.floor(values)).all(), "Noninteger ASV counts")
    return frame.astype(float)


def _metadata(workbook, farm, domain, pd, np, audit):
    frame = _wb_frame(workbook, "16S samples", pd)
    frame = frame.loc[frame.microbial_domain.map(_text) == domain].copy()
    required = ("analysis_sample", "farm", "sample_key", "collection_date",
                "biological_plot_id", "analytic_system", "primary_included",
                "depth_sensitivity_included", "target_read_count")
    _require(all(key in frame.columns for key in required), "Metadata schema changed")
    for key in ("analysis_sample", "farm", "sample_key", "collection_date",
                "biological_plot_id", "analytic_system", "plant_type", "cultivar",
                "treatment", "field_location_id"):
        frame[key] = frame[key].map(_text)
    _require(not frame.analysis_sample.duplicated().any(),
             "Duplicate corrected domain analysis_sample")
    frame["primary_included"] = frame.primary_included.map(_truth)
    frame["depth_sensitivity_included"] = frame.depth_sensitivity_included.map(_truth)
    frame["target_read_count"] = pd.to_numeric(frame.target_read_count,
                                                errors="raise")
    _require(np.isfinite(frame.target_read_count).all()
             and (frame.target_read_count >= 0).all(), "Invalid recorded read counts")
    farm_rows = frame.loc[frame.farm == farm].copy()
    admitted = []
    for _, row in farm_rows.iterrows():
        reason = None
        if _truth(row.get("descriptive_reference")):
            reason = "DESCRIPTIVE_REFERENCE_NOT_REINSTATED"
        elif not (row.primary_included or row.depth_sensitivity_included):
            reason = "EXISTING_SOURCE_FLAGS_EXCLUDE_BOTH_SCOPES"
        elif row.target_read_count <= 0:
            reason = "ZERO_TARGET_READS"
        elif any(not _text(row[key]) for key in
                 ("analysis_sample", "sample_key", "biological_plot_id", "analytic_system")):
            reason = "INCOMPLETE_CONFIRMED_MICROBIAL_IDENTITY"
        elif _date(row.collection_date) is None:
            reason = "NONEXACT_MICROBIAL_COLLECTION_DATE"
        if reason:
            audit.append({"stage": "metadata", "analysis_sample": row.analysis_sample,
                          "sample_key": row.sample_key, "status": "SKIPPED",
                          "reason": reason})
        else:
            admitted.append(row.analysis_sample)
    selected = farm_rows.loc[farm_rows.analysis_sample.isin(admitted)].copy()
    keys = ["farm", "analytic_system", "biological_plot_id", "collection_date"]
    _require(not selected.duplicated(keys).any(),
             "Unresolved duplicated biological plot/event; no new merges")
    _require(not selected.duplicated(["farm", "sample_key", "collection_date"]).any(),
             "Ambiguous metadata endpoint join identity")
    selected["log10_target_reads"] = np.log10(selected.target_read_count.to_numpy(float))
    return selected.set_index("analysis_sample", drop=False), frame


def _taxon_value(value, rank):
    value = _text(value)
    prefix = {"Kingdom": "d__", "Phylum": "p__", "Class": "c__",
              "Order": "o__", "Family": "f__", "Genus": "g__"}[rank]
    if value.lower().startswith(prefix):
        value = value[len(prefix):]
    if value.lower() in ("", "na", "n/a", "unclassified", "unassigned",
                          "uncultured", "unknown", "ambiguous_taxa", "metagenome"):
        return "[unclassified]"
    if re.match(r"^(?:uncultured|uncultivated|unclassified|unidentified|unassigned)(?:[_\s]|$)",
                value, flags=re.I):
        return "[unclassified]"
    return value


def _taxa(paths, domain, metadata, full_metadata, pd, np, audit):
    counts = _matrix(paths["corrected_counts_" + domain], "sample_id", pd, np,
                     integer=True)
    # Verify every corrected sample, including excluded rows, but never admit
    # a historical count row simply because it exists in the count matrix.
    for _, row in full_metadata.iterrows():
        sid = _text(row.analysis_sample)
        expected = float(row.target_read_count)
        _require(sid in counts.index or expected == 0,
                 "Missing positive-read corrected sample " + sid)
        if sid in counts.index:
            _require(float(counts.loc[sid].sum()) == expected,
                     "Corrected target-read row total differs: " + sid)
    for sid in sorted(set(counts.index) - set(full_metadata.analysis_sample)):
        audit.append({"stage": "taxa", "analysis_sample": sid,
                      "status": "EXCLUDED", "reason": "NOT_IN_CORRECTED_METADATA"})
    targets = set(counts.columns)
    taxonomy = {}
    with paths["taxonomy_" + domain].open(newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        _require(reader.fieldnames is not None
                 and set(("asv_id",) + RANKS).issubset(reader.fieldnames),
                 "Frozen taxonomy schema changed")
        for row in reader:
            sid = row["asv_id"]
            if sid not in targets:
                continue
            _require(sid not in taxonomy, "Duplicated target ASV taxonomy row")
            kingdom = _taxon_value(row["Kingdom"], "Kingdom").lower()
            _require(kingdom == domain, "Frozen target ASV Kingdom mismatch")
            taxonomy[sid] = row
    _require(set(taxonomy) == targets, "Frozen taxonomy lacks exact target ASVs")
    frame = counts.reindex(index=metadata.index)
    results = []
    for rank in ("Family", "Genus"):
        ranks = RANKS[:RANKS.index(rank) + 1]
        keys, feature_meta = [], {}
        for asv in frame.columns:
            lineage = {name: _taxon_value(taxonomy[asv][name], name) for name in ranks}
            key = json.dumps([(name, lineage[name]) for name in ranks],
                             ensure_ascii=False, separators=(",", ":"))
            keys.append(key)
            if key not in feature_meta:
                named = lineage[rank] != "[unclassified]"
                feature_meta[key] = {"class": rank, "display_label": lineage[rank],
                                     "lineage": lineage, "named_taxon": named,
                                     "testable": named, "asv_n": 0,
                                     "interpretation": "RELATIVE_COMPOSITION"}
            feature_meta[key]["asv_n"] += 1
        aggregate = frame.T.groupby(pd.Index(keys), sort=True).sum().T
        _require(np.array_equal(aggregate.sum(axis=1).to_numpy(),
                                frame.sum(axis=1).to_numpy()),
                 "Taxonomic aggregation did not conserve all target counts")
        results.append({"class": rank, "source_variant": "CORRECTED_TARGET_COUNTS",
                        "variant": "PRIMARY", "raw": aggregate,
                        "feature_metadata": feature_meta,
                        "quality": pd.DataFrame(index=metadata.index)})
    return results


def _functions(paths, domain, metadata, pd, np, audit):
    frozen_meta = pd.read_csv(paths["functional_metadata_" + domain],
                              keep_default_na=False).set_index("analysis_sample", drop=False)
    _require(not frozen_meta.index.has_duplicates, "Duplicate functional metadata")
    qc = pd.read_csv(paths["functional_qc_" + domain], keep_default_na=False)
    _require(not qc.analysis_sample.duplicated().any(), "Duplicate original prediction QC")
    qc = qc.set_index("analysis_sample", drop=False)
    _require(set(qc.index) == set(frozen_meta.index), "Functional QC sample identities differ")
    absent = sorted(set(metadata.index) - set(frozen_meta.index))
    for sid in absent:
        audit.append({"stage": "functions", "analysis_sample": sid,
                      "status": "SKIPPED", "reason": "NO_ORIGINAL_PREDICTION_SAMPLE"})
    selected = metadata.loc[metadata.index.isin(frozen_meta.index)].copy()
    for sid, row in selected.iterrows():
        old = frozen_meta.loc[sid]
        for key in ("farm", "analytic_system", "biological_plot_id",
                    "sample_key", "collection_date", "plant_type", "cultivar"):
            _require(_text(row[key]) == _text(old[key]),
                     "Functional/corrected metadata identity mismatch: " + key + "/" + sid)
        _require(_truth(row.primary_included) == _truth(old.primary_included)
                 and _truth(row.depth_sensitivity_included) ==
                 _truth(old.depth_sensitivity_included), "Functional source flags differ")
        _require(float(row.target_read_count) == float(qc.loc[sid, "input_target_reads"]),
                 "Functional QC target reads differ from corrected metadata")
    sensitivity = None
    if domain == "archaea":
        sensitivity = pd.read_csv(paths["archaeal_sensitivity_qc"], keep_default_na=False)
        _require(not sensitivity.duplicated(["analysis_sample", "family"]).any(),
                 "Duplicated filtered-function source quality row")
    results = []
    for family in ("EC", "KO", "METACYC"):
        original = _matrix(paths["functional_" + domain + "_" + family],
                           "function", pd, np).T
        _require(set(original.index) == set(frozen_meta.index),
                 "Functional profile sample IDs differ from frozen metadata")
        variants = [("PRIMARY", "BACTERIA_ORIGINAL_PREDICTION", original)]
        if domain == "archaea":
            filtered = _matrix(paths["archaeal_filtered_" + family],
                               "function", pd, np).T
            _require(set(filtered.index) == set(original.index),
                     "Filtered/original archaeal sample sets differ")
            variants = [("PRIMARY", "ARCHAEA_DOMAIN_CONCORDANT_PREDICTION", filtered),
                        ("ORIGINAL_SENSITIVITY", "ARCHAEA_ORIGINAL_PREDICTION", original)]
        for variant, source_variant, frame in variants:
            quality = qc.reindex(selected.index).copy()
            quality = quality.add_prefix("original_prediction_")
            quality["source_variant"] = source_variant
            quality["raw_profile_total"] = frame.reindex(selected.index).sum(axis=1)
            quality["prediction_interpretation"] = "PREDICTED_GENOMIC_POTENTIAL_ONLY"
            if domain == "archaea":
                sq = sensitivity.loc[sensitivity.family == family].set_index("analysis_sample", drop=False)
                _require(set(sq.index) == set(frozen_meta.index),
                         "Filtered quality source sample IDs differ")
                quality = quality.join(sq.reindex(selected.index).add_prefix("domain_concordant_source_"))
                quality["original_NSTI_is_filtered_NSTI"] = False
                for sid, row in selected.iterrows():
                    for key in ("farm", "analytic_system", "biological_plot_id", "sample_key", "collection_date"):
                        _require(_text(row[key]) == _text(sq.loc[sid, key]),
                                 "Filtered source quality identity differs")
                    _require(float(sq.loc[sid, "input_reads"]) == float(row.target_read_count),
                             "Filtered quality target-read count differs")
            feature_meta = {}
            for feature in frame.columns:
                special = feature.strip().upper() in ("UNMAPPED", "UNINTEGRATED")
                feature_meta[feature] = {"class": family, "display_label": feature,
                                         "testable": not special,
                                         "interpretation": "PREDICTED_GENOMIC_POTENTIAL_ONLY",
                                         "special_nonbiological_summary_row": special}
                if special:
                    audit.append({"stage": "function_annotation", "class": family,
                                  "feature_id": feature, "variant": variant,
                                  "status": "DENOMINATOR_ONLY",
                                  "reason": "SPECIAL_SUMMARY_ROW_IS_NOT_NAMED_FUNCTION_HYPOTHESIS"})
            results.append({"class": family, "variant": variant,
                            "source_variant": source_variant,
                            "raw": frame.reindex(selected.index),
                            "feature_metadata": feature_meta, "quality": quality})
    return selected, results


def _clr(frame, delta, pd, np):
    values = frame.to_numpy(dtype=float)
    _require(np.isfinite(values).all() and (values >= 0).all(), "Invalid CLR input")
    totals = values.sum(axis=1)
    _require((totals > 0).all(), "Zero-total profile must be skipped, never imputed")
    composition = values / totals[:, None]
    zero_n = (composition == 0).sum(axis=1)
    applied_delta = np.zeros(len(frame), dtype=float)
    replaced_mass = np.zeros(len(frame), dtype=float)
    for index, row in enumerate(composition):
        zeros = row == 0
        if not zeros.any():
            continue
        use_delta = min(float(delta), 0.5 * float(row[~zeros].min()),
                        0.5 / int(zeros.sum()))
        _require(math.isfinite(use_delta) and use_delta > 0,
                 "Invalid multiplicative replacement delta")
        mass = use_delta * int(zeros.sum())
        row[~zeros] *= 1 - mass
        row[zeros] = use_delta
        applied_delta[index] = use_delta
        replaced_mass[index] = mass
    _require((composition > 0).all() and np.isfinite(composition).all()
             and np.allclose(composition.sum(axis=1), 1, rtol=0, atol=1e-12),
             "Zero replacement failed simplex validation")
    logged = np.log(composition)
    clr = logged - logged.mean(axis=1, keepdims=True)
    _require(np.isfinite(clr).all(), "Nonfinite CLR after valid replacement")
    quality = pd.DataFrame({"profile_total": totals, "zero_n": zero_n,
                            "reference_feature_n": frame.shape[1],
                            "zero_fraction": zero_n / frame.shape[1],
                            "requested_delta": delta, "applied_delta": applied_delta,
                            "replaced_mass": replaced_mass}, index=frame.index)
    return pd.DataFrame(clr, index=frame.index, columns=frame.columns), quality


def _feature_sets(base_sets, metadata, pd, np, audit):
    references = {}
    for item in base_sets:
        if item["variant"] != "PRIMARY":
            continue
        primary = item["raw"].loc[metadata.primary_included]
        references[item["class"]] = sorted(primary.columns[(primary > 0).any(axis=0)])
    sets = []
    for item in base_sets:
        reference = references[item["class"]]
        if not reference:
            audit.append({"stage": "feature_universe", "class": item["class"],
                          "variant": item["variant"], "status": "SKIPPED",
                          "reason": "NO_FEATURES_OBSERVED_IN_PRIMARY_FARM_SAMPLES"})
            continue
        for feature in sorted(set(item["raw"].columns) - set(reference)):
            audit.append({"stage": "feature_universe", "class": item["class"],
                          "variant": item["variant"], "feature_id": feature,
                          "status": "EXCLUDED_FROM_FIXED_REFERENCE",
                          "reason": ("ALL_ZERO_IN_PRIMARY_FARM_SAMPLES"
                                     if item["variant"] == "PRIMARY" else
                                     "OUTSIDE_FIXED_PRIMARY_DOMAIN_CONCORDANT_REFERENCE")})
        universe_digest = hashlib.sha256(json.dumps(reference, ensure_ascii=False,
                                                    separators=(",", ":")).encode()).hexdigest()
        raw = item["raw"].reindex(columns=reference, fill_value=0)
        quality = item["quality"].copy()
        quality["full_source_profile_total"] = item["raw"].sum(axis=1)
        quality["fixed_reference_profile_total"] = raw.sum(axis=1)
        quality["source_mass_outside_fixed_reference"] = (
            quality.full_source_profile_total - quality.fixed_reference_profile_total)
        feature_meta = {feature: item["feature_metadata"].get(feature, {
            "class": item["class"], "display_label": feature, "testable": True,
            "interpretation": "PREDICTED_GENOMIC_POTENTIAL_ONLY"}) for feature in reference}
        for scope, flag in (("PRIMARY", "primary_included"),
                            ("DEPTH_SENSITIVITY", "depth_sensitivity_included")):
            ids = metadata.index[metadata[flag]]
            chosen = raw.reindex(index=ids)
            total = chosen.sum(axis=1)
            nonzero = total > 0
            for sid in chosen.index[~nonzero]:
                audit.append({"stage": "profile", "class": item["class"],
                              "variant": item["variant"], "source_scope": scope,
                              "analysis_sample": sid, "status": "SKIPPED",
                              "reason": "ZERO_TOTAL_FIXED_REFERENCE_PROFILE"})
            chosen = chosen.loc[nonzero]
            for zero_rule, delta in ZERO_RULES:
                if len(chosen):
                    clr, transform_audit = _clr(chosen, delta, pd, np)
                else:
                    clr = chosen.copy()
                    transform_audit = pd.DataFrame(index=chosen.index)
                sets.append({"class": item["class"], "variant": item["variant"],
                             "source_variant": item["source_variant"],
                             "source_scope": scope, "zero_rule": zero_rule,
                             "scenario": scope + "__" + zero_rule + "__" + item["variant"],
                             "delta": delta, "raw": chosen, "clr": clr,
                             "positive": chosen > 0,
                             "metadata": metadata.reindex(chosen.index),
                             "reference": reference, "reference_sha256": universe_digest,
                             "feature_metadata": feature_meta,
                             "quality": quality.reindex(chosen.index),
                             "transform_audit": transform_audit,
                             "feature_selection": "PRIMARY_COHORT_SCREEN_REUSED_BY_WORKER"})
    return sets


def _censored(measurement):
    for field in ("qualifier", "text_value", "source_value"):
        token = _text(measurement.get(field)).lower()
        if "<" in token or "≤" in token or re.search(r"\b(below|nondetect|non-detect|undetected|bdl|nd)\b", token):
            return True
        if "not detected" in token:
            return True
    return False


def _endpoint_provenance(observation, workbook, measurements, records, dispositions, pd):
    """Resolve only frozen source identities; never recalculate selected means."""
    variable, unit = _text(observation["variable"]), _text(observation["unit"])
    ids = _tokens(observation["record_id"])
    if not ids:
        return None, "EMPTY_MEASUREMENT_RECORD_ID"
    contributors = []
    if observation["dataset"] == "Haney soil":
        means = _wb_frame(workbook, "Oatman soil means", pd)
        matches = means.loc[(means.sample_key.map(_text) == _text(observation["sample_key"]))
                            & (means.collection_date.map(_text) == _text(observation["event"]))
                            & (means.variable.map(_text) == variable)
                            & (means.unit.map(_text) == unit)]
        if len(matches) != 1:
            return None, "AMBIGUOUS_EXISTING_HANEY_MEAN_PROVENANCE"
        mean = matches.iloc[0]
        if _tokens(mean.lab_ids) != ids:
            return None, "HANEY_SOURCE_LAB_IDS_DIFFER"
        # Exact source Excel rows already used by corrected source validation.
        try:
            indices = [int(value.strip()) - 2 for value in _text(mean.measurement_excel_rows).split(",")]
        except ValueError:
            return None, "INVALID_HANEY_FROZEN_MEASUREMENT_ROW_CROSSWALK"
        if not indices or len(indices) != len(set(indices)) or any(index < 0 or index >= len(measurements) for index in indices):
            return None, "AMBIGUOUS_HANEY_FROZEN_MEASUREMENT_ROWS"
        contributors = [measurements.iloc[index].to_dict() for index in indices]
        if float(mean.mean_value) != float(observation["value"]):
            return None, "SELECTED_HANEY_MEAN_DOES_NOT_MATCH_FROZEN_VALUE"
        lab_ids = []
        for measurement in contributors:
            rid = _text(measurement["record_id"])
            if rid not in records.index:
                return None, "UNKNOWN_HANEY_CONTRIBUTOR_RECORD"
            source = records.loc[rid]
            lab_ids.append(_text(source.original_lab_id))
            if (_text(source.farm) != "Oatman Flats"
                    or _text(source.dataset) != "Oatman Soil lab records"):
                return None, "HANEY_CONTRIBUTOR_FARM_OR_DATASET_DIFFERS"
        if tuple(sorted(lab_ids)) != ids:
            return None, "HANEY_CONTRIBUTOR_ORIGINAL_LAB_IDS_DIFFER"
    else:
        if len(ids) != 1 or ids[0] not in records.index:
            return None, "MULTIRECORD_MEASUREMENT_HAS_NO_APPROVED_AGGREGATION"
        subset = measurements.loc[(measurements.record_id.map(_text) == ids[0])
                                  & (measurements.variable.map(_text) == variable)
                                  & (measurements.unit.map(_text) == unit)]
        if len(subset) != 1:
            return None, "AMBIGUOUS_OR_MISSING_SELECTED_MEASUREMENT_PROVENANCE"
        contributors = [subset.iloc[0].to_dict()]
        value = contributors[0].get("value")
        try:
            if not math.isfinite(float(value)) or float(value) != float(observation["value"]):
                return None, "SELECTED_VALUE_DIFFERS_FROM_CORRECTED_SOURCE"
        except (TypeError, ValueError):
            return None, "NONNUMERIC_SELECTED_SOURCE_VALUE"
    methods, source_ids, locations, contributor_ids = [], [], [], []
    for measurement in contributors:
        rid = _text(measurement["record_id"])
        if rid not in records.index:
            return None, "UNKNOWN_CONTRIBUTOR_RECORD"
        source = records.loc[rid]
        if (_text(source.farm) != _text(observation["farm"])
                or _text(source.confirmed_sample_key) != _text(observation["sample_key"])
                or _date(source.collection_date) != _date(observation["event"])):
            return None, "SOURCE_RECORD_EXACT_COLLECTION_IDENTITY_DIFFERS"
        if _has_exclusion(source.get("intentional_exclusion")):
            return None, "INTENTIONAL_SOURCE_EXCLUSION"
        declared = dispositions.loc[dispositions.record_id.map(_text) == rid]
        if len(declared) and any(_text(state).startswith("retained_outside")
                                 for state in declared.status):
            return None, "SOURCE_RECORD_RETAINED_OUTSIDE_INFERENTIAL_TESTS"
        if (_text(measurement.get("variable")) != variable
                or _text(measurement.get("unit")) != unit):
            return None, "CONTRIBUTOR_VARIABLE_OR_UNIT_DIFFERS"
        if _censored(measurement):
            return None, "CENSORED_SOURCE_CONTRIBUTOR"
        method = _text(measurement.get("method"))
        preferred = _text(measurement.get("preferred_pdf_source_id"))
        source_id = preferred or _text(measurement.get("source_id"))
        if not source_id:
            return None, "EMPTY_CONTRIBUTOR_SOURCE_PROVENANCE"
        methods.append(method)
        source_ids.append(source_id)
        locations.append(_text(measurement.get("source_location")))
        contributor_ids.append(rid)
    if len(contributor_ids) != len(set(contributor_ids)):
        return None, "DUPLICATE_CONTRIBUTOR_RECORD_IN_SELECTED_VALUE"
    if len(set(methods)) != 1:
        return None, "CONFLICTING_OR_AMBIGUOUS_CONTRIBUTOR_ASSAY_METHODS"
    known = bool(methods[0])
    # A missing method is allowed only as a source-isolated stratum. Different
    # laboratory source IDs are never harmonized under NOT_REPORTED.
    if not known and len(set(source_ids)) != 1:
        return None, "UNREPORTED_METHOD_WITH_MULTIPLE_CONTRIBUTOR_SOURCES"
    assay_group = ";".join(sorted(set(source_ids)))
    return {"method": methods[0] if known else "NOT_REPORTED",
            "method_verified": known, "assay_source_group": assay_group,
            "measurement_record_ids": ";".join(sorted(contributor_ids)),
            "record_key": json.dumps(sorted(contributor_ids), separators=(",", ":")),
            "source_location": ";".join(sorted(set(locations)))}, None


def _endpoints(paths, workbook, metadata, farm, domain, pd, audit):
    observations = pd.read_csv(paths["selected_observations"], keep_default_na=False,
                               float_precision="round_trip")
    observations = observations.loc[observations.farm.map(_text) == farm].copy()
    measurements = _wb_frame(workbook, "Measurements", pd)
    records = _wb_frame(workbook, "Sample records", pd).set_index("record_id", drop=False)
    _require(not records.index.has_duplicates, "Duplicate laboratory source record ID")
    dispositions = pd.read_csv(paths["record_disposition"], keep_default_na=False)
    identity = {(row.farm, row.sample_key, row.collection_date): row
                for _, row in metadata.iterrows()}
    matched = []
    for _, source_row in observations.iterrows():
        observation = source_row.to_dict()
        base = {"stage": "endpoint", "farm": farm, "domain": domain,
                "sample_key": _text(observation["sample_key"]),
                "record_id": _text(observation["record_id"]),
                "dataset": _text(observation["dataset"]),
                "variable": _text(observation["variable"]),
                "event": _text(observation["event"])}
        reason = None
        key = (farm, base["sample_key"], _date(observation["event"]))
        if not base["sample_key"]:
            reason = "EMPTY_CONFIRMED_SAMPLE_KEY_NO_BIOMASS_OR_COMPOSITE_CLONING"
        elif key[2] is None:
            reason = "NONEXACT_MEASURED_EVENT_DATE"
        elif key not in identity:
            reason = "NO_EXACT_FARM_SAMPLE_KEY_DATE_MICROBIAL_MATCH"
        elif _truth(observation.get("censored")):
            reason = "CENSORED_SELECTED_OBSERVATION"
        elif any(not _text(observation[name]) for name in ("dataset", "variable", "unit")):
            reason = "MISSING_DATASET_VARIABLE_OR_UNIT"
        try:
            value = float(observation["value"])
            if not math.isfinite(value):
                reason = reason or "NONFINITE_MEASURED_VALUE"
        except (ValueError, TypeError):
            reason = reason or "NONNUMERIC_MEASURED_VALUE"
            value = None
        provenance = None
        if reason is None:
            provenance, reason = _endpoint_provenance(observation, workbook,
                                                       measurements, records,
                                                       dispositions, pd)
        if reason:
            audit.append(dict(base, status="SKIPPED", reason=reason))
            continue
        microbial = identity[key]
        row = {name: microbial.get(name, "") for name in ENDPOINT_COLUMNS}
        row.update(farm=farm, domain=domain, event=key[2],
                   dataset=_text(observation["dataset"]), variable=_text(observation["variable"]),
                   unit=_text(observation["unit"]), value=value,
                   source_id=_text(observation["source_id"]),
                   record_id=_text(observation["record_id"]),
                   measured_system=_text(observation["system"]),
                   endpoint_origin="EXACT_MATCHED_MEASURED_PARAMETER",
                   mathematically_coupled=False, coupling_caution="")
        row.update(provenance)
        matched.append(row)
        audit.append(dict(base, analysis_sample=microbial.analysis_sample,
                          biological_plot_id=microbial.biological_plot_id,
                          status="MATCHED", reason="EXACT_CONFIRMED_IDENTITY",
                          method=provenance["method"],
                          method_verified=provenance["method_verified"]))
    # These are already-returned corrected source values, not fresh diversity
    # calculations. They originate from the same underlying target-filtered
    # unrarefied 16S count data as taxonomic/inferred-functional predictors;
    # resulting associations are explicitly exploratory and coupled.
    alpha = (("Shannon", "Shannon_nats",
              "Shannon=-sum(p*ln(p)); target-filtered unrarefied ASV counts"),
             ("observed_ASVs", "ASVs",
              "observed_ASVs=count(ASV_count>0); target-filtered unrarefied ASV counts"))
    for sid, microbial in metadata.iterrows():
        for variable, unit, method in alpha:
            try:
                value = float(microbial[variable])
            except (KeyError, ValueError, TypeError):
                value = math.nan
            if not math.isfinite(value) or value < 0:
                audit.append({"stage": "metadata_endpoint", "analysis_sample": sid,
                              "variable": variable, "status": "SKIPPED",
                              "reason": "MISSING_OR_INVALID_CORRECTED_ALPHA_SOURCE_VALUE"})
                continue
            record_id = "16S|" + domain + "|" + sid
            row = {name: microbial.get(name, "") for name in ENDPOINT_COLUMNS}
            row.update(farm=farm, domain=domain, event=microbial.collection_date,
                       dataset="16S_alpha_diversity", variable=variable, unit=unit,
                       method=method, method_verified=True,
                       assay_source_group="CORRECTED_HPC_JOB_24067160",
                       value=value, record_id=record_id,
                       measurement_record_ids=record_id,
                       record_key=json.dumps([record_id], separators=(",", ":")),
                       source_id="CORRECTED_HPC_JOB_24067160;" + _text(microbial.get("source_id")),
                       measured_system=microbial.analytic_system,
                       source_location="workbook_data.json/tables/16S samples/analysis_sample=" + sid,
                       endpoint_origin="DERIVED_FROM_SAME_MICROBIAL_COUNT_DATA",
                       mathematically_coupled=True,
                       coupling_caution="Exploratory association with alpha diversity derived from the same target-filtered unrarefied 16S counts; mathematical coupling prevents interpretation as independent validation.")
            matched.append(row)
            audit.append({"stage": "metadata_endpoint", "analysis_sample": sid,
                          "variable": variable, "status": "MATCHED",
                          "reason": "EXISTING_CORRECTED_VALUE_EXPLORATORY_MATHEMATICAL_COUPLING"})
    endpoints = pd.DataFrame(matched, columns=ENDPOINT_COLUMNS)
    if not len(endpoints):
        return endpoints
    strata = ["analytic_system", "plant_type", "cultivar", "collection_date",
              "dataset", "variable", "unit", "method", "assay_source_group"]
    # More than one independently selected measurement for a same trait/sample
    # is unresolved. No averaging, collapsing or most-favourable value choice.
    duplicate = endpoints.duplicated(strata + ["analysis_sample"], keep=False)
    shared = endpoints.duplicated(strata + ["record_key"], keep=False)
    # A contributor shared by only part of two different selected means also
    # breaks outcome independence; matching complete contributor sets alone
    # would miss that overlap.
    for _, cohort in endpoints.groupby(strata, dropna=False, sort=False):
        by_record = {}
        for index, row in cohort.iterrows():
            for rid in _tokens(row.measurement_record_ids):
                by_record.setdefault(rid, []).append(index)
        for indices in by_record.values():
            if len(indices) > 1:
                shared.loc[indices] = True
    ambiguous = duplicate | shared
    for _, row in endpoints.loc[ambiguous].iterrows():
        audit.append({"stage": "endpoint_independence", "analysis_sample": row.analysis_sample,
                      "record_id": row.record_id, "variable": row.variable,
                      "status": "SKIPPED", "reason": "DUPLICATED_SAMPLE_TRAIT_OR_SHARED_MEASUREMENT_RECORD"})
    return endpoints.loc[~ambiguous].reset_index(drop=True)


def prepare_shard(run_root, shard, config, source_specs):
    """Prepare immutable feature matrices and exact numeric endpoint cohorts.

    ``shard`` contains id, farm, domain and feature_type (taxa/functions).
    ``source_specs`` maps frozen role names to path/sha256/optional bytes.
    All source and scientific operations occur only inside the Puma guard.
    Metadata/feature matrices use exact analysis_sample IDs as their indices.
    ``config`` is reserved for orchestration; scientific constants are frozen
    in this adapter, never relaxed by a data-dependent runtime argument.
    """
    from hpc_guard import require_hpc
    execution = require_hpc()
    _require(shard["domain"] in ("bacteria", "archaea"), "Invalid domain")
    kind = shard.get("feature_type", shard.get("kind", shard.get("type")))
    _require(kind in ("taxa", "functions"), "Invalid feature type")
    _require(re.fullmatch(r"[A-Za-z0-9_-]+", shard["id"]), "Unsafe shard ID")
    paths, receipts = _stage_sources(run_root, shard, source_specs)
    import numpy as np
    import pandas as pd
    workbook = json.loads(paths["corrected_workbook"].read_text())
    audit = []
    metadata, full_metadata = _metadata(workbook, shard["farm"], shard["domain"], pd, np, audit)
    if kind == "taxa":
        base_sets = _taxa(paths, shard["domain"], metadata, full_metadata, pd, np, audit)
    else:
        metadata, base_sets = _functions(paths, shard["domain"], metadata, pd, np, audit)
    feature_sets = _feature_sets(base_sets, metadata, pd, np, audit)
    endpoints = _endpoints(paths, workbook, metadata, shard["farm"], shard["domain"], pd, audit)
    verify_sources_unchanged(receipts)
    return {"adapter_version": ADAPTER_VERSION, "execution": execution,
            "metadata": metadata, "feature_sets": feature_sets,
            "endpoints": endpoints, "match_audit": pd.DataFrame(audit),
            "source_hash_receipts": receipts}
