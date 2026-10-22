"""Verify ten preserved and two class-assembled outputs, then adjust global families.

No numeric computation or report generation is permitted off Puma. The
submission receipt is not scheduler state; all prerequisite main jobs must
actually be COMPLETED with exit code 0:0 before their output is read.
"""
from __future__ import annotations

import argparse
from array import array
import csv
import gzip
import hashlib
import heapq
import json
import math
import os
import re
import sqlite3
import time
import traceback
from contextlib import ExitStack
from pathlib import Path

from hpc_guard import require_hpc
from orchestration_common import (json_file, load_shards, require, safe_relative,
                                  save, scheduler_main_rows, sha, utc,
                                  verify_manifest)


REQUIRED_WORKER_OUTPUTS = ["associations.csv.gz", "feature_screen.csv.gz",
                           "match_audit.csv.gz", "cohorts.csv",
                           "diagnostics.csv.gz", "FEATURE_REFERENCE.json",
                           "matched_endpoints.csv.gz", "sample_metadata.csv.gz",
                           "scenario_cohorts.csv"]
KEY_FIELDS = ["shard_id", "feature_class", "feature_id", "association_type",
              "cohort_id", "scenario", "source_variant"]
REQUIRED_ASSOCIATION_COLUMNS = ["shard_id", "farm", "domain", "feature_class",
                                "feature_id", "feature_label", "association_type",
                                "cohort_id", "scenario", "source_variant", "n",
                                "status", "p", "correction_eligible"]


def _remember_file(records, path, expected=None):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(),
            "Missing or symlinked immutable source: "+str(path))
    path = path.resolve()
    before = sha(path)
    expected = before if expected is None else expected
    require(re.fullmatch(r"[a-f0-9]{64}", expected) is not None,
            "Invalid expected source digest")
    require(before == expected, "Source checksum mismatch: "+str(path))
    records[str(path)] = {"path": str(path), "expected_sha256": expected,
                          "before_sha256": before, "after_sha256": None}


def _worker_output_manifest(directory, records, expected_manifest_sha):
    manifest = directory / "OUTPUT_SHA256SUMS.txt"
    _remember_file(records, manifest, expected_manifest_sha)
    entries = set()
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        relative = str(safe_relative(relative))
        require(relative not in entries, "Duplicate worker output manifest path")
        entries.add(relative)
        _remember_file(records, directory / relative, digest)
    require(bool(entries), "Empty worker output manifest")
    for name in REQUIRED_WORKER_OUTPUTS:
        require(name in entries, "Worker output is not manifested: "+name)
    return entries


def _verify_workers(run, execution, records):
    # All source stage/chunk/JOIN checks precede scientific imports. The twelve
    # logical outputs retain ten original worker identities and two actual JOIN
    # identities; their submission aliases are not invented old job receipts.
    from recovery_validation import (scheduler_completed, verify_recovery_stages,
                                     verify_worker)
    stages = verify_recovery_stages(run, records)
    receipt = stages["receipt"]
    require(receipt["jobs"].get("combine") == execution["job_id"],
            "Live combine allocation differs from its actual submitted job")
    shards = stages["shards"]
    jobs = [receipt["jobs"].get("worker:" + shard["id"], "") for shard in shards]
    scheduling, raw = scheduler_completed(jobs,
        run / "audit/combine/prerequisite_sacct.txt")
    scheduling.update(stages["scheduling"])
    statuses = [verify_worker(run, shard, records, scheduling, stages=stages)
                for shard in shards]
    save(run / "audit/combine/PREREQUISITE_VERIFICATION.json",
         {"passed": True, "execution": execution, "workers": statuses,
          "logical_worker_count": 12, "preserved_original_workers": 10,
          "assembled_logical_workers": 2, "retry_chunk_count": 6,
          "stage_execution": stages["stage"]["execution"],
          "join_execution": stages["join"]["execution"],
          "immutable_input_files": len(records), "checked_utc": utc()})
    return shards, statuses


def _family(row):
    scenario = str(row["scenario"])
    original_archaea = (str(row["domain"]).lower() == "archaea" and
                         str(row["feature_class"]).upper() in
                         {"EC", "KO", "METACYC"} and
                         "original" in str(row["source_variant"]).lower())
    if original_archaea:
        return "ARCHAEA_ORIGINAL_DIAGNOSTIC__"+scenario
    return "PRIMARY" if scenario == "PRIMARY__PRIMARY__PRIMARY" else scenario


