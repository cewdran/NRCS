#!/usr/bin/env python3
"""Deterministic within-library rarefaction and exhaustive one-way PERMANOVA diagnostics.

Run: python3 analyze.py
No R installation or random sampling is needed. No sample is removed from a primary analysis.
"""
from pathlib import Path
from itertools import combinations, permutations
from math import comb, factorial
import json
import platform
import sys

import numpy as np
import pandas as pd
import scipy
from scipy.special import gammaln
from scipy.spatial.distance import pdist, squareform
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent
FARMS = ["OatmanFlatsRanch", "PageSpringsCellars", "Whipstone"]
DOMAINS = ["archaea", "bacteria"]
FARM_LABEL = {"OatmanFlatsRanch":"Oatman Flats Ranch", "PageSpringsCellars":"Page Springs Cellars", "Whipstone":"Whipstone"}
COLORS = {"ControlFertigationOnly":"#355C9B", "FishManure":"#21918C", "HorseManurePomace":"#A46A25", "SteerManure":"#7651A0"}
TREAT_LABEL = {"ControlFertigationOnly":"Control (fertigation)", "FishManure":"Fish manure", "HorseManurePomace":"Horse manure + pomace", "SteerManure":"Steer manure"}
for folder in ["tables", "figures"]:
    (ROOT / folder).mkdir(exist_ok=True)
plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":10, "axes.spines.top":False,
                     "axes.spines.right":False, "figure.facecolor":"white", "savefig.facecolor":"white"})


def expected_richness(counts, depths):
    """Exact expectation S(m)=sum[1-C(N-n_i,m)/C(N,m)], interpolation only."""
    x = np.asarray(counts, dtype=np.int64)
    assert (x >= 0).all()
    x = x[x > 0]
    n = int(x.sum())
    depths = np.asarray(depths, dtype=np.int64)
    assert ((depths >= 0) & (depths <= n)).all()
    out = []
    for m in depths:
        if m == 0:
            out.append(0.0)
            continue
        if m == n:
            out.append(float(len(x)))
            continue
        # Probability of omission is zero whenever fewer than m other reads exist.
        valid = n - x >= m
        omission = np.zeros(len(x), dtype=float)
        z = x[valid]
        logp = (gammaln(n-z+1) - gammaln(n-z-m+1)
                - gammaln(n+1) + gammaln(n-m+1))
        omission[valid] = np.exp(np.minimum(logp, 0.0))
        out.append(float(np.sum(1 - omission)))
    return np.asarray(out)


def ss_statistics(distance, labels):
    labels = np.asarray(labels)
    n = len(labels)
    groups = [np.flatnonzero(labels == g) for g in sorted(set(labels))]
    dsq = np.asarray(distance)**2
    total = dsq.sum() / (2*n)
    within = sum(dsq[np.ix_(g, g)].sum()/(2*len(g)) for g in groups)
    between = total-within
    return {"n_samples":n, "n_groups":len(groups), "total_SS":total,
            "within_SS":within, "between_SS":between, "R2":between/total,
            "pseudo_F":(between/(len(groups)-1))/(within/(n-len(groups)))}


def exact_permanova(distance, labels):
    """All distinct assignments of fixed group sizes; original assignment included.

    Equal-weight labelled allocations, p=number(F_perm>=F_obs)/number(all).
    Counts equal those from all n! row permutations after cancelling repeated labels.
    Intended only for the small, one-date, independent-plot cohort in this package.
    """
    labels = np.asarray(labels)
    stat = ss_statistics(distance, labels)
    n = len(labels)
    sizes = [int(np.sum(labels == g)) for g in sorted(set(labels))]
    dsq = np.asarray(distance)**2
    costs = {}
    for size in set(sizes):
        for group in combinations(range(n), size):
            mask = sum(1 << i for i in group)
            costs[mask] = sum(dsq[i,j] for i,j in combinations(group,2))/size
    tolerance = 1e-12 * max(1, abs(stat["total_SS"]))
    count = extreme = 0

    def visit(remaining, level, subtotal):
        nonlocal count, extreme
        if level == len(sizes)-1:
            mask = sum(1 << i for i in remaining)
            score = subtotal + costs[mask]
            count += 1
            extreme += score <= stat["within_SS"]+tolerance
            return
        for group in combinations(remaining, sizes[level]):
            mask = sum(1 << i for i in group)
            rest = tuple(i for i in remaining if not (mask & (1 << i)))
            visit(rest, level+1, subtotal+costs[mask])

    visit(tuple(range(n)), 0, 0.0)
    expected = factorial(n)
    for size in sizes:
        expected //= factorial(size)
    assert count == expected
    assert 1 <= extreme <= count
    return dict(stat, assignments=count, extreme_assignments=int(extreme),
                exact_p_unadjusted=extreme/count, comparison_tolerance=tolerance)


