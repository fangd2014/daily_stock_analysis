"""Walk-forward parameter selection with a locked out-of-sample holdout."""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .backtest import BacktestEngine, slice_bundle
from .config import QuantConfig
from .data import QuantDataBundle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalkForwardFold:
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str


def build_walk_forward_folds(config: QuantConfig) -> list[WalkForwardFold]:
    """Build rolling train/validation windows ending before the blind holdout."""
    optimization = config.optimization
    cursor = pd.Timestamp(config.start_date).normalize()
    development_end = pd.Timestamp(optimization.holdout_start).normalize() - pd.Timedelta(days=1)
    folds: list[WalkForwardFold] = []
    while True:
        train_end = cursor + pd.DateOffset(months=optimization.train_months) - pd.Timedelta(days=1)
        validation_start = train_end + pd.Timedelta(days=1)
        validation_end = validation_start + pd.DateOffset(months=optimization.validation_months) - pd.Timedelta(days=1)
        if validation_end > development_end:
            break
        folds.append(
            WalkForwardFold(
                train_start=cursor.strftime("%Y-%m-%d"),
                train_end=train_end.strftime("%Y-%m-%d"),
                validation_start=validation_start.strftime("%Y-%m-%d"),
                validation_end=validation_end.strftime("%Y-%m-%d"),
            )
        )
        cursor += pd.DateOffset(months=optimization.step_months)
    if not folds:
        raise ValueError("Configured date range cannot produce a walk-forward fold")
    return folds


def parameter_grid(config: QuantConfig) -> Iterable[dict[str, Any]]:
    """Yield the bounded parameter grid declared in the research config."""
    optimization = config.optimization
    if config.strategy.strategy_type == "chip_double_peak":
        for lookback, low, high, exit_value, fraction, stop in itertools.product(
            optimization.chip_lookback_days_values,
            optimization.chip_low_entry_position_values,
            optimization.chip_high_entry_position_values,
            optimization.chip_exit_position_values,
            optimization.position_fractions,
            optimization.stop_loss_values,
        ):
            if not low < exit_value < high:
                continue
            yield {
                "chip_lookback_days": int(lookback),
                "chip_low_entry_position": float(low),
                "chip_high_entry_position": float(high),
                "chip_exit_position": float(exit_value),
                "position_fraction": float(fraction),
                "stop_loss_pct": float(stop),
            }
        return
    for window, entry, exit_value, fraction, stop in itertools.product(
        optimization.zscore_windows,
        optimization.entry_z_values,
        optimization.exit_z_values,
        optimization.position_fractions,
        optimization.stop_loss_values,
    ):
        if exit_value >= entry:
            continue
        yield {
            "zscore_window": int(window),
            "entry_z": float(entry),
            "exit_z": float(exit_value),
            "position_fraction": float(fraction),
            "stop_loss_pct": float(stop),
        }


def _safe_number(value: Any) -> float:
    number = float(value)
    if np.isposinf(number):
        return 1_000_000.0
    if np.isneginf(number):
        return -1_000_000.0
    if np.isnan(number):
        return 0.0
    return number


