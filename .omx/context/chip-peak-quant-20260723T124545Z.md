# Autoresearch Context: Chip-Peak Quant System

## Task statement

Build an independent quantitative trading system on the `quant` branch. Start from stocks with confirmed double
volume-profile peaks, add and iteratively optimize other factors, and include explicit portfolio risk controls.

## Desired outcome

- Research target: stable 10% monthly return.
- Hard drawdown ceiling: 10% maximum drawdown.
- Persistent model-guided research loop that continues until a validator passes or evidence shows the target is not
  supportable under honest out-of-sample testing.
- A standalone system rather than a thin extension of the existing paper-trading command.

## Stated solution

- Use double chip peaks as the initial universe filter.
- Let the research loop choose, reject, and combine additional factors.
- Allow broad adjustment of other parameters and risk controls.

## Probable intent hypothesis

The user wants an autonomous research and paper-trading platform that searches for a high-return strategy while
keeping losses bounded, without requiring the user to choose factors manually.

## Known facts and evidence

- The repository already contains causal double-peak features, a five-minute execution simulator, T+1 inventory
  controls, transaction costs, walk-forward optimization, stress tests, and a locked holdout framework.
- The existing three-month daily approximation returned 13.90%, versus 13.52% for its static base benchmark. The
  strategy increment was only 0.38% of initial capital, with 8.14% maximum drawdown.
- The new `quant` branch was created from commit `3947469` and pushed to `fangd2014/daily_stock_analysis`.
- A 10% compounded monthly return is approximately 213.84% annually before costs and taxes.

## Constraints

- No look-ahead data, holdout leakage, survivorship-biased universe reconstruction, or fabricated fills.
- Include commissions, stamp tax, transfer fees, slippage, liquidity caps, price limits, suspensions, and T+1 rules.
- Research and paper trading only unless the user separately authorizes broker connectivity.
- Python 3.10+, 120-character line width, English code comments, tests, README, and changelog updates.
- Never present the return target as a guarantee.

## Unknowns and open questions

- Statistical definition of "stable 10% monthly return" and the minimum evaluation horizon.
- Whether leverage, derivatives, short selling, or only long A-shares are permitted.
- Whether the 10% requirement is arithmetic mean, geometric mean, median, or a per-month floor.
- Minimum capacity and turnover constraints beyond the existing simulated 1,000,000 CNY account.

## Decision-boundary unknowns

- The user delegated factor and risk-rule selection, but leverage and instruments could materially change both risk
  and feasibility and should not be inferred silently.
- The loop may reject the 10% target as unsupported if the locked validator fails; it may not weaken the validator to
  declare success.

## Likely codebase touchpoints

- `src/quant/strategy.py`, `src/quant/backtest.py`, `src/quant/optimize.py`, and `src/quant/report.py`
- New standalone research, factor, portfolio, risk, experiment registry, and validator modules under `src/quant/`
- New configs under `configs/quant/`, deterministic tests under `tests/`, and artifacts under `.omx/`

## Relevant repository sources inspected

- User-provided `AGENTS.md`
- `README.md` quantitative research and paper-trading sections
- `docs/CHANGELOG.md`
- Existing quant configs and implementation under `configs/quant/` and `src/quant/`

## Prompt-safe initial-context summary

Status: `not_needed`. The original request and repository evidence fit safely in the working context.
