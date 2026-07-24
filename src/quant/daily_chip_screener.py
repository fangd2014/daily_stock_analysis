"""Nightly market-wide chip double-peak stock screening."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional, Protocol

import pandas as pd

from src.config import get_dotenv_value, setup_env
from src.tushare_client import create_tushare_pro_api

from .config import StrategyConfig
from .strategy import _select_double_peak


@dataclass(frozen=True)
class DailyChipScreenConfig:
    top_n: int
    lookback_days: int
    calendar_days: int
    selection_buy_position_max: float
    min_average_amount_20d: float
    min_sector_members: int
    exclude_st: bool
    output_dir: str
    cache_dir: str


@dataclass(frozen=True)
class DailyChipScreenResult:
    symbol: str
    name: str
    industry: str
    trade_date: str
    close: float
    lower_peak: Optional[float]
    upper_peak: Optional[float]
    valley: Optional[float]
    chip_position: Optional[float]
    distance_to_lower_pct: Optional[float]
    sector_pct_chg: float
    main_net_inflow: float
    average_amount_20d: float
    eligible: bool
    reason: str


class MarketDataProvider(Protocol):
    def load(self, as_of: date, config: DailyChipScreenConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Return stock basic, daily price, and signal-day money-flow data."""


class GuidanceGenerator(Protocol):
    def analyze(self, stock: dict[str, Any], trade_date: str) -> str:
        """Generate next-session paper-trading guidance for one stock."""


class GuidanceNotifier(Protocol):
    def send(self, content: str) -> bool:
        """Push the completed guidance report."""


class TushareMarketDataProvider:
    """Fetch and incrementally cache the market-wide Tushare inputs."""

    def __init__(self) -> None:
        setup_env()
        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token or token.startswith("your_"):
            raise ValueError("TUSHARE_TOKEN is required for nightly chip screening")
        self.api = create_tushare_pro_api(token)

    def _daily(self, trade_date: str, cache_dir: Path) -> pd.DataFrame:
        path = cache_dir / "daily" / f"{trade_date}.csv"
        if path.exists() and path.stat().st_size > 0:
            return pd.read_csv(path, dtype={"trade_date": str})
        frame = self.api.daily(
            trade_date=trade_date,
            fields="ts_code,trade_date,open,high,low,close,pct_chg,vol,amount",
        )
        if frame is not None and not frame.empty:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)
        return frame if frame is not None else pd.DataFrame()

    def _moneyflow(self, trade_date: str, cache_dir: Path) -> pd.DataFrame:
        path = cache_dir / "moneyflow" / f"{trade_date}.csv"
        if path.exists() and path.stat().st_size > 0:
            return pd.read_csv(path, dtype={"trade_date": str})
        frame = self.api.moneyflow(
            trade_date=trade_date,
            fields=(
                "ts_code,trade_date,buy_lg_amount,sell_lg_amount,"
                "buy_elg_amount,sell_elg_amount"
            ),
        )
        if frame is not None and not frame.empty:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)
        return frame if frame is not None else pd.DataFrame()

    def load(self, as_of: date, config: DailyChipScreenConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        end_date = as_of.strftime("%Y%m%d")
        start_date = (as_of - timedelta(days=config.calendar_days)).strftime("%Y%m%d")
        calendar = self.api.trade_cal(
            exchange="",
            start_date=start_date,
            end_date=end_date,
            is_open="1",
            fields="cal_date",
        )
        if calendar is None or calendar.empty:
            raise ValueError("Trading calendar is unavailable")
        trade_dates = sorted(calendar["cal_date"].astype(str).tolist())[-(config.lookback_days + 8) :]
        cache_dir = Path(config.cache_dir)
        daily_frames = [self._daily(trade_date, cache_dir) for trade_date in trade_dates]
        daily_frames = [frame for frame in daily_frames if frame is not None and not frame.empty]
        if not daily_frames:
            raise ValueError("Daily market data is unavailable")
        daily = pd.concat(daily_frames, ignore_index=True)
        daily["trade_date"] = daily["trade_date"].astype(str)
        actual_trade_date = daily["trade_date"].max()
        moneyflow = self._moneyflow(actual_trade_date, cache_dir)
        stock_basic = self.api.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,industry,market,list_date",
        )
        return stock_basic, daily, moneyflow


