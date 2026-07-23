"""Weekly Markdown reporting for the persistent paper account."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .config import load_quant_config
from .paper_config import PaperConfig


def _read_csv(path: Path, date_columns: tuple[str, ...] = ()) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    frame = pd.read_csv(path)
    for column in date_columns:
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


def _money(value: float) -> str:
    return f"¥{value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.2%}"


def generate_weekly_report(config: PaperConfig, now: datetime) -> Path:
    """Generate an idempotent account report for the week containing ``now``."""
    state_dir = Path(config.state_dir)
    report_dir = Path(config.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"Paper account has not been initialized: {state_path}")
    state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
    quant_config = load_quant_config(config.quant_config)
    initial_cash = config.initial_cash if config.initial_cash is not None else quant_config.portfolio.initial_cash
    current = pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize(config.timezone)
    else:
        current = current.tz_convert(config.timezone)
    week_start = current.normalize() - timedelta(days=current.weekday())
    week_end = week_start + pd.Timedelta(days=4)

    equity = _read_csv(state_dir / "equity.csv", ("timestamp",))
    trades = _read_csv(state_dir / "trades.csv", ("timestamp", "signal_time"))
    pairs = _read_csv(state_dir / "pairs.csv", ("entry_time", "exit_time"))
    if equity.empty:
        end_equity = float(state.get("last_equity", initial_cash))
        start_equity = initial_cash
        week_equity = pd.DataFrame()
    else:
        equity = equity.sort_values("timestamp")
        before_week = equity[equity["timestamp"] < week_start]
        start_equity = float(before_week.iloc[-1]["equity"]) if not before_week.empty else initial_cash
        week_equity = equity[
            (equity["timestamp"] >= week_start) & (equity["timestamp"] < week_end + pd.Timedelta(days=1))
        ]
        end_equity = float(week_equity.iloc[-1]["equity"]) if not week_equity.empty else start_equity
    weekly_return = end_equity / start_equity - 1.0 if start_equity > 0 else 0.0
    cumulative_return = end_equity / initial_cash - 1.0
    if week_equity.empty:
        max_drawdown = 0.0
    else:
        series = pd.concat([pd.Series([start_equity]), week_equity["equity"].astype(float)], ignore_index=True)
        max_drawdown = abs(float((series / series.cummax() - 1.0).min()))

    week_trades = trades
    if not trades.empty:
        week_trades = trades[
            (trades["timestamp"] >= week_start) & (trades["timestamp"] < week_end + pd.Timedelta(days=1))
        ]
    filled_trades = week_trades
    if not week_trades.empty and "status" in week_trades:
        filled_trades = week_trades[week_trades["status"] == "filled"]
    week_pairs = pairs
    if not pairs.empty:
        week_pairs = pairs[(pairs["exit_time"] >= week_start) & (pairs["exit_time"] < week_end + pd.Timedelta(days=1))]
    pair_pnl = float(week_pairs["pnl"].sum()) if not week_pairs.empty else 0.0
    win_rate = float((week_pairs["pnl"] > 0).mean()) if not week_pairs.empty else 0.0
    fees = float(filled_trades["total_fees"].sum()) if not filled_trades.empty else 0.0
    last_price = float(state.get("last_price", 0.0))
    shares = int(state.get("total_shares", 0))
    exposure = shares * last_price / end_equity if end_equity > 0 else 0.0

    lines = [
        f"# {config.name}（{config.symbol}）模拟账户周报",
        "",
        f"报告周期：{week_start:%Y-%m-%d} 至 {week_end:%Y-%m-%d}",
        "",
        "## 账户概览",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 周初权益 | {_money(start_equity)} |",
        f"| 周末权益 | {_money(end_equity)} |",
        f"| 本周收益率 | {_percent(weekly_return)} |",
        f"| 累计收益率 | {_percent(cumulative_return)} |",
        f"| 本周最大回撤 | {_percent(max_drawdown)} |",
        f"| 现金 | {_money(float(state.get('cash', initial_cash)))} |",
        f"| 持仓股数 | {shares:,} |",
        f"| 底仓市值占比 | {_percent(exposure)} |",
        "",
        "## T+0 执行",
        "",
        f"- 完成配对：{len(week_pairs)} 次",
        f"- 配对净收益：{_money(pair_pnl)}",
        f"- 配对胜率：{_percent(win_rate)}",
        f"- 模拟成交：{len(filled_trades)} 笔",
        f"- 拒绝委托：{len(week_trades) - len(filled_trades)} 笔",
        f"- 交易费用：{_money(fees)}",
        "",
        "## 本周成交",
        "",
    ]
    if filled_trades.empty:
        lines.append("本周无模拟成交。")
    else:
        lines.extend(
            [
                "| 时间 | 方向 | 数量 | 成交价 | 费用 | 原因 |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for trade in filled_trades.itertuples(index=False):
            lines.append(
                f"| {trade.timestamp:%Y-%m-%d %H:%M} | {trade.side} | {int(trade.quantity):,} | "
                f"{float(trade.price):.2f} | {float(trade.total_fees):.2f} | {trade.reason} |"
            )
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "该账户为模拟盘，不连接券商、不产生真实委托。行情快照按 5 分钟聚合，"
            "成交使用下一快照并计入佣金、印花税、过户费和滑点。",
            "报告仅用于策略研究，不构成投资建议。",
        ]
    )
    content = "\n".join(lines) + "\n"
    report_path = report_dir / f"week_{week_start:%Y-%m-%d}_{week_end:%Y-%m-%d}.md"
    report_path.write_text(content, encoding="utf-8")
    (report_dir / "latest.md").write_text(content, encoding="utf-8")
    return report_path
