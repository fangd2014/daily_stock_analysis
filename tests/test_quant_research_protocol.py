"""Tests for the causal monthly research protocol."""

from src.quant.config import OptimizationConfig, PortfolioConfig, QuantConfig, UniverseConfig
from src.quant.research_protocol import build_prequential_months, parameter_digest, protocol_template


def test_protocol_builds_all_36_months_with_prior_train_and_validation_windows():
    config = QuantConfig(
        symbol="PORTFOLIO",
        name="Technology Portfolio",
        start_date="2021-07-01",
        end_date="2026-06-30",
        output_dir="reports/quant/test",
        portfolio=PortfolioConfig(max_positions=5),
        universe=UniverseConfig(scope="all_a"),
        optimization=OptimizationConfig(
            train_months=18,
            validation_months=6,
            research_sample_months=24,
            holdout_start="2023-07-01",
            holdout_end="2026-06-30",
        ),
    )

    months = build_prequential_months(config)

    assert len(months) == 36
    assert months[0].training_start == "2021-07-01"
    assert months[0].validation_end == "2023-06-30"
    assert months[0].evaluation_start == "2023-07-01"
    assert months[-1].month == "2026-06"
    assert months[-1].evaluation_end == "2026-06-30"
    template = protocol_template(config)
    assert template["required_positions"] == 5
    assert template["research_sample_months"] == 24
    assert template["universe_scope"] == "all_a"
    assert template["excluded_symbols"] == []


def test_parameter_digest_is_order_independent_and_sensitive_to_values():
    first = parameter_digest({"lookback": 60, "fraction": 0.3})
    reordered = parameter_digest({"fraction": 0.3, "lookback": 60})
    changed = parameter_digest({"lookback": 60, "fraction": 0.2})

    assert first == reordered
    assert first != changed