class DeepSeekGuidanceGenerator:
    """Generate concise next-session plans through DeepSeek's OpenAI-compatible API."""

    def __init__(self) -> None:
        setup_env()
        from openai import OpenAI

        api_key = get_dotenv_value("DEEPSEEK_API_KEY")
        if not api_key or api_key.startswith("your_"):
            raise ValueError("DEEPSEEK_API_KEY is required for next-session guidance")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip()
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
        self.request_delay = float(os.getenv("DEEPSEEK_REQUEST_DELAY", "1"))
        self.max_retries = int(os.getenv("DEEPSEEK_MAX_RETRIES", "3"))
        if self.max_retries < 1:
            raise ValueError("DEEPSEEK_MAX_RETRIES must be positive")
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def analyze(self, stock: dict[str, Any], trade_date: str) -> str:
        prompt = (
            f"你是A股模拟盘风控分析师。基于{trade_date}收盘后的量化数据，为{stock['name']}"
            f"（{stock['symbol']}）制定下一交易日操作指导。已知：所属行业{stock['industry']}，"
            f"行业当日涨跌{stock['sector_pct_chg']:.2f}%，主力净买入{stock['main_net_inflow']:.2f}万元，"
            f"收盘价{stock['close']:.2f}元，筹码低峰{stock['lower_peak']:.2f}元，"
            f"高峰{stock['upper_peak']:.2f}元，峰间位置{stock['chip_position']:.1%}。"
            "只依据这些数据，不虚构新闻或基本面。"
            "输出四项：操作结论、触发条件与参考区间、止损/失效条件、止盈/减仓条件。"
            "必须说明不追高、开盘大幅跳空时如何处理，控制在180字内。"
        )
        response = None
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "输出审慎、可执行的A股次日模拟操作计划，"
                                "不承诺收益，不建议满仓。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=500,
                )
                break
            except Exception:
                if attempt + 1 == self.max_retries:
                    raise
                time.sleep(2**attempt)
        if response is None:
            raise RuntimeError(f"DeepSeek request did not complete for {stock['symbol']}")
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError(f"DeepSeek returned empty guidance for {stock['symbol']}")
        if self.request_delay > 0:
            time.sleep(self.request_delay)
        return content.strip()[:240]


class FeishuGuidanceNotifier:
    """Send only the nightly guidance report to the configured Feishu webhook."""

    def __init__(self) -> None:
        setup_env()
        self.webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not self.webhook or "your_key_here" in self.webhook:
            raise ValueError("FEISHU_WEBHOOK_URL is required for nightly guidance push")

    def send(self, content: str) -> bool:
        import requests

        from src.formatters import format_feishu_markdown

        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "筹码双峰次日模拟操作指导"},
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": format_feishu_markdown(content),
                    }
                ],
            },
        }
        response = requests.post(self.webhook, json=payload, timeout=30)
        if response.status_code != 200:
            return False
        result = response.json()
        code = result.get("code", result.get("StatusCode"))
        return code == 0


def load_daily_chip_screen_config(path: str | Path) -> DailyChipScreenConfig:
    """Load and validate nightly screen settings."""
    config = DailyChipScreenConfig(**json.loads(Path(path).read_text(encoding="utf-8")))
    if config.top_n != 10:
        raise ValueError("top_n must be 10")
    if config.lookback_days < 48:
        raise ValueError("lookback_days must be at least 48")
    if config.calendar_days < config.lookback_days:
        raise ValueError("calendar_days must cover lookback_days")
    if not 0 < config.selection_buy_position_max < 0.5:
        raise ValueError("selection_buy_position_max must be between 0 and 0.5")
    if config.min_average_amount_20d < 0:
        raise ValueError("min_average_amount_20d cannot be negative")
    if config.min_sector_members < 1:
        raise ValueError("min_sector_members must be positive")
    return config


