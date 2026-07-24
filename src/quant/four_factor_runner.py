"""Executable point-in-time pipeline for the four-factor selection strategy."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from .four_factor_strategy import (
    IntervalLearningConfig,
    IntervalLearningModel,
    compute_auction_linkage,
    compute_index_momentum,
    compute_semantic_sentiment,
    rank_three_factor_watchlist,
    select_four_factor_portfolio,
)


LOGGER = logging.getLogger(__name__)


def _load_frame(root: Path, name: str, required: bool = True) -> pd.DataFrame | None:
    parquet = root / f"{name}.parquet"
    csv = root / f"{name}.csv"
    if parquet.exists():
        frame = pd.read_parquet(parquet)
    elif csv.exists():
        frame = pd.read_csv(csv)
    elif required:
        raise FileNotFoundError(f"Required input is missing: {parquet} or {csv}")
    else:
        return None
    LOGGER.info("Loaded factor input: dataset=%s rows=%d", name, len(frame))
    return frame


def _write_frame(root: Path, name: str, frame: pd.DataFrame) -> None:
    root.mkdir(parents=True, exist_ok=True)
    frame.to_csv(root / f"{name}.csv", index=False, encoding="utf-8-sig")


def _read_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_four_factor_pipeline(
    config_path: str | Path,
    input_dir: str | Path,
    output_dir: str | Path,
    as_of: str | pd.Timestamp,
) -> dict[str, Any]:
    """Compute all observable factors, apply gates, and persist an auditable report."""
    config = _read_config(config_path)
    source = Path(input_dir)
    output = Path(output_dir) / pd.Timestamp(as_of).strftime("%Y%m%d_%H%M%S")
    LOGGER.info("Four-factor pipeline started: as_of=%s input=%s", as_of, source)

    stocks = _load_frame(source, "stocks")
    index_bars = _load_frame(source, "index_bars")
    member_bars = _load_frame(source, "member_bars", required=False)
    events = _load_frame(source, "sentiment_events")
    training = _load_frame(source, "interval_training")
    current = _load_frame(source, "current_features")
    auction_snapshots = _load_frame(source, "auction_snapshots", required=False)
    auction_history = _load_frame(source, "auction_history", required=False)

    factor_config = config.get("interval_learning", {})
    features = list(factor_config.get("feature_columns", []))
    if not features:
        raise ValueError("interval_learning.feature_columns must not be empty")
    index_factor = compute_index_momentum(
        index_bars,
        benchmark_symbol=config["benchmark_symbol"],
        as_of=as_of,
        member_bars=member_bars,
    )
    sentiment_factor = compute_semantic_sentiment(events, as_of)
    interval_model = IntervalLearningModel(
        IntervalLearningConfig(
            minimum_training_rows=int(factor_config.get("minimum_training_rows", 120)),
            transaction_cost=float(factor_config.get("transaction_cost", 0.002)),
            random_state=int(factor_config.get("random_state", 42)),
        )
    )
    interval_factor = interval_model.fit_predict(training, current, features, as_of)
    watchlist = rank_three_factor_watchlist(
        stocks,
        index_factor,
        sentiment_factor,
        interval_factor,
        limit=int(config.get("watchlist_size", 20)),
    )
    auction_factor = None
    auction_status = "missing"
    if auction_snapshots is not None or auction_history is not None:
        if auction_snapshots is None or auction_history is None:
            raise ValueError("auction_snapshots and auction_history must be supplied together")
        auction_factor = compute_auction_linkage(auction_snapshots, auction_history, sentiment_factor)
        auction_status = "available"
    selected = select_four_factor_portfolio(
        stocks,
        index_factor,
        sentiment_factor,
        interval_factor,
        auction_factor=auction_factor,
        max_positions=int(config.get("max_positions", 5)),
        max_per_industry=int(config.get("max_per_industry", 2)),
    )

    _write_frame(output, "index_factor", index_factor)
    _write_frame(output, "sentiment_factor", sentiment_factor)
    _write_frame(output, "interval_factor", interval_factor)
    _write_frame(output, "evening_watchlist", watchlist)
    if auction_factor is not None:
        _write_frame(output, "auction_factor", auction_factor)
    selections = [asdict(item) for item in selected]
    payload = {
        "as_of": pd.Timestamp(as_of).isoformat(),
        "strategy": config.get("name", "four-factor strategy"),
        "data_provider": "TickDB.ai for supported market fields; external snapshots for news and auction",
        "auction_status": auction_status,
        "watchlist_count": len(watchlist),
        "selected_count": len(selections),
        "selected": selections,
        "auto_trade_enabled": auction_status == "available" and bool(selections),
        "risk": config.get("risk", {}),
        "disclaimer": "Paper trading research only; no return is guaranteed.",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "selection.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        f"# {payload['as_of']} 四因子选股报告",
        "",
        f"- 晚间观察名单：{len(watchlist)} 只",
        f"- 真实竞价数据：{'已提供' if auction_status == 'available' else '缺失'}",
        f"- 通过全部买入门槛：{len(selections)} 只",
        "",
    ]
    if selections:
        for position, item in enumerate(selections, start=1):
            lines.append(f"{position}. {item['name']}（{item['symbol']}）：{item['reason']}")
    else:
        lines.append("未生成自动买入信号；资金保持现金，等待真实 09:25 竞价确认。")
    lines.extend(["", "📡 数据由 TickDB.ai 提供", "", "仅用于模拟盘研究，不构成投资建议。"])
    (output / "selection.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOGGER.info(
        "Four-factor pipeline completed: watchlist=%d selected=%d auction=%s output=%s",
        len(watchlist),
        len(selections),
        auction_status,
        output,
    )
    return {**payload, "output_dir": str(output)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run causal four-factor A-share selection")
    parser.add_argument("--config", default="configs/quant/four_factor_strategy.json")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", default="reports/quant/four_factor")
    parser.add_argument("--as-of", required=True, help="Observable cutoff, for example 2026-07-24T20:00:00")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = run_four_factor_pipeline(args.config, args.input_dir, args.output_dir, args.as_of)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