def _eligibility(values):
    converted = []
    for value in values:
        spelling = str(value).strip().lower()
        require(spelling in {"true", "false", "1", "0"},
                "Invalid correction_eligible CSV value: "+repr(value))
        converted.append(spelling in {"true", "1"})
    return converted


def _merge_self_checks(run, execution):
    from association_core import adjust_global_pvalues
    checks = []
    examples = [
        ({"domain": "bacteria", "feature_class": "KO", "source_variant": "bacteria_original",
          "scenario": "PRIMARY__PRIMARY__PRIMARY"}, "PRIMARY"),
        ({"domain": "archaea", "feature_class": "KO", "source_variant": "archaea_domain_concordant",
          "scenario": "PRIMARY__ZERO_LOW__PRIMARY"}, "PRIMARY__ZERO_LOW__PRIMARY"),
        ({"domain": "archaea", "feature_class": "KO", "source_variant": "archaea_original_diagnostic",
          "scenario": "PRIMARY__PRIMARY__PRIMARY"},
         "ARCHAEA_ORIGINAL_DIAGNOSTIC__PRIMARY__PRIMARY__PRIMARY"),
    ]
    for row, expected in examples:
        checks.append({"check": "Scenario family normalization: "+expected,
                       "passed": _family(row) == expected})
    checks.append({"check": "CSV False is false rather than truthy string",
                   "passed": _eligibility(["False", "True", "0", "1"])
                   == [False, True, False, True]})
    adjusted = adjust_global_pvalues([.01, .04, .03, None, None],
                                     [True, True, True, True, False])
    checks.append({"check": "Global failed eligible test contributes one, skip stays missing",
                   "passed": adjusted["family_size"] == 4 and
                   adjusted["failed_eligible_p_one"] == 1 and
                   math.isclose(adjusted["q_bh"][0], .04, abs_tol=1e-12)
                   and adjusted["q_bh"][4] is None})
    passed = all(check["passed"] for check in checks)
    save(run / "audit/combine/MERGE_SELF_CHECKS.json",
         {"passed": passed, "execution": execution, "checks": checks})
    require(passed, "Global merge numerical self-check failed")


def _write_csv(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+".tmp")
    frame.to_csv(temporary, index=False,
                 compression="gzip" if path.name.endswith(".gz") else None)
    os.replace(temporary, path)