def _number(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(0.0, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _main_net_inflow(moneyflow: pd.DataFrame) -> pd.DataFrame:
    if moneyflow is None or moneyflow.empty:
        return pd.DataFrame(columns=["ts_code", "main_net_inflow"])
    values = moneyflow.copy()
    values["main_net_inflow"] = (
        _number(values, "buy_lg_amount")
        - _number(values, "sell_lg_amount")
        + _number(values, "buy_elg_amount")
        - _number(values, "sell_elg_amount")
    )
    return values[["ts_code", "main_net_inflow"]].drop_duplicates("ts_code", keep="last")


def evaluate_market(
    config: DailyChipScreenConfig,
    stock_basic: pd.DataFrame,
    daily: pd.DataFrame,
    moneyflow: pd.DataFrame,
) -> tuple[list[DailyChipScreenResult], str]:
    """Evaluate the latest complete session without using its volume in the chip profile."""
    if stock_basic is None or stock_basic.empty or daily is None or daily.empty:
        raise ValueError("Stock basic and daily market data are required")
    prices = daily.copy()
    prices["trade_date"] = prices["trade_date"].astype(str)
    trade_date = prices["trade_date"].max()
    latest = prices[prices["trade_date"] == trade_date].copy()
    basic = stock_basic.copy()
    basic["industry"] = basic["industry"].fillna("").astype(str)
    basic["name"] = basic["name"].fillna("").astype(str)
    if config.exclude_st:
        basic = basic[~basic["name"].str.contains(r"^\*?ST|退", case=False, regex=True)]
    latest = latest.merge(basic[["ts_code", "name", "industry"]], on="ts_code", how="inner")
    latest["pct_chg"] = _number(latest, "pct_chg")
    sector = latest.groupby("industry", as_index=False).agg(
        sector_pct_chg=("pct_chg", "mean"),
        sector_member_count=("ts_code", "nunique"),
    )
    latest = latest.merge(sector, on="industry", how="left")
    latest = latest.merge(_main_net_inflow(moneyflow), on="ts_code", how="left")
    latest["main_net_inflow"] = latest["main_net_inflow"].fillna(0.0)
    latest_by_symbol = latest.set_index("ts_code")
    history_prices = prices[prices["trade_date"] < trade_date].sort_values(["ts_code", "trade_date"])
    history_by_symbol = {
        symbol: frame.tail(config.lookback_days)
        for symbol, frame in history_prices.groupby("ts_code", sort=False)
    }
    strategy = StrategyConfig(
        strategy_type="chip_double_peak",
        chip_lookback_days=config.lookback_days,
        chip_min_history_days=48,
    )
    results = []
    for symbol, metadata in latest_by_symbol.iterrows():
        history = history_by_symbol.get(symbol, pd.DataFrame())
        average_amount = float(_number(history.tail(20), "amount").mean()) * 1_000
        common = {
            "symbol": symbol,
            "name": str(metadata["name"]),
            "industry": str(metadata["industry"]),
            "trade_date": trade_date,
            "close": float(metadata["close"]),
            "sector_pct_chg": float(metadata["sector_pct_chg"]),
            "main_net_inflow": float(metadata["main_net_inflow"]),
            "average_amount_20d": average_amount,
        }
        reason = ""
        peaks = None
        if not common["industry"] or int(metadata["sector_member_count"]) < config.min_sector_members:
            reason = "sector_unavailable"
        elif common["sector_pct_chg"] <= 0:
            reason = "sector_not_up"
        elif common["main_net_inflow"] <= 0:
            reason = "main_funds_not_net_buying"
        elif average_amount < config.min_average_amount_20d:
            reason = "below_liquidity_floor"
        elif len(history) < 48:
            reason = "insufficient_history"
        else:
            typical = ((history["high"] + history["low"] + history["close"]) / 3.0).to_numpy(dtype=float)
            volumes = (_number(history, "vol") * 100).to_numpy(dtype=float)
            peaks = _select_double_peak(typical, volumes, strategy)
            if peaks is None:
                reason = "double_peak_not_confirmed"
        if peaks is None:
            results.append(
                DailyChipScreenResult(
                    lower_peak=None,
                    upper_peak=None,
                    valley=None,
                    chip_position=None,
                    distance_to_lower_pct=None,
                    eligible=False,
                    reason=reason,
                    **common,
                )
            )
            continue
        lower, upper, valley = peaks
        close = common["close"]
        position = (close - lower) / (upper - lower)
        distance = close / lower - 1.0
        between = 0.0 <= position <= 1.0
        buy_ready = between and position <= config.selection_buy_position_max
        if not between:
            reason = "price_outside_peaks"
        elif not buy_ready:
            reason = "above_selection_buy_zone"
        else:
            reason = "eligible"
        results.append(
            DailyChipScreenResult(
                lower_peak=lower,
                upper_peak=upper,
                valley=valley,
                chip_position=position,
                distance_to_lower_pct=distance,
                eligible=buy_ready,
                reason=reason,
                **common,
            )
        )
    return results, trade_date


def select_top_ten(results: list[DailyChipScreenResult], top_n: int = 10) -> list[DailyChipScreenResult]:
    """Rank eligible names by proximity to the lower chip peak."""
    eligible = sorted(
        (item for item in results if item.eligible),
        key=lambda item: (
            item.distance_to_lower_pct if item.distance_to_lower_pct is not None else float("inf"),
            -item.main_net_inflow,
            -item.average_amount_20d,
        ),
    )
    if len(eligible) < top_n:
        raise ValueError(f"Only {len(eligible)} stocks meet all conditions; {top_n} are required")
    return eligible[:top_n]


def _write_reports(
    config: DailyChipScreenConfig,
    results: list[DailyChipScreenResult],
    selected: list[DailyChipScreenResult],
    trade_date: str,
) -> dict[str, Any]:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_frame = pd.DataFrame([asdict(item) for item in results])
    result_frame.to_csv(output_dir / f"screen_{trade_date}.csv", index=False)
    result_frame.to_csv(output_dir / "latest_screen.csv", index=False)
    summary = {
        "as_of": trade_date,
        "rules": {
            "chip_history_excludes_signal_day": True,
            "lookback_days": config.lookback_days,
            "selection_buy_position_max": config.selection_buy_position_max,
            "sector_rule": "same_day_industry_constituent_mean_pct_chg_above_zero",
            "main_fund_rule": "large_plus_extra_large_net_inflow_above_zero",
            "rank": "distance_to_lower_chip_peak_pct_ascending",
        },
        "eligible_count": sum(item.eligible for item in results),
        "selected": [asdict(item) for item in selected],
    }
    content = json.dumps(summary, ensure_ascii=False, indent=2)
    (output_dir / f"selection_{trade_date}.json").write_text(content, encoding="utf-8")
    (output_dir / "latest_selection.json").write_text(content, encoding="utf-8")
    lines = [
        "# 每日晚间筹码双峰选股",
        "",
        f"数据日期：{trade_date}",
        f"符合全部条件：{summary['eligible_count']} 只；选出：{len(selected)} 只",
        "",
        "| 排名 | 股票 | 行业 | 行业涨跌 | 主力净买入（万元） | 距低峰 | 峰间位置 |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for rank, item in enumerate(selected, start=1):
        lines.append(
            f"| {rank} | {item.name}（{item.symbol}） | {item.industry} | {item.sector_pct_chg:.2f}% | "
            f"{item.main_net_inflow:,.2f} | {item.distance_to_lower_pct:.2%} | {item.chip_position:.1%} |"
        )
    lines.extend(
        [
            "",
            "筹码峰仅使用当日以前数据；行业涨跌和主力资金使用当日收盘数据。",
            "结果仅用于模拟研究，不构成投资建议。",
        ]
    )
    markdown = "\n".join(lines) + "\n"
    (output_dir / f"selection_{trade_date}.md").write_text(markdown, encoding="utf-8")
    (output_dir / "latest_selection.md").write_text(markdown, encoding="utf-8")
    return summary


def run_daily_chip_screen(
    config: DailyChipScreenConfig,
    provider: MarketDataProvider,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Run and persist one nightly market-wide selection."""
    stock_basic, daily, moneyflow = provider.load(as_of or date.today(), config)
    results, trade_date = evaluate_market(config, stock_basic, daily, moneyflow)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_frame = pd.DataFrame([asdict(item) for item in results])
    result_frame.to_csv(output_dir / f"screen_{trade_date}.csv", index=False)
    result_frame.to_csv(output_dir / "latest_screen.csv", index=False)
    selected = select_top_ten(results, config.top_n)
    return _write_reports(config, results, selected, trade_date)


def _write_guidance_report(
    config: DailyChipScreenConfig,
    selection: dict[str, Any],
    guidance: list[dict[str, str]],
) -> tuple[Path, str]:
    trade_date = selection["as_of"]
    output_dir = Path(config.output_dir)
    payload = {
        "as_of": trade_date,
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "guidance": guidance,
        "risk_notice": "模拟研究，不构成投资建议；次日开盘条件变化时以失效条件为准。",
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    (output_dir / f"guidance_{trade_date}.json").write_text(content, encoding="utf-8")
    (output_dir / "latest_guidance.json").write_text(content, encoding="utf-8")
    lines = [
        f"# {trade_date} 晚间选股与次日模拟操作指导",
        "",
        "筛选条件：筹码双峰、行业当日上涨、主力资金净买入；"
        "按距低筹码峰由近到远排序。",
        "",
    ]
    selected_by_symbol = {item["symbol"]: item for item in selection["selected"]}
    for rank, item in enumerate(guidance, start=1):
        stock = selected_by_symbol[item["symbol"]]
        lines.extend(
            [
                f"## {rank}. {stock['name']}（{stock['symbol']}）",
                "",
                f"- 收盘/低峰/高峰：{stock['close']:.2f} / {stock['lower_peak']:.2f} / "
                f"{stock['upper_peak']:.2f}",
                f"- 行业涨跌：{stock['sector_pct_chg']:.2f}%",
                f"- 主力净买入：{stock['main_net_inflow']:,.2f} 万元",
                f"- DeepSeek指导：{item['guidance']}",
                "",
                "---",
                "",
            ]
        )
    lines.extend(["仅用于模拟盘研究，不构成投资建议，也不保证次日收益。"])
    markdown = "\n".join(lines) + "\n"
    report_path = output_dir / f"guidance_{trade_date}.md"
    report_path.write_text(markdown, encoding="utf-8")
    (output_dir / "latest_guidance.md").write_text(markdown, encoding="utf-8")
    return report_path, markdown


def run_nightly_pipeline(
    config: DailyChipScreenConfig,
    provider: MarketDataProvider,
    generator: GuidanceGenerator,
    notifier: GuidanceNotifier,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Select ten stocks, generate DeepSeek guidance, and push it to Feishu."""
    selection = run_daily_chip_screen(config, provider, as_of)
    output_dir = Path(config.output_dir)
    push_state_path = output_dir / "feishu_push_state.json"
    if push_state_path.exists():
        push_state = json.loads(push_state_path.read_text(encoding="utf-8"))
        if push_state.get("last_pushed_trade_date") == selection["as_of"]:
            return {
                "status": "skipped",
                "as_of": selection["as_of"],
                "reason": "trade_date_already_pushed",
                "feishu_pushed": False,
            }
    guidance_path = output_dir / f"guidance_{selection['as_of']}.json"
    guidance = []
    if guidance_path.exists():
        existing = json.loads(guidance_path.read_text(encoding="utf-8"))
        guidance = existing.get("guidance", [])
        expected_symbols = [stock["symbol"] for stock in selection["selected"]]
        if [item.get("symbol") for item in guidance] != expected_symbols:
            guidance = []
    if not guidance:
        guidance = [
            {
                "symbol": stock["symbol"],
                "guidance": generator.analyze(stock, selection["as_of"]),
            }
            for stock in selection["selected"]
        ]
    report_path, markdown = _write_guidance_report(config, selection, guidance)
    if not notifier.send(markdown):
        raise RuntimeError("Feishu rejected the nightly guidance report")
    push_state_path.write_text(
        json.dumps(
            {
                "last_pushed_trade_date": selection["as_of"],
                "pushed_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "as_of": selection["as_of"],
        "selected_count": len(selection["selected"]),
        "guidance_count": len(guidance),
        "guidance_report": str(report_path),
        "feishu_pushed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly market-wide chip double-peak screening")
    parser.add_argument("--config", required=True)
    parser.add_argument("--as-of", help="Optional YYYY-MM-DD screen date")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("screen", "run"),
        default="run",
        help="screen only, or run the complete DeepSeek and Feishu pipeline",
    )
    args = parser.parse_args()
    config = load_daily_chip_screen_config(args.config)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    provider = TushareMarketDataProvider()
    if args.command == "screen":
        summary = run_daily_chip_screen(config, provider, as_of)
    else:
        summary = run_nightly_pipeline(
            config,
            provider,
            DeepSeekGuidanceGenerator(),
            FeishuGuidanceNotifier(),
            as_of,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
