"""Small preregistered factor families for the all-A research loop."""

from __future__ import annotations

from dataclasses import dataclass

from .all_a_selector import FactorWeights


@dataclass(frozen=True)
class FactorExperiment:
    name: str
    hypothesis: str
    weights: FactorWeights


@dataclass(frozen=True)
class ExitPolicy:
    """A bounded, preregistered daily exit policy for monthly stock selections."""

    name: str
    hypothesis: str
    stop_loss_pct: float | None
    take_profit_position: float | None
    max_holding_sessions: int
    take_profit_return: float | None = None


def preregistered_factor_experiments() -> list[FactorExperiment]:
    """Return a bounded experiment list to control multiple testing."""
    return [
        FactorExperiment(
            name="baseline_four_factor",
            hypothesis="Current chip proximity, 20-day momentum, low volatility, and liquidity baseline.",
            weights=FactorWeights(),
        ),
        FactorExperiment(
            name="chip_low_risk",
            hypothesis="Chip proximity plus downside risk controls should reduce unstable lower-peak entries.",
            weights=FactorWeights(
                lower_peak_proximity=0.45,
                momentum_20d=0.10,
                low_volatility_20d=0.0,
                liquidity_20d=0.15,
                low_downside_volatility_20d=0.15,
                low_max_drawdown_20d=0.15,
            ),
        ),
        FactorExperiment(
            name="dual_horizon_momentum",
            hypothesis="A skip-window medium-horizon trend should be more robust than a single 20-day horizon.",
            weights=FactorWeights(
                lower_peak_proximity=0.35,
                momentum_20d=0.15,
                low_volatility_20d=0.15,
                liquidity_20d=0.15,
                momentum_60d_skip_5d=0.20,
            ),
        ),
        FactorExperiment(
            name="momentum_reversal_barbell",
            hypothesis="Medium continuation and short reversal may identify pullbacks within persistent trends.",
            weights=FactorWeights(
                lower_peak_proximity=0.35,
                momentum_20d=0.0,
                low_volatility_20d=0.10,
                liquidity_20d=0.15,
                momentum_60d_skip_5d=0.20,
                reversal_5d=0.20,
            ),
        ),
        FactorExperiment(
            name="liquidity_stability",
            hypothesis="Stable tradable amount should reduce crowding and execution-cost surprises.",
            weights=FactorWeights(
                lower_peak_proximity=0.40,
                momentum_20d=0.15,
                low_volatility_20d=0.10,
                liquidity_20d=0.15,
                low_downside_volatility_20d=0.10,
                amount_stability_20d=0.10,
            ),
        ),
    ]


def preregistered_exit_policies() -> list[ExitPolicy]:
    """Return conservative exit templates instead of optimizing unrestricted thresholds."""
    return [
        ExitPolicy(
            name="month_end_hold",
            hypothesis="Baseline holds each selected stock through the monthly rebalance.",
            stop_loss_pct=None,
            take_profit_position=None,
            max_holding_sessions=0,
        ),
        ExitPolicy(
            name="mid_peak_stop_6",
            hypothesis="Sell at the midpoint between chip peaks and cap an individual loss near six percent.",
            stop_loss_pct=0.06,
            take_profit_position=0.50,
            max_holding_sessions=15,
        ),
        ExitPolicy(
            name="upper_zone_stop_8",
            hypothesis="Allow more trend room while selling near the upper chip zone with an eight-percent stop.",
            stop_loss_pct=0.08,
            take_profit_position=0.72,
            max_holding_sessions=20,
        ),
        ExitPolicy(
            name="trend_take_15_stop_7",
            hypothesis="Hold medium-term leaders unless they gain fifteen percent or lose seven percent.",
            stop_loss_pct=0.07,
            take_profit_position=None,
            max_holding_sessions=20,
            take_profit_return=0.15,
        ),
        ExitPolicy(
            name="reversal_take_8_stop_5",
            hypothesis="Use a tighter eight-percent target and five-percent stop for short-horizon reversals.",
            stop_loss_pct=0.05,
            take_profit_position=None,
            max_holding_sessions=10,
            take_profit_return=0.08,
        ),
    ]


def validate_experiment_registry() -> None:
    """Reject duplicate names or invalid weights before any backtest runs."""
    experiments = preregistered_factor_experiments()
    names = [experiment.name for experiment in experiments]
    if len(names) != len(set(names)):
        raise ValueError("Factor experiment names must be unique")
    for experiment in experiments:
        experiment.weights.validate()
    policies = preregistered_exit_policies()
    policy_names = [policy.name for policy in policies]
    if len(policy_names) != len(set(policy_names)):
        raise ValueError("Exit policy names must be unique")
    for policy in policies:
        if policy.stop_loss_pct is not None and not 0 < policy.stop_loss_pct < 1:
            raise ValueError("Exit stop losses must be in (0, 1)")
        if policy.take_profit_position is not None and not 0 < policy.take_profit_position <= 1:
            raise ValueError("Exit take-profit positions must be in (0, 1]")
        if policy.take_profit_return is not None and not 0 < policy.take_profit_return < 1:
            raise ValueError("Exit take-profit returns must be in (0, 1)")
        if policy.max_holding_sessions < 0:
            raise ValueError("Exit holding sessions must not be negative")