def _valid_probability(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 < value <= 1 else None


def _finite_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class _WorstDisplayItem:
    __slots__ = ("key", "row")

    def __init__(self, key, row):
        self.key, self.row = key, row

    def __lt__(self, other):
        return self.key > other.key


def _keep_display(heap, limit, key, row):
    item = _WorstDisplayItem(key, dict(row))
    if len(heap) < limit:
        heapq.heappush(heap, item)
    elif key < heap[0].key:
        heapq.heapreplace(heap, item)


def _stream_rows(run, shard):
    path = run / "workers" / shard["id"] / "associations.csv.gz"
    handle = gzip.open(path, "rt", newline="")
    return handle, csv.DictReader(handle)


def _combine_tables(run, shards, statuses, config):
    """Two-pass full-table export with no all-row string DataFrame.

    Only correction-eligible numeric p-values occupy arrays. Uniqueness uses a
    temporary on-disk SQLite primary-key index with an 8-MB cache. Every source
    row, including descriptive and skipped rows, is written in pass two. Small
    bounded primary display heaps are the only result rows loaded into pandas.
    """
    from association_core import adjust_global_pvalues
    import pandas as pd

    results = run / "results"
    results.mkdir(parents=True, exist_ok=True)
    group_keys = ["farm", "domain", "feature_class", "association_type",
                  "global_family", "status"]
    counters, family_info, p_arrays = {}, {}, {}
    totals = {"total_rows": 0, "primary_BY_discoveries": 0,
              "primary_per_farm": {shard["farm"]: {"rows": 0, "eligible": 0,
                                                       "by_discoveries": 0}
                                   for shard in shards},
              "consolidation": "TWO_PASS_STREAM_NO_ALL_ROW_DATAFRAME"}
    base_fields = None
    database_path = run / "audit/combine" / ("_test_ids_"+str(os.getpid())+".sqlite")
    database = sqlite3.connect(str(database_path))
    try:
        database.execute("PRAGMA cache_size=-8192")
        database.execute("PRAGMA journal_mode=MEMORY")
        database.execute("PRAGMA synchronous=NORMAL")
        database.execute("PRAGMA temp_store=FILE")
        database.execute("CREATE TABLE ids(test_id TEXT PRIMARY KEY) WITHOUT ROWID")
        for shard, receipt in zip(shards, statuses):
            handle, reader = _stream_rows(run, shard)
            rows_count = inferential_count = 0
            with handle:
                fields = reader.fieldnames
                require(fields is not None and set(REQUIRED_ASSOCIATION_COLUMNS+
                        ["test_id"]).issubset(fields),
                        shard["id"]+": missing required association columns")
                if base_fields is None:
                    base_fields = list(fields)
                else:
                    require(fields == base_fields, "Worker association headers differ")
                for row in reader:
                    require(None not in row and all(value is not None for value in row.values()),
                            "Malformed association CSV row")
                    require(row["shard_id"] == shard["id"] and
                            row["farm"] == shard["farm"] and
                            row["domain"] == shard["domain"],
                            "Worker output escaped its declared shard")
                    require(bool(row["test_id"]), "Missing canonical test ID")
                    try:
                        database.execute("INSERT INTO ids(test_id) VALUES (?)", (row["test_id"],))
                    except sqlite3.IntegrityError as error:
                        raise RuntimeError("Duplicate canonical test ID: "+row["test_id"]) from error
                    included = _eligibility([row["correction_eligible"]])[0]
                    p_value = _valid_probability(row["p"])
                    family = _family(row)
                    if family not in p_arrays:
                        p_arrays[family] = array("d")
                        family_info[family] = {"rows": 0, "farms": set(), "domains": set(),
                                               "association_types": set()}
                    info = family_info[family]
                    info["rows"] += 1
                    info["farms"].add(row["farm"])
                    info["domains"].add(row["domain"])
                    info["association_types"].add(row["association_type"])
                    if included:
                        p_arrays[family].append(p_value if p_value is not None else math.nan)
                    key = tuple(row.get(field, "") if field != "global_family" else family
                                for field in group_keys)
                    if key not in counters:
                        counters[key] = dict(rows=0, eligible=0, raw_p_available=0,
                                             by_discoveries=0, bh_supplementary_discoveries=0)
                    counter = counters[key]
                    counter["rows"] += 1
                    counter["eligible"] += int(included)
                    counter["raw_p_available"] += int(p_value is not None)
                    rows_count += 1
                    inferential_count += int(row["status"] == "INFERENTIAL")
                    totals["total_rows"] += 1
                    if family == "PRIMARY":
                        totals["primary_per_farm"][row["farm"]]["rows"] += 1
                        totals["primary_per_farm"][row["farm"]]["eligible"] += int(included)
                    if rows_count % 50_000 == 0:
                        database.commit()
                database.commit()
            require(rows_count == int(receipt["status"]["tests_rows"]),
                    shard["id"]+": association row count differs from receipt")
            require(inferential_count == int(receipt["status"]["inferential_rows"]),
                    shard["id"]+": inferential row count differs from receipt")
        totals["unique_test_ids_verified"] = int(database.execute(
            "SELECT count(*) FROM ids").fetchone()[0])
        require(totals["unique_test_ids_verified"] == totals["total_rows"],
                "Canonical test-ID count differs from all row count")
    finally:
        database.close()
        if database_path.exists():
            database_path.unlink()
    base_fields = base_fields or REQUIRED_ASSOCIATION_COLUMNS+["test_id"]
    extra_fields = ["global_family", "q_by_global", "q_bh_global",
                    "by_discovery", "bh_supplementary_discovery"]
    require(not set(extra_fields).intersection(base_fields),
            "Unadjusted worker output already contains global result fields")
    output_fields = base_fields+extra_fields
    alpha = float(config.get("analysis", {}).get("fdr_alpha", .05))
    require(0 < alpha < 1, "Invalid FDR alpha")
    q_arrays, audits = {}, []
    for family in sorted(p_arrays):
        values = p_arrays[family]
        adjusted = adjust_global_pvalues(values, [True]*len(values))
        q_arrays[family] = {"by": array("d", adjusted["q_by"]),
                            "bh": array("d", adjusted["q_bh"]), "cursor": 0}
        info = family_info[family]
        audits.append({"global_family": family, "rows": info["rows"],
                       "correction_family_size": adjusted["family_size"],
                       "failed_eligible_p_one": adjusted["failed_eligible_p_one"],
                       "farms": ";".join(sorted(info["farms"])),
                       "domains": ";".join(sorted(info["domains"])),
                       "association_types": ";".join(sorted(info["association_types"])),
                       "primary_adjustment": "BY", "supplementary_adjustment": "BH",
                       "alpha": alpha, "scope": "archaea_functions_only_diagnostic" if
                       family.startswith("ARCHAEA_ORIGINAL_DIAGNOSTIC__")
                       else "all_planned_shards_with_available_tests"})
        del adjusted
    # Raw eligible arrays are no longer needed; release before table export.
    p_arrays.clear()
    if family_info:
        del values
    display_heaps, report_heap = {}, []
    output_temporaries = []
    pass_two_rows = 0
    with ExitStack() as stack:
        global_path = results / "associations_global.csv.gz"
        global_temporary = global_path.with_name(global_path.name+".tmp")
        output_temporaries.append((global_temporary, global_path))
        global_handle = stack.enter_context(gzip.open(global_temporary, "wt", newline=""))
        global_writer = csv.DictWriter(global_handle, fieldnames=output_fields)
        global_writer.writeheader()
        farm_writers = {}
        for farm in sorted(totals["primary_per_farm"]):
            safe = re.sub(r"[^A-Za-z0-9]+", "_", farm).strip("_")
            path = results / "per_farm" / (safe+"_associations_global.csv.gz")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name+".tmp")
            output_temporaries.append((temporary, path))
            handle = stack.enter_context(gzip.open(temporary, "wt", newline=""))
            writer = csv.DictWriter(handle, fieldnames=output_fields)
            writer.writeheader()
            farm_writers[farm] = writer
        for shard in shards:
            handle, reader = _stream_rows(run, shard)
            with handle:
                require(reader.fieldnames == base_fields, "Association header changed between passes")
                for row in reader:
                    included = _eligibility([row["correction_eligible"]])[0]
                    p_value = _valid_probability(row["p"])
                    family = _family(row)
                    q_by = q_bh = None
                    if included:
                        family_q = q_arrays[family]
                        cursor = family_q["cursor"]
                        require(cursor < len(family_q["by"]), "Correction cursor exceeded eligible count")
                        if p_value is not None:
                            q_by, q_bh = family_q["by"][cursor], family_q["bh"][cursor]
                        family_q["cursor"] += 1
                    by_discovery = q_by is not None and q_by <= alpha
                    bh_discovery = q_bh is not None and q_bh <= alpha
                    row.update(correction_eligible=included, p=p_value if p_value is not None else "",
                               global_family=family, q_by_global=q_by if q_by is not None else "",
                               q_bh_global=q_bh if q_bh is not None else "",
                               by_discovery=by_discovery, bh_supplementary_discovery=bh_discovery)
                    global_writer.writerow(row)
                    farm_writers[row["farm"]].writerow(row)
                    key = tuple(row.get(field, "") for field in group_keys)
                    counters[key]["by_discoveries"] += int(by_discovery)
                    counters[key]["bh_supplementary_discoveries"] += int(bh_discovery)
                    pass_two_rows += 1
                    if family != "PRIMARY":
                        continue
                    totals["primary_BY_discoveries"] += int(by_discovery)
                    totals["primary_per_farm"][row["farm"]]["by_discoveries"] += int(by_discovery)
                    if p_value is not None:
                        report_key = (q_by if q_by is not None else math.inf,
                                      p_value, row["test_id"])
                        _keep_display(report_heap, 15, report_key, row)
                    effect_field = "rho" if row["association_type"] == "NUMERIC" else "rank_eta_squared"
                    effect = _finite_number(row.get(effect_field))
                    independent_n = _finite_number(row.get("n"))
                    if (effect is None or independent_n is None or independent_n < 3 or
                            row["status"] not in {"INFERENTIAL", "DESCRIPTIVE_ONLY", "FAILED_ELIGIBLE"}):
                        continue
                    display_key = (0 if p_value is not None else 1,
                                   q_by if q_by is not None else math.inf,
                                   p_value if p_value is not None else math.inf,
                                   -abs(effect), row["test_id"])
                    panel = "NUMERIC" if effect_field == "rho" else "CATEGORICAL"
                    heap = display_heaps.setdefault((row["shard_id"], panel), [])
                    _keep_display(heap, 20 if panel == "NUMERIC" else 12, display_key, row)
    require(pass_two_rows == totals["total_rows"], "Streaming row count changed between passes")
    require(all(item["cursor"] == len(item["by"]) for item in q_arrays.values()),
            "Not every correction-eligible source row consumed a global q value")
    for temporary, path in output_temporaries:
        os.replace(temporary, path)
    display_rows = {item.row["test_id"]: item.row for heap in display_heaps.values() for item in heap}
    display_rows.update({item.row["test_id"]: item.row for item in report_heap})
    display = pd.DataFrame(list(display_rows.values()), columns=output_fields)
    for field in ("n", "p", "rho", "rank_eta_squared", "q_by_global", "q_bh_global",
                  "minimum_attainable_p"):
        if field in display:
            display[field] = pd.to_numeric(display[field], errors="coerce")
    # Export already-selected display/report rows for compact byte-only review.
    # This does not rerank rows or change any family, test, p-value or q-value.
    _write_csv(display, results / "association_display_rows.csv")
    report_rows = [item.row for item in sorted(report_heap, key=lambda item: item.key)]
    _write_csv(pd.DataFrame(report_rows, columns=output_fields),
               results / "primary_top_associations.csv")
    family_columns = ["global_family", "rows", "correction_family_size", "failed_eligible_p_one",
                      "farms", "domains", "association_types", "primary_adjustment",
                      "supplementary_adjustment", "alpha", "scope"]
    family_table = pd.DataFrame(audits, columns=family_columns)
    summary_rows = [dict(zip(group_keys, key), **value) for key, value in sorted(counters.items())]
    summary = pd.DataFrame(summary_rows, columns=group_keys+["rows", "eligible", "raw_p_available",
                            "by_discoveries", "bh_supplementary_discoveries"])
    _write_csv(family_table, results / "multiplicity_family_audit.csv")
    _write_csv(summary, results / "association_summary.csv")
    totals["display_rows_retained"] = len(display)
    totals["display_row_limit"] = 12*(20+12)+15
    totals["all_descriptive_and_skipped_rows_exported"] = True
    save(run / "audit/combine/STREAMING_CONSOLIDATION.json", totals)
    return display, family_table, summary, totals


