# Three-Strategy Factor Research V2

## Research boundary

The chip-double-peak selector remains unchanged as the benchmark. Three new selectors are preregistered before their
portfolio returns are generated. They share the same historical all-A universe, five-stock limit, CNY 1,000,000
account, costs, T+1 rule, price-limit checks, and 36-month evaluation window.

The 8% median monthly return threshold remains a challenge gate. It is not a parameter objective that permits leverage,
month omission, unrestricted grid search, or reuse of current fundamentals as historical values.

## Strategy A: quality-value

Hypothesis: inexpensive companies with strong, cash-supported profitability and moderate leverage should avoid part of
the value-trap problem. The ranking uses point-in-time `daily_basic` valuation fields and the latest financial statement
whose `ann_date` is no later than the feature cutoff.

Preregistered components:

- earnings yield and book-to-price;
- diluted ROE and ROA;
- operating-cash-flow-to-revenue and gross margin;
- debt-to-assets penalty and positive quarterly profit growth;
- tradability and minimum-amount gates.

Primary evidence:

- Liu, Stambaugh, and Yuan, *Size and Value in China*: https://doi.org/10.1093/rfs/hhy030
- Novy-Marx, *The Other Side of Value: The Gross Profitability Premium*:
  https://doi.org/10.1016/j.jfineco.2013.01.003
- Hou, Xue, and Zhang, *Digesting Anomalies: An Investment Approach*:
  https://doi.org/10.1093/rfs/hhu068

## Strategy B: industry-leader momentum

Hypothesis: medium-horizon winners are more robust when the stock, its industry, and the broad trend agree. The selector
uses only prior closes and amounts; it does not require fundamentals.

Preregistered components:

- 120-session momentum skipping the most recent five sessions;
- 20-session momentum and price above the 60-session moving average;
- industry median 60-session return and positive-industry breadth gate;
- low downside volatility, shallow recent drawdown, and liquidity.

Primary evidence:

- Jegadeesh and Titman, *Returns to Buying Winners and Selling Losers*:
  https://doi.org/10.1111/j.1540-6261.1993.tb04702.x
- Moskowitz, Ooi, and Pedersen, *Time Series Momentum*:
  https://doi.org/10.1016/j.jfineco.2011.11.003
- Liu, Stambaugh, and Yuan, China factor construction and redundant momentum evidence:
  https://doi.org/10.1093/rfs/hhy030

## Strategy C: flow-confirmed reversal

Hypothesis: a liquid stock in a positive medium-term trend can mean-revert after a short pullback when large and
extra-large order flow is net positive. Money flow is taken only from the feature-cutoff session.

Preregistered components:

- negative five-session return, but positive 60-session return and close above the 60-session moving average;
- large plus extra-large buy amount minus corresponding sell amount, normalized by traded amount;
- low downside volatility, stable traded amount, and no limit-up entry;
- fixed stop and time exit; the strategy may not silently drop the flow gate when data are missing.

Primary evidence:

- Nagel, *Evaporating Liquidity* (short-term reversal and liquidity provision):
  https://doi.org/10.1093/rfs/hhs066
- Da, Liu, and Schaumburg, *A Closer Look at the Short-Term Return Reversal*:
  https://doi.org/10.1287/mnsc.2013.1766

## Multiple-testing control

Only these three selectors and the existing chip benchmark enter the first tournament. Their formulas and default risk
policies are fixed before results are generated. Any later change is a new development iteration and cannot turn the
already observed 2023-07 through 2026-06 interval back into untouched evidence.

Factor proliferation is constrained in line with Harvey, Liu, and Zhu, *... and the Cross-Section of Expected Returns*:
https://doi.org/10.1093/rfs/hhv059
