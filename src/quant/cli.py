"""Command-line interface for quantitative T+0 research."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .all_a_data import TushareAllADailyProvider
from .backtest import BacktestEngine
from .config import load_quant_config
from .data import create_data_provider, ensure_bundle_coverage
from .factor_snapshot_data import FactorSnapshotProvider
from .optimize import WalkForwardOptimizer, load_locked_parameters
from .prequential_portfolio import PrequentialPortfolioRunner
from .report import run_holdout_research, write_holdout_report
from .strategy_tournament import run_tournament


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-share intraday T+0 quantitative backtesting")
    parser.add_argument("--config", required=True, help="Path to a quant JSON configuration")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch and cache minute and corporate-action data")
    fetch.add_argument("--force", action="store_true", help="Refresh existing cache partitions")
    fetch.add_argument("--max-days", type=int, default=None, help="Override the all-A daily partition limit")

    optimize = subparsers.add_parser("optimize", help="Select parameters with rolling validation")
    optimize.add_argument("--max-candidates", type=int, default=None, help="Limit candidates for a smoke run")

    backtest = subparsers.add_parser("backtest", help="Run a configured or locked backtest")
    backtest.add_argument("--holdout", action="store_true", help="Use the locked parameters and blind holdout")

    research = subparsers.add_parser("research", help="Fetch, optimize, and run the blind holdout")
    research.add_argument("--force-fetch", action="store_true", help="Refresh cached data")
    research.add_argument("--max-candidates", type=int, default=None, help="Limit candidates for a smoke run")
    subparsers.add_parser("prequential", help="Run strict all-A monthly factor research from cached data")
    factors = subparsers.add_parser("fetch-factors", help="Fetch point-in-time all-A factor snapshots")
    factors.add_argument("--force", action="store_true", help="Refresh existing factor snapshots")
    tournament = subparsers.add_parser("tournament", help="Run three preregistered all-A strategies")
    tournament.add_argument("--fetch-factors", action="store_true", help="Fetch factor snapshots before research")
    return parser


def _locked_config(config):
    return config.with_strategy(**load_locked_parameters(config))


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_quant_config(args.config)
    if config.universe.scope == "all_a":
        provider = TushareAllADailyProvider(config)
        if args.command == "fetch-factors":
            status = FactorSnapshotProvider(config, api=provider.api).fetch(force=args.force)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        if args.command == "tournament":
            print(json.dumps(run_tournament(config, fetch_factors=args.fetch_factors), ensure_ascii=False, indent=2))
            return 0
        if args.command == "prequential":
            bundle = provider.load_cached(config.start_date, config.end_date)
            print(
                json.dumps(
                    PrequentialPortfolioRunner(config, provider, bundle).run(),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command != "fetch":
            raise ValueError("All-A portfolio optimization and backtesting use the prequential command")
        max_days = config.data.daily_max_days_per_run if args.max_days is None else args.max_days
        if max_days < 0:
            raise ValueError("--max-days must not be negative")
        status = provider.fetch(
            max_days=max_days,
            force=args.force,
        )
        print(
            json.dumps(
                {
                    "expected_days": status["expected_days"],
                    "fetched_days": len(status["fetched_days"]),
                    "remaining_days": len(status["remaining_days"]),
                    "next_missing_day": status["remaining_days"][0] if status["remaining_days"] else None,
                    "complete": status["complete"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    provider = create_data_provider(config)

    if args.command == "fetch":
        bundle = provider.fetch(force=args.force)
        print(
            json.dumps(
                {
                    "rows": len(bundle.bars),
                    "start": str(bundle.bars.datetime.min()),
                    "end": str(bundle.bars.datetime.max()),
                }
            )
        )
        return 0
    if args.command == "optimize":
        bundle = provider.load()
        ensure_bundle_coverage(bundle, config.start_date, config.optimization.holdout_start)
        optimizer = WalkForwardOptimizer(config, bundle)
        best, ranked = optimizer.run(max_candidates=args.max_candidates)
        optimizer.save(best, ranked)
        print(json.dumps(best, ensure_ascii=False, indent=2))
        return 0
    if args.command == "backtest":
        bundle = provider.load()
        if args.holdout:
            ensure_bundle_coverage(bundle, config.optimization.holdout_start, config.optimization.holdout_end)
            summary = run_holdout_research(_locked_config(config), bundle)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            result = BacktestEngine(config).run(bundle)
            output_dir = Path(config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            write_holdout_report(config, result, BacktestEngine(config, strategy_enabled=False).run(bundle), {})
            print(json.dumps(result.metrics, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "research":
        bundle = provider.fetch(force=args.force_fetch)
        ensure_bundle_coverage(bundle, config.start_date, config.end_date)
        optimizer = WalkForwardOptimizer(config, bundle)
        best, ranked = optimizer.run(max_candidates=args.max_candidates)
        optimizer.save(best, ranked)
        summary = run_holdout_research(config.with_strategy(**load_locked_parameters(config)), bundle)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    raise RuntimeError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
