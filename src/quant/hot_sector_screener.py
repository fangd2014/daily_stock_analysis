"""Nightly hot-sector commander screening and next-session paper-trading guidance."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.config import get_dotenv_value, setup_env

from .daily_chip_screener import (
    GuidanceGenerator,
    GuidanceNotifier,
    TushareMarketDataProvider,
    _main_net_inflow,
    _number,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HotSectorScreenConfig:
    top_n: int
    hot_sector_count: int
    max_per_sector: int
    lookback_days: int
    calendar_days: int
    min_sector_members: int
    min_sector_pct_chg: float
    min_sector_breadth: float
    min_average_amount_20d: float
    min_amount_ratio: float
    max_stock_pct_chg: float
    max_distance_ma20: float
    exclude_st: bool
    output_dir: str
    cache_dir: str


@dataclass(frozen=True)
class HotSectorCandidate:
    symbol: str
    name: str
    industry: str
    trade_date: str
    close: float
    pct_chg: float
    ma20: float
    ma60: float
    momentum_20d: float
    distance_ma20: float
    average_amount_20d: float
    amount_ratio: float
    sector_pct_chg: float
    sector_breadth: float
    sector_amount_ratio: float
    sector_momentum_20d: float
    sector_score: float
    sector_amount_percentile: float
    main_net_inflow: float
    main_flow_ratio: float
    score: float
    eligible: bool
    reason: str
    selection_reason: str
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float


def load_hot_sector_screen_config(path: str | Path) -> HotSectorScreenConfig:
    config = HotSectorScreenConfig(**json.loads(Path(path).read_text(encoding="utf-8")))
    if config.top_n != 10:
        raise ValueError("top_n must be 10")
    if not 1 <= config.hot_sector_count <= 20:
        raise ValueError("hot_sector_count must be between 1 and 20")
    if not 1 <= config.max_per_sector <= config.top_n:
        raise ValueError("max_per_sector must be between 1 and top_n")
    if config.lookback_days < 60 or config.calendar_days < config.lookback_days:
        raise ValueError("The screen requires at least 60 sessions of history")
    if not 0 < config.min_sector_breadth <= 1:
        raise ValueError("min_sector_breadth must be in (0, 1]")
    if config.min_average_amount_20d < 0 or config.min_amount_ratio <= 0:
        raise ValueError("Liquidity thresholds must be positive")
    if not 0 < config.max_distance_ma20 < 1:
        raise ValueError("max_distance_ma20 must be in (0, 1)")
    return config


def _rank_pct(series: pd.Series, ascending: bool = True) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").rank(pct=True, ascending=ascending).fillna(0.0)


def evaluate_hot_sectors(
    config: HotSectorScreenConfig,
    stock_basic: pd.DataFrame,
    daily: pd.DataFrame,
    moneyflow: pd.DataFrame,
) -> tuple[list[HotSectorCandidate], pd.DataFrame, str]:
    """Score hot industries and liquid commander stocks using only completed sessions."""
    if stock_basic is None or stock_basic.empty or daily is None or daily.empty:
        raise ValueError("Stock basic and daily data are required")
    prices = daily.copy()
    prices["trade_date"] = prices["trade_date"].astype(str)
    trade_date = str(prices["trade_date"].max())
    LOGGER.info(
        "开始热门板块筛选：交易日=%s，股票基础信息=%d行，日线=%d行，资金流=%d行",
        trade_date,
        len(stock_basic),
        len(daily),
        0 if moneyflow is None else len(moneyflow),
    )
    basic = stock_basic[["ts_code", "name", "industry"]].copy()
    basic["name"] = basic["name"].fillna("").astype(str)
    basic["industry"] = basic["industry"].fillna("").astype(str)
    if config.exclude_st:
        original_count = len(basic)
        basic = basic[~basic["name"].str.contains(r"^\*?ST|退", case=False, regex=True)]
        LOGGER.info("股票池清洗：剔除ST/退市标记股票%d只，剩余%d只", original_count - len(basic), len(basic))
    prices = prices.merge(basic, on="ts_code", how="inner")
    prices["close"] = _number(prices, "close")
    prices["pct_chg"] = _number(prices, "pct_chg")
    prices["amount_yuan"] = _number(prices, "amount") * 1_000.0
    latest = prices[prices["trade_date"].eq(trade_date)].copy()
    histories = {
        symbol: frame.sort_values("trade_date").tail(config.lookback_days)
        for symbol, frame in prices.groupby("ts_code", sort=False)
    }
    feature_rows = []
    insufficient_history = 0
    for row in latest.itertuples(index=False):
        history = histories.get(row.ts_code, pd.DataFrame())
        close = _number(history, "close")
        amount = _number(history, "amount_yuan")
        if len(close) < 60:
            insufficient_history += 1
            continue
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean())
        average_amount = float(amount.tail(20).mean())
        amount_ratio = float(amount.iloc[-1] / average_amount) if average_amount > 0 else np.nan
        momentum20 = float(close.iloc[-1] / close.iloc[-21] - 1.0)
        feature_rows.append(
            {
                "ts_code": row.ts_code,
                "name": row.name,
                "industry": row.industry,
                "close": float(row.close),
                "pct_chg": float(row.pct_chg),
                "ma20": ma20,
                "ma60": ma60,
                "momentum_20d": momentum20,
                "distance_ma20": float(row.close / ma20 - 1.0) if ma20 > 0 else np.nan,
                "average_amount_20d": average_amount,
                "amount_ratio": amount_ratio,
                "amount_yuan": float(amount.iloc[-1]),
            }
        )
    features = pd.DataFrame(feature_rows)
    if features.empty:
        raise ValueError("No stock has enough history for hot-sector screening")
    LOGGER.info(
        "技术特征计算完成：当日股票=%d只，有效60日历史=%d只，历史不足=%d只",
        len(latest),
        len(features),
        insufficient_history,
    )
    sector = features.groupby("industry", as_index=False).agg(
        sector_pct_chg=("pct_chg", "mean"),
        sector_breadth=("pct_chg", lambda values: float((values > 0).mean())),
        sector_amount_ratio=("amount_ratio", "median"),
        sector_momentum_20d=("momentum_20d", "median"),
        sector_member_count=("ts_code", "nunique"),
    )
    valid_sector = (
        sector["industry"].ne("")
        & sector["sector_member_count"].ge(config.min_sector_members)
        & sector["sector_pct_chg"].ge(config.min_sector_pct_chg)
        & sector["sector_breadth"].ge(config.min_sector_breadth)
        & sector["sector_amount_ratio"].ge(config.min_amount_ratio)
        & sector["sector_momentum_20d"].gt(0)
    )
    sector["eligible"] = valid_sector
    sector["sector_score"] = (
        _rank_pct(sector["sector_pct_chg"]) * 0.35
        + _rank_pct(sector["sector_breadth"]) * 0.25
        + _rank_pct(sector["sector_amount_ratio"]) * 0.20
        + _rank_pct(sector["sector_momentum_20d"]) * 0.20
    )
    hot = sector[sector["eligible"]].nlargest(config.hot_sector_count, "sector_score").copy()
    LOGGER.info(
        "板块门槛完成：评估%d个行业，%d个通过门槛，取热度前%d个",
        len(sector),
        int(sector["eligible"].sum()),
        len(hot),
    )
    if hot.empty:
        LOGGER.info(
            "没有行业同时满足：平均涨幅>=%.2f%%、上涨占比>=%.0f%%、成交额比>=%.2f、20日动量>0",
            config.min_sector_pct_chg,
            config.min_sector_breadth * 100.0,
            config.min_amount_ratio,
        )
    else:
        for rank, row in enumerate(hot.itertuples(index=False), start=1):
            LOGGER.info(
                "热门板块#%d：%s，得分=%.3f，涨幅=%.2f%%，上涨占比=%.1f%%，成交额比=%.2f，"
                "20日动量=%.2f%%，成员=%d",
                rank,
                row.industry,
                row.sector_score,
                row.sector_pct_chg,
                row.sector_breadth * 100.0,
                row.sector_amount_ratio,
                row.sector_momentum_20d * 100.0,
                row.sector_member_count,
            )
    hot_names = set(hot["industry"])
    features = features.merge(
        sector.drop(columns="eligible"),
        on="industry",
        how="left",
    )
    features["sector_amount_percentile"] = features.groupby("industry")["amount_yuan"].rank(pct=True)
    features = features.merge(_main_net_inflow(moneyflow), on="ts_code", how="left")
    features["main_net_inflow"] = features["main_net_inflow"].fillna(0.0)
    features["main_flow_ratio"] = features["main_net_inflow"] * 10_000.0 / features["amount_yuan"].clip(lower=1.0)
    records = []
    for row in features.itertuples(index=False):
        eligible = True
        reason = "eligible"
        if row.industry not in hot_names:
            eligible, reason = False, "sector_not_hot"
        elif row.average_amount_20d < config.min_average_amount_20d:
            eligible, reason = False, "below_liquidity_floor"
        elif row.amount_ratio < config.min_amount_ratio:
            eligible, reason = False, "stock_amount_not_expanding"
        elif row.sector_amount_percentile < 0.60:
            eligible, reason = False, "not_sector_commander_liquidity"
        elif not (row.close > row.ma20 > row.ma60 and row.momentum_20d > 0):
            eligible, reason = False, "stock_trend_not_aligned"
        elif row.pct_chg > config.max_stock_pct_chg:
            eligible, reason = False, "do_not_chase_acceleration"
        elif abs(row.distance_ma20) > config.max_distance_ma20:
            eligible, reason = False, "too_far_from_ma20"
        elif row.main_net_inflow <= 0:
            eligible, reason = False, "main_funds_not_net_buying"
        score = (
            row.sector_score * 0.30
            + row.sector_amount_percentile * 0.25
            + float(np.clip(row.main_flow_ratio, -0.1, 0.1) / 0.2 + 0.5) * 0.15
            + float(np.clip(row.momentum_20d, 0.0, 0.30) / 0.30) * 0.15
            + max(0.0, 1.0 - abs(row.distance_ma20) / config.max_distance_ma20) * 0.15
        )
        selection_reason = (
            f"{row.industry}热度得分{row.sector_score:.2f}，板块上涨{row.sector_pct_chg:.2f}%且"
            f"上涨家数占比{row.sector_breadth:.0%}；个股成交额位于板块前"
            f"{1.0 - row.sector_amount_percentile:.0%}、20日趋势{row.momentum_20d:.1%}、"
            f"主力净流入{row.main_net_inflow:.0f}万元。"
        )
        close = float(row.close)
        entry_low = max(float(row.ma20), close * 0.985)
        entry_high = close * 1.01
        stop_loss = min(float(row.ma20) * 0.97, close * 0.95)
        records.append(
            HotSectorCandidate(
                symbol=row.ts_code,
                name=row.name,
                industry=row.industry,
                trade_date=trade_date,
                close=close,
                pct_chg=float(row.pct_chg),
                ma20=float(row.ma20),
                ma60=float(row.ma60),
                momentum_20d=float(row.momentum_20d),
                distance_ma20=float(row.distance_ma20),
                average_amount_20d=float(row.average_amount_20d),
                amount_ratio=float(row.amount_ratio),
                sector_pct_chg=float(row.sector_pct_chg),
                sector_breadth=float(row.sector_breadth),
                sector_amount_ratio=float(row.sector_amount_ratio),
                sector_momentum_20d=float(row.sector_momentum_20d),
                sector_score=float(row.sector_score),
                sector_amount_percentile=float(row.sector_amount_percentile),
                main_net_inflow=float(row.main_net_inflow),
                main_flow_ratio=float(row.main_flow_ratio),
                score=float(score),
                eligible=eligible,
                reason=reason,
                selection_reason=selection_reason,
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=stop_loss,
                take_profit_1=close * 1.06,
                take_profit_2=close * 1.10,
            )
        )
    reason_counts = pd.Series([candidate.reason for candidate in records]).value_counts()
    LOGGER.info(
        "中军门槛完成：评估%d只，合格%d只；淘汰原因=%s",
        len(records),
        int(reason_counts.get("eligible", 0)),
        ", ".join(f"{reason}={count}" for reason, count in reason_counts.items() if reason != "eligible") or "无",
    )
    return records, hot.sort_values("sector_score", ascending=False), trade_date


def select_commanders(
    candidates: list[HotSectorCandidate],
    top_n: int,
    max_per_sector: int,
) -> list[HotSectorCandidate]:
    """Select up to ten qualified commanders with a sector concentration cap."""
    ranked = sorted(
        (candidate for candidate in candidates if candidate.eligible),
        key=lambda candidate: (-candidate.score, -candidate.average_amount_20d, candidate.symbol),
    )
    selected = []
    sector_counts: dict[str, int] = {}
    concentration_skips = 0
    for candidate in ranked:
        if sector_counts.get(candidate.industry, 0) >= max_per_sector:
            concentration_skips += 1
            continue
        selected.append(candidate)
        sector_counts[candidate.industry] = sector_counts.get(candidate.industry, 0) + 1
        if len(selected) == top_n:
            break
    LOGGER.info(
        "候选排序完成：合格%d只，行业集中度跳过%d只，最终选出%d/%d只",
        len(ranked),
        concentration_skips,
        len(selected),
        top_n,
    )
    for rank, candidate in enumerate(selected, start=1):
        LOGGER.info(
            "入选#%d：%s(%s)，行业=%s，综合分=%.3f，涨幅=%.2f%%，20日动量=%.2f%%，"
            "主力净流入=%.0f万元，距MA20=%.2f%%，观察区间=%.2f-%.2f，止损=%.2f",
            rank,
            candidate.name,
            candidate.symbol,
            candidate.industry,
            candidate.score,
            candidate.pct_chg,
            candidate.momentum_20d * 100.0,
            candidate.main_net_inflow,
            candidate.distance_ma20 * 100.0,
            candidate.entry_low,
            candidate.entry_high,
            candidate.stop_loss,
        )
    return selected


class DeepSeekHotSectorGuidanceGenerator:
    """Generate grounded next-session plans for hot-sector commander candidates."""

    def __init__(self) -> None:
        setup_env()
        from openai import OpenAI

        api_key = get_dotenv_value("DEEPSEEK_API_KEY")
        if not api_key or api_key.startswith("your_"):
            raise ValueError("DEEPSEEK_API_KEY is required for hot-sector guidance")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
        self.client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip(),
        )
        self.max_retries = max(int(os.getenv("DEEPSEEK_MAX_RETRIES", "3")), 1)

    def analyze(self, stock: dict[str, Any], trade_date: str) -> str:
        LOGGER.info("开始DeepSeek操作分析：%s(%s)", stock["name"], stock["symbol"])
        prompt = (
            f"你是A股短线模拟盘风控员。只使用以下量化证据，为{stock['name']}（{stock['symbol']}）"
            f"制定{trade_date}之后1至5个交易日计划：{stock['selection_reason']}收盘{stock['close']:.2f}，"
            f"MA20={stock['ma20']:.2f}，建议观察区间{stock['entry_low']:.2f}-{stock['entry_high']:.2f}，"
            f"硬止损{stock['stop_loss']:.2f}，第一/第二止盈{stock['take_profit_1']:.2f}/"
            f"{stock['take_profit_2']:.2f}。输出：买入触发、放弃条件、分批止盈、最长持有期。"
            "次日高开超过3%必须说明不追高；板块跌出热度榜必须退出观察。不得虚构新闻，160字内。"
        )
        response = None
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "输出审慎、可执行的A股短线模拟计划，不承诺收益。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    max_tokens=450,
                )
                break
            except Exception:
                if attempt + 1 == self.max_retries:
                    raise
                time.sleep(2**attempt)
        if response is None or not response.choices[0].message.content:
            raise ValueError(f"DeepSeek returned no hot-sector guidance for {stock['symbol']}")
        LOGGER.info("DeepSeek操作分析完成：%s(%s)", stock["name"], stock["symbol"])
        return response.choices[0].message.content.strip()[:240]


class FeishuHotSectorNotifier:
    """Push the hot-sector commander report to a Feishu custom bot."""

    def __init__(self) -> None:
        setup_env()
        self.webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not self.webhook or "your_key_here" in self.webhook:
            raise ValueError("FEISHU_WEBHOOK_URL is required for hot-sector guidance")

    def send(self, content: str) -> bool:
        import requests

        from src.formatters import format_feishu_markdown

        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "热门板块中军短线候选"},
                    "template": "orange",
                },
                "elements": [{"tag": "markdown", "content": format_feishu_markdown(content)}],
            },
        }
        response = requests.post(self.webhook, json=payload, timeout=30)
        if response.status_code != 200:
            return False
        result = response.json()
        return result.get("code", result.get("StatusCode")) == 0


def _write_hot_sector_reports(
    config: HotSectorScreenConfig,
    candidates: list[HotSectorCandidate],
    hot_sectors: pd.DataFrame,
    selected: list[HotSectorCandidate],
    trade_date: str,
    guidance: list[dict[str, str]],
) -> tuple[Path, str]:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([asdict(candidate) for candidate in candidates]).to_csv(
        output_dir / f"screen_{trade_date}.csv",
        index=False,
    )
    payload = {
        "as_of": trade_date,
        "hot_sectors": hot_sectors.to_dict("records"),
        "eligible_count": sum(candidate.eligible for candidate in candidates),
        "selected_count": len(selected),
        "selected": [asdict(candidate) for candidate in selected],
        "guidance": guidance,
        "risk_notice": "仅用于模拟研究；高开超过3%不追，板块退潮时取消计划。",
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    (output_dir / f"selection_{trade_date}.json").write_text(content, encoding="utf-8")
    (output_dir / "latest_selection.json").write_text(content, encoding="utf-8")
    guidance_by_symbol = {item["symbol"]: item["guidance"] for item in guidance}
    lines = [
        f"# {trade_date} 热门板块中军短线候选",
        "",
        f"合格 {payload['eligible_count']} 只，选出 {len(selected)}/10 只。没有合格票时不强行补位。",
        "",
        "## 热门板块",
        "",
    ]
    for row in hot_sectors.itertuples(index=False):
        lines.append(
            f"- {row.industry}：热度{row.sector_score:.2f}，涨跌{row.sector_pct_chg:.2f}%，"
            f"上涨占比{row.sector_breadth:.0%}，成交额比{row.sector_amount_ratio:.2f}。"
        )
    for rank, candidate in enumerate(selected, start=1):
        lines.extend(
            [
                "",
                f"## {rank}. {candidate.name}（{candidate.symbol}）",
                "",
                f"- 选股理由：{candidate.selection_reason}",
                f"- 观察买入区：{candidate.entry_low:.2f}—{candidate.entry_high:.2f}；"
                "次日高开超过3%不追。",
                f"- 风控：止损{candidate.stop_loss:.2f}；止盈{candidate.take_profit_1:.2f}/"
                f"{candidate.take_profit_2:.2f}；最长5日。",
                f"- DeepSeek：{guidance_by_symbol.get(candidate.symbol, '未生成')}",
            ]
        )
    lines.extend(["", "仅用于模拟盘研究，不构成投资建议，也不保证短线收益。"])
    markdown = "\n".join(lines) + "\n"
    report_path = output_dir / f"guidance_{trade_date}.md"
    report_path.write_text(markdown, encoding="utf-8")
    (output_dir / "latest_guidance.md").write_text(markdown, encoding="utf-8")
    return report_path, markdown


def run_hot_sector_pipeline(
    config: HotSectorScreenConfig,
    provider: Any,
    generator: GuidanceGenerator,
    notifier: GuidanceNotifier,
    as_of: Optional[date] = None,
) -> dict[str, Any]:
    requested_date = as_of or date.today()
    LOGGER.info("热门板块中军任务启动：请求日期=%s，模式=DeepSeek+飞书", requested_date.isoformat())
    stock_basic, daily, moneyflow = provider.load(requested_date, config)
    candidates, hot_sectors, trade_date = evaluate_hot_sectors(config, stock_basic, daily, moneyflow)
    selected = select_commanders(candidates, config.top_n, config.max_per_sector)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    push_state_path = output_dir / "feishu_push_state.json"
    if push_state_path.exists():
        state = json.loads(push_state_path.read_text(encoding="utf-8"))
        if state.get("last_pushed_trade_date") == trade_date:
            LOGGER.info("飞书推送跳过：交易日%s已成功推送", trade_date)
            return {"status": "skipped", "as_of": trade_date, "reason": "trade_date_already_pushed"}
    guidance = [
        {"symbol": candidate.symbol, "guidance": generator.analyze(asdict(candidate), trade_date)}
        for candidate in selected
    ]
    report_path, markdown = _write_hot_sector_reports(
        config,
        candidates,
        hot_sectors,
        selected,
        trade_date,
        guidance,
    )
    LOGGER.info("筛选及操作报告已写入：%s", report_path)
    LOGGER.info("开始飞书推送：交易日=%s，候选=%d只", trade_date, len(selected))
    if not notifier.send(markdown):
        raise RuntimeError("Feishu rejected the hot-sector guidance report")
    push_state_path.write_text(
        json.dumps(
            {
                "last_pushed_trade_date": trade_date,
                "pushed_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    LOGGER.info("飞书推送成功：交易日=%s，候选=%d只", trade_date, len(selected))
    return {
        "status": "ok",
        "as_of": trade_date,
        "hot_sector_count": len(hot_sectors),
        "selected_count": len(selected),
        "guidance_count": len(guidance),
        "guidance_report": str(report_path),
        "feishu_pushed": True,
    }


def run_hot_sector_screen(
    config: HotSectorScreenConfig,
    provider: Any,
    as_of: Optional[date] = None,
) -> dict[str, Any]:
    """Run the deterministic screen without calling DeepSeek or Feishu."""
    requested_date = as_of or date.today()
    LOGGER.info("热门板块中军任务启动：请求日期=%s，模式=仅筛选", requested_date.isoformat())
    stock_basic, daily, moneyflow = provider.load(requested_date, config)
    candidates, hot_sectors, trade_date = evaluate_hot_sectors(config, stock_basic, daily, moneyflow)
    selected = select_commanders(candidates, config.top_n, config.max_per_sector)
    report_path, _ = _write_hot_sector_reports(
        config,
        candidates,
        hot_sectors,
        selected,
        trade_date,
        [],
    )
    LOGGER.info("筛选报告已写入：%s", report_path)
    return {
        "status": "ok",
        "as_of": trade_date,
        "hot_sector_count": len(hot_sectors),
        "eligible_count": sum(candidate.eligible for candidate in candidates),
        "selected_count": len(selected),
        "report": str(report_path),
        "feishu_pushed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly hot-sector commander screening")
    parser.add_argument("--config", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("command", nargs="?", choices=("screen", "run"), default="run")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_hot_sector_screen_config(args.config)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    provider = TushareMarketDataProvider()
    if args.command == "screen":
        result = run_hot_sector_screen(config, provider, as_of)
    else:
        result = run_hot_sector_pipeline(
            config,
            provider,
            DeepSeekHotSectorGuidanceGenerator(),
            FeishuHotSectorNotifier(),
            as_of,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