def _render_report(run, combined, families, execution, totals):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                   TableStyle, PageBreak)
    from xml.sax.saxutils import escape
    from pypdf import PdfReader

    output = run / "results/NRCS_association_review.pdf"
    temporary = output.with_name(output.stem+".tmp.pdf")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Compact", parent=styles["BodyText"],
                              fontSize=9, leading=12, spaceAfter=7))
    styles.add(ParagraphStyle(name="Cell", parent=styles["BodyText"],
                              fontSize=8, leading=10))
    story = []
    def para(text, style="Compact"):
        story.append(Paragraph(escape(str(text)), styles[style]))
    def table(data, widths):
        cells = [[Paragraph(escape(str(cell)), styles["Cell"]) for cell in row]
                 for row in data]
        item = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
        item.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e7eef4")),
            ("VALIGN", (0,0), (-1,-1), "TOP"), ("GRID", (0,0), (-1,-1), 0.25, colors.lightgrey),
            ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 5),
            ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
        story.append(item)
        story.append(Spacer(1, 10))
    para("NRCS microbial association review", "Title")
    para("Computed on Puma; scientific and visual review pending", "Heading2")
    para("Combine job "+execution["job_id"]+"; generated "+utc()+". All twelve workers passed prerequisite scheduler, identity, package, and source/output checksum checks.")
    primary = combined[combined["global_family"].eq("PRIMARY")]
    para("The primary family combines numeric, treatment, and crop association tests across all farms, bacterial/archaeal domains, genera/families, and EC/KO/MetaCyc features. BY is primary; BH is supplementary. Sensitivity families are adjusted independently. Otherwise eligible missing/failed p-values enter correction as one while the reported raw p remains missing.")
    data = [["Farm", "Primary rows", "Eligible", "BY discoveries"]]
    for farm in ("Oatman Flats", "Page Springs", "Whipstone"):
        counts = totals["primary_per_farm"][farm]
        data.append([farm, counts["rows"], counts["eligible"], counts["by_discoveries"]])
    table(data, [2.0*inch, 1.25*inch, 1.25*inch, 1.75*inch])
    para("Twelve per-worker effect displays are provided in results/figures as 300-dpi PNG and editable vector SVG. Each shows up to twenty numeric and twelve categorical primary point estimates, with global BY/BH status, descriptive labels, and sample sizes. Complete row identities and endpoint context are in figure_row_legend.csv. Display selection never changes the correction family.")
    para("Interpretation and limitations", "Heading2")
    for text in [
        "Numeric rho describes a log-ratio feature versus a measured parameter within an exact collection event and declared crop/matrix/method stratum. The main association is marginal across treatments and can be confounded by management or environmental gradients.",
        "Categorical rank eta-squared describes a group association, not a numeric correlation or causal treatment effect. Nonrandomized crop and treatment comparisons remain observational.",
        "At least six independently identified biological plots are required for inferential computation. Three to five plots provide descriptive estimates only. Repeated years are not pooled; ambiguous/shared environmental specimens are excluded by the matching audit.",
        "Compositional CLR values do not establish absolute microbial abundance. Zero replacement, sequencing depth, and prediction-version sensitivity must be reviewed. Predicted functions are inferred genomic potential, not measured activity or process rates.",
        "Shannon diversity and observed ASV richness are derived from the same corrected 16S count profiles as taxonomic composition. Their associations can contain mathematical coupling and must not be presented as independent environmental corroboration. Endpoint-origin and coupling-caution columns identify these cases.",
        "Exact small-sample and 19,999-permutation p-value grids can be too coarse for a large global BY family. Reported minimum_attainable_p is a grid lower bound; ties or group-label symmetry can raise the actually attainable minimum. No adjusted discovery is not proof that an ecological association is absent.",
        "No prospective predictive validation, accepted scientific review, causal interpretation, or numerical ten-year forecast is established by this run. Newly generated report pages require visual inspection before manuscript use.",
    ]:
        para(text)
    story.append(PageBreak())
    para("Global correction families", "Heading1")
    rows = [["Family", "Tests", "Failures as 1", "Scope"]]
    for record in families.to_dict("records"):
        rows.append([record["global_family"], record["correction_family_size"],
                     record["failed_eligible_p_one"], record["scope"]])
    table(rows, [2.9*inch, .65*inch, .9*inch, 1.8*inch])
    para("Primary associations ordered by adjusted BY value", "Heading2")
    top = primary[primary["p"].notna()].sort_values(
        ["q_by_global", "p", "test_id"], kind="stable").head(15)
    if top.empty:
        para("No primary inferential p-values were available. Review the full matched-unit, feature-screen, cohort, and skip tables to identify design limitations.")
    else:
        data = [["Farm/domain", "Feature", "Parameter/factor", "n", "Effect", "BY q"]]
        for record in top.to_dict("records"):
            effect = record.get("rho") if record["association_type"] == "NUMERIC" else record.get("rank_eta_squared")
            try:
                effect = "{:.3f}".format(float(effect))
            except (ValueError, TypeError):
                effect = "missing"
            data.append([record["farm"]+" / "+record["domain"],
                         record.get("feature_label", record["feature_id"]),
                         record.get("variable", record["association_type"]),
                         record["n"], effect, "{:.3g}".format(record["q_by_global"])])
        table(data, [1.3*inch, 1.65*inch, 1.4*inch, .4*inch, .65*inch, .85*inch])
        para("This ordered table includes weak and nonsignificant results. Selection for display does not create another discovery family; all tests and adjusted values remain in the complete CSV.")
    para("Audit files", "Heading2")
    para("associations_global.csv.gz and per_farm subsets contain the complete effects, raw p, global BY/BH q, status, and design fields. association_summary.csv and multiplicity_family_audit.csv summarize every result category. Each worker retains exact matching, cohort, feature-reference, diagnostic, and source checksum evidence.")
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(.65*inch, .4*inch, "Scientific and visual review pending")
        canvas.drawRightString(7.85*inch, .4*inch, str(document.page))
        canvas.restoreState()
    document = SimpleDocTemplate(str(temporary), pagesize=(8.5*inch, 11*inch),
                                  leftMargin=.65*inch, rightMargin=.65*inch,
                                  topMargin=.6*inch, bottomMargin=.65*inch)
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    reader = PdfReader(str(temporary))
    require(bool(reader.pages) and all(page.extract_text().strip() for page in reader.pages),
            "Report contains an empty PDF page")
    os.replace(temporary, output)
    save(run / "results/REPORT_STATUS.json", {"created": True,
         "pdf": str(output.relative_to(run)), "pages": len(reader.pages),
         "scientific_review_pending": True, "visual_review_pending": True,
         "execution": execution})
    return len(reader.pages)


