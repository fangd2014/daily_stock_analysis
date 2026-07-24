# Chip-Peak Quant Research Sandbox

- Research and paper trading only; no broker connectivity or live order submission.
- Long A-share cash positions and existing T+0 base-inventory simulation; no inferred leverage or short selling.
- Historical all-A daily bars, price limits, and point-in-time membership snapshots must pass coverage and quality gates.
- Each month in the 2023-07 through 2026-06 evaluation is run prequentially after an 18-month training and 6-month
  validation window; the rolling research sample is always 24 months.
- Final completion evidence contains exactly five buy-ready names per monthly rebalance; no symbol receives a special
  inclusion or exclusion rule, and single-stock results cannot complete the mission.
- Minute bars may refine execution in the paper account, but they are not required to screen thousands of historical
  candidates.
- Failed experiments remain recorded; thresholds and holdout dates may not be weakened to obtain a pass.
- The 8% monthly requirement means the median of all 36 recomputed out-of-sample monthly returns, not an average of
  selected months. At 8% monthly compounding the implied annual pace is about 152%, so leverage, omitted losses, and
  unrealistic fills are explicitly prohibited as shortcuts.
- Unrelated user files in the worktree remain outside the research scope.
