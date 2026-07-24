"""Tests for strict out-of-sample research artifact validation."""

import json

import pandas as pd
import pytest

from src.quant.config import (
    AcceptanceConfig,
    OptimizationConfig,
    PortfolioConfig,
    QuantConfig,
    UniverseConfig,
)
from src.quant.research_protocol import build_prequential_months, parameter_digest
from src.quant.validate_research import validate_research_artifacts


def make_validation_config(tmp_path) -> QuantConfig:
    return QuantConfig(
        symbol="ALL_A_PORTFOLIO",
        name="Validation Test",
        start_date="2021-07-01",
        end_date="2026-06-30",
        output_dir=str(tmp_path),
        portfolio=PortfolioConfig(max_positions=5),
        universe=UniverseConfig(scope="all_a"),
        optimization=OptimizationConfig(
            train_months=18,
            validation_months=6,
            research_sample_months=24,
            holdout_start="2023-07-01",
            holdout_end="2026-06-30",
        ),
        acceptance=AcceptanceConfig(
            annual_return_min=0.15,
            annual_return_target=0.20,
            max_drawdown_max=0.15,
            max_drawdown_target=0.10,
            calmar_min=0.0,
            mean_monthly_return_min=-1.0,
            median_monthly_return_min=-1.0,
            min_out_of_sample_months=36,
        ),
    )


def write_manifest(tmp_path, config: QuantConfig) -> dict:
    parameters = {"chip_lookback_days": 60, "position_fraction": 0.3}
    symbols = ["000001.SZ", "300750.SZ", "600000.SH", "600519.SH", "688008.SH"]
    folds = []
    for record in build_prequential_months(config):
        entry_date = pd.bdate_range(record.evaluation_start, record.evaluation_end)[0]
        feature_date = pd.bdate_range(end=record.validation_end, periods=1)[0]
        folds.append(
            {
                **record.__dict__,
                "parameter_data_cutoff": record.validation_end,
                "parameters": parameters,
                "parameter_digest": parameter_digest(parameters),
                "selected_symbols": symbols,
                "buy_ready": {symbol: True for symbol in symbols},
                "data_fingerprints": {symbol: f"sha256:{symbol}:{record.month}" for symbol in symbols},
                "universe_snapshot_hash": f"sha256:universe:{record.month}",
                "universe_data_cutoff": f"{feature_date:%Y-%m-%d}",
                "selection_feature_cutoff": f"{feature_date:%Y-%m-%d}T15:00:00",
                "selection_timestamp": f"{entry_date:%Y-%m-%d}T09:30:00",
                "execution_timestamp": f"{entry_date:%Y-%m-%d}T09:35:00",
            }
        )
    manifest = {
        "protocol_version": 1,
        "mode": "monthly_prequential",
        "research_sample_months": 24,
        "universe_scope": "all_a",
        "excluded_symbols": [],
        "required_positions": 5,
        "folds": folds,
    }
    (tmp_path / "research_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def write_artifacts(
    tmp_path,
    config: QuantConfig,
    returns: list[float],
    drawdown_value: float | None = None,
) -> None:
    months = pd.period_range("2023-07", periods=len(returns), freq="M")
    pd.DataFrame({"month": [str(month) for month in months], "return": returns}).to_csv(
        tmp_path / "monthly_returns.csv",
        index=False,
    )
    equity = 1_000_000.0
    equity_records = []
    if drawdown_value is not None:
        equity_records.append({"datetime": pd.Timestamp("2023-07-10 15:00:00"), "equity": drawdown_value})
    for month, monthly_return in zip(months, returns):
        equity *= 1.0 + monthly_return
        last_business_day = pd.bdate_range(month.start_time, month.end_time)[-1]
        equity_records.append({"datetime": last_business_day + pd.Timedelta(hours=15), "equity": equity})
    pd.DataFrame(equity_records).sort_values("datetime").to_csv(tmp_path / "equity.csv", index=False)
    write_manifest(tmp_path, config)


def test_validator_passes_when_sample_annual_return_and_drawdown_targets_pass(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, config, [0.013] * 36)

    result = validate_research_artifacts(config)

    assert result.passed is True
    assert result.observed_out_of_sample_months == 36
    assert 0.15 <= result.annual_return <= 0.20
    assert result.median_monthly_return == pytest.approx(0.013)
    assert result.max_drawdown < 0.15


def test_validator_rejects_short_sample_even_with_high_returns(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, config, [0.20] * 35)

    result = validate_research_artifacts(config)

    assert result.passed is False
    assert "missing_out_of_sample_months" in result.failures
    assert "insufficient_out_of_sample_months" in result.failures


def test_validator_rejects_annual_return_and_drawdown_failures(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, config, [0.005] * 36, drawdown_value=800_000.0)

    result = validate_research_artifacts(config)

    assert result.passed is False
    assert "annual_return_below_target" in result.failures
    assert "maximum_drawdown_above_limit" in result.failures


def test_validator_does_not_treat_20pct_preference_as_an_upper_cap(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, config, [0.03] * 36)

    result = validate_research_artifacts(config)

    assert result.passed is True
    assert result.annual_return > result.target_annual_return


def test_validator_rejects_non_causal_parameter_cutoff(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, config, [0.11] * 36)
    manifest = json.loads((tmp_path / "research_manifest.json").read_text(encoding="utf-8"))
    manifest["folds"][0]["parameter_data_cutoff"] = "2023-07-31"
    (tmp_path / "research_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_research_artifacts(config)

    assert result.passed is False
    assert "parameter_cutoff_not_causal:2023-07" in result.failures


def test_validator_rejects_portfolio_without_five_buy_ready_names(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, config, [0.11] * 36)
    manifest = json.loads((tmp_path / "research_manifest.json").read_text(encoding="utf-8"))
    manifest["folds"][0]["buy_ready"]["000001.SZ"] = False
    (tmp_path / "research_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_research_artifacts(config)

    assert result.passed is False
    assert "selection_not_buy_ready:2023-07" in result.failures


def test_validator_rejects_universe_snapshot_after_feature_cutoff(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, config, [0.11] * 36)
    manifest = json.loads((tmp_path / "research_manifest.json").read_text(encoding="utf-8"))
    manifest["folds"][0]["universe_data_cutoff"] = "2023-07-31"
    (tmp_path / "research_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_research_artifacts(config)

    assert result.passed is False
    assert "universe_cutoff_not_causal:2023-07" in result.failures


def test_validator_rejects_factor_snapshot_after_feature_cutoff(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, config, [0.11] * 36)
    manifest = json.loads((tmp_path / "research_manifest.json").read_text(encoding="utf-8"))
    manifest["folds"][0]["factor_data_cutoff"] = "2023-07-31"
    manifest["folds"][0]["factor_snapshot_hashes"] = {"daily_basic": "sha256:test"}
    (tmp_path / "research_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_research_artifacts(config)

    assert result.passed is False
    assert "factor_cutoff_not_causal:2023-07" in result.failures


def test_validator_rejects_monthly_returns_that_do_not_match_equity(tmp_path):
    config = make_validation_config(tmp_path)
    write_artifacts(tmp_path, config, [0.11] * 36)
    monthly = pd.read_csv(tmp_path / "monthly_returns.csv")
    monthly.loc[0, "return"] = 0.50
    monthly.to_csv(tmp_path / "monthly_returns.csv", index=False)

    result = validate_research_artifacts(config)

    assert result.passed is False
    assert "monthly_returns_equity_mismatch" in result.failures
