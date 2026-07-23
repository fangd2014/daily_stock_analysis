"""Performance statistics for quantitative backtests."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def daily_equity(equity_curve: pd.DataFrame) -> pd.Series:
    """Convert intraday equity observations to an end-of-day series."""
    if equity_curve.empty:
        return pd.Series(dtype=float)
    frame = equity_curve.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    return frame.set_index("datetime")["equity"].resample("1D").last().dropna()


def _with_initial_equity(daily: pd.Series, initial_equity: float | None) -> pd.Series:
    if initial_equity is None or daily.empty:
        return daily
    first_month = daily.index[0].to_period("M")
    opening_index = first_month.start_time - pd.Timedelta(days=1)
    return pd.concat([pd.Series([float(initial_equity)], index=[opening_index]), daily])


def calculate_metrics(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    pairs: pd.DataFrame | None = None,
    initial_equity: float | None = None,
) -> Dict[str, Any]:
    """Calculate account-level return, risk, and trading metrics."""
    daily = daily_equity(equity_curve)
    daily = _with_initial_equity(daily, initial_equity)
    if len(daily) < 2 or daily.iloc[0] <= 0:
        return {
            "start_equity": float(daily.iloc[0]) if len(daily) else 0.0,
            "end_equity": float(daily.iloc[-1]) if len(daily) else 0.0,
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "sharpe": 0.0,
            "mean_monthly_return": 0.0,
            "median_monthly_return": 0.0,
            "positive_month_ratio": 0.0,
            "out_of_sample_months": 0,
            "trade_count": 0,
            "pair_count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "turnover": 0.0,
        }
    start_equity = float(daily.iloc[0])
    end_equity = float(daily.iloc[-1])
    total_return = end_equity / start_equity - 1.0
    elapsed_days = max((daily.index[-1] - daily.index[0]).days, 1)
    annual_return = (end_equity / start_equity) ** (365.25 / elapsed_days) - 1.0
    running_peak = daily.cummax()
    drawdown = daily / running_peak - 1.0
    max_drawdown = abs(float(drawdown.min()))
    calmar = annual_return / max_drawdown if max_drawdown > 0 else (float("inf") if annual_return > 0 else 0.0)
    daily_returns = daily.pct_change().dropna()
    volatility = float(daily_returns.std(ddof=0))
    sharpe = float(daily_returns.mean() / volatility * np.sqrt(252.0)) if volatility > 0 else 0.0
    monthly = daily.resample("ME").last().pct_change().dropna()

    trade_count = 0 if trades is None else len(trades)
    gross_turnover = 0.0
    if trades is not None and not trades.empty and "gross_value" in trades:
        gross_turnover = float(trades["gross_value"].sum())
    average_equity = float(daily.mean())
    turnover = gross_turnover / average_equity if average_equity > 0 else 0.0

    pair_count = 0 if pairs is None else len(pairs)
    win_rate = 0.0
    profit_factor = 0.0
    if pairs is not None and not pairs.empty and "pnl" in pairs:
        profits = pd.to_numeric(pairs["pnl"], errors="coerce").fillna(0.0)
        win_rate = float((profits > 0).mean())
        gross_profit = float(profits[profits > 0].sum())
        gross_loss = abs(float(profits[profits < 0].sum()))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    return {
        "start_equity": start_equity,
        "end_equity": end_equity,
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "max_drawdown": max_drawdown,
        "calmar": float(calmar),
        "sharpe": sharpe,
        "mean_monthly_return": float(monthly.mean()) if len(monthly) else 0.0,
        "median_monthly_return": float(monthly.median()) if len(monthly) else 0.0,
        "positive_month_ratio": float((monthly > 0).mean()) if len(monthly) else 0.0,
        "out_of_sample_months": int(len(monthly)),
        "trade_count": int(trade_count),
        "pair_count": int(pair_count),
        "win_rate": win_rate,
        "profit_factor": float(profit_factor),
        "turnover": float(turnover),
    }


def monthly_returns(equity_curve: pd.DataFrame, initial_equity: float | None = None) -> pd.DataFrame:
    """Return a tabular monthly return series."""
    daily = daily_equity(equity_curve)
    daily = _with_initial_equity(daily, initial_equity)
    values = daily.resample("ME").last().pct_change().dropna()
    return pd.DataFrame({"month": values.index.strftime("%Y-%m"), "return": values.values})


def acceptance_results(metrics: Dict[str, Any], acceptance: Any) -> Dict[str, bool]:
    """Evaluate the agreed out-of-sample acceptance thresholds."""
    return {
        "out_of_sample_months": metrics["out_of_sample_months"] >= acceptance.min_out_of_sample_months,
        "median_monthly_return": metrics["median_monthly_return"] >= acceptance.median_monthly_return_min,
        "annual_return": metrics["annual_return"] >= acceptance.annual_return_min,
        "max_drawdown": metrics["max_drawdown"] <= acceptance.max_drawdown_max,
        "calmar": metrics["calmar"] >= acceptance.calmar_min,
        "mean_monthly_return": metrics["mean_monthly_return"] >= acceptance.mean_monthly_return_min,
    }