def validate_algorithms():
    x = [1,2,4,6]
    n = sum(x)
    want = [sum(1-(comb(n-c,m)/comb(n,m) if n-c>=m else 0) for c in x) for m in range(n+1)]
    got = expected_richness(x, range(n+1))
    assert np.allclose(got,want,rtol=0,atol=1e-12)
    points = np.array([[0,0], [0.4,1], [1,2], [2,1], [2.5,4], [3,3]])
    labels = np.array([0,0,1,1,2,2])
    distance = squareform(pdist(points))
    result = exact_permanova(distance,labels)
    brute = [ss_statistics(distance, lab)["pseudo_F"] for lab in set(permutations(labels))]
    brute_p = np.mean(np.array(brute)>=result["pseudo_F"]-1e-12)
    assert np.isclose(brute_p,result["exact_p_unadjusted"],atol=1e-14)
    total = np.sum((points-points.mean(axis=0))**2)
    within = sum(np.sum((points[labels==g]-points[labels==g].mean(axis=0))**2) for g in set(labels))
    assert np.isclose(result["total_SS"],total,atol=1e-12)
    assert np.isclose(result["within_SS"],within,atol=1e-12)
    return {"rarefaction_vs_integer_combinations":"PASS", "exact_permutation_vs_brute_force":"PASS",
            "euclidean_SS_vs_direct_ANOVA":"PASS"}


