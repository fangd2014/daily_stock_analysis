"""DeepSeek-assisted daily and weekly reviews for the paper portfolio."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import setup_env

from .daily_review import _journal_records, _read_csv, generate_daily_review
from .paper_config import load_paper_config
from .portfolio_paper import load_portfolio_config


class ReviewGenerator(Protocol):
    def analyze(self, review_type: str, context: dict[str, Any]) -> str:
        """Return a grounded operation review."""


class ReviewNotifier(Protocol):
    def send(self, title: str, content: str) -> bool:
        """Push one completed review."""


class DeepSeekReviewGenerator:
    """Generate a review through DeepSeek's OpenAI-compatible endpoint."""

    def __init__(self) -> None:
        setup_env()
        from openai import OpenAI

        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key or api_key.startswith("your_"):
            raise ValueError("DEEPSEEK_API_KEY is required for paper-trading review")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip()
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
        self.max_retries = int(os.getenv("DEEPSEEK_MAX_RETRIES", "3"))
        if self.max_retries < 1:
            raise ValueError("DEEPSEEK_MAX_RETRIES must be positive")
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def analyze(self, review_type: str, context: dict[str, Any]) -> str:
        if review_type == "daily":
            requirements = (
                "依次输出：组合结果、逐股操作评价、错误操作与证据、风险状态、次日执行重点、"
                "待验证优化建议。没有成交时必须说明原因和入场门禁状态。"
            )
        else:
            requirements = (
                "依次输出：本周绩效、有效操作、错误与策略内亏损、风险暴露、下周执行计划、"
                "待验证优化假设。评价收益时必须同时考虑最大回撤和费用。"
            )
        prompt = (
            "你是A股筹码双峰五股组合的模拟盘复盘员。只使用下方JSON证据，不补充新闻、基本面或"
            "未提供的行情。区分操作错误、正常拒单和策略规则内亏损；不要承诺收益。参数优化只能列为"
            "候选，累计至少30个完成配对并通过滚动验证前不得建议直接启用。"
            f"{requirements}用中文Markdown输出，控制在1200字内。\n\n"
            f"证据JSON：\n{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
        )
        response = None
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "执行严格、可审计的模拟交易复盘，结论必须能对应输入证据。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    max_tokens=1800,
                )
                break
            except Exception:
                if attempt + 1 == self.max_retries:
                    raise
                time.sleep(2**attempt)
        if response is None:
            raise RuntimeError("DeepSeek review request did not complete")
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError("DeepSeek returned an empty paper-trading review")
        return content.strip()


class FeishuReviewNotifier:
    """Push a completed paper-trading review to Feishu."""

    def __init__(self) -> None:
        setup_env()
        self.webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not self.webhook or "your_key_here" in self.webhook:
            raise ValueError("FEISHU_WEBHOOK_URL is required for paper-trading review")

    def send(self, title: str, content: str) -> bool:
        import requests

        from src.formatters import format_feishu_markdown

        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [{"tag": "markdown", "content": format_feishu_markdown(content)}],
            },
        }
        response = requests.post(self.webhook, json=payload, timeout=30)
        if response.status_code != 200:
            return False
        result = response.json()
        return result.get("code", result.get("StatusCode")) == 0


def _normalize_now(config_path: str | Path, now: datetime) -> tuple[Any, pd.Timestamp]:
    portfolio = load_portfolio_config(config_path)
    current = pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize(portfolio.timezone)
    else:
        current = current.tz_convert(portfolio.timezone)
    return portfolio, current


def build_daily_context(config_path: str | Path, now: datetime) -> dict[str, Any]:
    """Build deterministic daily evidence before asking DeepSeek for judgment."""
    report_path = generate_daily_review(config_path, now)
    return json.loads(report_path.with_suffix(".json").read_text(encoding="utf-8"))


