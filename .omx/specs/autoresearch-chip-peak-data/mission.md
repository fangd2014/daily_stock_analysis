# PyTDX Data Foundation Mission

## Objective

Implement a fail-closed PyTDX minute-data provider for the standalone chip-peak quantitative research system.

## Acceptance criteria

- Probe and rank the installed PyTDX endpoint catalog and cache healthy nodes with a TTL.
- Respect the 800-bar protocol limit and continue from the same offset when a node fails.
- Cache normalized bars in monthly Parquet partitions and refresh active ranges incrementally.
- Calibrate PyTDX volume and amount units against Tushare daily totals.
- Reject duplicates, invalid OHLC rows, incomplete trading days, and cross-source close mismatches.
- Record an explicit active source when Tushare or local data is used as a fallback.
- Pass all deterministic quantitative tests without network access.

## Scope boundary

This mission validates only the market-data foundation. It does not claim that the strategy return target has been
met. The broader factor research remains gated by walk-forward and holdout evidence.
