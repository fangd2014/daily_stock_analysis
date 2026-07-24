"""Causal monthly protocol for auditable prequential research."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from .config import QuantConfig


PROTOCOL_VERSION = 1
PROTOCOL_MODE = "monthly_prequential"


@dataclass(frozen=True)
class PrequentialMonth:
    """One parameter-selection and evaluation window with strict time ordering."""

    month: str
    training_start: str
    training_end: str
    validation_start: str
    validation_end: str
    evaluation_start: str
    evaluation_end: str


def build_prequential_months(config: QuantConfig) -> list[PrequentialMonth]:
    """Build one causal train/validation/evaluation record per holdout month."""
    holdout_start = pd.Timestamp(config.optimization.holdout_start).normalize()
    holdout_end = pd.Timestamp(config.optimization.holdout_end).normalize()
    available_start = pd.Timestamp(config.start_date).normalize()
    records: list[PrequentialMonth] = []
    for month in pd.period_range(holdout_start, holdout_end, freq="M"):
        evaluation_start = max(month.start_time.normalize(), holdout_start)
        evaluation_end = min(month.end_time.normalize(), holdout_end)
        validation_end = evaluation_start - pd.Timedelta(days=1)
        validation_start = evaluation_start - pd.DateOffset(months=config.optimization.validation_months)
        training_end = validation_start - pd.Timedelta(days=1)
        training_start = validation_start - pd.DateOffset(months=config.optimization.train_months)
        if training_start < available_start:
            raise ValueError(
                f"Insufficient history for prequential month {month}: "
                f"requires {training_start:%Y-%m-%d}, available from {available_start:%Y-%m-%d}"
            )
        records.append(
            PrequentialMonth(
                month=str(month),
                training_start=training_start.strftime("%Y-%m-%d"),
                training_end=training_end.strftime("%Y-%m-%d"),
                validation_start=validation_start.strftime("%Y-%m-%d"),
                validation_end=validation_end.strftime("%Y-%m-%d"),
                evaluation_start=evaluation_start.strftime("%Y-%m-%d"),
                evaluation_end=evaluation_end.strftime("%Y-%m-%d"),
            )
        )
    return records


def parameter_digest(parameters: dict[str, Any]) -> str:
    """Return a stable digest for a monthly parameter lock."""
    canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def protocol_template(config: QuantConfig) -> dict[str, Any]:
    """Return an unfilled protocol template for a prequential portfolio runner."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "mode": PROTOCOL_MODE,
        "research_sample_months": config.optimization.research_sample_months,
        "universe_scope": config.universe.scope,
        "excluded_symbols": sorted(config.universe.excluded_symbols),
        "required_positions": config.portfolio.max_positions,
        "folds": [asdict(record) for record in build_prequential_months(config)],
    }


