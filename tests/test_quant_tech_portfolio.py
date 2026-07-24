"""Tests for technology screening and portfolio paper allocation."""

from dataclasses import replace

import pandas as pd
import pytest

from src.quant.portfolio_paper import load_portfolio_config
from src.quant.paper import PaperTradingService
from src.quant.paper_config import load_paper_config
from src.quant.tech_screener import (
    ScreeningResult,
    TechCandidate,
    TechScreenerConfig,
    evaluate_candidate,
    select_candidates,
)


def _screener_config() -> TechScreenerConfig:
    return TechScreenerConfig(
        initial_cash=1_000_000,
        target_exposure=0.3,
        max_positions=3,
        min_market_cap=50_000_000_000,
        min_average_amount=500_000_000,
        lookback_days=60,
        calendar_days=240,
        selection_buy_position_max=0.45,
        output_dir="reports/quant/test_screen",
        universe=[],
    )


def test_daily_screen_detects_double_peak_without_using_signal_day_volume():
    records = []
    trade_dates = pd.bdate_range("2026-01-02", periods=61)
    for index, trade_date in enumerate(trade_dates[:-1]):
        if index % 3 == 0:
            price, volume = 90.0, 1_000_000
        elif index % 3 == 1:
            price, volume = 110.0, 900_000
        else:
            price, volume = 100.0, 20_000
        records.append(
            {
                "trade_date": trade_date.strftime("%Y%m%d"),
                "open": price,
                "high": price + 0.1,
                "low": price - 0.1,
                "close": price,
                "vol": volume,
                "amount": price * volume / 1_000,
            }
        )
    records.append(
        {
            "trade_date": trade_dates[-1].strftime("%Y%m%d"),
            "open": 98.0,
            "high": 98.1,
            "low": 97.9,
            "close": 98.0,
            "vol": 999_999_999,
            "amount": 10_000_000,
        }
    )
    basic = pd.DataFrame(
        [{"trade_date": trade_dates[-1].strftime("%Y%m%d"), "total_mv": 8_000_000}]
    )

    result = evaluate_candidate(
        TechCandidate("688001.SH", "Test", "AI"),
        pd.DataFrame(records),
        basic,
        _screener_config(),
    )

    assert result.eligible
    assert result.price_between_peaks
    assert 0.3 < result.position < 0.5


def test_selection_keeps_the_three_largest_eligible_names():
    base = ScreeningResult(
        symbol="A",
        name="A",
        industry="AI",
        trade_date="20260722",
        close=100.0,
        total_market_cap=100_000_000_000,
        average_amount_20d=1_000_000_000,
        double_peak=True,
        price_between_peaks=True,
        lower_peak=90.0,
        upper_peak=110.0,
        valley=100.0,
        position=0.5,
        one_lot_value=100_000.0,
        buy_ready=True,
        eligible=True,
        reason="eligible",
    )
    results = [
        replace(base, symbol=str(index), name=str(index), total_market_cap=(10 - index) * 1e11, one_lot_value=value)
        for index, value in enumerate([100_000, 70_000, 50_000, 40_000, 90_000, 40_000], start=1)
    ]

    selected = select_candidates(results, _screener_config())

    assert [item.symbol for item in selected] == ["1", "2", "3"]


def test_selection_refuses_to_fill_a_slot_with_an_ineligible_name():
    base = ScreeningResult(
        "A",
        "A",
        "AI",
        "20260722",
        100.0,
        100_000_000_000,
        1_000_000_000,
        True,
        True,
        90.0,
        110.0,
        100.0,
        0.5,
        10_000.0,
        True,
        True,
        "eligible",
    )

    with pytest.raises(ValueError, match="3 are required"):
        select_candidates([base, replace(base, symbol="B")], _screener_config())


def test_checked_in_portfolio_uses_five_accounts_and_one_million_capital():
    config = load_portfolio_config("configs/quant/tech_chip_portfolio_paper.json")

    assert len(config.account_configs) == 5
    assert config.max_positions == 5
    assert config.initial_cash == 1_000_000
    assert config.target_exposure == 0.3


def test_portfolio_sleeve_overrides_symbol_capital_and_trade_fraction():
    account = load_paper_config("configs/quant/paper/688256_chip.json")
    service = PaperTradingService(account)

    assert service.quant_config.symbol == "688256.SH"
    assert service.quant_config.portfolio.initial_cash == 300_000
    assert service.quant_config.portfolio.base_ratio == 0.44
    assert service.quant_config.strategy.position_fraction == 1.0
