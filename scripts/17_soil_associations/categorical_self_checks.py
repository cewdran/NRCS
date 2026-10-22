"""Numerical categorical association checks; execute only on Puma."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from hpc_guard import require_hpc


def main():
    execution = require_hpc()
    import numpy as np
    from association_core import PermutationCache
    from categorical_core import CORE_VERSION, analyze_categorical

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    checks = []

    def check(name, passed):
        checks.append({"check": name, "passed": bool(passed)})
        if not passed:
            raise RuntimeError("Categorical self-check failed: "+name)

    units = ["plot_"+str(index) for index in range(6)]
    groups = ["A"]*3+["B"]*3
    perfect = analyze_categorical(range(1, 7), groups, units,
                                  positive=[True]*6)
    check("Two three-plot groups enumerate20 unique assignments with two tails",
          perfect["status"] == "INFERENTIAL" and
          perfect["permutation_count"] == 20 and
          perfect["unique_label_assignment_space"] == "20" and
          math.isclose(perfect["p"], 2/20, abs_tol=1e-12) and
          math.isclose(perfect["effect"], 27/35, abs_tol=1e-12))
    check("Group sizes and native-vector medians retained without row loss",
          perfect["n"] == 6 and perfect["group_sizes"] == {"A": 3, "B": 3}
          and perfect["group_medians"] == {"A": 2.0, "B": 5.0})
    renamed = analyze_categorical(range(1, 7), [99]*3+[-7]*3, units)
    check("Numeric-looking labels are categories and do not change the result",
          renamed["effect"] == perfect["effect"] and
          renamed["p"] == perfect["p"])
    tied = analyze_categorical([1, 1, 1, 2, 2, 2], groups, units)
    check("Tied features retain valid rank effect and exact allocationp",
          math.isclose(tied["effect"], 1, abs_tol=1e-12) and
          math.isclose(tied["p"], 2/20, abs_tol=1e-12))
    constant = analyze_categorical([1]*6, groups, units, positive=[True]*6)
    check("Eligible constant feature retains nullp and correctioneligibility",
          constant["status"] == "FAILED_ELIGIBLE" and
          constant["correction_eligible"] is True and constant["p"] is None)
    held = analyze_categorical(range(1, 7), groups, units,
                               positive=[True]*6, descriptive_only=True)
    check("A prespecified field-design hold retains effect without permutation inference",
          held["status"] == "DESCRIPTIVE_ONLY" and held["p"] is None and
          held["correction_eligible"] is False and
          held["permutation_count"] is None and held["effect"] == perfect["effect"])
    five = analyze_categorical(range(5), ["A"]*2+["B"]*3, units[:5])
    check("Five independent plots produce descriptiveeffect and nopvalue",
          five["status"] == "DESCRIPTIVE_ONLY" and
          five["effect"] is not None and five["p"] is None and
          five["correction_eligible"] is False)
    singleton = analyze_categorical(range(6), ["A"]+["B"]*5, units)
    check("Singleton categorical group cannot receive inferential support",
          singleton["p"] is None and
          singleton["correction_eligible"] is False and
          singleton["skip_reason"] ==
          "GROUP_HAS_FEWER_THAN_TWO_INDEPENDENT_UNITS")
    repeated = analyze_categorical(range(6), groups, ["plot_0"]*6)
    check("Repeated samples cannot multiply independent biological units",
          repeated["skip_reason"] ==
          "REPEATED_BIOLOGICAL_UNIT_WITHIN_STRATUM" and repeated["p"] is None)
    nonfinite = analyze_categorical([0, 1, 2, 3, 4, np.nan], groups, units)
    check("Nonfinite value fails without silently dropping itsplot",
          nonfinite["n"] == 6 and nonfinite["p"] is None and
          nonfinite["skip_reason"] == "NONFINITE_INPUT_NO_SILENT_DROPPING")
    missing_label = analyze_categorical(range(6), ["A"]*3+["B", "B", None],
                                        units)
    check("Missing category cannot silently lose aplot",
          missing_label["n"] == 6 and missing_label["p"] is None and
          missing_label["skip_reason"] == "MISSING_CATEGORICAL_LABEL")
    sparse = analyze_categorical(range(6), groups, units,
                                 positive=[True]*2+[False]*4)
    check("Frozen detectiongate excludes unsupported sparse features",
          sparse["skip_reason"] == "FEATURE_PREVALENCE_GATE" and
          sparse["correction_eligible"] is False)
    cache = PermutationCache(max_entries=2)
    nine_units = ["plot_"+str(index) for index in range(9)]
    nine_groups = ["A"]*4+["B"]*5
    first = analyze_categorical(range(9), nine_groups, nine_units,
                                seed_context="categorical_mc_check",
                                permutation_cache=cache)
    second = analyze_categorical(range(9), nine_groups, nine_units,
                                 seed_context="categorical_mc_check",
                                 permutation_cache=cache)
    check("MonteCarlo label permutations reproduce with nonzero plusonep",
          first["status"] == "INFERENTIAL" and
          first["permutation_mode"] == "MONTE_CARLO_PLUS_ONE" and
          first["permutation_count"] == 19_999 and
          first["unique_label_assignment_space"] == "126" and
          first["p"] == second["p"] and first["seed"] == second["seed"] and
          first["p"] >= 1/20_000)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name+".tmp")
    temporary.write_text(json.dumps({"core_version": CORE_VERSION,
                                     "execution": execution,
                                     "checks": checks, "passed": True},
                                    indent=2, allow_nan=False)+"\n")
    temporary.replace(output)
    print("CATEGORICAL_SELF_CHECKS_PASSED="+str(len(checks)))


if __name__ == "__main__":
    main()