class WalkForwardOptimizer:
    """Rank stable parameters without reading the final holdout during selection."""

    def __init__(self, config: QuantConfig, bundle: QuantDataBundle):
        self.config = config
        self.bundle = bundle

    def run(self, max_candidates: int | None = None) -> tuple[dict[str, Any], pd.DataFrame]:
        folds = build_walk_forward_folds(self.config)
        candidates = list(parameter_grid(self.config))
        if max_candidates is not None:
            candidates = candidates[: max(1, max_candidates)]
        records: list[dict[str, Any]] = []
        for candidate_index, parameters in enumerate(candidates, start=1):
            logger.info("Evaluating candidate %d/%d: %s", candidate_index, len(candidates), parameters)
            fold_records: list[dict[str, Any]] = []
            for fold_index, fold in enumerate(folds):
                candidate_config = self.config.with_strategy(**parameters)
                warmup_days = _warmup_days(candidate_config)
                train_result = BacktestEngine(candidate_config).run(
                    slice_bundle(self.bundle, fold.train_start, fold.train_end, warmup_days=warmup_days),
                    evaluation_start=fold.train_start,
                )
                validation_result = BacktestEngine(candidate_config).run(
                    slice_bundle(
                        self.bundle,
                        fold.validation_start,
                        fold.validation_end,
                        warmup_days=warmup_days,
                    ),
                    evaluation_start=fold.validation_start,
                )
                train_days = max(
                    (pd.Timestamp(fold.train_end) - pd.Timestamp(fold.train_start)).days,
                    1,
                )
                trades_per_year = train_result.metrics["pair_count"] * 365.25 / train_days
                fold_records.append(
                    {
                        "fold": fold_index,
                        "train_max_drawdown": train_result.metrics["max_drawdown"],
                        "train_trades_per_year": trades_per_year,
                        "validation_calmar": _safe_number(validation_result.metrics["calmar"]),
                        "validation_annual_return": validation_result.metrics["annual_return"],
                        "validation_max_drawdown": validation_result.metrics["max_drawdown"],
                        "validation_turnover": validation_result.metrics["turnover"],
                    }
                )
            fold_frame = pd.DataFrame(fold_records)
            robust = bool(
                fold_frame["train_max_drawdown"].le(self.config.acceptance.max_drawdown_max).all()
                and fold_frame["train_trades_per_year"].ge(self.config.optimization.min_trades_per_year).all()
            )
            records.append(
                {
                    **parameters,
                    "robust": robust,
                    "validation_calmar_median": float(fold_frame["validation_calmar"].median()),
                    "validation_annual_return_median": float(fold_frame["validation_annual_return"].median()),
                    "validation_max_drawdown_worst": float(fold_frame["validation_max_drawdown"].max()),
                    "train_max_drawdown_worst": float(fold_frame["train_max_drawdown"].max()),
                    "train_trades_per_year_min": float(fold_frame["train_trades_per_year"].min()),
                    "validation_turnover_median": float(fold_frame["validation_turnover"].median()),
                }
            )
        results = pd.DataFrame(records)
        if results.empty:
            raise ValueError("Parameter grid is empty")
        ranked = results.sort_values(
            ["robust", "validation_calmar_median", "validation_max_drawdown_worst", "validation_turnover_median"],
            ascending=[False, False, True, True],
        ).reset_index(drop=True)
        parameter_keys = list(candidates[0])
        best = {key: ranked.iloc[0][key].item() for key in parameter_keys}
        best["robust"] = bool(ranked.iloc[0]["robust"])
        best["folds"] = [asdict(fold) for fold in folds]
        return best, ranked

    def save(self, best: dict[str, Any], ranked: pd.DataFrame) -> None:
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "locked_params.json").write_text(
            json.dumps(best, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        ranked.to_csv(output_dir / "optimization_results.csv", index=False)


def load_locked_parameters(config: QuantConfig) -> dict[str, Any]:
    """Load only the strategy values chosen before holdout evaluation."""
    path = Path(config.output_dir) / "locked_params.json"
    if not path.exists():
        raise FileNotFoundError(f"Locked parameters not found: {path}; run optimize first")
    values = json.loads(path.read_text(encoding="utf-8"))
    if config.strategy.strategy_type == "chip_double_peak":
        keys = {
            "chip_lookback_days",
            "chip_low_entry_position",
            "chip_high_entry_position",
            "chip_exit_position",
            "position_fraction",
            "stop_loss_pct",
        }
    else:
        keys = {"zscore_window", "entry_z", "exit_z", "position_fraction", "stop_loss_pct"}
    return {key: values[key] for key in keys}


def _warmup_days(config: QuantConfig) -> int:
    if config.strategy.strategy_type == "chip_double_peak":
        return max(45, config.strategy.chip_lookback_days * 2)
    return 45
