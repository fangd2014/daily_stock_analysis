"""Causality, execution, and risk tests for ETF momentum rotation."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from src.quant.etf_momentum import (
    ETFMomentumConfig,
    ETFSpec,
    TickDBETFProvider,
    TushareETFProvider,
    _candidate_table,
    run_backtest,
)


def _config(start: pd.Timestamp, end: pd.Timestamp, universe: tuple[ETFSpec, ...]) -> ETFMomentumConfig:
    return ETFMomentumConfig(
        name="ETF momentum test",
        data_source="tickdb",
        rebalance_frequency="weekly",
        initial_cash=1_000_000,
        backtest_start=start.strftime("%Y-%m-%d"),
        backtest_end=end.strftime("%Y-%m-%d"),
        data_start="2024-01-01",
        max_positions=min(3, len(universe)),
        max_per_asset_class=1,
        target_exposure=0.60,
        reduced_exposure=0.30,
        momentum_windows=(20, 60, 120, 250),
        momentum_weights=(0.20, 0.35, 0.30, 0.15),
        trend_ma_window=120,
        volatility_window=60,
        drawdown_window=120,
        volatility_penalty=0.20,
        drawdown_penalty=0.10,
        single_stop_loss=0.07,
        trailing_stop=0.06,
        portfolio_reduce_drawdown=0.08,
        portfolio_stop_drawdown=0.12,
        risk_cooldown_sessions=20,
        max_entry_gap=0.03,
        commission_rate=0.0001,
        minimum_commission=5.0,
        slippage_rate=0.0003,
        lot_size=100,
        cache_dir="unused",
        report_dir="unused",
        universe=universe,
    )


def _bars(specs: tuple[ETFSpec, ...], dates: pd.DatetimeIndex) -> pd.DataFrame:
    frames = []
    for index, spec in enumerate(specs):
        daily_growth = 1.0010 + index * 0.0002
        close = 100.0 * np.cumprod(np.full(len(dates), daily_growth))
        frames.append(
            pd.DataFrame(
                {
                    "symbol": spec.symbol,
                    "date": dates,
                    "open": close / daily_growth,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1_000_000,
                    "quote_volume": close * 1_000_000,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _monday_after_history(dates: pd.DatetimeIndex) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    fridays = [date for date in dates[250:-2] if date.weekday() == 4]
    signal_date = fridays[0]
    start = dates[dates.get_loc(signal_date) + 1]
    following = dates[dates.get_loc(start) + 1]
    return signal_date, start, following


def test_tickdb_daily_dates_are_timezone_naive():
    timestamp = int(pd.Timestamp("2026-07-24 07:00:00", tz="UTC").timestamp() * 1000)
    payload = {
        "klines": [
            {
                "time": timestamp,
                "open": "1.0",
                "high": "1.1",
                "low": "0.9",
                "close": "1.05",
                "volume": "100",
                "quote_volume": "105",
            }
        ]
    }

    result = TickDBETFProvider._normalize("510300.SH", payload)

    assert result.loc[0, "date"] == pd.Timestamp("2026-07-24")
    assert result["date"].dt.tz is None


def test_tushare_adjustment_removes_a_two_for_one_split():
    daily = pd.DataFrame(
        {
            "trade_date": ["20260724", "20260723"],
            "open": [10.0, 20.0],
            "high": [10.2, 20.4],
            "low": [9.8, 19.6],
            "close": [10.0, 20.0],
            "vol": [200.0, 100.0],
            "amount": [2.0, 2.0],
        }
    )
    adjustment = pd.DataFrame(
        {"trade_date": ["20260724", "20260723"], "adj_factor": [2.0, 1.0]}
    )

    result = TushareETFProvider._normalize("TEST.SH", daily, adjustment)

    assert result["close"].tolist() == [10.0, 10.0]


def test_candidate_selection_is_diversified_and_rejects_stale_data():
    dates = pd.bdate_range("2024-01-02", periods=280)
    specs = (
        ETFSpec("A.SH", "A", "equity"),
        ETFSpec("B.SH", "B", "equity"),
        ETFSpec("C.SH", "C", "gold"),
        ETFSpec("D.SH", "D", "bond"),
    )
    config = _config(dates[-10], dates[-1], specs)
    bars = _bars(specs, dates)
    bars = bars[~((bars["symbol"] == "D.SH") & (bars["date"] == dates[-1]))]

    candidates = _candidate_table(bars, config, dates[-1])
    selected = [item for item in candidates if item.selected]

    assert len(selected) == 2
    assert len({item.asset_class for item in selected}) == len(selected)
    assert "D.SH" not in {item.symbol for item in candidates}


def test_signal_executes_at_next_session_open_and_gap_limit_skips_entry():
    dates = pd.bdate_range("2024-01-02", periods=280)
    specs = (ETFSpec("A.SH", "A", "equity"),)
    signal_date, start, following = _monday_after_history(dates)
    bars = _bars(specs, dates)
    prior_close = float(bars.loc[bars["date"].eq(signal_date), "close"].iloc[0])
    bars.loc[bars["date"].eq(start), "open"] = prior_close * 1.02
    config = replace(_config(start, following, specs), max_positions=1)

    result = run_backtest(bars, config)

    buys = result["trades"][result["trades"]["side"].eq("buy")]
    assert buys.iloc[0]["date"] == start.strftime("%Y-%m-%d")
    assert buys.iloc[0]["price"] == pytest.approx(prior_close * 1.02 * (1 + config.slippage_rate))

    gapped = bars.copy()
    gapped.loc[gapped["date"].eq(start), "open"] = prior_close * 1.031
    skipped = run_backtest(gapped, config)

    assert skipped["trades"].empty


def test_stop_loss_exits_on_next_session_even_without_weekly_rebalance():
    dates = pd.bdate_range("2024-01-02", periods=280)
    specs = (ETFSpec("A.SH", "A", "equity"),)
    _, start, following = _monday_after_history(dates)
    bars = _bars(specs, dates)
    open_price = float(bars.loc[bars["date"].eq(start), "open"].iloc[0])
    bars.loc[bars["date"].eq(start), ["low", "close"]] = open_price * 0.90
    bars.loc[bars["date"].eq(following), "open"] = open_price * 0.89
    config = replace(_config(start, following, specs), max_positions=1)

    result = run_backtest(bars, config)
    trades = result["trades"]

    assert list(trades["side"]) == ["buy", "sell"]
    assert trades.iloc[1]["date"] == following.strftime("%Y-%m-%d")
    assert trades.iloc[1]["reason"] == "risk_or_weekly_rebalance"


def test_portfolio_cooldown_resets_high_water_mark_and_allows_reentry():
    dates = pd.bdate_range("2024-01-02", periods=290)
    specs = (ETFSpec("A.SH", "A", "equity"),)
    _, start, _ = _monday_after_history(dates)
    end = dates[dates.get_loc(start) + 12]
    bars = _bars(specs, dates)
    open_price = float(bars.loc[bars["date"].eq(start), "open"].iloc[0])
    bars.loc[bars["date"].eq(start), ["low", "close"]] = open_price * 0.96
    config = replace(
        _config(start, end, specs),
        max_positions=1,
        single_stop_loss=0.50,
        trailing_stop=0.50,
        portfolio_reduce_drawdown=0.01,
        portfolio_stop_drawdown=0.02,
        risk_cooldown_sessions=2,
    )

    result = run_backtest(bars, config)
    buys = result["trades"][result["trades"]["side"].eq("buy")]
    equity = result["equity"]
    independently_computed = abs(float((equity["equity"] / equity["equity"].cummax() - 1.0).min()))

    assert len(buys) >= 2
    assert pd.Timestamp(buys.iloc[1]["date"]) > start + pd.Timedelta(days=2)
    assert result["metrics"]["max_drawdown"] == pytest.approx(independently_computed)
    assert "risk_drawdown" in equity
