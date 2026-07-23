"""Command-line interface for quantitative T+0 research."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .backtest import BacktestEngine
from .config import load_quant_config
from .data import create_data_provider, ensure_bundle_coverage
from .optimize import WalkForwardOptimizer, load_locked_parameters
from .report import run_holdout_research, write_holdout_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-share intraday T+0 quantitative backtesting")
    parser.add_argument("--config", required=True, help="Path to a quant JSON configuration")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch and cache minute and corporate-action data")
    fetch.add_argument("--force", action="store_true", help="Refresh existing cache partitions")

    optimize = subparsers.add_parser("optimize", help="Select parameters with rolling validation")
    optimize.add_argument("--max-candidates", type=int, default=None, help="Limit candidates for a smoke run")

    backtest = subparsers.add_parser("backtest", help="Run a configured or locked backtest")
    backtest.add_argument("--holdout", action="store_true", help="Use the locked parameters and blind holdout")

    research = subparsers.add_parser("research", help="Fetch, optimize, and run the blind holdout")
    research.add_argument("--force-fetch", action="store_true", help="Refresh cached data")
    research.add_argument("--max-candidates", type=int, default=None, help="Limit candidates for a smoke run")
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
