# Point-in-Time Factor Research for the Chip-Peak Portfolio

## Research boundary

The portfolio remains long-only, unlevered, limited to five A-share names, and subject to T+1, price limits,
suspensions, slippage, commissions, stamp tax, transfer fees, and liquidity participation. The requested 8% monthly
return is interpreted as the median of 36 consecutive out-of-sample monthly returns. Compounding 8% for 12 months is
approximately 151.8%; no experiment may manufacture that result by weakening the 15% maximum-drawdown gate.

## Primary evidence and implementation decision

| Evidence | Finding used here | Decision |
| --- | --- | --- |
| Jegadeesh and Titman, *Returns to Buying Winners and Selling Losers* | Medium-horizon relative strength has an empirical return continuation effect. | Test 20-day and 60-day momentum, with a skip window to separate it from very short reversal. |
| Novy-Marx, *The Other Side of Value* | Gross profitability contains information distinct from book-to-market. | Add profitability only with announcement-date lagging and point-in-time financial data; never backfill revised reports into earlier folds. |
| Liu, Stambaugh, and Yuan, *Size and Value in China* | China factor construction is sensitive to the smallest stocks and shell-value effects; value and profitability remain relevant. | Keep listing-age and liquidity floors, avoid rewarding microcaps, and test quality/value only inside the tradable universe. |
| Gao et al., *Market intraday momentum in the Chinese stock market* | Chinese intraday continuation/reversal depends on trading-session timing and investor type. | Treat short-horizon price behavior as a separate tactical confirmation, not a universal monthly ranking factor. |
| Arnott et al., *Transaction Costs: Practical Application* | New entrants and rebalanced trades have asymmetric implementation costs. | Add incumbent/entrant hysteresis and score improvements net of estimated trading cost. |
| Harvey, Liu, and Zhu, *... and the Cross-Section of Expected Returns* | Multiple testing makes ordinary significance thresholds unsafe in a large factor search. | Prespecify small factor families, preserve every failed experiment, and select only inside the 18+6 month research window. |

Primary sources:

- https://doi.org/10.1111/j.1540-6261.1993.tb04702.x
- https://www.nber.org/papers/w15940
- https://academic.oup.com/rfs/article/32/1/48/5060446
- https://academic.oup.com/raps/article/9/3/547/5490414
- https://www.nber.org/papers/w31635
- https://www.nber.org/papers/w20592

## Prespecified factor families

1. **Chip structure**: distance to lower peak, peak separation, valley depth, and stability across adjacent lookbacks.
2. **Trend and reversal**: 20-day momentum, 60-day momentum excluding the most recent five sessions, and a separate
   five-day reversal candidate. Momentum and reversal may not be blended until validation establishes the sign.
3. **Risk**: 20-day volatility, downside volatility, and recent maximum drawdown. Lower risk receives the favorable
   rank unless a training fold proves otherwise.
4. **Liquidity and crowding**: 20-day amount floor, amount stability, turnover level, and turnover shock. Liquidity is
   first a tradability gate; raw illiquidity may not be treated as free alpha.
5. **Quality and value**: gross profitability, ROE, operating cash flow quality, and valuation, all lagged from their
   actual announcement dates. This family stays disabled until point-in-time coverage passes.
6. **Flow and breadth**: large-order net flow, industry breadth, and market regime. These are tactical entry/risk gates,
   not substitutes for a causal monthly factor history.

## Experiment ladder

1. Reproduce the current four-factor baseline: chip proximity, 20-day momentum, low volatility, and liquidity.
2. Run single-family ablations against the baseline; reject factors that do not improve median validation return after
   costs or that worsen worst-fold drawdown.
3. Test only pairwise combinations of surviving families before any larger ensemble.
4. Add incumbent/entrant hysteresis and turnover budgets.
5. Calibrate portfolio cash exposure without leverage; reject any result that needs hidden shorting or impossible fills.
6. Lock the selected specification at each monthly cutoff, execute the next month once, and append it permanently to
   the 36-month out-of-sample artifact.

## Promotion gates

- No feature may use a value published after the selection timestamp.
- Every fold must contain exactly five buy-ready stocks or fail closed.
- A factor must improve net validation performance in more than one fold and may not rely on one symbol or one month.
- Final completion still requires the independent validator to recompute all 36 months, median monthly return, annual
  return, and maximum drawdown from saved equity.
