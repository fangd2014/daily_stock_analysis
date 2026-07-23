"""Event-driven T+0 backtest engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from .broker import ExecutionBroker, Order
from .config import QuantConfig
from .data import QuantDataBundle
from .metrics import calculate_metrics
from .strategy import create_strategy, prepare_features


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    rejections: pd.DataFrame
    pairs: pd.DataFrame
    metrics: dict[str, Any]
    open_pair: bool


class BacktestEngine:
    """Simulate a base-position account and optional rolling intraday pairs."""

    def __init__(self, config: QuantConfig, strategy_enabled: bool = True, execution_delay_bars: int = 1):
        self.config = config
        self.strategy_enabled = strategy_enabled
        self.execution_delay_bars = max(1, execution_delay_bars)

    @staticmethod
    def _lookup_map(frame: pd.DataFrame, key: str) -> dict[pd.Timestamp, dict[str, Any]]:
        if frame.empty:
            return {}
        result: dict[pd.Timestamp, dict[str, Any]] = {}
        for record in frame.to_dict("records"):
            record_key = pd.Timestamp(record[key]).normalize()
            result[record_key] = record
        return result

    def run(self, bundle: QuantDataBundle, evaluation_start: str | None = None) -> BacktestResult:
        if bundle.bars.empty:
            raise ValueError("Backtest requires at least one minute bar")
        bars = prepare_features(bundle.bars, self.config.strategy, bundle.adjustments)
        if evaluation_start is not None:
            start = pd.Timestamp(evaluation_start).normalize()
            bars = bars[pd.to_datetime(bars["datetime"]) >= start].reset_index(drop=True)
        if bars.empty:
            raise ValueError("Backtest has no bars on or after evaluation_start")
        broker = ExecutionBroker(self.config.portfolio, self.config.costs, self.config.portfolio.initial_cash)
        strategy = create_strategy(self.config.strategy, self.config.portfolio.lot_size)
        limit_map = self._lookup_map(bundle.limits, "trade_date")
        dividend_map = self._lookup_map(bundle.dividends, "ex_date")
        equity_records: list[dict[str, Any]] = []
        pending_order: Optional[Order] = None
        pending_delay = 0
        current_date: Optional[pd.Timestamp] = None
        target_base_shares = 0

        rows = list(bars.itertuples(index=False))
        for index, row in enumerate(rows):
            timestamp = pd.Timestamp(row.datetime)
            trade_date = timestamp.normalize()
            if current_date is None or trade_date != current_date:
                if current_date is not None:
                    action = dividend_map.get(trade_date)
                    if action:
                        distribution = broker.ledger.apply_dividend(
                            float(action.get("cash_div", 0.0)),
                            float(action.get("stock_ratio", 0.0)),
                            self.config.portfolio.lot_size,
                        )
                        target_base_shares += int(distribution["bonus_shares"])
                    broker.ledger.settle_new_day()
                current_date = trade_date
                strategy.on_new_day(trade_date, broker.ledger.equity(float(row.open)))

            limits = limit_map.get(trade_date, {})
            if index == 0:
                desired = int(self.config.portfolio.initial_cash * self.config.portfolio.base_ratio / float(row.open))
                desired = desired // self.config.portfolio.lot_size * self.config.portfolio.lot_size
                broker.ledger.cash -= desired * float(row.open)
                broker.ledger.total_shares = desired
                broker.ledger.sellable_shares = desired
                target_base_shares = desired
            elif pending_order is not None:
                pending_delay -= 1
            if index > 0 and pending_order is not None and pending_delay <= 0:
                fill = broker.execute(
                    pending_order,
                    row,
                    limits.get("up_limit"),
                    limits.get("down_limit"),
                )
                strategy.on_fill(fill)
                pending_order = None

            if self.strategy_enabled and index < len(rows) - 1:
                next_order = strategy.on_bar(row, broker.ledger, target_base_shares)
                if pending_order is None and next_order is not None:
                    pending_order = next_order
                    pending_delay = self.execution_delay_bars

            equity_records.append(
                {
                    "datetime": timestamp,
                    "cash": broker.ledger.cash,
                    "shares": broker.ledger.total_shares,
                    "sellable_shares": broker.ledger.sellable_shares,
                    "close": float(row.close),
                    "equity": broker.ledger.equity(float(row.close)),
                }
            )

        equity = pd.DataFrame(equity_records)
        trades = pd.DataFrame([fill.to_dict() for fill in broker.fills])
        rejections = pd.DataFrame([fill.to_dict() for fill in broker.rejections])
        pairs = pd.DataFrame([pair.to_dict() for pair in strategy.closed_pairs])
        metrics = calculate_metrics(
            equity,
            trades,
            pairs,
            initial_equity=self.config.portfolio.initial_cash,
        )
        metrics["unclosed_pair"] = strategy.active_pair is not None
        metrics["rejection_count"] = len(rejections)
        metrics["total_fees"] = float(trades["total_fees"].sum()) if not trades.empty else 0.0
        metrics["t0_pair_pnl"] = float(pairs["pnl"].sum()) if not pairs.empty else 0.0
        return BacktestResult(
            equity=equity,
            trades=trades,
            rejections=rejections,
            pairs=pairs,
            metrics=metrics,
            open_pair=strategy.active_pair is not None,
        )


def slice_bundle(
    bundle: QuantDataBundle,
    start_date: str,
    end_date: str,
    warmup_days: int = 0,
) -> QuantDataBundle:
    """Create an isolated date range for walk-forward evaluation."""
    start = pd.Timestamp(start_date).normalize()
    data_start = start - pd.Timedelta(days=max(0, warmup_days))
    end = pd.Timestamp(end_date).normalize()
    bar_dates = pd.to_datetime(bundle.bars["datetime"])
    bars = bundle.bars[(bar_dates >= data_start) & (bar_dates < end + pd.Timedelta(days=1))].copy()

    def slice_daily(frame: pd.DataFrame, column: str) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()
        dates = pd.to_datetime(frame[column]).dt.normalize()
        return frame[(dates >= data_start) & (dates <= end)].copy()

    return QuantDataBundle(
        bars=bars.reset_index(drop=True),
        limits=slice_daily(bundle.limits, "trade_date"),
        adjustments=slice_daily(bundle.adjustments, "trade_date"),
        dividends=slice_daily(bundle.dividends, "ex_date"),
    )
