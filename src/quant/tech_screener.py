"""Screen a technology-leader universe for actionable chip double peaks."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional, Protocol

import pandas as pd

from src.config import setup_env
from src.tushare_client import create_tushare_pro_api

from .config import StrategyConfig
from .strategy import _select_double_peak


@dataclass(frozen=True)
class TechCandidate:
    symbol: str
    name: str
    industry: str


@dataclass(frozen=True)
class TechScreenerConfig:
    initial_cash: float
    target_exposure: float
    max_positions: int
    min_market_cap: float
    min_average_amount: float
    lookback_days: int
    calendar_days: int
    selection_buy_position_max: float
    output_dir: str
    universe: list[TechCandidate]


@dataclass(frozen=True)
class ScreeningResult:
    symbol: str
    name: str
    industry: str
    trade_date: str
    close: float
    total_market_cap: float
    average_amount_20d: float
    double_peak: bool
    price_between_peaks: bool
    lower_peak: Optional[float]
    upper_peak: Optional[float]
    valley: Optional[float]
    position: Optional[float]
    one_lot_value: float
    buy_ready: bool
    eligible: bool
    reason: str


class DailyDataProvider(Protocol):
    def load(self, symbol: str, start_date: str, end_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Return daily price and daily-basic frames."""


class TushareDailyDataProvider:
    """Fetch daily prices and capitalization from the configured Tushare account."""

    def __init__(self) -> None:
        setup_env()
        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token or token.startswith("your_"):
            raise ValueError("TUSHARE_TOKEN is required for technology screening")
        self.api = create_tushare_pro_api(token)

    def load(self, symbol: str, start_date: str, end_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        daily = self.api.daily(ts_code=symbol, start_date=start_date, end_date=end_date)
        basic = self.api.daily_basic(
            ts_code=symbol,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,close,turnover_rate,total_mv,circ_mv",
        )
        return daily, basic


def load_screener_config(path: str | Path) -> TechScreenerConfig:
    """Load and validate the technology universe."""
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    universe = [TechCandidate(**item) for item in values.pop("universe")]
    config = TechScreenerConfig(universe=universe, **values)
    if not 0 < config.target_exposure < 1:
        raise ValueError("target_exposure must be between 0 and 1")
    if not 1 <= config.max_positions <= 3:
        raise ValueError("max_positions must be between 1 and 3")
    if config.lookback_days < 20:
        raise ValueError("lookback_days must be at least 20")
    if not 0 < config.selection_buy_position_max < 0.5:
        raise ValueError("selection_buy_position_max must be between 0 and 0.5")
    return config


def evaluate_candidate(
    candidate: TechCandidate,
    daily: pd.DataFrame,
    basic: pd.DataFrame,
    config: TechScreenerConfig,
) -> ScreeningResult:
    """Evaluate one candidate using only observations before the signal close."""
    if daily is None or daily.empty:
        return _empty_result(candidate, "daily_data_unavailable")
    prices = daily.copy().sort_values("trade_date").reset_index(drop=True)
    if len(prices) < config.lookback_days + 1:
        return _empty_result(candidate, "insufficient_history")
    history = prices.iloc[-config.lookback_days - 1 : -1]
    strategy = StrategyConfig(
        strategy_type="chip_double_peak",
        chip_lookback_days=config.lookback_days,
        chip_min_history_days=min(20, config.lookback_days),
    )
    typical = ((history["high"] + history["low"] + history["close"]) / 3.0).to_numpy(dtype=float)
    volumes = (pd.to_numeric(history["vol"], errors="coerce").fillna(0.0) * 100).to_numpy(dtype=float)
    peaks = _select_double_peak(typical, volumes, strategy)
    latest = prices.iloc[-1]
    close = float(latest["close"])
    latest_basic = None
    if basic is not None and not basic.empty:
        latest_basic = basic.sort_values("trade_date").iloc[-1]
    market_cap = float(latest_basic["total_mv"]) * 10_000 if latest_basic is not None else 0.0
    average_amount = float(pd.to_numeric(prices.tail(20)["amount"], errors="coerce").mean()) * 1_000

    if peaks is None:
        return ScreeningResult(
            candidate.symbol,
            candidate.name,
            candidate.industry,
            str(latest["trade_date"]),
            close,
            market_cap,
            average_amount,
            False,
            False,
            None,
            None,
            None,
            None,
            close * 100,
            False,
            False,
            "double_peak_not_confirmed",
        )
    lower, upper, valley = peaks
    position = (close - lower) / (upper - lower)
    between = 0.0 <= position <= 1.0
    liquid = average_amount >= config.min_average_amount
    large = market_cap >= config.min_market_cap
    buy_ready = between and position <= config.selection_buy_position_max
    eligible = buy_ready and liquid and large
    if not between:
        reason = "price_outside_peaks"
    elif not buy_ready:
        reason = "above_selection_buy_zone"
    elif not large:
        reason = "below_market_cap_floor"
    elif not liquid:
        reason = "below_liquidity_floor"
    else:
        reason = "eligible"
    return ScreeningResult(
        candidate.symbol,
        candidate.name,
        candidate.industry,
        str(latest["trade_date"]),
        close,
        market_cap,
        average_amount,
        True,
        between,
        lower,
        upper,
        valley,
        position,
        close * 100,
        buy_ready,
        eligible,
        reason,
    )


def _empty_result(candidate: TechCandidate, reason: str) -> ScreeningResult:
    return ScreeningResult(
        candidate.symbol,
        candidate.name,
        candidate.industry,
        "",
        0.0,
        0.0,
        0.0,
        False,
        False,
        None,
        None,
        None,
        None,
        0.0,
        False,
        False,
        reason,
    )


def select_candidates(results: list[ScreeningResult], config: TechScreenerConfig) -> list[ScreeningResult]:
    """Keep the configured number of the largest currently eligible names."""
    eligible = sorted((item for item in results if item.eligible), key=lambda item: item.total_market_cap, reverse=True)
    if len(eligible) < config.max_positions:
        raise ValueError(
            f"Only {len(eligible)} candidates meet all conditions; {config.max_positions} are required"
        )
    return eligible[: config.max_positions]


def run_screen(
    config: TechScreenerConfig,
    provider: DailyDataProvider,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Run the screen and persist JSON, CSV, and Markdown artifacts."""
    current = as_of or date.today()
    end_date = current.strftime("%Y%m%d")
    start_date = (current - timedelta(days=config.calendar_days)).strftime("%Y%m%d")
    results = []
    for candidate in config.universe:
        daily, basic = provider.load(candidate.symbol, start_date, end_date)
        results.append(evaluate_candidate(candidate, daily, basic, config))
    selected = select_candidates(results, config)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_frame = pd.DataFrame([asdict(item) for item in results])
    result_frame.to_csv(output_dir / "latest_screen.csv", index=False)
    summary = {
        "as_of": max((item.trade_date for item in results), default=""),
        "initial_cash": config.initial_cash,
        "target_exposure": config.target_exposure,
        "target_market_value": config.initial_cash * config.target_exposure,
        "selection_buy_position_max": config.selection_buy_position_max,
        "eligible_count": sum(item.eligible for item in results),
        "selected": [asdict(item) for item in selected],
    }
    summary_content = json.dumps(summary, ensure_ascii=False, indent=2)
    (output_dir / "latest_selection.json").write_text(summary_content, encoding="utf-8")
    dated_suffix = summary["as_of"] or current.strftime("%Y%m%d")
    (output_dir / f"selection_{dated_suffix}.json").write_text(summary_content, encoding="utf-8")
    lines = [
        "# 科技龙头筹码双峰筛选",
        "",
        f"数据日期：{summary['as_of']}",
        f"目标组合仓位：{config.target_exposure:.0%}",
        f"符合条件：{summary['eligible_count']} 只；最终选择：{len(selected)} 只",
        "",
        "| 股票 | 行业 | 市值（亿元） | 下峰 | 上峰 | 区间位置 | 一手市值 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in selected:
        lines.append(
            f"| {item.name}（{item.symbol}） | {item.industry} | {item.total_market_cap / 1e8:,.0f} | "
            f"{item.lower_peak:.2f} | {item.upper_peak:.2f} | {item.position:.1%} | "
            f"¥{item.one_lot_value:,.0f} |"
        )
    lines.extend(["", "筛选仅用于模拟研究，不构成投资建议。"])
    markdown = "\n".join(lines) + "\n"
    (output_dir / "latest_selection.md").write_text(markdown, encoding="utf-8")
    (output_dir / f"selection_{dated_suffix}.md").write_text(markdown, encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen technology leaders for chip double peaks")
    parser.add_argument("--config", required=True)
    parser.add_argument("--as-of", help="Optional YYYY-MM-DD screen date")
    args = parser.parse_args()
    config = load_screener_config(args.config)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    summary = run_screen(config, TushareDailyDataProvider(), as_of)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
