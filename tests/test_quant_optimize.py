"""Tests for walk-forward window and parameter-grid construction."""

import pandas as pd

from src.quant.config import OptimizationConfig, QuantConfig, StrategyConfig
from src.quant.data import QuantDataBundle
from src.quant.optimize import WalkForwardOptimizer, build_walk_forward_folds, parameter_grid


def test_walk_forward_folds_stop_before_holdout():
    config = QuantConfig(
        symbol="688008.SH",
        name="Test",
        start_date="2020-07-01",
        end_date="2026-06-30",
        output_dir="reports/quant/test",
    )
    folds = build_walk_forward_folds(config)

    assert len(folds) == 6
    assert folds[0].train_start == "2020-07-01"
    assert folds[-1].validation_end == "2025-06-30"


def test_parameter_grid_uses_declared_values():
    optimization = OptimizationConfig(
        zscore_windows=[12],
        entry_z_values=[1.5],
        exit_z_values=[0.25],
        position_fractions=[0.2],
        stop_loss_values=[0.006],
    )
    config = QuantConfig(
        symbol="688008.SH",
        name="Test",
        start_date="2020-07-01",
        end_date="2026-06-30",
        output_dir="reports/quant/test",
        optimization=optimization,
    )
    assert list(parameter_grid(config)) == [
        {
            "zscore_window": 12,
            "entry_z": 1.5,
            "exit_z": 0.25,
            "position_fraction": 0.2,
            "stop_loss_pct": 0.006,
        }
    ]


def test_chip_parameter_grid_uses_only_relevant_values():
    optimization = OptimizationConfig(
        position_fractions=[0.2],
        stop_loss_values=[0.012],
        chip_lookback_days_values=[60],
        chip_low_entry_position_values=[0.28],
        chip_high_entry_position_values=[0.72],
        chip_exit_position_values=[0.5],
    )
    config = QuantConfig(
        symbol="688008.SH",
        name="Test",
        start_date="2020-07-01",
        end_date="2026-06-30",
        output_dir="reports/quant/test",
        strategy=StrategyConfig(strategy_type="chip_double_peak"),
        optimization=optimization,
    )

    assert list(parameter_grid(config)) == [
        {
            "chip_lookback_days": 60,
            "chip_low_entry_position": 0.28,
            "chip_high_entry_position": 0.72,
            "chip_exit_position": 0.5,
            "position_fraction": 0.2,
            "stop_loss_pct": 0.012,
        }
    ]


def test_optimizer_runs_single_candidate_without_touching_holdout():
    bars = []
    for day_index, trade_date in enumerate(pd.bdate_range("2025-01-02", "2025-03-31")):
        for bar_index, timestamp in enumerate(pd.date_range(f"{trade_date.date()} 09:30", periods=12, freq="5min")):
            price = 100 + day_index * 0.01 + (bar_index % 3) * 0.1
            bars.append(
                {
                    "datetime": timestamp,
                    "symbol": "688008.SH",
                    "open": price,
                    "high": price + 0.05,
                    "low": price - 0.05,
                    "close": price,
                    "volume": 1_000_000,
                    "amount": price * 1_000_000,
                }
            )
    bundle = QuantDataBundle(
        bars=pd.DataFrame(bars),
        limits=pd.DataFrame(),
        adjustments=pd.DataFrame(),
        dividends=pd.DataFrame(),
    )
    optimization = OptimizationConfig(
        train_months=1,
        validation_months=1,
        step_months=1,
        holdout_start="2025-04-01",
        holdout_end="2025-04-30",
        min_trades_per_year=0,
        zscore_windows=[4],
        entry_z_values=[1.5],
        exit_z_values=[0.25],
        position_fractions=[0.2],
        stop_loss_values=[0.006],
    )
    config = QuantConfig(
        symbol="688008.SH",
        name="Test",
        start_date="2025-01-02",
        end_date="2025-04-30",
        output_dir="reports/quant/test",
        optimization=optimization,
    )

    best, ranked = WalkForwardOptimizer(config, bundle).run()

    assert best["zscore_window"] == 4
    assert best["entry_z"] == 1.5
    assert len(ranked) == 1
