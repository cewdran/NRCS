#!/usr/bin/env python3
"""Build the human-readable report and scientific figures from validated tables."""
from pathlib import Path
from html import escape
import json
import textwrap

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.colors import ListedColormap, BoundaryNorm

ROOT = Path(__file__).resolve().parent
TABLES = ROOT / "tables"
FIG = ROOT / "figures"
FIG.mkdir(exist_ok=True)
COHORTS = ["Grape_PetiteSirah__2023", "Grape_PetiteSirah__2024", "Grape_Grenache__2023"]
LABEL = dict(zip(COHORTS, ["Petite Sirah 2023", "Petite Sirah 2024", "Grenache 2023"]))
CONTROL = "ControlFertigationOnly"
PS = [CONTROL, "FishManure", "HorseManurePomace", "SteerManure"]
GR = [CONTROL, "CompostedGrapePomaceHorseManure", "CompostedHorseManure", "FreshGrapePomace"]
SHORT = {CONTROL: "Fertigation\ncontrol", "FishManure": "Fish\nmanure", "HorseManurePomace": "Horse manure\n+ pomace", "SteerManure": "Steer\nmanure", "CompostedGrapePomaceHorseManure": "Composted horse\nmanure + pomace", "CompostedHorseManure": "Composted\nhorse manure", "FreshGrapePomace": "Fresh grape\npomace"}
COLORS = {t: c for order in [PS, GR] for t, c in zip(order, ["#456988", "#d28a4e", "#568879", "#a36c88"])}
om = pd.read_csv(TABLES / "primary_omnibus.csv")
all_om = pd.read_csv(TABLES / "all_variants_omnibus.csv")
effects = pd.read_csv(TABLES / "primary_contrasts_with_sensitivity.csv")
raw = pd.read_csv(TABLES / "primary_sample_logratios.csv")
design = pd.read_csv(TABLES / "sample_design_and_reference_coverage.csv")
validation = json.loads((ROOT / "VALIDATION.json").read_text())
assert validation["status"] == "PASS" and (om.q_BH_joint_155 >= .05).all()

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.titleweight": "bold", "axes.labelcolor": "#263746",
    "text.color": "#263746", "axes.edgecolor": "#b7c1c8",
    "xtick.color": "#425563", "ytick.color": "#425563",
    "savefig.facecolor": "white", "svg.fonttype": "none"})


def save(fig, stem):
    fig.savefig(FIG / (stem + ".png"), dpi=240, bbox_inches="tight")
    fig.savefig(FIG / (stem + ".svg"), bbox_inches="tight")
    plt.close(fig)


def best(cohort, n=2):
    return om.loc[om.cohort.eq(cohort)].sort_values(["exact_p", "taxon"]).head(n)


def figure_overview():
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.9), sharey=True)
    for ax, cohort in zip(axes, COHORTS):
        d = om.loc[om.cohort.eq(cohort)].sort_values(["exact_p", "taxon"])
        ax.scatter(np.arange(1, len(d)+1), -np.log10(d.q_BH_joint_155), s=24, color="#456988", zorder=3)
        ax.axhline(-np.log10(.05), color="#b4643e", lw=1.4, ls="--")
        ax.set(xlabel="Genus tests ranked by exact p", title=f"{LABEL[cohort]}\n{len(d)} tests · 12 samples", ylim=(-.03, 1.7))
        top = d.iloc[0]
        ax.annotate(f"{top.taxon}\nq = {top.q_BH_joint_155:.4f}",
                    xy=(1, -np.log10(top.q_BH_joint_155)), xytext=(.16, .85),
                    textcoords="axes fraction", fontsize=9,
                    arrowprops={"arrowstyle": "-", "color": "#7d919f"})
        ax.grid(axis="y", alpha=.18)
    axes[0].set_ylabel("−log₁₀(joint BH-adjusted p)")
    axes[2].text(.97, -np.log10(.05)+.035, "q = 0.05", transform=axes[2].get_yaxis_transform(), ha="right", fontsize=9, color="#a55530")
    fig.suptitle("No primary genus test crosses the joint q < 0.05 threshold", fontsize=15, x=.5, y=.98)
    fig.text(.5, .035, "Correction family: all 155 genus × cohort tests together. These are exploratory follow-up tests.", ha="center", fontsize=10)
    fig.tight_layout(rect=(0, .09, 1, .92))
    save(fig, "01_all_primary_tests")


