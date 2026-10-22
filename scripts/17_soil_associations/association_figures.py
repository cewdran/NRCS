"""Transparent per-shard effect displays, generated only on Puma."""
from __future__ import annotations

import json
import math
import textwrap
from pathlib import Path

from hpc_guard import require_hpc

_EXECUTION = require_hpc()

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

from orchestration_common import require, save


COLORS = {"BY discovery": "#aa2a35", "BH only": "#ba7700",
          "Inferential, not adjusted discovery": "#325b79",
          "Descriptive only": "#8b8e91", "Eligible p unavailable": "#292929"}


def _safe_text(value):
    return "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)


def _display_label(record):
    feature = (_safe_text(record.get("feature_label")) or
               _safe_text(record.get("feature_id")))
    endpoint = (_safe_text(record.get("variable")) or
                _safe_text(record.get("association_type")))
    context = " / ".join(value for value in
                         (_safe_text(record.get("event")),
                          _safe_text(record.get("plant_type")),
                          _safe_text(record.get("cultivar")),
                          _safe_text(record.get("treatment_stratum"))) if value)
    abbreviated = " | ".join((feature[:42], endpoint[:34], context[:42]))
    return "\n".join(textwrap.wrap(abbreviated, 52)[:3])


def _group(record):
    if record["status"] == "FAILED_ELIGIBLE":
        return "Eligible p unavailable"
    if record["status"] == "DESCRIPTIVE_ONLY":
        return "Descriptive only"
    if bool(record.get("by_discovery", False)):
        return "BY discovery"
    if bool(record.get("bh_supplementary_discovery", False)):
        return "BH only"
    return "Inferential, not adjusted discovery"


def _selected(frame, effect, limit):
    rows = frame.copy()
    if effect not in rows:
        return rows.iloc[:0]
    rows[effect] = pd.to_numeric(rows[effect], errors="coerce")
    rows = rows[rows[effect].notna() & np.isfinite(rows[effect]) &
                rows["n"].ge(3) & rows["status"].isin(
                    ["INFERENTIAL", "DESCRIPTIVE_ONLY", "FAILED_ELIGIBLE"])].copy()
    rows["_display_abs_effect"] = rows[effect].abs()
    rows["_display_tier"] = np.where(rows["p"].notna(), 0, 1)
    return rows.sort_values(["_display_tier", "q_by_global", "p",
                             "_display_abs_effect", "test_id"],
                            ascending=[True, True, True, False, True],
                            kind="stable", na_position="last").head(limit)


def _panel(axis, selected, effect, title, labels, legend_rows, shard):
    axis.set_title(title, loc="left", fontsize=12, fontweight="bold", pad=12)
    axis.set_xlim((-1.05, 1.40) if effect == "rho" else (-.025, 1.30))
    axis.set_xticks([-1, -.5, 0, .5, 1] if effect == "rho" else [0, .25, .5, .75, 1])
    axis.set_xlabel("Spearman rho" if effect == "rho" else "Rank eta-squared", fontsize=10)
    axis.axvline(0, color="#bcc5cb", linewidth=1, zorder=1)
    axis.grid(axis="x", color="#e8ecef", linewidth=.7, zorder=0)
    for side in ("top", "right", "left"):
        axis.spines[side].set_visible(False)
    axis.spines["bottom"].set_color("#adb8c0")
    axis.tick_params(axis="y", length=0, pad=8)
    axis.tick_params(axis="x", labelsize=9)
    if selected.empty:
        axis.set_ylim(0, 1)
        axis.set_yticks([])
        axis.text(.45, .5, "No supported point estimates\nfor this display.\nSee cohort and skip audits.",
                  transform=axis.transAxes, ha="center", va="center", fontsize=11,
                  color="#52636f", linespacing=1.6)
        return
    row_records = selected.to_dict("records")
    positions = np.arange(len(row_records))
    axis.set_yticks(positions, [_display_label(record) for record in row_records], fontsize=8)
    axis.set_ylim(len(row_records)-.4, -.7)
    for index, record in enumerate(row_records):
        group = _group(record)
        marker = "x" if group == "Eligible p unavailable" else "o"
        axis.scatter(record[effect], index, s=43, color=COLORS[group], marker=marker,
                     linewidths=1.2, zorder=3)
        q_value = record.get("q_by_global")
        q_label = ("{:.2g}".format(q_value) if q_value is not None and
                   math.isfinite(float(q_value)) else "NA")
        annotation = "n={}  q={}".format(int(record["n"]), q_label)
        axis.text(1.05 if effect == "rho" else 1.03, index, annotation,
                  fontsize=8, va="center", color="#34434e", clip_on=False)
        legend_rows.append({"shard_id": shard["id"], "panel": labels,
            "display_rank": index+1, "test_id": record["test_id"],
            "farm": record["farm"], "domain": record["domain"],
            "feature_class": record["feature_class"], "feature_id": record["feature_id"],
            "feature_label": record.get("feature_label"), "feature_lineage": record.get("feature_lineage"),
            "association_type": record["association_type"], "variable": record.get("variable"),
            "dataset": record.get("dataset"), "event": record.get("event"),
            "plant_type": record.get("plant_type"), "cultivar": record.get("cultivar"),
            "unit": record.get("unit"), "method": record.get("method"),
            "endpoint_origin": record.get("endpoint_origin"), "coupling_caution": record.get("coupling_caution"),
            "display_group": group, "effect_statistic": effect, "effect": record[effect],
            "n": record["n"], "p": record.get("p"), "q_by_global": record.get("q_by_global"),
            "q_bh_global": record.get("q_bh_global"), "cohort_id": record["cohort_id"]})