def validate_prequential_manifest(config: QuantConfig, manifest: dict[str, Any]) -> list[str]:
    """Validate causal ordering, parameter locks, and portfolio entry evidence."""
    failures: list[str] = []
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        failures.append("unsupported_research_protocol_version")
    if manifest.get("mode") != PROTOCOL_MODE:
        failures.append("research_protocol_not_prequential")
    required_positions = config.portfolio.max_positions
    if manifest.get("required_positions") != required_positions:
        failures.append("research_protocol_position_limit_invalid")
    if manifest.get("research_sample_months") != config.optimization.research_sample_months:
        failures.append("research_protocol_sample_window_invalid")
    if manifest.get("universe_scope") != config.universe.scope:
        failures.append("research_protocol_universe_scope_invalid")
    manifest_exclusions = manifest.get("excluded_symbols")
    if not isinstance(manifest_exclusions, list) or sorted(manifest_exclusions) != sorted(
        config.universe.excluded_symbols
    ):
        failures.append("research_protocol_exclusions_invalid")
    folds = manifest.get("folds")
    if not isinstance(folds, list):
        return failures + ["research_protocol_folds_missing"]

    expected = {record.month: record for record in build_prequential_months(config)}
    observed: dict[str, dict[str, Any]] = {}
    for fold in folds:
        if not isinstance(fold, dict) or not isinstance(fold.get("month"), str):
            failures.append("research_protocol_fold_invalid")
            continue
        month = fold["month"]
        if month in observed:
            failures.append("research_protocol_duplicate_month")
            continue
        observed[month] = fold
    if set(observed) != set(expected):
        failures.append("research_protocol_months_mismatch")

    schedule_fields = (
        "training_start",
        "training_end",
        "validation_start",
        "validation_end",
        "evaluation_start",
        "evaluation_end",
    )
    for month, expected_record in expected.items():
        fold = observed.get(month)
        if fold is None:
            continue
        if any(fold.get(field) != getattr(expected_record, field) for field in schedule_fields):
            failures.append(f"research_protocol_schedule_mismatch:{month}")
            continue
        if not _cutoff_is_causal(fold.get("parameter_data_cutoff"), expected_record.validation_end):
            failures.append(f"parameter_cutoff_not_causal:{month}")
        parameters = fold.get("parameters")
        if not isinstance(parameters, dict) or not parameters:
            failures.append(f"parameter_lock_missing:{month}")
        elif fold.get("parameter_digest") != parameter_digest(parameters):
            failures.append(f"parameter_digest_mismatch:{month}")

        symbols = fold.get("selected_symbols")
        if (
            not isinstance(symbols, list)
            or len(symbols) != required_positions
            or len(set(symbols)) != required_positions
        ):
            failures.append(f"portfolio_position_count_invalid:{month}")
            symbols = []
        excluded = set(config.universe.excluded_symbols)
        if excluded.intersection(symbols):
            failures.append(f"excluded_symbol_selected:{month}")
        buy_ready = fold.get("buy_ready")
        if not isinstance(buy_ready, dict) or any(buy_ready.get(symbol) is not True for symbol in symbols):
            failures.append(f"selection_not_buy_ready:{month}")

        fingerprints = fold.get("data_fingerprints")
        if not isinstance(fingerprints, dict) or any(not fingerprints.get(symbol) for symbol in symbols):
            failures.append(f"data_fingerprint_missing:{month}")
        if not fold.get("universe_snapshot_hash"):
            failures.append(f"universe_snapshot_missing:{month}")
        if not _cutoff_is_causal(fold.get("universe_data_cutoff"), fold.get("selection_feature_cutoff")):
            failures.append(f"universe_cutoff_not_causal:{month}")
        if "factor_data_cutoff" in fold:
            if not _cutoff_is_causal(fold.get("factor_data_cutoff"), fold.get("selection_feature_cutoff")):
                failures.append(f"factor_cutoff_not_causal:{month}")
            factor_hashes = fold.get("factor_snapshot_hashes")
            if not isinstance(factor_hashes, dict) or not factor_hashes:
                failures.append(f"factor_snapshot_missing:{month}")

        _validate_entry_timing(fold, expected_record, month, failures)
    return list(dict.fromkeys(failures))


def _validate_entry_timing(
    fold: dict[str, Any],
    expected: PrequentialMonth,
    month: str,
    failures: list[str],
) -> None:
    """Check that selection uses prior data and fills later on the same buy-ready day."""
    try:
        feature_cutoff = pd.Timestamp(fold["selection_feature_cutoff"])
        selection_time = pd.Timestamp(fold["selection_timestamp"])
        execution_time = pd.Timestamp(fold["execution_timestamp"])
    except (KeyError, TypeError, ValueError):
        failures.append(f"selection_timing_missing:{month}")
        return
    feature_cutoff = _utc_naive(feature_cutoff)
    selection_time = _utc_naive(selection_time)
    execution_time = _utc_naive(execution_time)
    evaluation_start = pd.Timestamp(expected.evaluation_start)
    evaluation_end = pd.Timestamp(expected.evaluation_end) + pd.Timedelta(days=1)
    if not feature_cutoff < selection_time < execution_time:
        failures.append(f"selection_timing_not_causal:{month}")
    if not evaluation_start <= selection_time < evaluation_end:
        failures.append(f"selection_outside_evaluation_month:{month}")
    if selection_time.date() != execution_time.date():
        failures.append(f"selection_execution_not_same_day:{month}")


def _utc_naive(timestamp: pd.Timestamp) -> pd.Timestamp:
    """Normalize mixed timezone inputs before causal timestamp comparisons."""
    if timestamp.tzinfo is None:
        return timestamp
    return timestamp.tz_convert("UTC").tz_localize(None)


def _cutoff_is_causal(value: Any, latest_allowed: str) -> bool:
    """Accept the last available session on or before a calendar cutoff."""
    try:
        cutoff = _utc_naive(pd.Timestamp(value)).normalize()
    except (TypeError, ValueError):
        return False
    return cutoff <= pd.Timestamp(latest_allowed)
