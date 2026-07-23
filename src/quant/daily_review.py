"""Generate a post-close review for the technology portfolio paper account."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .paper_config import load_paper_config
from .portfolio_paper import load_portfolio_config


def _read_csv(path: Path, date_columns: tuple[str, ...]) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    frame = pd.read_csv(path)
    for column in date_columns:
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


def _optimization_advice(all_pairs: pd.DataFrame) -> list[str]:
    if all_pairs.empty or len(all_pairs) < 30:
        remaining = 30 - len(all_pairs)
        return [f"继续收集至少 {remaining} 组配对；样本不足，禁止自动修改参数。"]
    pnl = pd.to_numeric(all_pairs["pnl"], errors="coerce").fillna(0.0)
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = abs(float(pnl[pnl < 0].sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    breakout_ratio = float(all_pairs["exit_reason"].eq("chip_peak_breakout").mean())
    advice = []
    if profit_factor < 1.0:
        advice.append("候选优化：将高抛位置由 72% 提高到 78%，并执行滚动验证。")
    if breakout_ratio > 0.35:
        advice.append("候选优化：将峰间谷值上限由 70% 收紧至 60%，过滤不稳定双峰。")
    if not advice:
        advice.append("当前样本未触发参数调整门槛，继续沿用锁定参数。")
    return advice


def _loss_summary(day_pairs: pd.DataFrame) -> list[dict[str, Any]]:
    if day_pairs.empty:
        return []
    pnl = pd.to_numeric(day_pairs["pnl"], errors="coerce").fillna(0.0)
    losing = day_pairs[pnl < 0]
    if losing.empty:
        return []
    summary = (
        losing.assign(pnl=pnl[pnl < 0])
        .groupby("exit_reason", dropna=False)["pnl"]
        .agg(["count", "sum"])
        .reset_index()
    )
    return [
        {
            "exit_reason": "unknown" if pd.isna(row.exit_reason) else str(row.exit_reason),
            "count": int(row.count),
            "pnl": float(row.sum),
            "classification": "strategy_loss_within_rules",
        }
        for row in summary.itertuples(index=False)
    ]


def generate_daily_review(config_path: str | Path, now: datetime) -> Path:
    """Review all trades for one session and update the optimization queue."""
    portfolio = load_portfolio_config(config_path)
    current = pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize(portfolio.timezone)
    else:
        current = current.tz_convert(portfolio.timezone)
    review_date = current.normalize()
    account_rows = []
    pair_frames = []
    mistakes = []
    losses = []

    for account_path in portfolio.account_configs:
        account = load_paper_config(account_path)
        state_dir = Path(account.state_dir)
        trades = _read_csv(state_dir / "trades.csv", ("timestamp", "signal_time"))
        pairs = _read_csv(state_dir / "pairs.csv", ("entry_time", "exit_time"))
        if not pairs.empty:
            exit_time = pairs["exit_time"]
            cutoff = current
            if exit_time.dt.tz is None:
                cutoff = current.tz_localize(None)
            tagged = pairs[exit_time <= cutoff].copy()
            tagged.insert(0, "symbol", account.symbol)
            pair_frames.append(tagged)
        day_trades = trades
        if not trades.empty:
            day_trades = trades[trades["timestamp"].dt.normalize() == review_date]
        day_pairs = pairs
        if not pairs.empty:
            day_pairs = pairs[pairs["exit_time"].dt.normalize() == review_date]
        rejected = day_trades[day_trades["status"] != "filled"] if not day_trades.empty else day_trades
        same_bar = (
            day_trades[
                day_trades["timestamp"].dt.floor("5min") <= day_trades["signal_time"].dt.floor("5min")
            ]
            if not day_trades.empty
            else day_trades
        )
        invalid_entry = pd.DataFrame()
        if not day_trades.empty:
            entries = day_trades[day_trades["action"].isin(["entry_high", "entry_low"])]
            valid_reasons = entries["reason"].isin(["chip_upper_zone", "chip_lower_zone"])
            invalid_entry = entries[~valid_reasons]
        for category, frame in (
            ("execution_rejection", rejected),
            ("same_bar_execution", same_bar),
            ("invalid_entry_reason", invalid_entry),
        ):
            if not frame.empty:
                mistakes.append({"symbol": account.symbol, "category": category, "count": len(frame)})
        for item in _loss_summary(day_pairs):
            losses.append({"symbol": account.symbol, **item})
        account_rows.append(
            {
                "symbol": account.symbol,
                "name": account.name,
                "trades": len(day_trades),
                "pairs": len(day_pairs),
                "pnl": float(day_pairs["pnl"].sum()) if not day_pairs.empty else 0.0,
                "fees": float(day_trades["total_fees"].sum()) if not day_trades.empty else 0.0,
                "mistakes": len(rejected) + len(same_bar) + len(invalid_entry),
            }
        )

    all_pairs = pd.concat(pair_frames, ignore_index=True) if pair_frames else pd.DataFrame()
    advice = _optimization_advice(all_pairs)
    report_dir = Path(portfolio.report_dir) / "daily_reviews"
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "date": review_date.strftime("%Y-%m-%d"),
        "accounts": account_rows,
        "mistakes": mistakes,
        "strategy_losses": losses,
        "historical_pair_count": len(all_pairs),
        "optimization_advice": advice,
        "parameters_changed": False,
    }
    report_path = report_dir / f"review_{review_date:%Y-%m-%d}.md"
    lines = [
        f"# {review_date:%Y-%m-%d} 科技双峰组合复盘",
        "",
        "| 股票 | 成交 | 完成配对 | 配对盈亏 | 费用 | 规则错误 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in account_rows:
        lines.append(
            f"| {row['name']}（{row['symbol']}） | {row['trades']} | {row['pairs']} | "
            f"¥{row['pnl']:,.2f} | ¥{row['fees']:,.2f} | {row['mistakes']} |"
        )
    lines.extend(["", "## 错误操作", ""])
    if mistakes:
        lines.extend(f"- {item['symbol']}：{item['category']} × {item['count']}" for item in mistakes)
    else:
        lines.append("- 未发现违反信号、下一根K线成交或委托规则的操作。")
    lines.extend(["", "## 策略内亏损", ""])
    if losses:
        for item in losses:
            lines.append(
                f"- {item['symbol']}：{item['exit_reason']} × {item['count']}，"
                f"合计 ¥{item['pnl']:,.2f}；归类为规则内策略亏损，不记作操作错误。"
            )
    else:
        lines.append("- 当日没有已完成的亏损配对。")
    lines.extend(["", "## 优化建议", ""])
    lines.extend(f"- {item}" for item in advice)
    lines.extend(
        [
            "",
            "当日复盘只生成候选优化，不直接修改实盘参数；"
            "参数需累计30组以上并通过滚动验证。",
        ]
    )
    content = "\n".join(lines) + "\n"
    report_path.write_text(content, encoding="utf-8")
    (report_dir / "latest.md").write_text(content, encoding="utf-8")
    (report_dir / f"review_{review_date:%Y-%m-%d}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_dir / "optimization_queue.json").write_text(
        json.dumps(
            {
                "updated_at": current.isoformat(),
                "historical_pair_count": len(all_pairs),
                "advice": advice,
                "auto_apply": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily portfolio operation review")
    parser.add_argument("--config", required=True)
    parser.add_argument("--at", help="Optional ISO timestamp")
    args = parser.parse_args()
    config = load_portfolio_config(args.config)
    now = datetime.fromisoformat(args.at) if args.at else datetime.now(ZoneInfo(config.timezone))
    path = generate_daily_review(args.config, now)
    print(json.dumps({"status": "ok", "report": str(path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