def build_association_figures(run, combined, shards):
    run = Path(run)
    require(len(shards) == 12, "Expected twelve figure shard displays")
    output = run / "results/figures"
    output.mkdir(parents=True, exist_ok=True)
    primary = combined[combined["global_family"].eq("PRIMARY")]
    legend_rows, figures = [], []
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 9,
                         "svg.fonttype": "none", "axes.unicode_minus": False}):
        for shard in shards:
            rows = primary[primary["shard_id"].eq(shard["id"])]
            numeric = _selected(rows[rows["association_type"].eq("NUMERIC")], "rho", 20)
            categorical = _selected(rows[rows["association_type"].isin(["TREATMENT", "CROP"])],
                                     "rank_eta_squared", 12)
            count = max(len(numeric), len(categorical), 5)
            height = max(7.5, count*.68+2.8)
            figure, axes = plt.subplots(1, 2, figsize=(22, height))
            figure.subplots_adjust(left=.22, right=.97, bottom=.13, top=.87, wspace=1.10)
            figure.suptitle(shard["farm"]+" / "+shard["domain"]+" / "+shard["feature_type"],
                           x=.03, y=.98, ha="left", fontsize=17, fontweight="bold", color="#223b4a")
            figure.text(.03, .943, "Primary associations | BY global adjustment | Scientific and visual review pending",
                        fontsize=11, color="#4c6270")
            _panel(axes[0], numeric, "rho", "Numeric parameters: up to 20 displayed estimates",
                   "NUMERIC", legend_rows, shard)
            _panel(axes[1], categorical, "rank_eta_squared", "Categorical factors: up to 12 displayed estimates",
                   "CATEGORICAL", legend_rows, shard)
            handles = [Line2D([0], [0], color=value, marker="x" if key == "Eligible p unavailable" else "o",
                              linestyle="none", markersize=7, label=key)
                       for key, value in COLORS.items()]
            figure.legend(handles=handles, loc="lower left", bbox_to_anchor=(.025, .045),
                          ncol=3, fontsize=9, frameon=False)
            figure.text(.03, .022,
                "Display selection: inferential BY q, raw p, then absolute effect; descriptive estimates follow. Selection does not change the global family.\n"
                "Labels abbreviated; complete identities and context are in figure_row_legend.csv. NA q indicates no valid inferential result. Same-source diversity can be mathematically coupled.",
                fontsize=8.5, color="#52636f", linespacing=1.5)
            png = output / (shard["id"]+"_primary_associations.png")
            vector = output / (shard["id"]+"_primary_associations.svg")
            figure.savefig(png, dpi=300, facecolor="white")
            figure.savefig(vector, facecolor="white")
            plt.close(figure)
            require(png.is_file() and png.stat().st_size > 1000 and
                    vector.is_file() and vector.stat().st_size > 1000,
                    "Figure export failed")
            figures.append({"shard_id": shard["id"], "png": str(png.relative_to(run)),
                            "svg": str(vector.relative_to(run)),
                            "numeric_rows_displayed": len(numeric),
                            "categorical_rows_displayed": len(categorical)})
    columns = ["shard_id", "panel", "display_rank", "test_id", "farm", "domain",
               "feature_class", "feature_id", "feature_label", "feature_lineage", "association_type",
               "variable", "dataset", "event", "plant_type", "cultivar", "unit", "method",
               "endpoint_origin", "coupling_caution", "display_group", "effect_statistic", "effect",
               "n", "p", "q_by_global", "q_bh_global", "cohort_id"]
    pd.DataFrame(legend_rows, columns=columns).to_csv(output / "figure_row_legend.csv", index=False)
    result = {"created": True, "figure_count": len(figures), "figures": figures,
              "execution": _EXECUTION, "scientific_review_pending": True,
              "visual_review_pending": True, "raster_dpi": 300,
              "vector_format": "SVG", "selection_is_display_only": True,
              "scope": "PRIMARY family only; complete sensitivity results remain in tables"}
    save(output / "FIGURE_STATUS.json", result)
    return result
