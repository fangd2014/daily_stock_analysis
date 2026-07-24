"""Run three preregistered non-chip stock-selection strategies under one audit protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd

from .all_a_data import TushareAllADailyProvider
from .all_a_selector import MonthlyPortfolioSelection
from .config import QuantConfig, load_quant_config
from .factor_experiments import ExitPolicy, preregistered_exit_policies
from .factor_snapshot_data import FactorSnapshotProvider
from .multi_strategy_selector import STRATEGY_NAMES, select_strategy_portfolio
from .prequential_portfolio import PrequentialPortfolioRunner, _hash_frame
from .research_protocol import build_prequential_months, parameter_digest, protocol_template
from .validate_research import validate_research_artifacts, write_validation_result


@dataclass(frozen=True)
class StrategySpecification:
    name: str
    exposure: float
    exit_policy: str
    description: str


def preregistered_strategy_specifications() -> list[StrategySpecification]:
    return [
        StrategySpecification(
            name="quality_value",
            exposure=0.60,
            exit_policy="trend_take_15_stop_7",
            description="Point-in-time profitability, cash quality, leverage, earnings yield, and book-to-price.",
        ),
        StrategySpecification(
            name="industry_leader_momentum",
            exposure=0.60,
            exit_policy="trend_take_15_stop_7",
            description="Stock and industry relative strength with trend, downside-risk, and liquidity gates.",
        ),
        StrategySpecification(
            name="flow_confirmed_reversal",
            exposure=0.60,
            exit_policy="reversal_take_8_stop_5",
            description="Short pullback inside a positive trend confirmed by large-order net buying.",
        ),
    ]


class StrategyTournamentRunner(PrequentialPortfolioRunner):
    """Reuse the conservative execution engine while replacing the chip-only selector."""

    def __init__(
        self,
        config: QuantConfig,
        market_provider: TushareAllADailyProvider,
        factor_provider: FactorSnapshotProvider,
    ) -> None:
        bundle = market_provider.load_cached(config.start_date, config.end_date)
        super().__init__(config, market_provider, bundle)
        self.factor_provider = factor_provider
        self.strategy_selection_cache: dict[tuple[str, str], MonthlyPortfolioSelection] = {}
        self.monthly_factor_cutoffs = self._factor_cutoffs_by_month()
        self.factor_hash_cache: dict[str, dict[str, str]] = {}

    def _factor_cutoffs_by_month(self) -> dict[str, str]:
        cutoffs = self.factor_provider.required_monthly_cutoffs()
        months = [
            str(month)
            for month in pd.period_range(
                self.config.optimization.holdout_start,
                self.config.optimization.holdout_end,
                freq="M",
            )
        ]
        if len(cutoffs) != len(months):
            raise ValueError("Factor snapshot cutoffs do not match the holdout months")
        return dict(zip(months, cutoffs))

    def _strategy_selection(self, month: str, strategy_name: str) -> MonthlyPortfolioSelection:
        cache_key = (month, strategy_name)
        if cache_key in self.strategy_selection_cache:
            return self.strategy_selection_cache[cache_key]
        month_dates = self._month_dates(month)
        if not month_dates:
            raise ValueError(f"No trading sessions are available for {month}")
        factor_cutoff = self.monthly_factor_cutoffs[month]
        daily_basic = self.factor_provider.load_daily_basic(factor_cutoff)
        moneyflow = self.factor_provider.load_moneyflow(factor_cutoff)
        financials = self.factor_provider.load_financials(factor_cutoff)
        universe = self._universe_as_of(factor_cutoff)
        last_error: ValueError | None = None
        for candidate_date in month_dates:
            candidate_index = self.trade_dates.index(candidate_date)
            if candidate_index < 121:
                raise ValueError(f"Insufficient price history before {candidate_date}")
            history_dates = self.trade_dates[candidate_index - 121 : candidate_index]
            input_dates = history_dates + [candidate_date]
            bars = self.bars.loc[input_dates].reset_index(drop=True)
            limits = self.limits.loc[[candidate_date]].reset_index(drop=True)
            try:
                selection = select_strategy_portfolio(
                    self.config,
                    strategy_name,
                    bars,
                    limits,
                    universe,
                    pd.Timestamp(candidate_date).strftime("%Y-%m-%d"),
                    factor_cutoff,
                    daily_basic,
                    financials,
                    moneyflow,
                )
                self.strategy_selection_cache[cache_key] = selection
                return selection
            except ValueError as error:
                if "buy-ready" not in str(error):
                    raise
                last_error = error
        raise ValueError(f"No session has five {strategy_name} stocks in {month}") from last_error

    def _exit_policy(self, name: str) -> ExitPolicy:
        try:
            return next(policy for policy in preregistered_exit_policies() if policy.name == name)
        except StopIteration as error:
            raise ValueError(f"Exit policy is not preregistered: {name}") from error

    def _factor_hashes(self, cutoff: str) -> dict[str, str]:
        if cutoff in self.factor_hash_cache:
            return self.factor_hash_cache[cutoff]
        frames = {
            "daily_basic": self.factor_provider.load_daily_basic(cutoff),
            "moneyflow": self.factor_provider.load_moneyflow(cutoff),
            "financials": self.factor_provider.load_financials(cutoff),
        }
        hashes = {}
        for name, frame in frames.items():
            values = frame.sort_values(list(frame.columns)).reset_index(drop=True)
            payload = values.to_csv(index=False, lineterminator="\n", float_format="%.10g")
            hashes[name] = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.factor_hash_cache[cutoff] = hashes
        return hashes

    def run_strategy(self, specification: StrategySpecification, output_dir: Path) -> dict[str, Any]:
        """Run one fixed selector exactly once across all 36 evaluation months."""
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = protocol_template(self.config)
        manifest["selection_strategy"] = specification.name
        manifest_folds = []
        equity = float(self.config.portfolio.initial_cash)
        equity_frames = []
        monthly_records = []
        selection_records = []
        exit_policy = self._exit_policy(specification.exit_policy)
        for record in build_prequential_months(self.config):
            selection = self._strategy_selection(record.month, specification.name)
            curve = self._month_curve(record.month, selection, specification.exposure, exit_policy)
            scaled = curve * equity
            equity_frames.append(pd.DataFrame({"datetime": scaled.index, "equity": scaled.to_numpy()}))
            month_return = float(curve.iloc[-1] - 1.0)
            monthly_records.append({"month": record.month, "return": month_return})
            equity = float(scaled.iloc[-1])
            symbols = [candidate.symbol for candidate in selection.selected]
            selection_date = selection.selection_date
            selection_index = self.trade_dates.index(selection_date)
            history_dates = self.trade_dates[selection_index - 121 : selection_index]
            history = self.bars.loc[history_dates].reset_index(drop=True)
            history = history[history["ts_code"].isin(symbols)]
            factor_cutoff = self.monthly_factor_cutoffs[record.month]
            universe = self._universe_as_of(factor_cutoff)
            parameters = {
                "selection_mode": "fixed_preregistered",
                "selection_strategy": asdict(specification),
                "exit_policy": asdict(exit_policy),
            }
            manifest_folds.append(
                {
                    **asdict(record),
                    "parameter_data_cutoff": record.validation_end,
                    "parameters": parameters,
                    "parameter_digest": parameter_digest(parameters),
                    "selected_symbols": symbols,
                    "buy_ready": {candidate.symbol: candidate.buy_ready for candidate in selection.selected},
                    "data_fingerprints": {
                        symbol: _hash_frame(
                            history[history["ts_code"].eq(symbol)],
                            ["ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount_yuan"],
                        )
                        for symbol in symbols
                    },
                    "factor_snapshot_hashes": self._factor_hashes(factor_cutoff),
                    "factor_data_cutoff": pd.Timestamp(factor_cutoff).strftime("%Y-%m-%d"),
                    "universe_snapshot_hash": _hash_frame(universe, ["ts_code", "name", "list_date"]),
                    "universe_data_cutoff": pd.Timestamp(factor_cutoff).strftime("%Y-%m-%d"),
                    "selection_feature_cutoff": f"{pd.Timestamp(selection.feature_cutoff):%Y-%m-%d}T15:00:00",
                    "selection_timestamp": f"{pd.Timestamp(selection_date):%Y-%m-%d}T09:30:00",
                    "execution_timestamp": f"{pd.Timestamp(selection_date):%Y-%m-%d}T09:35:00",
                }
            )
            selection_records.append(
                {
                    "month": record.month,
                    "selection_date": selection_date,
                    "factor_cutoff": factor_cutoff,
                    "selected": [asdict(candidate) for candidate in selection.selected],
                }
            )
            print(
                f"completed {specification.name} {record.month}: return={month_return:.4f}, "
                f"entry={selection_date}",
                flush=True,
            )
        pd.concat(equity_frames, ignore_index=True).to_csv(output_dir / "equity.csv", index=False)
        pd.DataFrame(monthly_records).to_csv(output_dir / "monthly_returns.csv", index=False)
        manifest["folds"] = manifest_folds
        (output_dir / "research_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "selections.json").write_text(
            json.dumps(selection_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary = {
            "status": "completed",
            "strategy": specification.name,
            "months": len(monthly_records),
            "end_equity": equity,
            "median_monthly_return": float(pd.DataFrame(monthly_records)["return"].median()),
        }
        (output_dir / "prequential_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return summary


def run_tournament(config: QuantConfig, fetch_factors: bool = False) -> dict[str, Any]:
    market_provider = TushareAllADailyProvider(config)
    factor_provider = FactorSnapshotProvider(config, api=market_provider.api)
    if fetch_factors:
        factor_provider.fetch()
    runner = StrategyTournamentRunner(config, market_provider, factor_provider)
    root = Path(config.output_dir) / "strategy_tournament"
    results = []
    for specification in preregistered_strategy_specifications():
        output_dir = root / specification.name
        summary = runner.run_strategy(specification, output_dir)
        strategy_config = replace(config, output_dir=str(output_dir))
        validation = validate_research_artifacts(strategy_config)
        write_validation_result(validation, output_dir / "research_validation.json")
        results.append({"specification": asdict(specification), "summary": summary, "validation": asdict(validation)})
    payload = {"status": "completed", "strategies": results}
    root.mkdir(parents=True, exist_ok=True)
    (root / "tournament_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run three all-A stock-selection strategies")
    parser.add_argument("--config", required=True)
    parser.add_argument("--fetch-factors", action="store_true")
    args = parser.parse_args()
    config = load_quant_config(args.config)
    result = run_tournament(config, fetch_factors=args.fetch_factors)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