def _finish_sources(records, execution, path):
    for record in records.values():
        after = sha(record["path"])
        record["after_sha256"] = after
        require(after == record["expected_sha256"] == record["before_sha256"],
                "Read-only combine source changed: "+record["path"])
    require(bool(records), "Empty combine source evidence")
    save(path, {"passed": True, "source_files_changed": False,
                "execution": execution, "files": list(records.values()),
                "checked_utc": utc()})


def main():
    execution = require_hpc()
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    args = parser.parse_args()
    run = Path(args.run_root).resolve()
    status_path = run / "results/COMBINE_STATUS.json"
    records = {}
    try:
        shards, statuses = _verify_workers(run, execution, records)
        config = json_file(run / "CONFIG.json")
        _merge_self_checks(run, execution)
        combined, families, summary, totals = _combine_tables(run, shards, statuses, config)
        from association_figures import build_association_figures
        figure_receipt = build_association_figures(run, combined, shards)
        pages = _render_report(run, combined, families, execution, totals)
        verify_manifest(run)
        _finish_sources(records, execution, run / "audit/combine/SOURCE_VERIFICATION.json")
        outputs = sorted(path for path in (run / "results").rglob("*")
                         if path.is_file() and path.name not in
                         {"COMBINE_STATUS.json", "OUTPUT_SHA256SUMS.txt"})
        (run / "results/OUTPUT_SHA256SUMS.txt").write_text("".join(
            sha(path)+"  "+str(path.relative_to(run / "results"))+"\n" for path in outputs))
        save(status_path, {"completed": True, "source_files_changed": False,
             "package_integrity_passed": True, "scientific_review_pending": True,
             "visual_review_pending": True, "execution": execution,
             "numerical_self_checks_passed": json_file(run / "audit/combine/MERGE_SELF_CHECKS.json")["passed"],
             "output_manifest_sha256": sha(run / "results/OUTPUT_SHA256SUMS.txt"),
             "worker_count": len(shards), "tests_rows": totals["total_rows"],
             "recovery": {"preserved_original_workers": 10,
                          "assembled_logical_workers": 2, "retry_chunk_count": 6,
                          "logical_worker_count": 12},
             "display_rows": len(combined), "consolidation": totals["consolidation"],
             "families": len(families), "report_pages": pages,
             "figure_count": figure_receipt["figure_count"],
             "primary_BY_discoveries": totals["primary_BY_discoveries"],
             "numerical_ten_year_forecast": False,
             "ten_year_forecast_generated": False,
             "predictive_validation_established": False, "completed_utc": utc()})
        print("GLOBAL_ASSOCIATIONS_COMPLETED="+str(totals["total_rows"]))
    except Exception as error:
        save(status_path, {"completed": False, "source_files_changed": None,
             "package_integrity_passed": False, "scientific_review_pending": True,
             "visual_review_pending": True, "execution": execution,
             "error": str(error), "traceback": traceback.format_exc(),
             "numerical_ten_year_forecast": False, "failed_utc": utc()})
        raise


if __name__ == "__main__":
    main()
