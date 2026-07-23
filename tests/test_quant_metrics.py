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
    metrics = calculate_metrics(equity, trades, pairs, initial_equity=1_000_000)

    assert metrics["annual_return"] > 0.25
    assert metrics["max_drawdown"] == 0
    assert metrics["pair_count"] == 2
    assert metrics["win_rate"] == 0.5
    assert metrics["profit_factor"] == 2
    assert metrics["out_of_sample_months"] == 12


def test_acceptance_checks_use_minimum_and_maximum_thresholds():
    class Acceptance:
        annual_return_min = 0.30
        max_drawdown_max = 0.25
        calmar_min = 2.0
        mean_monthly_return_min = 0.025
        median_monthly_return_min = 0.10
        min_out_of_sample_months = 36

    checks = acceptance_results(
        {
            "annual_return": 0.31,
            "max_drawdown": 0.10,
            "calmar": 3.1,
            "mean_monthly_return": 0.02,
            "median_monthly_return": 0.11,
            "out_of_sample_months": 35,
        },
        Acceptance(),
    )
    assert checks == {
        "out_of_sample_months": False,
        "median_monthly_return": True,
        "annual_return": True,
        "max_drawdown": True,
        "calmar": True,
        "mean_monthly_return": False,
    }


def test_monthly_metrics_include_partial_first_out_of_sample_month():
    dates = pd.bdate_range("2023-01-16", "2025-12-31")
    equity = pd.DataFrame(
        {
            "datetime": dates + pd.Timedelta(hours=15),
            "equity": [1_000_000 * (1.0001**index) for index in range(len(dates))],
        }
    )

    metrics = calculate_metrics(equity, initial_equity=1_000_000)

    assert metrics["out_of_sample_months"] == 36
