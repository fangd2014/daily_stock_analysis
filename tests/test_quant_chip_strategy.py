"""Deterministic tests for the chip double-peak strategy."""

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd

from src.quant.broker import PortfolioLedger
from src.quant.config import StrategyConfig
from src.quant.strategy import ChipDoublePeakStrategy, prepare_features


def _double_peak_bars() -> pd.DataFrame:
    records = []
    trade_dates = pd.bdate_range("2026-01-02", periods=26)
    for trade_date in trade_dates[:-1]:
        for minute, price, volume in (
            (0, 90.0, 1_000_000),
            (5, 90.2, 800_000),
            (10, 100.0, 50_000),
            (15, 109.8, 800_000),
            (20, 110.0, 1_000_000),
        ):
            timestamp = trade_date + pd.Timedelta(hours=9, minutes=30 + minute)
            records.append(
                {
                    "datetime": timestamp,
                    "symbol": "688008.SH",
                    "open": price,
                    "high": price + 0.05,
                    "low": price - 0.05,
                    "close": price,
                    "volume": volume,
                    "amount": price * volume,
                }
            )
    timestamp = trade_dates[-1] + pd.Timedelta(hours=10)
    records.append(
        {
            "datetime": timestamp,
            "symbol": "688008.SH",
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
            "volume": 100_000,
            "amount": 10_000_000,
        }
    )
    return pd.DataFrame(records)


def _strategy_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_type="chip_double_peak",
        chip_lookback_days=20,
        chip_min_history_days=10,
        chip_bins=24,
        chip_min_peak_separation_pct=0.08,
        daily_trend_limit=0.5,
        daily_volatility_limit=0.5,
        position_fraction=0.2,
    )


def test_prepare_features_detects_prior_day_double_peak_without_current_volume():
    bars = _double_peak_bars()

    features = prepare_features(bars, _strategy_config())
    signal = features.iloc[-1]

    assert bool(signal["chip_double_peak"])
    assert 89.0 < signal["chip_lower_peak"] < 92.0
    assert 108.0 < signal["chip_upper_peak"] < 111.0
    assert bool(signal["chip_price_between_peaks"])
    assert 0.4 < signal["chip_position"] < 0.6


def test_chip_strategy_sells_in_the_high_zone():
    strategy = ChipDoublePeakStrategy(_strategy_config(), lot_size=100)
    strategy.on_new_day(pd.Timestamp("2026-02-09"), 1_000_000.0)
    ledger = PortfolioLedger(cash=500_000.0, total_shares=5_000, sellable_shares=5_000)
    high_row = SimpleNamespace(
        datetime=pd.Timestamp("2026-02-09 10:00"),
        close=106.0,
        chip_position=0.8,
        chip_lower_peak=90.0,
        chip_upper_peak=110.0,
        chip_double_peak=True,
        chip_price_between_peaks=True,
        prior_regime_allowed=True,
        zscore=1.0,
    )

    entry = strategy.on_bar(high_row, ledger, target_base_shares=5_000)

    assert entry is not None
    assert entry.side == "sell"
    assert entry.quantity == 1_000
    assert entry.reason == "chip_upper_zone"


def test_chip_strategy_does_not_trade_without_a_confirmed_valley():
    strategy = ChipDoublePeakStrategy(_strategy_config(), lot_size=100)
    strategy.on_new_day(pd.Timestamp("2026-02-09"), 1_000_000.0)
    ledger = PortfolioLedger(cash=500_000.0, total_shares=5_000, sellable_shares=5_000)
    row = SimpleNamespace(
        datetime=pd.Timestamp("2026-02-09 10:00"),
        close=106.0,
        chip_position=0.8,
        chip_lower_peak=90.0,
        chip_upper_peak=110.0,
        chip_double_peak=False,
        chip_price_between_peaks=True,
        prior_regime_allowed=True,
        zscore=1.0,
    )

    assert strategy.on_bar(row, ledger, target_base_shares=5_000) is None


def test_chip_strategy_applies_direction_and_vwap_confirmation_filters():
    config = _strategy_config()
    config = replace(config, chip_enable_high_sell=False, chip_vwap_confirmation_z=1.5)
    strategy = ChipDoublePeakStrategy(config, lot_size=100)
    strategy.on_new_day(pd.Timestamp("2026-02-09"), 1_000_000.0)
    ledger = PortfolioLedger(cash=500_000.0, total_shares=5_000, sellable_shares=5_000)
    row = SimpleNamespace(
        datetime=pd.Timestamp("2026-02-09 10:00"),
        close=106.0,
        chip_position=0.8,
        chip_lower_peak=90.0,
        chip_upper_peak=110.0,
        chip_double_peak=True,
        chip_price_between_peaks=True,
        prior_regime_allowed=True,
        zscore=2.0,
    )

    assert strategy.on_bar(row, ledger, target_base_shares=5_000) is None
