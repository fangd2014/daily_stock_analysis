"""Tests for account-level performance calculations."""

import pandas as pd

from src.quant.metrics import acceptance_results, calculate_metrics


def test_metrics_calculate_drawdown_monthly_return_and_trade_statistics():
    dates = pd.bdate_range("2025-01-02", periods=260)
    equity = pd.DataFrame(
        {
            "datetime": dates + pd.Timedelta(hours=15),
            "equity": [1_000_000 * (1.001**index) for index in range(len(dates))],
        }
    )
    trades = pd.DataFrame({"gross_value": [100_000, 100_000]})
    pairs = pd.DataFrame({"pnl": [1000, -500]})
    metrics = calculate_metrics(equity, trades, pairs)

    assert metrics["annual_return"] > 0.25
    assert metrics["max_drawdown"] == 0
    assert metrics["pair_count"] == 2
    assert metrics["win_rate"] == 0.5
    assert metrics["profit_factor"] == 2


def test_acceptance_checks_use_minimum_and_maximum_thresholds():
    class Acceptance:
        annual_return_min = 0.30
        max_drawdown_max = 0.25
        calmar_min = 2.0
        mean_monthly_return_min = 0.025

    checks = acceptance_results(
        {"annual_return": 0.31, "max_drawdown": 0.10, "calmar": 3.1, "mean_monthly_return": 0.02},
        Acceptance(),
    )
    assert checks == {
        "annual_return": True,
        "max_drawdown": True,
        "calmar": True,
        "mean_monthly_return": False,
    }
