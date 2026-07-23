"""Research artifact generation for holdout and stress-test results."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .backtest import BacktestEngine, BacktestResult, slice_bundle
from .config import QuantConfig
from .data import QuantDataBundle
from .metrics import acceptance_results, daily_equity, monthly_returns


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    return value


def _run_stress_tests(
    config: QuantConfig,
    bundle: QuantDataBundle,
    evaluation_start: str,
) -> dict[str, dict[str, Any]]:
    double_cost = replace(
        config,
        costs=replace(
            config.costs,
            commission_rate=config.costs.commission_rate * 2,
            minimum_commission=config.costs.minimum_commission * 2,
        ),
    )
    double_slippage = replace(
        config,
        costs=replace(config.costs, slippage_bps=config.costs.slippage_bps * 2),
    )
    half_liquidity = replace(
        config,
        portfolio=replace(
            config.portfolio,
            max_volume_participation=config.portfolio.max_volume_participation / 2,
        ),
    )
    scenarios = {
        "double_commission": BacktestEngine(double_cost),
        "double_slippage": BacktestEngine(double_slippage),
        "half_liquidity": BacktestEngine(half_liquidity),
        "extra_bar_delay": BacktestEngine(config, execution_delay_bars=2),
    }
    return {name: engine.run(bundle, evaluation_start=evaluation_start).metrics for name, engine in scenarios.items()}


def _write_chart(
    strategy: BacktestResult,
    benchmark: BacktestResult,
    output_path: Path,
    initial_equity: float,
) -> None:
    cache_dir = output_path.parent / ".matplotlib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    strategy_daily = daily_equity(strategy.equity)
    benchmark_daily = daily_equity(benchmark.equity)
    if len(strategy_daily):
        opening_index = strategy_daily.index[0].normalize() - pd.Timedelta(days=1)
        strategy_daily = pd.concat([pd.Series([initial_equity], index=[opening_index]), strategy_daily])
        benchmark_daily = pd.concat([pd.Series([initial_equity], index=[opening_index]), benchmark_daily])
    strategy_nav = strategy_daily / strategy_daily.iloc[0]
    benchmark_nav = benchmark_daily / benchmark_daily.iloc[0]
    drawdown = strategy_nav / strategy_nav.cummax() - 1.0
    figure, (axis_nav, axis_dd) = plt.subplots(
        2,
        1,
        figsize=(11, 7),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    axis_nav.plot(strategy_nav.index, strategy_nav.values, label="T+0 account", linewidth=1.5)
    axis_nav.plot(benchmark_nav.index, benchmark_nav.values, label="Static base", linewidth=1.2)
    axis_nav.set_ylabel("Normalized NAV")
    axis_nav.grid(alpha=0.25)
    axis_nav.legend()
    axis_dd.fill_between(drawdown.index, drawdown.values, 0.0, color="#d9534f", alpha=0.45)
    axis_dd.set_ylabel("Drawdown")
    axis_dd.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(output_path, dpi=150)
    plt.close(figure)


def write_holdout_report(
    config: QuantConfig,
    strategy: BacktestResult,
    benchmark: BacktestResult,
    stress: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Write machine-readable and human-readable backtest artifacts."""
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checks = acceptance_results(strategy.metrics, config.acceptance)
    summary = {
        "symbol": config.symbol,
        "name": config.name,
        "period": {
            "start": config.optimization.holdout_start,
            "end": config.optimization.holdout_end,
        },
        "strategy": strategy.metrics,
        "benchmark": benchmark.metrics,
        "t0_incremental_end_value": strategy.metrics["end_equity"] - benchmark.metrics["end_equity"],
        "acceptance": checks,
        "all_targets_passed": all(checks.values()),
        "stress_tests": stress,
        "assumptions": config.to_dict(),
    }
    safe_summary = _json_safe(summary)
    (output_dir / "summary.json").write_text(
        json.dumps(safe_summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    strategy.equity.to_csv(output_dir / "equity.csv", index=False)
    strategy.trades.to_csv(output_dir / "trades.csv", index=False)
    strategy.pairs.to_csv(output_dir / "pairs.csv", index=False)
    strategy.rejections.to_csv(output_dir / "rejections.csv", index=False)
    monthly_returns(strategy.equity, config.portfolio.initial_cash).to_csv(
        output_dir / "monthly_returns.csv",
        index=False,
    )
    benchmark.equity.to_csv(output_dir / "benchmark_equity.csv", index=False)
    _write_chart(
        strategy,
        benchmark,
        output_dir / "equity_drawdown.png",
        config.portfolio.initial_cash,
    )

    def percentage(value: float) -> str:
        return f"{value:.2%}"

    lines = [
        f"# {config.name} ({config.symbol}) T+0 Holdout Backtest",
        "",
        f"Period: {config.optimization.holdout_start} to {config.optimization.holdout_end}",
        "",
        "## Outcome",
        "",
        f"- All targets passed: **{'yes' if summary['all_targets_passed'] else 'no'}**",
        f"- Annual return: {percentage(strategy.metrics['annual_return'])}",
        f"- Maximum drawdown: {percentage(strategy.metrics['max_drawdown'])}",
        f"- Calmar ratio: {strategy.metrics['calmar']:.2f}",
        f"- Mean monthly return: {percentage(strategy.metrics['mean_monthly_return'])}",
        f"- T+0 pair PnL: CNY {strategy.metrics['t0_pair_pnl']:,.2f}",
        f"- Incremental ending value vs static base: CNY {summary['t0_incremental_end_value']:,.2f}",
        "",
        "## Acceptance Checks",
        "",
    ]
    labels = {
        "annual_return": f"Annual return >= {percentage(config.acceptance.annual_return_min)}",
        "max_drawdown": f"Maximum drawdown <= {percentage(config.acceptance.max_drawdown_max)}",
        "calmar": f"Calmar >= {config.acceptance.calmar_min:.2f}",
        "mean_monthly_return": (
            f"Mean monthly return >= {percentage(config.acceptance.mean_monthly_return_min)}"
        ),
    }
    lines.extend(f"- {'PASS' if passed else 'FAIL'}: {labels[key]}" for key, passed in checks.items())
    lines.extend(["", "## Stress Tests", ""])
    for name, metrics in stress.items():
        lines.append(
            f"- {name}: annual {percentage(metrics['annual_return'])}, "
            f"drawdown {percentage(metrics['max_drawdown'])}, Calmar {metrics['calmar']:.2f}"
        )
    lines.extend(
        [
            "",
            "## Method Note",
            "",
            "Signals are confirmed at a bar close and filled no earlier than the next bar open. "
            "The simulation includes T+1 sellability, board limits, volume participation, commissions, taxes, "
            "and slippage.",
            "This research report is not investment advice and does not guarantee future returns.",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return safe_summary


def run_holdout_research(config: QuantConfig, bundle: QuantDataBundle) -> dict[str, Any]:
    """Run the locked blind holdout, benchmark, stress tests, and report generation."""
    warmup_days = 45
    if config.strategy.strategy_type == "chip_double_peak":
        warmup_days = max(warmup_days, config.strategy.chip_lookback_days * 2)
    holdout = slice_bundle(
        bundle,
        config.optimization.holdout_start,
        config.optimization.holdout_end,
        warmup_days=warmup_days,
    )
    evaluation_start = config.optimization.holdout_start
    strategy = BacktestEngine(config).run(holdout, evaluation_start=evaluation_start)
    benchmark = BacktestEngine(config, strategy_enabled=False).run(
        holdout,
        evaluation_start=evaluation_start,
    )
    stress = _run_stress_tests(config, holdout, evaluation_start)
    return write_holdout_report(config, strategy, benchmark, stress)
