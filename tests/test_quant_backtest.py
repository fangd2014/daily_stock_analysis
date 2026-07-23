"""End-to-end deterministic tests for the event-driven backtester."""

from dataclasses import replace

import numpy as np
import pandas as pd

from src.quant.backtest import BacktestEngine
from src.quant.config import QuantConfig, StrategyConfig
from src.quant.data import QuantDataBundle


def synthetic_bundle() -> QuantDataBundle:
    records = []
    for day_index, trade_date in enumerate(pd.bdate_range("2025-01-02", periods=30)):
        base = 100 + day_index * 0.02
        for bar_index, timestamp in enumerate(pd.date_range(f"{trade_date.date()} 09:30", periods=48, freq="5min")):
            price = base + np.sin(bar_index / 2.0) * 1.2
            records.append(
                {
                    "datetime": timestamp,
                    "symbol": "688008.SH",
                    "open": price,
                    "high": price + 0.1,
                    "low": price - 0.1,
                    "close": price,
                    "volume": 2_000_000,
                    "amount": price * 2_000_000,
                }
            )
    bars = pd.DataFrame(records)
    limits = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2025-01-02", periods=30),
            "up_limit": 200.0,
            "down_limit": 1.0,
        }
    )
    adjustments = pd.DataFrame({"trade_date": pd.bdate_range("2025-01-02", periods=30), "adj_factor": 1.0})
    return QuantDataBundle(bars, limits, adjustments, pd.DataFrame(columns=["ex_date", "cash_div", "stock_ratio"]))


def test_backtest_executes_signals_on_a_later_bar_and_restores_base():
    strategy = StrategyConfig(
        zscore_window=4,
        entry_z=0.5,
        exit_z=0.0,
        position_fraction=0.1,
        stop_loss_pct=0.02,
        max_holding_bars=8,
        max_pairs_per_day=2,
        min_edge_bps=1,
        daily_trend_limit=0.5,
        daily_volatility_limit=0.5,
    )
    config = QuantConfig(
        symbol="688008.SH",
        name="Synthetic",
        start_date="2025-01-02",
        end_date="2025-02-28",
        output_dir="reports/quant/test",
        strategy=strategy,
    )
    result = BacktestEngine(config).run(synthetic_bundle())
    pair_trades = result.trades[result.trades["action"].isin(["entry_high", "entry_low", "exit"])]

    assert not pair_trades.empty
    assert (pd.to_datetime(pair_trades["timestamp"]) > pd.to_datetime(pair_trades["signal_time"])).all()
    assert result.metrics["pair_count"] > 0
    assert result.equity.iloc[-1]["shares"] == result.equity.iloc[0]["shares"]


def test_extra_delay_moves_fills_by_at_least_two_bars():
    strategy = replace(
        StrategyConfig(),
        zscore_window=4,
        entry_z=0.5,
        exit_z=0.0,
        min_edge_bps=1,
        daily_trend_limit=0.5,
        daily_volatility_limit=0.5,
    )
    config = QuantConfig("688008.SH", "Synthetic", "2025-01-02", "2025-02-28", "reports/quant/test", strategy=strategy)
    result = BacktestEngine(config, execution_delay_bars=2).run(synthetic_bundle())
    pair_trades = result.trades[result.trades["action"].isin(["entry_high", "entry_low", "exit"])]

    assert not pair_trades.empty
    delays = pd.to_datetime(pair_trades["timestamp"]) - pd.to_datetime(pair_trades["signal_time"])
    assert (delays >= pd.Timedelta(minutes=10)).all()


def test_dynamic_base_enters_on_prior_uptrend_and_exits_after_trend_break():
    bundle = synthetic_bundle()
    trade_dates = sorted(bundle.bars["datetime"].dt.normalize().unique())
    falling_dates = set(trade_dates[-6:])
    falling = bundle.bars["datetime"].dt.normalize().isin(falling_dates)
    for column in ("open", "high", "low", "close"):
        bundle.bars.loc[falling, column] *= 0.75
    bundle.bars.loc[falling, "amount"] = bundle.bars.loc[falling, "close"] * bundle.bars.loc[falling, "volume"]
    strategy = replace(
        StrategyConfig(),
        dynamic_base_enabled=True,
        dynamic_base_trend_min=0.001,
        dynamic_base_volatility_max=0.5,
        daily_trend_limit=0.5,
        daily_volatility_limit=0.5,
    )
    config = QuantConfig(
        "688008.SH",
        "Dynamic Base",
        "2025-01-02",
        "2025-02-28",
        "reports/quant/test",
        strategy=strategy,
    )

    result = BacktestEngine(config, strategy_enabled=False).run(bundle)

    assert "base_entry" in set(result.trades["action"])
    assert "base_exit" in set(result.trades["action"])
