# Chip-Peak Quant Research Mission

## Objective

Build and honestly validate a standalone A-share chip-double-peak quantitative system for a simulated CNY 1,000,000
account.

## Hard completion criteria

- At least 36 consecutive calendar months evaluated strictly out of sample.
- Median monthly account return of at least 10% across those out-of-sample months.
- Maximum account drawdown no greater than 10% over the same out-of-sample equity curve.
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
