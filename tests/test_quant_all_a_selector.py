"""Tests for causal all-A five-stock portfolio selection."""

from dataclasses import replace

import pandas as pd

from src.quant.all_a_selector import FactorWeights, select_monthly_portfolio
from src.quant.config import PortfolioConfig, QuantConfig, StrategyConfig, UniverseConfig
from src.quant.factor_experiments import preregistered_factor_experiments, validate_experiment_registry


def make_config() -> QuantConfig:
    return QuantConfig(
        symbol="ALL_A_PORTFOLIO",
        name="All-A Test",
        start_date="2021-07-01",
        end_date="2026-06-30",
        output_dir="reports/quant/test",
        portfolio=PortfolioConfig(max_positions=5),
        universe=UniverseConfig(
            scope="all_a",
            min_listing_days=250,
            min_average_amount=0.0,
        ),
        strategy=StrategyConfig(
            strategy_type="chip_double_peak",
            chip_lookback_days=60,
            chip_min_history_days=20,
            chip_bins=48,
            chip_low_entry_position=0.28,
        ),
    )


def make_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    symbols = ["000001.SZ", "000002.SZ", "300001.SZ", "600000.SH", "600001.SH", "688001.SH", "688008.SH"]
    history_dates = pd.bdate_range("2023-03-31", periods=61)
    selection_date = pd.bdate_range(history_dates[-1] + pd.Timedelta(days=1), periods=1)[0]
    records = []
    for symbol_index, symbol in enumerate(symbols):
        for index, trade_date in enumerate(history_dates):
            if index % 3 == 0:
                price, volume = 90.0, 1_000_000.0
            elif index % 3 == 1:
                price, volume = 110.0, 900_000.0
            else:
                price, volume = 100.0, 20_000.0
            records.append(
                {
                    "ts_code": symbol,
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "open": price,
                    "high": price + 0.1,
                    "low": price - 0.1,
                    "close": price,
                    "volume": volume,
                    "amount_yuan": price * volume,
                }
            )
        open_price = 91.0 + symbol_index * 0.4
        records.append(
            {
                "ts_code": symbol,
                "trade_date": selection_date.strftime("%Y%m%d"),
                "open": open_price,
                "high": open_price + 0.2,
                "low": open_price - 0.2,
                "close": open_price + 0.1,
                "volume": 500_000.0,
                "amount_yuan": open_price * 500_000.0,
            }
        )
    limits = pd.DataFrame(
        {
            "ts_code": symbols,
            "trade_date": selection_date.strftime("%Y%m%d"),
            "up_limit": [150.0] * len(symbols),
            "down_limit": [50.0] * len(symbols),
        }
    )
    universe = pd.DataFrame(
        {
            "ts_code": symbols,
            "name": [f"Stock {index}" for index in range(len(symbols))],
            "industry": ["Mixed"] * len(symbols),
            "list_date": ["20200101"] * len(symbols),
        }
    )
    return pd.DataFrame(records), limits, universe, selection_date.strftime("%Y-%m-%d")


def test_selector_keeps_exactly_five_buy_ready_names_and_allows_688008():
    bars, limits, universe, selection_date = make_inputs()

    result = select_monthly_portfolio(make_config(), bars, limits, universe, selection_date)

    assert len(result.selected) == 5
    assert all(item.buy_ready for item in result.selected)
    assert "688008.SH" in {item.symbol for item in result.candidates}
    assert result.feature_cutoff < result.selection_date


def test_selector_uses_signal_open_for_limit_check_and_fills_from_next_candidate():
    bars, limits, universe, selection_date = make_inputs()
    limited_symbol = "000001.SZ"
    signal_open = bars.loc[
        (bars["ts_code"] == limited_symbol) & (bars["trade_date"] == selection_date.replace("-", "")),
        "open",
    ].iloc[0]
    limits.loc[limits["ts_code"] == limited_symbol, "up_limit"] = signal_open

    result = select_monthly_portfolio(make_config(), bars, limits, universe, selection_date)

    selected_symbols = {item.symbol for item in result.selected}
    rejected = next(item for item in result.candidates if item.symbol == limited_symbol)
    assert len(selected_symbols) == 5
    assert limited_symbol not in selected_symbols
    assert rejected.reason == "locked_at_upper_limit"


def test_signal_day_volume_cannot_change_prior_double_peak():
    bars, limits, universe, selection_date = make_inputs()
    baseline = select_monthly_portfolio(make_config(), bars, limits, universe, selection_date)
    changed = bars.copy()
    changed.loc[changed["trade_date"] == selection_date.replace("-", ""), "volume"] = 9_999_999_999.0

    result = select_monthly_portfolio(make_config(), changed, limits, universe, selection_date)

    baseline_peaks = {item.symbol: (item.lower_peak, item.upper_peak) for item in baseline.candidates}
    changed_peaks = {item.symbol: (item.lower_peak, item.upper_peak) for item in result.candidates}
    assert changed_peaks == baseline_peaks


def test_selector_fails_closed_when_fewer_than_five_are_buy_ready():
    bars, limits, universe, selection_date = make_inputs()
    config = make_config()
    config = replace(config, strategy=replace(config.strategy, chip_low_entry_position=0.01))

    try:
        select_monthly_portfolio(config, bars, limits, universe, selection_date)
    except ValueError as error:
        assert "5 are required" in str(error)
    else:
        raise AssertionError("Expected selection to reject an underfilled portfolio")


def test_extended_factor_values_are_causal_and_registry_is_bounded():
    bars, limits, universe, selection_date = make_inputs()

    result = select_monthly_portfolio(
        make_config(),
        bars,
        limits,
        universe,
        selection_date,
        weights=FactorWeights(
            lower_peak_proximity=0.4,
            momentum_20d=0.0,
            low_volatility_20d=0.0,
            liquidity_20d=0.0,
            momentum_60d_skip_5d=0.2,
            reversal_5d=0.2,
            low_downside_volatility_20d=0.1,
            amount_stability_20d=0.1,
        ),
    )
    experiments = preregistered_factor_experiments()
    validate_experiment_registry()

    assert len(result.selected) == 5
    assert all(item.momentum_60d_skip_5d is not None for item in result.selected)
    assert all(item.return_5d is not None for item in result.selected)
    assert len(experiments) == 5
    assert experiments[0].name == "baseline_four_factor"
