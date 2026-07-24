"""Strict monthly prequential runner for the all-A five-stock factor portfolio."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .all_a_data import AllADailyBundle, TushareAllADailyProvider
from .all_a_selector import MonthlyPortfolioSelection, _rank_candidates, select_monthly_portfolio
from .config import QuantConfig, load_quant_config
from .factor_experiments import (
    ExitPolicy,
    FactorExperiment,
    preregistered_exit_policies,
    preregistered_factor_experiments,
    validate_experiment_registry,
)
from .research_protocol import build_prequential_months, parameter_digest, protocol_template


EXPOSURE_CANDIDATES = (0.30, 0.60, 1.00)
ENTRY_ZONE_CANDIDATES = (0.28, 0.35, 0.45)


def _hash_frame(frame: pd.DataFrame, columns: list[str]) -> str:
    values = frame.loc[:, columns].copy().sort_values(columns).reset_index(drop=True)
    payload = values.to_csv(index=False, lineterminator="\n", float_format="%.10g")
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PrequentialPortfolioRunner:
    """Select factor specifications on prior validation months and execute each OOS month once."""

    def __init__(
        self,
        config: QuantConfig,
        provider: TushareAllADailyProvider,
        bundle: AllADailyBundle,
    ) -> None:
        self.config = config
        self.provider = provider
        self.bars = bundle.bars.copy()
        self.limits = bundle.limits.copy()
        self.bars["trade_date"] = self.bars["trade_date"].astype(str)
        self.limits["trade_date"] = self.limits["trade_date"].astype(str)
        self.trade_dates = sorted(self.bars["trade_date"].unique().tolist())
        self.trade_dates_by_month: dict[str, list[str]] = {}
        for trade_date in self.trade_dates:
            self.trade_dates_by_month.setdefault(trade_date[:6], []).append(trade_date)
        self.bars = self.bars.set_index("trade_date", drop=False)
        self.limits = self.limits.set_index("trade_date", drop=False)
        self.experiments = preregistered_factor_experiments()
        self.exit_policies = preregistered_exit_policies()
        validate_experiment_registry()
        self.selection_cache: dict[tuple[str, str, float], MonthlyPortfolioSelection] = {}
        self.base_selection_cache: dict[tuple[str, float], MonthlyPortfolioSelection] = {}
        self.month_curve_cache: dict[tuple[str, tuple[str, ...], str, float, str], pd.Series] = {}
        self.universe_cache: dict[str, pd.DataFrame] = {}

    def _month_dates(self, month: str) -> list[str]:
        return self.trade_dates_by_month.get(month.replace("-", ""), [])

    def _universe_as_of(self, feature_cutoff: str) -> pd.DataFrame:
        cutoff = feature_cutoff.replace("-", "")
        if cutoff not in self.universe_cache:
            self.universe_cache[cutoff] = self.provider.reconstruct_cached_universe(cutoff)
        return self.universe_cache[cutoff]

    def _selection(
        self,
        month: str,
        experiment: FactorExperiment,
        entry_zone: float,
    ) -> MonthlyPortfolioSelection:
        cache_key = (month, experiment.name, entry_zone)
        if cache_key in self.selection_cache:
            return self.selection_cache[cache_key]
        month_dates = self._month_dates(month)
        if not month_dates:
            raise ValueError(f"No trading sessions are available for {month}")
        selection_date = month_dates[0]
        date_index = self.trade_dates.index(selection_date)
        if date_index < 61:
            raise ValueError(f"Insufficient factor history before {selection_date}")
        history_dates = self.trade_dates[date_index - 61 : date_index]
        base_key = (month, entry_zone)
        if base_key not in self.base_selection_cache:
            selection_config = replace(
                self.config,
                strategy=replace(
                    self.config.strategy,
                    chip_low_entry_position=entry_zone,
                ),
            )
            last_error: ValueError | None = None
            for candidate_date in month_dates:
                candidate_index = self.trade_dates.index(candidate_date)
                candidate_history = self.trade_dates[candidate_index - 61 : candidate_index]
                input_dates = candidate_history + [candidate_date]
                bars = self.bars.loc[input_dates].reset_index(drop=True)
                limits = self.limits.loc[[candidate_date]].reset_index(drop=True)
                universe = self._universe_as_of(candidate_history[-1])
                try:
                    self.base_selection_cache[base_key] = select_monthly_portfolio(
                        selection_config,
                        bars,
                        limits,
                        universe,
                        pd.Timestamp(candidate_date).strftime("%Y-%m-%d"),
                    )
                    break
                except ValueError as error:
                    if "are buy-ready" not in str(error):
                        raise
                    last_error = error
            if base_key not in self.base_selection_cache:
                raise ValueError(f"No session has five buy-ready stocks in {month} at {entry_zone:.2f}") from last_error
        base = self.base_selection_cache[base_key]
        ranked = _rank_candidates(base.candidates, experiment.weights)
        required = self.config.portfolio.max_positions
        if len(ranked) < required:
            raise ValueError(f"Only {len(ranked)} all-A stocks are buy-ready; {required} are required")
        result = MonthlyPortfolioSelection(
            selection_date=base.selection_date,
            feature_cutoff=base.feature_cutoff,
            candidates=base.candidates,
            selected=ranked[:required],
        )
        self.selection_cache[cache_key] = result
        return result

    def _roundtrip_costs(self, exposure: float) -> tuple[float, float]:
        costs = self.config.costs
        notional = self.config.portfolio.initial_cash * exposure / self.config.portfolio.max_positions
        commission = max(costs.commission_rate, costs.minimum_commission / max(notional, 1.0))
        buy = costs.slippage_bps / 10_000.0 + commission + costs.transfer_fee_rate
        sell = buy + costs.stamp_tax_rate
        return buy, sell

    def _month_curve(
        self,
        month: str,
        selection: MonthlyPortfolioSelection,
        exposure: float,
        exit_policy: ExitPolicy,
    ) -> pd.Series:
        month_dates = self._month_dates(month)
        symbols = [candidate.symbol for candidate in selection.selected]
        entry_date = selection.selection_date
        invested_dates = [trade_date for trade_date in month_dates if trade_date >= entry_date]
        curve_key = (month, tuple(symbols), entry_date, exposure, exit_policy.name)
        cached = self.month_curve_cache.get(curve_key)
        if cached is not None:
            return cached.copy()
        month_bars = self.bars.loc[invested_dates].reset_index(drop=True)
        month_bars = month_bars[month_bars["ts_code"].isin(symbols)].copy()
        opens = month_bars[month_bars["trade_date"].eq(entry_date)].set_index("ts_code")["open"]
        if any(symbol not in opens.index or float(opens[symbol]) <= 0 for symbol in symbols):
            raise ValueError(f"Selected entry open is unavailable for {month}")
        buy_cost, sell_cost = self._roundtrip_costs(exposure)
        candidate_by_symbol = {candidate.symbol: candidate for candidate in selection.selected}
        symbol_curves = []
        for symbol in symbols:
            entry_price = float(opens[symbol])
            candidate = candidate_by_symbol[symbol]
            stop_price = entry_price * (1.0 - exit_policy.stop_loss_pct) if exit_policy.stop_loss_pct else None
            target_price = None
            if (
                exit_policy.take_profit_position is not None
                and candidate.lower_peak is not None
                and candidate.upper_peak is not None
            ):
                target_price = candidate.lower_peak + exit_policy.take_profit_position * (
                    candidate.upper_peak - candidate.lower_peak
                )
                if target_price <= entry_price:
                    target_price = None
            if exit_policy.take_profit_return is not None:
                target_price = entry_price * (1.0 + exit_policy.take_profit_return)
            rows = month_bars[month_bars["ts_code"].eq(symbol)].set_index("trade_date")
            limit_rows = self.limits.loc[invested_dates].reset_index(drop=True)
            limit_rows = limit_rows[limit_rows["ts_code"].eq(symbol)].set_index("trade_date")
            held = True
            last_price = entry_price
            cash_wealth: float | None = None
            values = []
            for session_number, trade_date in enumerate(invested_dates, start=1):
                row = rows.loc[trade_date] if trade_date in rows.index else None
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[-1]
                if row is not None and pd.notna(row.get("close")):
                    last_price = float(row["close"])
                if held and session_number > 1 and row is not None and float(row.get("volume", 0.0)) > 0:
                    limit_row = limit_rows.loc[trade_date] if trade_date in limit_rows.index else None
                    if isinstance(limit_row, pd.DataFrame):
                        limit_row = limit_row.iloc[-1]
                    down_limit = None
                    if limit_row is not None and pd.notna(limit_row.get("down_limit")):
                        down_limit = float(limit_row["down_limit"])
                    open_price = float(row["open"])
                    high_price = float(row["high"])
                    low_price = float(row["low"])
                    tolerance = max(open_price * 1e-6, 1e-6)
                    locked_down = (
                        down_limit is not None
                        and open_price <= down_limit + tolerance
                        and high_price <= down_limit + tolerance
                    )
                    exit_price = None
                    if not locked_down and stop_price is not None and low_price <= stop_price:
                        exit_price = min(open_price, stop_price)
                    elif not locked_down and target_price is not None and high_price >= target_price:
                        exit_price = target_price
                    elif (
                        not locked_down
                        and exit_policy.max_holding_sessions > 0
                        and session_number >= exit_policy.max_holding_sessions
                    ):
                        exit_price = last_price
                    if exit_price is not None:
                        cash_wealth = (1.0 - buy_cost) * exit_price / entry_price * (1.0 - sell_cost)
                        held = False
                if held:
                    values.append((1.0 - buy_cost) * last_price / entry_price)
                else:
                    values.append(float(cash_wealth))
            if held:
                values[-1] *= 1.0 - sell_cost
            symbol_curves.append(pd.Series(values, index=invested_dates, dtype=float))
        invested = pd.concat(symbol_curves, axis=1).mean(axis=1)
        invested.index = pd.to_datetime(invested.index, format="%Y%m%d") + pd.Timedelta(hours=15)
        portfolio = (1.0 - exposure) + exposure * invested
        waiting_dates = [trade_date for trade_date in month_dates if trade_date < entry_date]
        if waiting_dates:
            waiting_index = pd.to_datetime(waiting_dates, format="%Y%m%d") + pd.Timedelta(hours=15)
            portfolio = pd.concat([pd.Series(1.0, index=waiting_index), portfolio])
        result = portfolio.astype(float)
        self.month_curve_cache[curve_key] = result
        return result.copy()

    def _validation_score(
        self,
        experiment: FactorExperiment,
        exposure: float,
        entry_zone: float,
        exit_policy: ExitPolicy,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        months = [
            str(month)
            for month in pd.period_range(pd.Timestamp(start_date), pd.Timestamp(end_date), freq="M")
        ]
        returns = []
        combined = []
        equity = 1.0
        for month in months:
            try:
                selection = self._selection(month, experiment, entry_zone)
                curve = self._month_curve(month, selection, exposure, exit_policy)
            except ValueError:
                return {
                    "valid": False,
                    "months": len(returns),
                    "median_monthly_return": -1.0,
                    "total_return": -1.0,
                    "max_drawdown": 1.0,
                }
            scaled = curve * equity
            combined.append(scaled)
            monthly_return = float(curve.iloc[-1] - 1.0)
            returns.append(monthly_return)
            equity = float(scaled.iloc[-1])
        series = pd.concat(combined)
        drawdown = series / series.cummax() - 1.0
        return {
            "valid": len(returns) == len(months),
            "months": len(returns),
            "median_monthly_return": float(np.median(returns)),
            "total_return": equity - 1.0,
            "max_drawdown": abs(float(drawdown.min())),
        }

    def _select_parameters(
        self,
        record: Any,
    ) -> tuple[FactorExperiment, float, float, ExitPolicy, dict[str, Any], list[dict[str, Any]]]:
        optimization = self.config.optimization
        if optimization.parameter_selection_mode == "fixed_preregistered":
            try:
                experiment = next(
                    item for item in self.experiments if item.name == optimization.fixed_factor_experiment
                )
                exit_policy = next(
                    item for item in self.exit_policies if item.name == optimization.fixed_exit_policy
                )
            except StopIteration as error:
                raise ValueError("Configured fixed factor or exit policy is not preregistered") from error
            fixed = {
                "valid": True,
                "selection_mode": "fixed_preregistered",
                "experiment": experiment.name,
                "exposure": optimization.fixed_exposure,
                "entry_zone": optimization.fixed_entry_zone,
                "exit_policy": exit_policy.name,
                "months": 0,
                "median_monthly_return": None,
                "total_return": None,
                "max_drawdown": None,
            }
            return (
                experiment,
                optimization.fixed_exposure,
                optimization.fixed_entry_zone,
                exit_policy,
                fixed,
                [fixed],
            )
        candidates = []
        for experiment in self.experiments:
            for exposure in EXPOSURE_CANDIDATES:
                for entry_zone in ENTRY_ZONE_CANDIDATES:
                    for exit_policy in self.exit_policies:
                        score = self._validation_score(
                            experiment,
                            exposure,
                            entry_zone,
                            exit_policy,
                            record.validation_start,
                            record.validation_end,
                        )
                        candidates.append(
                            {
                                "experiment": experiment.name,
                                "exposure": exposure,
                                "entry_zone": entry_zone,
                                "exit_policy": exit_policy.name,
                                **score,
                            }
                        )
        valid = [candidate for candidate in candidates if candidate["valid"]]
        if not valid:
            raise ValueError(f"No factor experiment completed validation for {record.month}")
        ranked = sorted(
            valid,
            key=lambda item: (
                item["max_drawdown"] <= self.config.acceptance.max_drawdown_max,
                item["median_monthly_return"],
                item["total_return"],
                -item["max_drawdown"],
                -item["exposure"],
                -item["entry_zone"],
                item["exit_policy"],
                item["experiment"],
            ),
            reverse=True,
        )
        best = ranked[0]
        experiment = next(item for item in self.experiments if item.name == best["experiment"])
        exit_policy = next(item for item in self.exit_policies if item.name == best["exit_policy"])
        return experiment, float(best["exposure"]), float(best["entry_zone"]), exit_policy, best, candidates

    def run(self) -> dict[str, Any]:
        """Run all 36 folds and persist the exact artifacts consumed by the independent validator."""
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = protocol_template(self.config)
        manifest_folds = []
        experiment_log = []
        equity = float(self.config.portfolio.initial_cash)
        equity_frames = []
        monthly_records = []
        for record in build_prequential_months(self.config):
            experiment, exposure, entry_zone, exit_policy, validation, candidates = self._select_parameters(record)
            selection = self._selection(record.month, experiment, entry_zone)
            curve = self._month_curve(record.month, selection, exposure, exit_policy)
            scaled = curve * equity
            equity_frames.append(pd.DataFrame({"datetime": scaled.index, "equity": scaled.to_numpy()}))
            month_return = float(curve.iloc[-1] - 1.0)
            monthly_records.append({"month": record.month, "return": month_return})
            equity = float(scaled.iloc[-1])
            selected_symbols = [candidate.symbol for candidate in selection.selected]
            selection_date = selection.selection_date
            date_index = self.trade_dates.index(selection_date)
            history_dates = self.trade_dates[date_index - 61 : date_index]
            history = self.bars.loc[history_dates].reset_index(drop=True)
            history = history[history["ts_code"].isin(selected_symbols)]
            universe_cutoff = selection.feature_cutoff.replace("-", "")
            universe = self._universe_as_of(universe_cutoff)
            weights = asdict(experiment.weights)
            parameters = {
                "factor_experiment": experiment.name,
                "factor_weights": weights,
                "exposure": exposure,
                "entry_zone": entry_zone,
                "exit_policy": asdict(exit_policy),
                "validation": validation,
            }
            fingerprints = {
                symbol: _hash_frame(
                    history[history["ts_code"].eq(symbol)],
                    ["ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount_yuan"],
                )
                for symbol in selected_symbols
            }
            manifest_folds.append(
                {
                    **asdict(record),
                    "parameter_data_cutoff": record.validation_end,
                    "parameters": parameters,
                    "parameter_digest": parameter_digest(parameters),
                    "selected_symbols": selected_symbols,
                    "buy_ready": {candidate.symbol: candidate.buy_ready for candidate in selection.selected},
                    "data_fingerprints": fingerprints,
                    "universe_snapshot_hash": _hash_frame(
                        universe,
                        ["ts_code", "name", "list_date"],
                    ),
                    "universe_data_cutoff": pd.Timestamp(universe_cutoff).strftime("%Y-%m-%d"),
                    "selection_feature_cutoff": f"{pd.Timestamp(selection.feature_cutoff):%Y-%m-%d}T15:00:00",
                    "selection_timestamp": f"{pd.Timestamp(selection_date):%Y-%m-%d}T09:30:00",
                    "execution_timestamp": f"{pd.Timestamp(selection_date):%Y-%m-%d}T09:35:00",
                }
            )
            experiment_log.append(
                {
                    "month": record.month,
                    "selected_experiment": experiment.name,
                    "selected_exposure": exposure,
                    "selected_entry_zone": entry_zone,
                    "selected_exit_policy": exit_policy.name,
                    "validation": validation,
                    "candidates": candidates,
                }
            )
            print(
                f"completed {record.month}: {experiment.name}, exposure={exposure:.2f}, "
                f"entry_zone={entry_zone:.2f}, exit={exit_policy.name}, return={month_return:.4f}",
                flush=True,
            )
        equity_frame = pd.concat(equity_frames, ignore_index=True)
        equity_frame.to_csv(output_dir / "equity.csv", index=False)
        pd.DataFrame(monthly_records).to_csv(output_dir / "monthly_returns.csv", index=False)
        manifest["folds"] = manifest_folds
        (output_dir / "research_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "factor_experiments.json").write_text(
            json.dumps(experiment_log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary = {
            "status": "completed",
            "months": len(monthly_records),
            "end_equity": equity,
            "median_monthly_return": float(pd.DataFrame(monthly_records)["return"].median()),
        }
        (output_dir / "prequential_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strict all-A prequential factor research")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_quant_config(args.config)
    provider = TushareAllADailyProvider(config)
    bundle = provider.load_cached(config.start_date, config.end_date)
    result = PrequentialPortfolioRunner(config, provider, bundle).run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
