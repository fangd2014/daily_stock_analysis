# Chip-Peak Quant Research Mission

## Objective

Build and honestly validate a standalone A-share chip-double-peak quantitative system for a simulated CNY 1,000,000
account.

The final research universe is the historical all-A market. Each out-of-sample month uses only the preceding 24 months
for training and validation and holds exactly five stocks selected from buy-ready names. `688008.SH` is eligible under
the same rules as every other stock, but no single-stock result can be used as completion evidence.

## Hard completion criteria

- At least 36 consecutive calendar months evaluated strictly out of sample.
- Exactly five portfolio constituents after each successful monthly rebalance, never more than five.
- Annualized account return of at least 15% across the out-of-sample equity curve; 20% is the preferred target, not an
  upper cap.
- Median monthly account return of at least 8% across the same consecutive out-of-sample months. This is a research
  challenge threshold, not a promised return, and it may not be weakened or replaced with a best-month statistic.
- Maximum account drawdown no greater than 15% over the same out-of-sample equity curve; 10% is the preferred risk
  target.
- No look-ahead features, holdout parameter selection, survivorship substitution, fabricated fills, or missing-month
  omission.
- Transaction costs, slippage, liquidity participation, price limits, suspensions, and A-share T+1 sellability remain
  enabled.

## Completion command

```bash
python -m src.quant.validate_research \
  --config configs/quant/quant_research_36m.json \
  --output .omx/specs/autoresearch-chip-peak-quant/result.json
```

The mission is complete only when the command writes `passed: true`. A promising training result or a shorter holdout
cannot complete the mission.
