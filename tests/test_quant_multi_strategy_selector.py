"""Tests for the three non-chip all-A stock selectors."""

import numpy as np
import pandas as pd
import pytest

from src.quant.multi_strategy_selector import STRATEGY_NAMES, select_strategy_portfolio
from tests.test_quant_all_a_selector import make_config


def make_inputs():
    symbols = ["000001.SZ", "000002.SZ", "300001.SZ", "600000.SH", "600001.SH", "688001.SH", "688008.SH"]
    history_dates = pd.bdate_range("2022-12-01", periods=121)
    selection_date = pd.bdate_range(history_dates[-1] + pd.Timedelta(days=1), periods=1)[0]
    records = []
    for stock_index, symbol in enumerate(symbols):
        trend = np.linspace(80.0 + stock_index, 110.0 + stock_index, len(history_dates))
        trend[-5:] = np.linspace(trend[-6] * 0.995, trend[-6] * 0.97, 5)
        for trade_date, price in zip(history_dates, trend):
            records.append(
                {
                    "ts_code": symbol,
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": 1_000_000.0,
                    "amount_yuan": price * 1_000_000.0,
                }
            )
        records.append(
            {
                "ts_code": symbol,
                "trade_date": selection_date.strftime("%Y%m%d"),
                "open": trend[-1],
                "high": trend[-1] * 1.01,
                "low": trend[-1] * 0.99,
                "close": trend[-1],
                "volume": 1_000_000.0,
                "amount_yuan": trend[-1] * 1_000_000.0,
            }
        )
    limits = pd.DataFrame(
        {
            "ts_code": symbols,
            "trade_date": selection_date.strftime("%Y%m%d"),
            "up_limit": [200.0] * len(symbols),
            "down_limit": [50.0] * len(symbols),
        }
    )
    universe = pd.DataFrame(
        {
            "ts_code": symbols,
            "name": symbols,
            "industry": ["Leader Industry"] * len(symbols),
            "list_date": ["20200101"] * len(symbols),
        }
    )
    daily_basic = pd.DataFrame(
        {
            "ts_code": symbols,
            "trade_date": history_dates[-1].strftime("%Y%m%d"),
            "pe_ttm": np.linspace(8.0, 15.0, len(symbols)),
            "pb": np.linspace(0.8, 1.5, len(symbols)),
        }
    )
    financials = pd.DataFrame(
        {
            "ts_code": symbols,
            "ann_date": [history_dates[-2].strftime("%Y%m%d")] * len(symbols),
            "roe_dt": np.linspace(18.0, 10.0, len(symbols)),
            "roa": np.linspace(9.0, 5.0, len(symbols)),
            "grossprofit_margin": np.linspace(40.0, 25.0, len(symbols)),
            "ocf_to_or": np.linspace(0.4, 0.2, len(symbols)),
            "debt_to_assets": np.linspace(25.0, 45.0, len(symbols)),
            "q_profit_yoy": np.linspace(20.0, 5.0, len(symbols)),
        }
    )
    moneyflow = pd.DataFrame(
        {
            "ts_code": symbols,
            "trade_date": history_dates[-1].strftime("%Y%m%d"),
            "buy_lg_amount": [2_000.0] * len(symbols),
            "buy_elg_amount": [1_000.0] * len(symbols),
            "sell_lg_amount": [500.0] * len(symbols),
            "sell_elg_amount": [300.0] * len(symbols),
        }
    )
    return (
        pd.DataFrame(records),
        limits,
        universe,
        daily_basic,
        financials,
        moneyflow,
        selection_date.strftime("%Y-%m-%d"),
        history_dates[-1].strftime("%Y-%m-%d"),
    )


@pytest.mark.parametrize("strategy_name", STRATEGY_NAMES)
def test_each_preregistered_strategy_selects_exactly_five_buy_ready_stocks(strategy_name):
    bars, limits, universe, daily_basic, financials, moneyflow, selection_date, cutoff = make_inputs()

    result = select_strategy_portfolio(
        make_config(),
        strategy_name,
        bars,
        limits,
        universe,
        selection_date,
        cutoff,
        daily_basic,
        financials,
        moneyflow,
    )

    assert len(result.selected) == 5
    assert all(candidate.buy_ready for candidate in result.selected)
    assert all(candidate.strategy_name == strategy_name for candidate in result.selected)


def test_selector_rejects_factor_snapshot_after_price_cutoff():
    bars, limits, universe, daily_basic, financials, moneyflow, selection_date, _ = make_inputs()

    with pytest.raises(ValueError, match="later than the price feature cutoff"):
        select_strategy_portfolio(
            make_config(),
            "quality_value",
            bars,
            limits,
            universe,
            selection_date,
            selection_date,
            daily_basic,
            financials,
            moneyflow,
        )
