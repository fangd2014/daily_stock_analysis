"""Tests for strict out-of-sample research artifact validation."""

import pandas as pd

from src.quant.config import AcceptanceConfig, OptimizationConfig, QuantConfig
from src.quant.validate_research import validate_research_artifacts


def make_validation_config(tmp_path) -> QuantConfig:
    return QuantConfig(
        symbol="688008.SH",
        name="Validation Test",
        start_date="2021-07-01",
        end_date="2026-06-30",
        output_dir=str(tmp_path),
        optimization=OptimizationConfig(
            holdout_start="2023-07-01",
            holdout_end="2026-06-30",
        ),
        acceptance=AcceptanceConfig(
            annual_return_min=0.0,
            max_drawdown_max=0.10,
            calmar_min=0.0,
            mean_monthly_return_min=-1.0,
            median_monthly_return_min=0.10,
            min_out_of_sample_months=36,
        ),
    )


def write_artifacts(tmp_path, returns: list[float], equity_values: list[float] | None = None) -> None:
    months = pd.period_range("2023-07", periods=len(returns), freq="M")
    pd.DataFrame({"month": [str(month) for month in months], "return": returns}).to_csv(
        tmp_path / "monthly_returns.csv",
        index=False,
    )
    dates = pd.bdate_range("2023-07-03", periods=len(equity_values or [1_000_000.0]))
    pd.DataFrame(
        {
            "datetime": dates + pd.Timedelta(hours=15),
            "equity": equity_values or [1_000_000.0],
        }
    ).to_csv(tmp_path / "equity.csv", index=False)


def test_validator_passes_only_when_all_three_hard_targets_pass(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, [0.11] * 36, [1_000_000.0, 1_120_000.0, 1_080_000.0])

    result = validate_research_artifacts(config)

    assert result.passed is True
    assert result.observed_out_of_sample_months == 36
    assert result.median_monthly_return == 0.11
    assert result.max_drawdown < 0.10


def test_validator_rejects_short_sample_even_with_high_returns(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, [0.20] * 35)

    result = validate_research_artifacts(config)

    assert result.passed is False
    assert "missing_out_of_sample_months" in result.failures
    assert "insufficient_out_of_sample_months" in result.failures


def test_validator_rejects_median_return_and_drawdown_failures(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, [0.09] * 36, [1_000_000.0, 1_200_000.0, 1_000_000.0])

    result = validate_research_artifacts(config)

    assert result.passed is False
    assert "median_monthly_return_below_target" in result.failures
    assert "maximum_drawdown_above_limit" in result.failures