def figure_samples():
    fig, axes = plt.subplots(3, 2, figsize=(12, 11.5))
    for row, cohort in enumerate(COHORTS):
        order = PS if "Petite" in cohort else GR
        for ax, (_, test) in zip(axes[row], best(cohort).iterrows()):
            d = raw.loc[raw.cohort.eq(cohort) & raw.taxon.eq(test.taxon)]
            for pos, treatment in enumerate(order):
                s = d.loc[d.treatment.eq(treatment)].sort_values(["collection_date", "sample_name"])
                for jitter, (_, sample) in zip([-.10, 0, .10], s.iterrows()):
                    marker = "s" if sample.collection_date == "2024-11-06" else "o"
                    ax.scatter(pos+jitter, sample.percent, s=62, marker=marker,
                               color=COLORS[treatment], edgecolor="white", linewidth=.7, zorder=4)
                ax.plot([pos-.20, pos+.20], [s.percent.mean()]*2, color=COLORS[treatment], lw=2, zorder=3)
            ax.set_xticks(range(4), [SHORT[t] for t in order], fontsize=9)
            ax.set_xlim(-.5, 3.5)
            ax.set_ylabel("Observed target bacterial reads (%)")
            ax.set_title(f"{LABEL[cohort]} · {test.taxon}\nPrimary omnibus q = {test.q_BH_joint_155:.4f}", fontsize=11, loc="left")
            ax.grid(axis="y", alpha=.18)
            lo, hi = ax.get_ylim()
            ax.set_ylim(bottom=-.04*max(hi, .01))
    fig.suptitle("Leading candidates: all sample-level proportions", fontsize=16, y=.99)
    handles = [Line2D([], [], marker="o", linestyle="", color="#536d80", label="October 2023 / August 2024"),
               Line2D([], [], marker="s", linestyle="", color="#536d80", label="November 2024"),
               Line2D([], [], color="#536d80", lw=2, label="Descriptive treatment mean")]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(.5, .035), ncol=3, frameon=False, fontsize=9)
    fig.text(.5, .012, "Shown: two smallest exact p values per cohort, selected for display only. Raw 2024 proportions are not date-adjusted.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .085, 1, .955), h_pad=2.2)
    save(fig, "02_candidate_sample_proportions")


def figure_sensitivity():
    cols = ["q_primary", "q_pseudocount_0.1", "q_pseudocount_1", "q_all_positive_reference"]
    selected = pd.concat([best(c, 5) for c in COHORTS]).copy()
    # Ensure every sensitivity-only hit is shown, even if outside the leading five.
    selected = pd.concat([selected, om.loc[om.q_all_positive_reference.lt(.05)]])
    selected = selected.drop_duplicates(["cohort", "taxon"])
    selected["order"] = selected.cohort.map({c: i for i,c in enumerate(COHORTS)})
    selected = selected.sort_values(["order", "exact_p", "taxon"])
    selected.to_csv(TABLES / "figure_sensitivity_candidates.csv", index=False)
    data = selected[cols].to_numpy()
    fig, ax = plt.subplots(figsize=(10, 9))
    cmap = ListedColormap(["#c6e4d9", "#f8e7c8", "#eef2f4"])
    norm = BoundaryNorm([0, .05, .1, 1.000001], cmap.N)
    ax.imshow(data, cmap=cmap, norm=norm, aspect="auto")
    ax.set_xticks(range(4), ["Primary\npseudocount 0.5", "Pseudocount\n0.1", "Pseudocount\n1.0", "All-positive\nreference"])
    ax.xaxis.tick_top()
    ax.tick_params(axis="both", length=0)
    ax.set_yticks(range(len(selected)), [f"{LABEL[r.cohort]}  |  {r.taxon}" for r in selected.itertuples()], fontsize=10)
    for i in range(len(selected)):
        for j in range(4):
            ax.text(j, i, f"{data[i,j]:.4f}", ha="center", va="center", fontsize=10,
                    fontweight="bold" if data[i,j] < .05 else "normal")
    for i in range(1, len(selected)):
        if selected.iloc[i].cohort != selected.iloc[i-1].cohort:
            ax.axhline(i-.5, color="white", lw=5)
    for spine in ax.spines.values(): spine.set_visible(False)
    fig.suptitle("Two threshold crossings depend on the reference set", fontsize=15, y=.995)
    fig.text(.61, .035, "Every column uses the same 155-test BH family.\nGreen: q < 0.05; tan: 0.05 ≤ q < 0.10; gray: q ≥ 0.10.\nShown: five lowest primary p values per cohort, plus sensitivity-only hits.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .10, 1, .96))
    save(fig, "03_reference_and_pseudocount_sensitivity")


def figure_effects():
    fig, axes = plt.subplots(3, 2, figsize=(12, 10.7), sharex=True)
    for row, cohort in enumerate(COHORTS):
        order = (PS if "Petite" in cohort else GR)[1:]
        for ax, (_, test) in zip(axes[row], best(cohort).iterrows()):
            d = effects.loc[effects.cohort.eq(cohort) & effects.taxon.eq(test.taxon)].set_index("treatment").loc[order]
            for j, (treatment, r) in enumerate(d.iterrows()):
                ax.plot([r.nominal_95_lower, r.nominal_95_upper], [j, j], color="#b7c4cc", lw=2, zorder=2)
                ax.plot([r.loo_min_effect, r.loo_max_effect], [j, j], color=COLORS[treatment], lw=6, alpha=.5, solid_capstyle="butt", zorder=3)
                ax.scatter(r.log2_ratio_effect, j, color=COLORS[treatment], s=46, zorder=4)
            ax.axvline(0, lw=1, ls="--", color="#8798a3")
            ax.set_yticks(range(3), [SHORT[t] for t in order], fontsize=9)
            ax.set_ylim(2.6, -.6)
            ax.set_title(f"{LABEL[cohort]} · {test.taxon}\nPrimary omnibus q = {test.q_BH_joint_155:.4f}", fontsize=11, loc="left")
            ax.grid(axis="x", alpha=.14)
    for ax in axes[-1]:
        ax.set_xlabel("Treatment − control (log₂ ratio scale)")
    fig.suptitle("Effect estimates and single-sample influence", fontsize=16, y=.99)
    handles = [Line2D([], [], marker="o", ls="", color="#536d80", label="Primary estimate"),
               Line2D([], [], color="#b7c4cc", lw=2, label="Nominal model-based 95% interval"),
               Line2D([], [], color="#568879", lw=6, alpha=.5, label="Leave-one-sample-out range")]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(.5, .04), ncol=3, frameon=False, fontsize=9)
    fig.text(.5, .014, "Intervals are not multiplicity-adjusted; omission ranges are not confidence intervals. Effects are relative to each cohort’s CLR reference.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .10, 1, .955), h_pad=2)
    save(fig, "04_candidate_effects_and_influence")


md, html = [], []
def heading(s, level=2):
    md.append("#"*level + " " + s + "\n")
    html.append(f"<h{level}>{escape(s)}</h{level}>")
def para(s):
    md.append(s + "\n")
    html.append("<p>" + escape(s) + "</p>")
def table(headers, rows):
    rows = [[str(v) for v in row] for row in rows]
    md.append("| " + " | ".join(headers) + " |\n| " + " | ".join(["---"]*len(headers)) + " |\n" + "\n".join("| " + " | ".join(r) + " |" for r in rows) + "\n")
    html.append('<div class="tablewrap"><table><thead><tr>' + ''.join('<th>'+escape(h)+'</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in r)+'</tr>' for r in rows) + '</tbody></table></div>')
def link(path, label):
    md.append(f"[{label}]({path})\n")
    html.append(f'<p><a href="{escape(path)}">{escape(label)}</a></p>')
def figure(stem, caption):
    md.append(f"![{caption}](figures/{stem}.png)\n")
    html.append(f'<figure><a href="figures/{stem}.svg"><img src="figures/{stem}.png" alt="{escape(caption)}"></a><figcaption>{escape(caption)}</figcaption></figure>')


def build_report():
    heading("Page Springs bacterial genus follow-up", 1)
    para("NRCS 16S analyses · 24 September 2026 · Exploratory analysis, version 1")
    para("None of the 155 primary genus-by-cohort tests meets the pre-specified joint BH-adjusted q < 0.05 threshold. The smallest primary q is 0.0615. This follow-up does not establish a robust individual genus association, and it does not invalidate the earlier community-level treatment associations. The community and genus tests address different outcomes and use different correction families.")
    para("All 12 positive-target bacterial samples were retained in each of Petite Sirah 2023, Petite Sirah 2024, and Grenache 2023. Each cohort contains four treatments with three distinct plots each. These cohorts were selected after reviewing their community-level results, so this is exploratory follow-up rather than independent confirmation. No additional HPC job is required to obtain these results.")
    rows = []
    for cohort in COHORTS:
        b = best(cohort, 1).iloc[0]
        rows.append([LABEL[cohort], len(om[om.cohort.eq(cohort)]), b.taxon, f"{b.exact_p:.6f}", f"{b.q_BH_joint_155:.4f}", "0"])
    table(["Cohort", "Eligible genus labels", "Leading genus label", "Exact p", "Joint q", "Primary q < 0.05"], rows)
    figure("01_all_primary_tests", "Figure 1. All primary tests ranked within cohort. The dashed line denotes the joint q = 0.05 threshold; no point crosses it.")

    heading("What the leading candidates show")
    para("Candidates below are descriptive leads selected by the smallest primary exact p values. Their displayed treatment differences are not declarations of pairwise significance. No pairwise p values were calculated; the primary test asks whether any of the four treatment groups differs in the specified genus log-ratio.")
    para("Petite Sirah 2023: Paracoccus has the smallest exact p (0.0107), but joint q = 0.1537. Mean proportions are 0.083% in controls, 0.582% with fish manure, 0.405% with horse manure plus pomace, and 0.357% with steer manure. One control has no assigned Paracoccus reads. Novibacillus is also a descriptive candidate (q = 0.2101), with substantial variation within treatments.")
    para("Petite Sirah 2024: Paenibacillus has primary q = 0.0615 after collection-date adjustment. It has zero assigned reads in all three horse-manure-plus-pomace samples, versus detected reads in all three controls. This describes non-detection in these libraries, not biological absence or eradication. The large negative log-ratio estimate depends on handling zeros and must not be presented as an absolute organism fold change. Flavisolibacter is higher on average in the horse-manure-plus-pomace group, but its primary q is 0.0879.")
    para("Grenache 2023: Ornithinicoccus has primary q = 0.0615. It is not detected in the three control libraries and has mean proportions of 2.22% in composted horse manure plus pomace and 1.14% in composted horse manure. Planococcus averages 20.05% in composted horse manure plus pomace and is not detected in the controls, but primary q = 0.0879. Large estimates involving all-zero groups should be read alongside the raw observations and pseudocount sensitivity.")
    para("The earlier Grenache 2023 community analysis also detected unequal dispersion (q = 0.0272). That finding warrants caution about attributing its community association to a consistent treatment location shift, and it does not establish the exchangeability assumptions of these genus tests.")
    figure("02_candidate_sample_proportions", "Figure 2. Every sample for the two lowest-p candidates per cohort. Points are observed relative abundances; lines are arithmetic treatment means. The 2024 graph pools dates visually, while inference adjusts for date. Zero reads denote non-detection at the observed sequencing depth.")

    heading("Sensitivity and uncertainty")
    sensitivity_rows = []
    for variant, label in [("primary", "Primary: eligible reference, +0.5"), ("pseudocount_0.1", "Eligible reference, +0.1"), ("pseudocount_1", "Eligible reference, +1.0"), ("all_positive_reference", "All-positive reference, +0.5")]:
        subset = all_om[all_om.variant.eq(variant)]
        sensitivity_rows.append([label] + [int((subset[subset.cohort.eq(c)].q_BH_joint_155 < .05).sum()) for c in COHORTS])
    table(["Variant", "Petite Sirah 2023 q < 0.05", "Petite Sirah 2024 q < 0.05", "Grenache 2023 q < 0.05"], sensitivity_rows)
    para("Both pre-specified pseudocount alternatives retain zero threshold crossings. Changing the reference to every genus label positive anywhere in a cohort, including Unclassified_Genus, yields q = 0.0302 for Ornithinicoccus and Truepera in Grenache 2023. Their primary q values are 0.0615 and 0.0879, respectively. These are reference-sensitive signals, not robust primary discoveries. A different CLR reference changes the scientific ratio being tested; this sensitivity should not be used to select the most favorable result.")
    figure("03_reference_and_pseudocount_sensitivity", "Figure 3. Joint BH q values for leading candidates and sensitivity-only threshold crossings. Values are adjusted across all 155 tests separately for each variant, rather than across this displayed subset.")
    para("The report supplies 465 treatment-versus-control estimates and 5,580 estimates after a single-sample omission. A leave-one-out range is an influence diagnostic, not a confidence interval. A direction that remains the same in every omission does not establish statistical significance or remove possible bias. Across all contrasts, omission changes the sign for 49/114 in Petite Sirah 2023, 90/189 in Petite Sirah 2024, and 57/162 in Grenache 2023. These totals include estimates near zero.")
    para("The displayed 95% intervals are nominal t intervals from ordinary least squares, conditional on the chosen transformation. They assume independent, homoskedastic, normally distributed residuals, are not corrected for multiple comparisons or candidate selection, and can be optimistic with only three plots per group. An interval excluding zero does not override the adjusted omnibus result. The primary omnibus p values come from exhaustive permutations, not the parametric F distribution.")
    figure("04_candidate_effects_and_influence", "Figure 4. Treatment-versus-control log2-ratio estimates for the same displayed candidates. The ratio denominator is each cohort's eligible-genus geometric mean. Gray intervals are nominal model-based 95% intervals; thick colored segments show the range after each single-sample omission.")
    link("tables/primary_contrasts_with_sensitivity.csv", "All primary effects, nominal intervals, omission ranges, and transformation sensitivity")

    heading("Reference coverage and interpretation")
    cover = []
    for c in COHORTS:
        s = design.loc[design.cohort.eq(c)]
        cover.append([LABEL[c], f"{s.target_read_count.min():,}–{s.target_read_count.max():,}",
                      f"{100*s.eligible_reference_read_fraction.mean():.1f}%",
                      f"{100*s.eligible_reference_read_fraction.min():.1f}–{100*s.eligible_reference_read_fraction.max():.1f}%",
                      f"{100*s.unclassified_genus_read_fraction.mean():.1f}%"])
    table(["Cohort", "Target reads per sample", "Mean eligible-genus read coverage", "Coverage range", "Mean unresolved genus fraction"], cover)
    para("The primary denominator is the geometric mean of the eligible genus counts after adding the pseudocount. It is not the total bacterial read count and does not imply stable absolute abundance of that reference. Some database labels represent uncultured groups rather than formally named genera. Unclassified_Genus remains in all descriptive read proportions and the reference sensitivity but is excluded from primary testing and the primary reference.")
    para("Counts are observed sequencing reads. Treatment coefficients measure differences in log2(genus/reference) between an amendment and the fertigation control. Exponentiating a coefficient yields a ratio of ratios, not an absolute microbial population fold change. Separate bacterial and archaeal amplicon libraries cannot establish their absolute abundance ratio, and genus assignments do not demonstrate soil functions or nutrient-processing rates [1, 2].")

    heading("Methods and reproducibility")
    para("Rules were saved in ANALYSIS_PROTOCOL.json before fitting any genus hypothesis tests. Per cohort, a genus label was eligible if it had at least 10 reads in at least six of the 12 samples and mean per-sample relative abundance at least 0.1%; the unresolved label was excluded. Filtering pooled treatments without inspecting their association. This retained 38, 63, and 54 labels, respectively. All eligible labels formed the primary reference; this set stayed fixed across permutations and sensitivity fits. The all-positive reference uses 264, 326, and 306 labels, respectively.")
    para("For primary analysis, each sample's transformed value was log2(count + 0.5) minus the mean of log2(count + 0.5) across its eligible reference labels. The models were CLR ~ treatment for each 2023 cohort and CLR ~ collection_date + treatment for Petite Sirah 2024. Every treatment in 2024 has two samples on 5 August and one on 6 November. The additive model does not estimate a treatment-by-date interaction.")
    para("The exact omnibus test permuted treatment assignments with group sizes fixed at three, within collection date where applicable. There are 369,600 labelled allocations per 2023 cohort and 60,480 within-date allocations for Petite Sirah 2024. Omnibus F is invariant to renaming the four treatment groups, so the implementation enumerates 15,400 and 2,520 unlabelled partitions, each with equal multiplicity 24. Exact p is the fraction of all partitions with a treatment sum of squares at least the observed value, using a stated numerical tolerance. The observed allocation is included; no Monte Carlo +1 adjustment is needed for exhaustive enumeration [3].")
    para("Equal date proportions in all groups make treatment contrasts orthogonal to date terms in this design. After subtracting date means, treatment sums of squares can therefore be calculated from group sums. The saved code validates this shortcut against independent QR projections for all fitted features and variants. Residual degrees of freedom are eight for 2023 and seven for 2024. The reported partial R² is treatment sum of squares divided by date-reduced sum of squares; it is not the earlier Bray–Curtis community R².")
    para("The primary multiple-testing family comprises all 155 eligible genus-by-cohort omnibus tests jointly. Each pre-specified sensitivity repeats that same 155-test family. Per-cohort BH values are provided only as secondary context and do not replace the joint values; some are below 0.05. The q columns are BH-adjusted p values, not probabilities that a hypothesis is true. These are exploratory nominal adjustments: screening cohorts in the same data, correlated compositional features, and repeated physical plots across years limit confirmatory interpretation [4].")
    para("Exact enumeration removes Monte Carlo error; it does not establish exchangeability, field randomization, independence, or causation. Each separate cohort has 12 distinct plot IDs, but plots observed again in 2024 are not independent new experiments. No field blocks, technical batch covariates, or soil nutrient data were available for this fit. Conditional exchangeability within date is an assumption. The generic CLR linear-model calculation here is not an implementation of ANCOM-BC2, ALDEx2, LinDA, or the LDM package.")
    para("Validation passed: source SHA-256 hashes and frozen protocol hash; sample and design joins; integer nonnegative counts summing to target reads; supplied relative abundances; all allocation counts, uniqueness, date balance, and observed-allocation inclusion; brute-force labelled toy designs versus the partition method; all model F statistics versus QR projections. The largest absolute F discrepancy was below 5 × 10⁻¹³. Rscript was unavailable locally; analysis used Python/NumPy/SciPy with the software versions recorded. This is numerical validation, not validation of biological assumptions.")
    link("ANALYSIS_PROTOCOL.json", "Frozen analysis protocol")
    link("VALIDATION.json", "Machine-readable validation results")
    link("tables/primary_omnibus.csv", "Complete primary tests and sensitivity q values")
    link("tables/feature_eligibility.csv", "Every genus eligibility decision")
    link("tables/sample_design_and_reference_coverage.csv", "Sample design and reference coverage")

    heading("What is ready for the next step")
    para("The genus follow-up is complete. Keep the earlier Page Springs bacterial community associations as the current community-level evidence and retain these genus patterns as exploratory candidates. No individual genus should be described as a robust treatment biomarker from this analysis.")
    para("The next data handoff is the soil nutrient and other environmental measurements in their original Excel or CSV files. Include sample/collection identifiers, collection dates, units, depth, analytical method where available, and any plot or treatment lookup. Preserve missing values and laboratory flags such as below-detection-limit results. A separate lookup is sufficient if the laboratory labels differ from the sequencing labels; do not force a match or average different collection dates.")
    para("On receipt, match measurements to collections, audit missingness and units, and define a small set of scientific questions suited to the number of independent plots. Technical extraction/PCR/library batch records and the field layout or randomization description would also help distinguish date, plot, and treatment patterns. Until those files arrive, a matched-plot temporal design audit and alpha-diversity method specification remain possible with the existing data; no additional unplanned genus screening is needed to complete this step.")

    heading("Method references")
    para("These sources motivate compositional interpretation, reference choices, enumeration, and multiple testing. They do not validate this custom implementation or the field design.")
    link("https://www.frontiersin.org/journals/microbiology/articles/10.3389/fmicb.2017.02224/full", "[1] Gloor et al. (2017). Microbiome Datasets Are Compositional: And This Is Not Optional.")
    link("https://www.nature.com/articles/s41467-019-10656-5", "[2] Morton et al. (2019). Establishing microbial composition measurement standards with reference frames.")
    link("https://gavinsimpson.github.io/permute/reference/allPerms.html", "[3] permute documentation: allPerms, enumeration of distinct permutations.")
    link("https://doi.org/10.1111/j.2517-6161.1995.tb02031.x", "[4] Benjamini and Hochberg (1995). Controlling the False Discovery Rate: A Practical and Powerful Approach to Multiple Testing.")

    (ROOT / "REPORT.md").write_text("\n".join(md))
    css = """body{margin:0;background:#f3f5f7;color:#243746;font:16px/1.65 system-ui,sans-serif}main{max-width:1120px;margin:36px auto;padding:42px 54px;background:white;border-top:7px solid #456988}h1{font-size:34px;line-height:1.2;margin-top:0}h2{font-size:24px;margin:40px 0 12px;color:#2d5268}p{max-width:1000px}a{color:#256185}table{border-collapse:collapse;width:100%;font-size:14px}th{text-align:left;background:#e9f0f4}th,td{padding:10px 12px;border-bottom:1px solid #dce3e7;vertical-align:top}.tablewrap{overflow:auto;margin:22px 0}figure{margin:28px 0}img{max-width:100%;height:auto}figcaption{font-size:14px;color:#5a6a75;margin-top:10px}@media(max-width:700px){main{margin:0;padding:24px 18px}h1{font-size:28px}}@media print{body{background:white}main{margin:0;padding:0;border:0}figure,table{break-inside:avoid}h2{break-after:avoid}a{color:inherit}}"""
    (ROOT / "REPORT.html").write_text('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Page Springs bacterial genus follow-up</title><style>'+css+'</style></head><body><main>'+"\n".join(html)+'</main></body></html>')
    (ROOT / "RESULTS_DRAFT.md").write_text("""# Draft results paragraph

An exploratory genus-level follow-up of Page Springs Cellars bacteria included 12 samples per cohort for Petite Sirah 2023, Petite Sirah 2024, and Grenache 2023. Pre-specified abundance and prevalence filters retained 38, 63, and 54 genus labels, respectively. Exact treatment-permutation tests of log2-centered abundance models adjusted for collection date and restricted permutations within date for Petite Sirah 2024. No primary genus-by-cohort test met q < 0.05 after Benjamini–Hochberg adjustment across all 155 tests. The smallest adjusted values were 0.0615 for Paenibacillus in Petite Sirah 2024 and Ornithinicoccus in Grenache 2023. Alternative pseudocounts retained this overall conclusion. An alternative reference comprising all locally detected genus labels yielded q = 0.0302 for Ornithinicoccus and Truepera in Grenache 2023, indicating reference sensitivity. These patterns do not establish robust individual genus treatment associations. Interpretation is limited by three plots per treatment, selection of these cohorts following community-level screening, the compositional reference and zero handling, and previously detected community dispersion differences in Grenache 2023.

The existing community-level treatment associations and the genus follow-up address different outcomes and correction families. Failure to identify an individual genus at the primary adjusted threshold does not establish absence of treatment-related community differences. Relative read counts and taxonomic labels do not measure absolute microbial abundance or functional activity.
""")
    (ROOT / "README.md").write_text("""# NRCS Page Springs bacterial genus follow-up v1

Start by extracting the ZIP and opening **REPORT.html** in a browser. Keep the figures folder beside the report. REPORT.md and RESULTS_DRAFT.md provide plain-text versions. No new HPC submission is required.

## Main result

None of the 155 primary genus-by-cohort omnibus tests has joint BH q < 0.05. Two Grenache labels cross only under the all-positive-reference sensitivity; these are not robust primary discoveries.

## Reproduce

Python 3.10+ with numpy, pandas, scipy, and matplotlib is required. Exact versions used are recorded in ENVIRONMENT.json and requirements-used.txt. From this directory:

```bash
python3 analyze.py
python3 build_report.py
```

The analysis checks source hashes and the frozen protocol hash before fitting. It runs deterministically; random numbers occur only in seeded numerical-validation toys. No R packages, external downloads, cluster resources, or soil data are needed to reproduce the included fit. Re-running with a different environment may give very small floating-point differences. The generic model is not ANCOM-BC2, ALDEx2, LinDA, or the LDM package.

## Files

- ANALYSIS_PROTOCOL.json: decisions saved before fitting genus tests.
- INPUT_PROVENANCE.json: source paths and exact input checksums.
- inputs/: copied source genus counts/proportions, sample design, and prior community results for context.
- tables/primary_omnibus.csv: all 155 primary tests, joint and secondary within-cohort q, and every sensitivity q.
- tables/primary_contrasts_with_sensitivity.csv: 465 treatment-versus-control log2-ratio estimates, nominal intervals, leave-one-out ranges, and sensitivity directions. No pairwise p values.
- tables/all_variants_*.csv: complete tests and effects for all four transformations.
- tables/primary_leave_one_sample_out.csv: every single-sample-omission estimate (5,580 rows).
- tables/feature_eligibility.csv: inclusion/exclusion decisions for every source genus label in each selected cohort.
- tables/primary_sample_logratios.csv: individual raw proportions and primary CLR values.
- tables/*_genus_counts.tsv and *_design_matrix.tsv: analysis matrices.
- figures/: four figures in PNG and editable SVG formats.
- VALIDATION.json: numerical checks and data reconciliation.
- SHA256SUMS.txt: release checksums. Verify with `sha256sum -c SHA256SUMS.txt` before re-running scripts.

## Interpretation

q_BH_joint_155 is the primary decision column. Secondary within-cohort values do not replace it. Nominal intervals are not adjusted for multiplicity or candidate selection. Leave-one-out ranges are influence diagnostics, not confidence intervals. Large estimates involving zero counts are sensitive to the chosen pseudocount and relative reference. Treatment groups have three plots each; exact permutation tests still require exchangeability. The earlier community screen used the same samples, so this is exploratory follow-up, not independent validation. See REPORT.html for full qualifications and the requested soil-data handoff.
""")
    (ROOT / "requirements-used.txt").write_text(f"numpy=={np.__version__}\npandas=={pd.__version__}\nscipy=={__import__('scipy').__version__}\nmatplotlib=={matplotlib.__version__}\n")
    env = json.loads((ROOT / "ENVIRONMENT.json").read_text())
    env["matplotlib"] = matplotlib.__version__
    (ROOT / "ENVIRONMENT.json").write_text(json.dumps(env, indent=2) + "\n")


if __name__ == "__main__":
    figure_overview()
    figure_samples()
    figure_sensitivity()
    figure_effects()
    build_report()
    print("Built REPORT.html, REPORT.md, draft results, and four PNG/SVG figure pairs.")
