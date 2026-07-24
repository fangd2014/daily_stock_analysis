"""Independent artifact validator for strict out-of-sample research targets."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .config import QuantConfig, load_quant_config
from .metrics import daily_equity, monthly_returns
from .research_protocol import validate_prequential_manifest


@dataclass(frozen=True)
class ResearchValidationResult:
    status: str
    passed: bool
    observed_out_of_sample_months: int
    required_out_of_sample_months: int
    annual_return: float
    required_annual_return: float
    target_annual_return: float
    median_monthly_return: float
    required_median_monthly_return: float
    max_drawdown: float
    maximum_allowed_drawdown: float
    target_max_drawdown: float
    missing_months: list[str]
    failures: list[str]
    summary: str


def _daily_with_opening_equity(equity: pd.DataFrame, initial_equity: float) -> pd.Series:
    daily = daily_equity(equity)
    if daily.empty:
        return daily
    opening_index = daily.index[0].to_period("M").start_time - pd.Timedelta(days=1)
    return pd.concat([pd.Series([float(initial_equity)], index=[opening_index]), daily])


def _strict_drawdown(equity: pd.DataFrame, initial_equity: float) -> float:
    daily = _daily_with_opening_equity(equity, initial_equity)
    if daily.empty:
        return 1.0
    return abs(float((daily / daily.cummax() - 1.0).min()))


def _strict_annual_return(equity: pd.DataFrame, initial_equity: float) -> float:
    daily = _daily_with_opening_equity(equity, initial_equity)
    if len(daily) < 2 or daily.iloc[0] <= 0 or daily.iloc[-1] <= 0:
        return -1.0
    elapsed_days = max((daily.index[-1] - daily.index[0]).days, 1)
    return float((daily.iloc[-1] / daily.iloc[0]) ** (365.25 / elapsed_days) - 1.0)


def validate_research_artifacts(
    config: QuantConfig,
    output_dir: str | Path | None = None,
) -> ResearchValidationResult:
    """Recompute sample length, annual return, and drawdown from saved artifacts."""
    root = Path(output_dir or config.output_dir)
    monthly_path = root / "monthly_returns.csv"
    equity_path = root / "equity.csv"
    manifest_path = root / "research_manifest.json"
    failures = []
    if not monthly_path.exists():
        failures.append("monthly_returns_missing")
    if not equity_path.exists():
        failures.append("equity_missing")
    if not manifest_path.exists():
        failures.append("research_manifest_missing")
    if failures:
        return ResearchValidationResult(
            status="failed",
            passed=False,
            observed_out_of_sample_months=0,
            required_out_of_sample_months=config.acceptance.min_out_of_sample_months,
            annual_return=-1.0,
            required_annual_return=config.acceptance.annual_return_min,
            target_annual_return=config.acceptance.annual_return_target,
            median_monthly_return=0.0,
            required_median_monthly_return=config.acceptance.median_monthly_return_min,
            max_drawdown=1.0,
            maximum_allowed_drawdown=config.acceptance.max_drawdown_max,
            target_max_drawdown=config.acceptance.max_drawdown_target,
            missing_months=[],
            failures=failures,
            summary="Required holdout artifacts are missing.",
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        manifest = {}
        failures.append("research_manifest_invalid_json")
    if isinstance(manifest, dict):
        failures.extend(validate_prequential_manifest(config, manifest))
    else:
        failures.append("research_manifest_invalid_root")

    reported_monthly = pd.read_csv(monthly_path, dtype={"month": str})
    if not {"month", "return"}.issubset(reported_monthly.columns):
        raise ValueError("monthly_returns.csv must contain month and return columns")
    reported_monthly["return"] = pd.to_numeric(reported_monthly["return"], errors="coerce")
    reported_monthly = reported_monthly.dropna(subset=["month", "return"])
    if reported_monthly["month"].duplicated().any():
        failures.append("duplicate_months")
    equity = pd.read_csv(equity_path)
    if not {"datetime", "equity"}.issubset(equity.columns):
        raise ValueError("equity.csv must contain datetime and equity columns")
    recomputed_monthly = monthly_returns(equity, config.portfolio.initial_cash)
    recomputed_monthly["month"] = recomputed_monthly["month"].astype(str)
    observed_months = sorted(recomputed_monthly["month"].unique().tolist())
    expected_months = [
        str(period)
        for period in pd.period_range(
            config.optimization.holdout_start,
            config.optimization.holdout_end,
            freq="M",
        )
    ]
    missing_months = sorted(set(expected_months) - set(observed_months))
    unexpected_months = sorted(set(observed_months) - set(expected_months))
    if missing_months:
        failures.append("missing_out_of_sample_months")
    if unexpected_months:
        failures.append("returns_outside_holdout")
    reported = reported_monthly.sort_values("month").reset_index(drop=True)
    recomputed = recomputed_monthly.sort_values("month").reset_index(drop=True)
    monthly_matches_equity = (
        reported["month"].tolist() == recomputed["month"].tolist()
        and len(reported) == len(recomputed)
        and pd.Series(reported["return"] - recomputed["return"]).abs().le(1e-10).all()
    )
    if not monthly_matches_equity:
        failures.append("monthly_returns_equity_mismatch")
    month_count = len(observed_months)
    median_return = float(recomputed_monthly["return"].median()) if not recomputed_monthly.empty else 0.0
    annual_return = _strict_annual_return(equity, config.portfolio.initial_cash)
    max_drawdown = _strict_drawdown(equity, config.portfolio.initial_cash)
    if month_count < config.acceptance.min_out_of_sample_months:
        failures.append("insufficient_out_of_sample_months")
    if annual_return < config.acceptance.annual_return_min:
        failures.append("annual_return_below_target")
    if (
        config.acceptance.median_monthly_return_min > -1
        and median_return < config.acceptance.median_monthly_return_min
    ):
        failures.append("median_monthly_return_below_target")
    if max_drawdown > config.acceptance.max_drawdown_max:
        failures.append("maximum_drawdown_above_limit")
    passed = not failures
    summary = (
        "All strict out-of-sample targets passed."
        if passed
        else "Strict out-of-sample validation failed: " + ", ".join(failures)
    )
    return ResearchValidationResult(
        status="passed" if passed else "failed",
        passed=passed,
        observed_out_of_sample_months=month_count,
        required_out_of_sample_months=config.acceptance.min_out_of_sample_months,
        annual_return=annual_return,
        required_annual_return=config.acceptance.annual_return_min,
        target_annual_return=config.acceptance.annual_return_target,
        median_monthly_return=median_return,
        required_median_monthly_return=config.acceptance.median_monthly_return_min,
        max_drawdown=max_drawdown,
        maximum_allowed_drawdown=config.acceptance.max_drawdown_max,
        target_max_drawdown=config.acceptance.max_drawdown_target,
        missing_months=missing_months,
        failures=failures,
        summary=summary,
    )


def write_validation_result(result: ResearchValidationResult, output_path: str | Path) -> dict[str, Any]:
    """Persist a validator-gated completion artifact."""
    payload = asdict(result)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate strict out-of-sample quant research artifacts")
    parser.add_argument("--config", required=True, help="Quant research configuration")
    parser.add_argument("--output", default="", help="Validation result JSON path")
    args = parser.parse_args()
    config = load_quant_config(args.config)
    result = validate_research_artifacts(config)
    output = args.output or str(Path(config.output_dir) / "research_validation.json")
    print(json.dumps(write_validation_result(result, output), ensure_ascii=False, indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