def sampling_diagnostics():
    sample_rows, curve_rows, fixed_rows, loaded = [], [], [], {}
    for domain in DOMAINS:
        counts = pd.read_csv(ROOT/f"inputs/{domain}_counts.tsv",sep="\t",index_col=0)
        assert counts.index.is_unique and counts.columns.is_unique
        assert np.isfinite(counts.to_numpy()).all()
        assert (counts.to_numpy()>=0).all()
        assert np.array_equal(counts.to_numpy(),counts.to_numpy().astype(np.int64))
        metadata = pd.concat([pd.read_csv(ROOT/f"inputs/{f}_{domain}_metadata.tsv",sep="\t") for f in FARMS],ignore_index=True)
        assert metadata.sample_name.is_unique and set(metadata.sample_name)==set(counts.index)
        metadata = metadata.set_index("sample_name").loc[counts.index].rename_axis("sample_name").reset_index()
        assert np.array_equal(metadata.target.to_numpy(),counts.sum(axis=1).to_numpy())
        assert not set(["C108","C111","D108","D111"]) & set(counts.index)
        loaded[domain] = (counts, metadata)
        for _, row in metadata.iterrows():
            sample = row.sample_name
            x = counts.loc[sample].to_numpy(dtype=np.int64)
            n, observed = int(x.sum()), int(np.count_nonzero(x))
            singles, doubles = int(np.sum(x==1)), int(np.sum(x==2))
            fields = {k:row[k] for k in ["farm","sample_name","cohort","treatment_label","plot_id","collection_date"]}
            fields.update(domain=domain)
            stats = dict(fields, target_reads=n, observed_ASVs=observed, singleton_ASVs=singles, doubleton_ASVs=doubles,
                         good_coverage_descriptive=1-singles/n if n else np.nan,
                         zero_singletons=singles==0, positive_target=n>0)
            if n:
                half, ninety = int(n//2), int(np.floor(0.9*n))
                ehalf, e90 = expected_richness(x,[half,ninety])
                stats.update(half_depth=half, expected_ASVs_half=ehalf, fraction_observed_ASVs_half=ehalf/observed,
                             ninety_percent_depth=ninety, expected_ASVs_ninety_percent=e90,
                             fraction_observed_ASVs_ninety_percent=e90/observed)
                depths = np.unique(np.r_[0, np.geomspace(1,n,90).astype(int),np.linspace(1,n,45).astype(int),
                                         [d for d in [100,500,998,1000,2000,5000,10000,half,ninety,n] if d<=n]])
                values = expected_richness(x,depths)
                assert np.all(np.diff(values)>=-1e-6)
                assert np.isclose(values[-1],observed)
                assert ((values>=-1e-6)&(values<=observed+1e-6)).all()
                for depth,value in zip(depths,values):
                    curve_rows.append(dict(fields,subsample_reads=int(depth),expected_ASVs=float(value)))
            for depth in [100,500,998,1000,2000,5000,10000]:
                eligible = n>=depth
                value = expected_richness(x,[depth])[0] if eligible else np.nan
                fixed_rows.append(dict(fields,subsample_reads=depth,eligible=eligible,expected_ASVs=value,
                                       fraction_observed_ASVs=value/observed if eligible else np.nan))
            sample_rows.append(stats)
    samples=pd.DataFrame(sample_rows)
    curves=pd.DataFrame(curve_rows)
    fixed=pd.DataFrame(fixed_rows)
    samples.to_csv(ROOT/"tables/sample_diagnostics.csv",index=False)
    curves.to_csv(ROOT/"tables/rarefaction_curves.csv",index=False)
    fixed.to_csv(ROOT/"tables/fixed_depth_richness.csv",index=False)
    flags=samples[samples.target_reads.lt(100)].copy()
    flags["review_reason"]=np.where(flags.target_reads.eq(0),"No target reads; already excluded from positive-target analyses",
                                   "Fewer than 100 target reads; inspect raw QC and report the existing depth sensitivity")
    flags.to_csv(ROOT/"tables/very_low_depth_review_flags.csv",index=False)
    summary=[]
    for domain in DOMAINS:
        for farm in ["ALL"]+FARMS:
            sub=samples[samples.domain.eq(domain)&(True if farm=="ALL" else samples.farm.eq(farm))]
            pos=sub[sub.positive_target]
            summary.append({"domain":domain,"farm":farm,"n_approved":len(sub),"n_positive":len(pos),
                            "median_target_reads_positive":pos.target_reads.median(),"min_positive_reads":pos.target_reads.min(),
                            "median_observed_ASVs_positive":pos.observed_ASVs.median(),
                            "median_fraction_observed_at_half_depth":pos.fraction_observed_ASVs_half.median(),
                            "q25_fraction_observed_at_half_depth":pos.fraction_observed_ASVs_half.quantile(.25),
                            "q75_fraction_observed_at_half_depth":pos.fraction_observed_ASVs_half.quantile(.75),
                            "median_good_coverage_descriptive":pos.good_coverage_descriptive.median(),
                            "n_positive_zero_singletons":int(pos.zero_singletons.sum())})
    pd.DataFrame(summary).to_csv(ROOT/"tables/sampling_summary.csv",index=False)
    return loaded,samples,curves,fixed


def influence_diagnostics(loaded,samples):
    counts,md=loaded["archaea"]
    cohort=md[md.farm.eq("PageSpringsCellars")&md.cohort.eq("Grape_PetiteSirah__2023")].copy().sort_values("sample_name")
    assert len(cohort)==12 and cohort.collection_date.nunique()==1
    assert cohort.plot_id.is_unique
    assert cohort.groupby("treatment_label").size().eq(3).all()
    assert not cohort.descriptive_reference.any()
    names=cohort.sample_name.to_numpy()
    x=counts.loc[names].to_numpy(dtype=float)
    rel=x/x.sum(axis=1,keepdims=True)
    distance=squareform(pdist(rel,metric="braycurtis"))
    labels=cohort.treatment_label.to_numpy()
    pd.DataFrame(distance,index=names,columns=names).to_csv(ROOT/"tables/C012_cohort_bray_curtis.csv",index_label="sample_name")
    reltable=pd.DataFrame(rel,index=names,columns=counts.columns)
    reltable.loc[:,reltable.sum(axis=0)>0].to_csv(ROOT/"tables/C012_cohort_relative_abundance.csv",index_label="sample_name")
    rows=[]
    for omit in [None]+list(names):
        keep=np.ones(len(names),dtype=bool) if omit is None else names!=omit
        stat=exact_permanova(distance[np.ix_(keep,keep)],labels[keep])
        if omit is None:
            omitted_treatment="none"
            omitted_reads=np.nan
        else:
            omitted_treatment=cohort.set_index("sample_name").loc[omit,"treatment_label"]
            omitted_reads=int(counts.loc[omit].sum())
        stat.update(omitted_sample="none" if omit is None else omit,omitted_treatment=omitted_treatment,omitted_reads=omitted_reads)
        rows.append(stat)
        print(f"Omit {stat['omitted_sample']}: R2={stat['R2']:.6f}; exact p={stat['exact_p_unadjusted']:.6f}; assignments={stat['assignments']}",flush=True)
    result=pd.DataFrame(rows)
    result["delta_R2_vs_full"]=result.R2-result.iloc[0].R2
    result.to_csv(ROOT/"tables/C012_cohort_leave_one_out.csv",index=False)
    distances=[]
    for i,name in enumerate(names):
        same=np.flatnonzero((labels==labels[i])&(names!=name))
        other=np.flatnonzero(labels!=labels[i])
        distances.append({"sample_name":name,"mean_distance_same_treatment":float(distance[i,same].mean()),
                          "mean_distance_other_treatments":float(distance[i,other].mean()),
                          "same_treatment_comparisons":len(same)})
    cohort_out=samples[samples.domain.eq("archaea")&samples.sample_name.isin(names)].merge(pd.DataFrame(distances),on="sample_name")
    cohort_out.to_csv(ROOT/"tables/C012_cohort_sample_diagnostics.csv",index=False)
    fixed=pd.read_csv(ROOT/"tables/fixed_depth_richness.csv")
    fixed[fixed.domain.eq("archaea")&fixed.sample_name.isin(names)&fixed.subsample_reads.eq(998)].to_csv(
        ROOT/"tables/C012_cohort_expected_richness_at_998.csv",index=False)
    validation=[]
    for depth,omit in [(0,"none"),(1000,"C012")]:
        ref=pd.read_csv(ROOT/f"reference/vegan_depth_{depth}_permanova.tsv",sep="\t").set_index("term").loc["treatment"]
        got=result[result.omitted_sample.eq(omit)].iloc[0]
        for key,col in [("R2","R2"),("pseudo_F","F"),("between_SS","SumOfSqs")]:
            error=abs(float(got[key])-float(ref[col]))
            assert error<1e-11
            validation.append({"omitted_sample":omit,"statistic":key,"python":got[key],"vegan":ref[col],"absolute_error":error})
    pd.DataFrame(validation).to_csv(ROOT/"tables/validation_against_vegan.csv",index=False)
    return result,cohort_out


def save_figure(fig,stem):
    fig.savefig(ROOT/f"figures/{stem}.png",dpi=190,bbox_inches="tight")
    fig.savefig(ROOT/f"figures/{stem}.svg",bbox_inches="tight")
    plt.close(fig)


def figures(samples,curves,influence,cohort):
    fig,axes=plt.subplots(3,2,figsize=(13,11),sharex="col",sharey="col")
    for row,farm in enumerate(FARMS):
        for col,domain in enumerate(DOMAINS):
            ax=axes[row,col]
            sub=curves[curves.domain.eq(domain)&curves.farm.eq(farm)]
            for name,group in sub.groupby("sample_name"):
                special=domain=="archaea" and name=="C012"
                ax.plot(group.subsample_reads,group.expected_ASVs,color="#C33D3D" if special else ("#397397" if col==0 else "#218C76"),
                        alpha=1 if special else .22,lw=2.3 if special else .9)
            n=samples[samples.domain.eq(domain)&samples.farm.eq(farm)&samples.positive_target].shape[0]
            ax.set_title(f"{FARM_LABEL[farm]} · {domain} (n={n})",loc="left",fontsize=11,fontweight="bold")
            ax.set_xscale("symlog",linthresh=100)
            ax.grid(alpha=.15)
            ax.set_ylabel("Expected retained ASVs")
            if row==2:ax.set_xlabel("Target reads sampled (linear to 100; log above)")
            ax.set_ylim(0,1.06*samples.loc[samples.domain.eq(domain),"observed_ASVs"].max())
    fig.suptitle("Within-library rarefaction across the NRCS samples",fontsize=18,x=.07,ha="left")
    fig.text(.07,.935,"Each curve ends at its observed library size. C012 is red. Domain-specific y scales.",fontsize=11)
    fig.text(.07,.012,"Conditional on the denoised, target-filtered ASV tables; curve flattening does not establish complete soil diversity.",fontsize=10,color="#555555")
    fig.tight_layout(rect=(0,.035,1,.925),h_pad=2)
    save_figure(fig,"01_all_sample_rarefaction")

    fig,axes=plt.subplots(1,2,figsize=(13,5.5))
    for ax,domain in zip(axes,DOMAINS):
        sub=samples[samples.domain.eq(domain)&samples.positive_target]
        for farm,color in zip(FARMS,["#355C9B","#A46A25","#21918C"]):
            g=sub[sub.farm.eq(farm)]
            ax.scatter(g.target_reads,100*g.fraction_observed_ASVs_half,s=26,color=color,alpha=.7,label=FARM_LABEL[farm])
        if domain=="archaea":
            c=sub[sub.sample_name.eq("C012")].iloc[0]
            ax.scatter(c.target_reads,100*c.fraction_observed_ASVs_half,s=115,facecolors="none",edgecolors="#C33D3D",linewidths=2)
            ax.annotate("C012",(c.target_reads,100*c.fraction_observed_ASVs_half),xytext=(-6,-20),textcoords="offset points",color="#B32D2D",ha="right")
        ax.set_xscale("log")
        ax.set_title(domain.capitalize(),loc="left",fontweight="bold")
        ax.set_xlabel("Observed target reads (log scale)")
        ax.set_ylabel("Observed ASVs expected at half depth (%)")
        ax.set_ylim(0,102)
        ax.grid(alpha=.15)
    axes[1].legend(loc="lower right",frameon=False,fontsize=9)
    fig.suptitle("How much of each retained ASV library is recovered at half depth?",fontsize=16,x=.07,ha="left")
    fig.text(.07,.012,"This proportion uses observed ASVs as its denominator. It is not the proportion of true community richness sampled.",fontsize=10,color="#555555")
    fig.tight_layout(rect=(0,.035,1,.94))
    save_figure(fig,"02_half_depth_richness")

    fig,axes=plt.subplots(1,3,figsize=(16,6.6),gridspec_kw={"width_ratios":[1.05,1,1]})
    ax=axes[0]
    sub=curves[curves.domain.eq("archaea")&curves.sample_name.isin(cohort.sample_name)]
    for name,g in sub.groupby("sample_name"):
        row=cohort.set_index("sample_name").loc[name]
        special=name=="C012"
        ax.plot(g.subsample_reads,g.expected_ASVs,color="#C33D3D" if special else COLORS[row.treatment_label],alpha=1 if special else .55,lw=2.6 if special else 1.1)
    c=cohort.set_index("sample_name").loc["C012"]
    ax.scatter(c.target_reads,c.observed_ASVs,color="#C33D3D",s=35,zorder=5)
    ax.annotate(f"C012: {c.target_reads:,} reads\n{c.observed_ASVs} ASVs",(c.target_reads,c.observed_ASVs),xytext=(1600,c.observed_ASVs-8),arrowprops={"arrowstyle":"-","color":"#C33D3D"},color="#B32D2D",fontsize=9)
    ax.set_xlabel("Target reads sampled")
    ax.set_ylabel("Expected retained ASVs")
    ax.set_title("A  Rarefaction in this cohort",loc="left",fontweight="bold")
    ax.grid(alpha=.15)
    handles=[Line2D([0],[0],color=COLORS[k],label=TREAT_LABEL[k],lw=2) for k in COLORS]
    ax.legend(handles=handles,fontsize=8,frameon=False,loc="lower right")
    loo=influence[influence.omitted_sample.ne("none")].sort_values("delta_R2_vs_full",ascending=False)
    y=np.arange(len(loo))
    colors=["#C33D3D" if s=="C012" else "#507087" for s in loo.omitted_sample]
    baseline=influence.iloc[0]
    for ax,col,base,title,xlabel in [(axes[1],"R2",baseline.R2,"B  Treatment R² after omission","PERMANOVA R²"),
                                   (axes[2],"exact_p_unadjusted",baseline.exact_p_unadjusted,"C  Exact p after omission","Unadjusted exact p (log scale)")]:
        ax.scatter(loo[col],y,c=colors,s=50,zorder=3)
        ax.axvline(base,color="#333333",ls="--",lw=1.2,label="All 12 samples")
        ax.set_yticks(y,loo.omitted_sample)
        ax.invert_yaxis()
        ax.set_ylabel("Sample omitted")
        ax.set_xlabel(xlabel)
        ax.set_title(title,loc="left",fontweight="bold")
        ax.grid(axis="x",alpha=.15)
        ax.legend(frameon=False,fontsize=9,loc="lower right")
    axes[2].set_xscale("log")
    axes[1].set_xlim(.28,.62)
    fig.suptitle("Sample influence in the Petite Sirah 2023 archaeal cohort",fontsize=17,x=.04,ha="left")
    fig.text(.04,.919,"Page Springs Cellars · 12 plots · 4 treatments · one collection date · each omission evaluated separately",fontsize=11)
    fig.text(.04,.025,"Exact p values enumerate all fixed-size treatment allocations. These are influence diagnostics, not new confirmatory tests.\nAll samples remain in the primary analysis; removing an influential sample requires an independent quality reason.",fontsize=10,color="#555555")
    fig.tight_layout(rect=(0,.075,1,.90),w_pad=2)
    save_figure(fig,"03_C012_influence")


def main():
    validations=validate_algorithms()
    loaded,samples,curves,fixed=sampling_diagnostics()
    influence,cohort=influence_diagnostics(loaded,samples)
    validations.update({"count_metadata_alignment":"PASS", "integer_nonnegative_counts":"PASS",
                        "rarefaction_endpoints_and_monotonicity":"PASS", "one_date_independent_plot_design":"PASS",
                        "full_and_C012_omitted_statistics_vs_vegan":"PASS", "all_permutation_allocation_counts":"PASS",
                        "no_primary_sample_removal":True, "not_performed":"No local R execution; p values are Python exhaustive tests, not the original R Monte Carlo values."})
    (ROOT/"VALIDATION.json").write_text(json.dumps(validations,indent=2)+"\n")
    (ROOT/"environment.json").write_text(json.dumps({"python":sys.version,"platform":platform.platform(),
        "numpy":np.__version__,"pandas":pd.__version__,"scipy":scipy.__version__,"matplotlib":matplotlib.__version__},indent=2)+"\n")
    figures(samples,curves,influence,cohort)
    print("All calculations and verification completed.",flush=True)
    print(pd.read_csv(ROOT/"tables/sampling_summary.csv").query("farm == 'ALL'").to_string(index=False),flush=True)
    print(cohort[["sample_name","target_reads","observed_ASVs","singleton_ASVs","fraction_observed_ASVs_half","mean_distance_same_treatment"]].to_string(index=False),flush=True)


if __name__=="__main__":
    main()