def _window(frame: pd.DataFrame, column: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if frame.empty or column not in frame:
        return frame.iloc[0:0].copy()
    values = frame[column]
    lower = start
    upper = end
    if values.dt.tz is None:
        lower = start.tz_localize(None)
        upper = end.tz_localize(None)
    return frame[(values >= lower) & (values <= upper)].copy()


def _weekly_equity_curve(
    account_series: list[tuple[str, float, pd.Series]],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.Series:
    start_utc = start.tz_convert("UTC")
    end_utc = end.tz_convert("UTC")
    index = pd.DatetimeIndex([start_utc, end_utc])
    prepared = []
    for symbol, initial_cash, series in account_series:
        values = series.copy()
        if not values.empty:
            values.index = pd.to_datetime(values.index, errors="coerce", utc=True)
            values = pd.to_numeric(values, errors="coerce").dropna().sort_index()
            values = values[~values.index.duplicated(keep="last")]
            index = index.union(values[(values.index >= start_utc) & (values.index <= end_utc)].index)
        prepared.append((symbol, initial_cash, values))
    index = index.sort_values()
    total = pd.Series(0.0, index=index, dtype=float)
    for _, initial_cash, values in prepared:
        if values.empty:
            total = total.add(initial_cash)
            continue
        expanded = values.reindex(values.index.union(index)).sort_index().ffill().reindex(index)
        total = total.add(expanded.fillna(initial_cash))
    return total


def build_weekly_context(config_path: str | Path, now: datetime) -> dict[str, Any]:
    """Aggregate Monday-to-Friday journals into auditable weekly review evidence."""
    portfolio, current = _normalize_now(config_path, now)
    start = current.normalize() - pd.Timedelta(days=current.weekday())
    end = current
    accounts = []
    account_series = []
    for account_path in portfolio.account_configs:
        account = load_paper_config(account_path)
        state_dir = Path(account.state_dir)
        trades = _read_csv(state_dir / "trades.csv", ("timestamp", "signal_time"))
        pairs = _read_csv(state_dir / "pairs.csv", ("entry_time", "exit_time"))
        equity = _read_csv(state_dir / "equity.csv", ("timestamp",))
        week_trades = _window(trades, "timestamp", start, end)
        week_pairs = _window(pairs, "exit_time", start, end)
        state_path = state_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        initial_cash = float(account.initial_cash or 0.0)
        equity_series = pd.Series(dtype=float)
        if not equity.empty and "equity" in equity:
            equity_series = equity.set_index("timestamp")["equity"]
        account_series.append((account.symbol, initial_cash, equity_series))
        pnl = pd.to_numeric(week_pairs.get("pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        fees = pd.to_numeric(week_trades.get("total_fees", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        accounts.append(
            {
                "symbol": account.symbol,
                "name": account.name,
                "filled_trades": int(week_trades.get("status", pd.Series(dtype=str)).eq("filled").sum()),
                "rejected_trades": int(week_trades.get("status", pd.Series(dtype=str)).ne("filled").sum()),
                "closed_pairs": len(week_pairs),
                "winning_pairs": int((pnl > 0).sum()),
                "losing_pairs": int((pnl < 0).sum()),
                "realized_pnl": float(pnl.sum()),
                "fees": float(fees.sum()),
                "ending_equity": float(state.get("last_equity", initial_cash)),
                "shares": int(state.get("total_shares", 0)),
                "last_signal": state.get("last_signal"),
                "trades": _journal_records(
                    week_trades,
                    (
                        "timestamp",
                        "side",
                        "quantity",
                        "price",
                        "total_fees",
                        "reason",
                        "action",
                        "status",
                        "message",
                    ),
                ),
                "pairs": _journal_records(
                    week_pairs,
                    ("direction", "entry_time", "exit_time", "entry_price", "quantity", "pnl", "exit_reason"),
                ),
            }
        )
    curve = _weekly_equity_curve(account_series, start, end)
    drawdown = curve / curve.cummax() - 1.0 if not curve.empty else pd.Series([0.0])
    daily_reviews = []
    daily_dir = Path(portfolio.report_dir) / "daily_reviews"
    day = start
    while day.normalize() <= current.normalize():
        path = daily_dir / f"review_{day:%Y-%m-%d}.json"
        if path.exists():
            daily_reviews.append(json.loads(path.read_text(encoding="utf-8")))
        day += pd.Timedelta(days=1)
    return {
        "week_start": start.strftime("%Y-%m-%d"),
        "week_end": current.strftime("%Y-%m-%d"),
        "portfolio": {
            "initial_cash": portfolio.initial_cash,
            "starting_equity": float(curve.iloc[0]),
            "ending_equity": float(curve.iloc[-1]),
            "weekly_return": float(curve.iloc[-1] / curve.iloc[0] - 1.0),
            "max_drawdown": abs(float(drawdown.min())),
            "target_exposure": portfolio.target_exposure,
        },
        "accounts": accounts,
        "daily_reviews": daily_reviews,
        "parameters_changed": False,
    }


def _write_review_report(
    config_path: str | Path,
    review_type: str,
    review_key: str,
    context: dict[str, Any],
    analysis: str,
    model: str,
) -> tuple[Path, str]:
    portfolio = load_portfolio_config(config_path)
    output_dir = Path(portfolio.report_dir) / "deepseek_reviews"
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = "daily" if review_type == "daily" else "weekly"
    title = "每日DeepSeek模拟盘复盘" if review_type == "daily" else "每周DeepSeek模拟盘复盘"
    markdown = (
        f"# {review_key} {title}\n\n"
        f"{analysis}\n\n"
        "---\n\n仅用于模拟盘研究；模型建议不会自动修改策略参数，也不构成投资建议。\n"
    )
    report_path = output_dir / f"{prefix}_{review_key}.md"
    report_path.write_text(markdown, encoding="utf-8")
    (output_dir / f"latest_{prefix}.md").write_text(markdown, encoding="utf-8")
    payload = {
        "review_type": review_type,
        "review_key": review_key,
        "model": model,
        "context": context,
        "analysis": analysis,
        "parameters_changed": False,
    }
    json_content = json.dumps(payload, ensure_ascii=False, indent=2)
    report_path.with_suffix(".json").write_text(json_content, encoding="utf-8")
    (output_dir / f"latest_{prefix}.json").write_text(json_content, encoding="utf-8")
    return report_path, markdown


def run_deepseek_review(
    config_path: str | Path,
    review_type: str,
    generator: ReviewGenerator,
    notifier: ReviewNotifier,
    now: datetime,
) -> dict[str, Any]:
    """Generate, persist, and idempotently push one daily or weekly review."""
    if review_type not in {"daily", "weekly"}:
        raise ValueError("review_type must be daily or weekly")
    portfolio, current = _normalize_now(config_path, now)
    context = (
        build_daily_context(config_path, current.to_pydatetime())
        if review_type == "daily"
        else build_weekly_context(config_path, current.to_pydatetime())
    )
    review_key = context["date"] if review_type == "daily" else context["week_end"]
    output_dir = Path(portfolio.report_dir) / "deepseek_reviews"
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "feishu_push_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state_field = f"last_{review_type}_review"
    if state.get(state_field) == review_key:
        return {
            "status": "skipped",
            "review_type": review_type,
            "review_key": review_key,
            "reason": "review_already_pushed",
            "feishu_pushed": False,
        }
    prefix = "daily" if review_type == "daily" else "weekly"
    cached_path = output_dir / f"{prefix}_{review_key}.json"
    analysis = ""
    if cached_path.exists():
        cached = json.loads(cached_path.read_text(encoding="utf-8"))
        if cached.get("review_key") == review_key:
            analysis = str(cached.get("analysis", "")).strip()
    if not analysis:
        analysis = generator.analyze(review_type, context)
    model = getattr(generator, "model", "test-generator")
    report_path, markdown = _write_review_report(
        config_path,
        review_type,
        review_key,
        context,
        analysis,
        model,
    )
    title = "筹码双峰模拟盘每日复盘" if review_type == "daily" else "筹码双峰模拟盘周复盘"
    if not notifier.send(title, markdown):
        raise RuntimeError("Feishu rejected the DeepSeek paper-trading review")
    state[state_field] = review_key
    state["updated_at"] = current.isoformat()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "ok",
        "review_type": review_type,
        "review_key": review_key,
        "report": str(report_path),
        "feishu_pushed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="DeepSeek paper-trading operation review")
    parser.add_argument("--config", required=True)
    subparsers = parser.add_subparsers(dest="review_type", required=True)
    for review_type in ("daily", "weekly"):
        command = subparsers.add_parser(review_type)
        command.add_argument("--at", help="Optional ISO timestamp")
    args = parser.parse_args()
    portfolio = load_portfolio_config(args.config)
    now = datetime.fromisoformat(args.at) if args.at else datetime.now(ZoneInfo(portfolio.timezone))
    result = run_deepseek_review(
        args.config,
        args.review_type,
        DeepSeekReviewGenerator(),
        FeishuReviewNotifier(),
        now,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
