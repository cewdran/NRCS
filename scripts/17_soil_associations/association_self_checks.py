"""Meaningful numerical checks. Never execute locally; Puma allocation only."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from hpc_guard import require_hpc


def main():
    execution = require_hpc()
    # Import scientific modules only after the scheduler-backed guard succeeds.
    import numpy as np
    from association_core import (CORE_VERSION, PermutationCache,
                                  analyze_association, adjust_global_pvalues)

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    checks = []

    def check(name, passed):
        checks.append({"check": name, "passed": bool(passed)})
        if not passed:
            raise RuntimeError("Association self-check failed: "+name)

    units = ["plot_"+str(index) for index in range(6)]
    perfect = analyze_association(list(range(6)), list(range(6)), units,
                                  positive=[True]*6,
                                  seed_context="selfcheck_exact")
    check("Six independent monotone pairs use exact 720 assignments and two tails",
          perfect["status"] == "INFERENTIAL" and
          perfect["permutation_count"] == 720 and
          math.isclose(perfect["rho"], 1, abs_tol=1e-12) and
          math.isclose(perfect["p"], 2/720, abs_tol=1e-12))

    tied = analyze_association([1, 1, 2, 2, 3, 3], [1, 1, 2, 2, 3, 3], units,
                               seed_context="selfcheck_ties")
    check("Tied rank permutations preserve index multiplicities",
          math.isclose(tied["p"], 16/720, abs_tol=1e-12))

    tiny = analyze_association([1, 2, 3], [1, 2, 3], units[:3])
    check("Three independent pairs remain descriptive with no raw p",
          tiny["status"] == "DESCRIPTIVE_ONLY" and tiny["p"] is None and
          tiny["correction_eligible"] is False and
          math.isclose(tiny["rho"], 1, abs_tol=1e-12))

    repeated = analyze_association(range(6), range(6), ["plot_0"]*6)
    check("Repeated plot libraries cannot become independent units",
          repeated["status"] == "SKIPPED" and
          repeated["skip_reason"] == "REPEATED_BIOLOGICAL_UNIT_WITHIN_STRATUM")
    constant = analyze_association([1]*6, range(6), units)
    check("Eligible constant feature keeps p-null and stays in global family",
          constant["status"] == "FAILED_ELIGIBLE" and constant["p"] is None and
          constant["correction_eligible"] is True)
    missing = analyze_association([0, 1, 2, 3, 4, np.nan], range(6), units)
    check("Nonfinite inputs cannot silently lose a unit",
          missing["n"] == 6 and missing["status"] == "SKIPPED" and
          missing["skip_reason"] == "NONFINITE_INPUT_NO_SILENT_DROPPING")
    sparse = analyze_association(range(6), range(6), units,
                                 positive=[True, True, False, False, False, False])
    check("Presence gate prevents unsupported sparse feature inference",
          sparse["status"] == "SKIPPED" and
          sparse["skip_reason"] == "FEATURE_PREVALENCE_GATE")
    diagnostic = analyze_association(range(6), [0, 2, 1, 3, 5, 4], units,
                                     log_depth=range(6),
                                     treatment=["A"]*3+["B"]*3)
    check("Depth adjustment with no residual feature variation stays p-null",
          diagnostic["depth_diagnostic"]["status"] == "ZERO_RESIDUAL_VARIATION"
          and diagnostic["depth_diagnostic"]["p"] is None)
    check("Within-treatment sensitivity is descriptive even when rho exists",
          len(diagnostic["treatment_diagnostics"]["groups"]) == 2 and
          all(group["p"] is None for group in
              diagnostic["treatment_diagnostics"]["groups"]))

    nine_units = ["plot_"+str(index) for index in range(9)]
    cache = PermutationCache(max_entries=2)
    mc1 = analyze_association(range(9), [4, 0, 8, 2, 6, 1, 7, 3, 5], nine_units,
                              seed_context="selfcheck_mc", permutation_cache=cache)
    mc2 = analyze_association(range(9), [4, 0, 8, 2, 6, 1, 7, 3, 5], nine_units,
                              seed_context="selfcheck_mc", permutation_cache=cache)
    check("Monte Carlo reproducibility and nonzero corrected resolution",
          mc1["permutation_mode"] == "MONTE_CARLO_PLUS_ONE" and
          mc1["permutation_count"] == 19_999 and mc1["seed"] == mc2["seed"] and
          mc1["p"] == mc2["p"] and mc1["p"] >= 1/20_000)

    adjusted = adjust_global_pvalues([0.01, 0.04, 0.03, None, None],
                                     [True, True, True, True, False])
    check("Failed eligible tests enter global family as one; skips stay missing",
          adjusted["family_size"] == 4 and
          adjusted["failed_eligible_p_one"] == 1 and
          adjusted["q_bh"][3] == 1 and adjusted["q_bh"][4] is None and
          adjusted["q_by"][4] is None)
    check("Global BH and BY agree with fixed hand-calculated examples",
          math.isclose(adjusted["q_bh"][0], 0.04, abs_tol=1e-12) and
          math.isclose(adjusted["q_bh"][1], 0.16/3, abs_tol=1e-12) and
          math.isclose(adjusted["q_by"][0], 0.04*(1+1/2+1/3+1/4),
                       abs_tol=1e-12) and
          all(by >= bh for bh, by in zip(adjusted["q_bh"][:4],
                                         adjusted["q_by"][:4])))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name+".tmp")
    temporary.write_text(json.dumps({"core_version": CORE_VERSION,
                                     "execution": execution,
                                     "checks": checks, "passed": True},
                                    indent=2, allow_nan=False)+"\n")
    temporary.replace(output)
    print("ASSOCIATION_SELF_CHECKS_PASSED="+str(len(checks)))


if __name__ == "__main__":
    main()
