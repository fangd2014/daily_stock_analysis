"""Live evening four-factor watchlist built from point-in-time market caches."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.config import setup_env
from src.search_service import get_search_service

from .four_factor_strategy import (
    IntervalLearningConfig,
    IntervalLearningModel,
    compute_index_momentum,
    compute_semantic_sentiment,
    rank_three_factor_watchlist,
)
from .tickdb_client import TickDBClient, TickDBError


LOGGER = logging.getLogger(__name__)
MODEL_FEATURES = (
    "return_5d",
    "return_20d",
    "relative_strength_20d",
    "volatility_20d",
    "turnover_zscore_20d",
    "main_net_inflow_ratio",
)


def _load_partitions(root: Path, cutoff: str, limit: int = 140, minimum: int = 66) -> pd.DataFrame:
    paths = [path for path in sorted(root.glob("*.csv")) if path.stem <= cutoff][-limit:]
    if len(paths) < minimum:
        raise ValueError(f"At least {minimum} partitions are required; found {len(paths)}")
    return pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)


def _load_market_inputs(cache_root: Path, screen_path: Path, cutoff: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = _load_partitions(cache_root / "daily", cutoff)
    moneyflow = _load_partitions(cache_root / "moneyflow", cutoff, minimum=1)
    names = pd.read_csv(screen_path)[["symbol", "name", "industry"]].drop_duplicates("symbol")
    names = names.rename(columns={"symbol": "ts_code"})
    daily["trade_date"] = daily["trade_date"].astype(str)
    moneyflow["trade_date"] = moneyflow["trade_date"].astype(str)
    daily = daily.merge(names, on="ts_code", how="inner")
    return daily, moneyflow


def _prepare_features(daily: pd.DataFrame, moneyflow: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    values = daily.sort_values(["ts_code", "trade_date"]).copy()
    values["close"] = pd.to_numeric(values["close"], errors="coerce")
    values["amount_yuan"] = pd.to_numeric(values["amount"], errors="coerce") * 1_000.0
    group = values.groupby("ts_code", sort=False)
    values["return_1d"] = group["close"].pct_change(fill_method=None)
    values["return_5d"] = values["close"] / group["close"].shift(5) - 1.0
    values["return_20d"] = values["close"] / group["close"].shift(20) - 1.0
    values["volatility_20d"] = group["return_1d"].transform(lambda item: item.rolling(20).std(ddof=0))
    amount_mean = group["amount_yuan"].transform(lambda item: item.rolling(20).mean())
    amount_std = group["amount_yuan"].transform(lambda item: item.rolling(20).std(ddof=0))
    values["turnover_zscore_20d"] = (values["amount_yuan"] - amount_mean) / amount_std.replace(0, np.nan)
    values["average_amount_20d"] = amount_mean

    flow = moneyflow.copy()
    for column in ("buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"):
        flow[column] = pd.to_numeric(flow[column], errors="coerce").fillna(0.0)
    flow["main_net_inflow_yuan"] = (
        flow["buy_lg_amount"]
        + flow["buy_elg_amount"]
        - flow["sell_lg_amount"]
        - flow["sell_elg_amount"]
    ) * 10_000.0
    values = values.merge(
        flow[["ts_code", "trade_date", "main_net_inflow_yuan"]],
        on=["ts_code", "trade_date"],
        how="left",
    )
    values["main_net_inflow_yuan"] = values["main_net_inflow_yuan"].fillna(0.0)
    values["main_net_inflow_ratio"] = values["main_net_inflow_yuan"] / values["amount_yuan"].replace(0, np.nan)
    group = values.groupby("ts_code", sort=False)

    market_returns = values.groupby("trade_date")["return_1d"].mean()
    market_close = 100.0 * (1.0 + market_returns.fillna(0.0)).cumprod()
    market_20d = market_close / market_close.shift(20) - 1.0
    values["market_return_20d"] = values["trade_date"].map(market_20d)
    values["relative_strength_20d"] = values["return_20d"] - values["market_return_20d"]

    values["future_close_5d"] = group["close"].shift(-5)
    values["label_end"] = group["trade_date"].shift(-5)
    forward_market = market_close.shift(-5) / market_close - 1.0
    values["forward_market_5d"] = values["trade_date"].map(forward_market)
    values["forward_excess_return_5d"] = (
        values["future_close_5d"] / values["close"] - 1.0 - values["forward_market_5d"]
    )
    values["feature_time"] = pd.to_datetime(values["trade_date"], format="%Y%m%d")
    values["label_end"] = pd.to_datetime(values["label_end"], format="%Y%m%d", errors="coerce")
    return values, market_close.rename("close").reset_index()


def _sector_factor_inputs(values: pd.DataFrame, market_close: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    sector_returns = values.groupby(["industry", "trade_date"])["return_1d"].mean().reset_index()
    sector_returns["close"] = sector_returns.groupby("industry")["return_1d"].transform(
        lambda item: 100.0 * (1.0 + item.fillna(0.0)).cumprod()
    )
    index_bars = sector_returns.rename(columns={"industry": "symbol", "trade_date": "date"})[
        ["symbol", "date", "close"]
    ]
    benchmark = market_close.rename(columns={"trade_date": "date"}).copy()
    benchmark["symbol"] = "__MARKET__"
    index_bars = pd.concat([index_bars, benchmark[["symbol", "date", "close"]]], ignore_index=True)
    member_bars = values.rename(
        columns={"ts_code": "symbol", "industry": "index_symbol", "trade_date": "date"}
    )[["symbol", "index_symbol", "date", "close"]]
    return index_bars, member_bars


def _price_limit(symbol: str) -> float:
    code = symbol.split(".", maxsplit=1)[0]
    if code.startswith(("300", "301", "688")):
        return 20.0
    if code.startswith(("4", "8", "92")):
        return 30.0
    return 10.0


def _current_universe(values: pd.DataFrame, cutoff: str) -> pd.DataFrame:
    current = values[values["trade_date"].eq(cutoff)].copy()
    current["pct_chg"] = pd.to_numeric(current["pct_chg"], errors="coerce")
    current["buy_ready"] = [
        (not bool(re.search(r"^\*?ST|退", str(name), re.IGNORECASE)))
        and float(amount) >= 100_000_000.0
        and abs(float(change)) < _price_limit(symbol) - 0.3
        for symbol, name, amount, change in zip(
            current["ts_code"], current["name"], current["average_amount_20d"], current["pct_chg"]
        )
    ]
    current = current.rename(columns={"ts_code": "symbol"})
    current["index_symbol"] = current["industry"]
    return current


def _parse_news_date(value: str | None, cutoff: pd.Timestamp) -> pd.Timestamp | None:
    if not value:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    parsed = pd.Timestamp(parsed)
    if parsed.tzinfo is not None:
        parsed = parsed.tz_localize(None)
    if parsed.hour == 0 and parsed.minute == 0:
        parsed += pd.Timedelta(hours=12)
    return parsed if parsed <= cutoff else None


def _classify_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return []
    setup_env()
    from openai import OpenAI

    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is required to classify news")
    client = OpenAI(
        api_key=key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    )
    prompt = (
        "对输入A股新闻逐条分类，只返回JSON对象，格式为{\"items\":[{\"id\":整数,"
        "\"p_positive\":0到1,\"p_negative\":0到1,\"relevance\":0到1,"
        "\"credibility\":0到1,\"novelty\":0到1}]}。正负概率可不互补；标题党降低可信度，"
        "重复或旧闻降低新颖度，不得补充输入外事实。输入：" + json.dumps(items, ensure_ascii=False)
    )
    response = client.chat.completions.create(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        messages=[
            {"role": "system", "content": "你是严格输出结构化概率的金融文本分类器。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=3000,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    payload = json.loads(content)
    return list(payload.get("items", []))


def _sentiment_events(stocks: pd.DataFrame, cutoff: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, Any]]:
    service = get_search_service()
    raw_items: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    for stock in stocks.itertuples(index=False):
        response = service.search_stock_news(stock.symbol.split(".")[0], stock.name, max_results=3)
        sources[stock.symbol] = {
            "provider": response.provider,
            "success": response.success,
            "results": len(response.results),
        }
        for result in response.results:
            published_at = _parse_news_date(result.published_date, cutoff)
            if published_at is None:
                continue
            raw_items.append(
                {
                    "id": len(raw_items),
                    "symbol": stock.symbol,
                    "published_at": published_at.isoformat(),
                    "title": result.title[:180],
                    "snippet": result.snippet[:500],
                    "source": result.source,
                    "url": result.url,
                }
            )
    classified = {int(item["id"]): item for item in _classify_news(raw_items)} if raw_items else {}
    events = []
    for item in raw_items:
        probabilities = classified.get(item["id"])
        if probabilities is None:
            continue
        content_hash = hashlib.sha256((item["title"] + item["snippet"]).encode("utf-8")).hexdigest()
        events.append(
            {
                "symbol": item["symbol"],
                "published_at": item["published_at"],
                "observed_at": cutoff.isoformat(),
                "content_hash": content_hash,
                **{
                    name: float(np.clip(probabilities.get(name, 0.5), 0.0, 1.0))
                    for name in ("p_positive", "p_negative", "relevance", "credibility", "novelty")
                },
            }
        )
    observed_symbols = {event["symbol"] for event in events}
    for symbol in stocks["symbol"]:
        if symbol not in observed_symbols:
            events.append(
                {
                    "symbol": symbol,
                    "published_at": cutoff.isoformat(),
                    "observed_at": cutoff.isoformat(),
                    "content_hash": f"neutral:{symbol}:{cutoff.date()}",
                    "p_positive": 0.5,
                    "p_negative": 0.5,
                    "relevance": 0.0,
                    "credibility": 0.0,
                    "novelty": 0.0,
                }
            )
    return pd.DataFrame(events), sources


def _tickdb_confirmation(symbols: list[str]) -> tuple[dict[str, Any], str | None]:
    client = TickDBClient()
    try:
        payload = client.get_market_metrics(symbols)
    except TickDBError as metrics_error:
        try:
            payload = client.get_ticker(symbols)
        except TickDBError as ticker_error:
            return {}, f"metrics={metrics_error}; ticker={ticker_error}"
    rows = payload if isinstance(payload, list) else payload.get("items", payload.get("data", []))
    return {str(item.get("symbol")): item for item in rows if isinstance(item, dict)}, None


def _diversified_candidates(
    ranked: pd.DataFrame,
    limit: int,
    max_per_industry: int = 2,
) -> pd.DataFrame:
    selected = []
    industry_counts: dict[str, int] = {}
    for index, row in ranked.iterrows():
        industry = str(row["industry"])
        if industry_counts.get(industry, 0) >= max_per_industry:
            continue
        selected.append(index)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        if len(selected) >= limit:
            break
    return ranked.loc[selected].copy()


def run_live_four_factor(
    as_of: str,
    cache_root: str = "data/quant_cache/daily_chip_screen",
    report_root: str = "reports/quant/four_factor_live",
    news_candidates: int = 15,
) -> dict[str, Any]:
    cutoff = pd.Timestamp(as_of)
    cutoff_key = cutoff.strftime("%Y%m%d")
    screen_path = Path("reports/quant/daily_chip_screen") / f"screen_{cutoff_key}.csv"
    LOGGER.info("Four-factor live run started: cutoff=%s", cutoff.isoformat())
    daily, moneyflow = _load_market_inputs(Path(cache_root), screen_path, cutoff_key)
    LOGGER.info("Market caches loaded: daily=%d moneyflow=%d", len(daily), len(moneyflow))
    features, market_close = _prepare_features(daily, moneyflow)
    LOGGER.info("Stock features prepared: rows=%d sessions=%d", len(features), features["trade_date"].nunique())
    index_bars, member_bars = _sector_factor_inputs(features, market_close)
    index_factor = compute_index_momentum(index_bars, "__MARKET__", cutoff, member_bars)
    current = _current_universe(features, cutoff_key)
    LOGGER.info("Index factor and current universe prepared: industries=%d stocks=%d", len(index_factor), len(current))

    training = features.dropna(subset=[*MODEL_FEATURES, "forward_excess_return_5d", "label_end"])
    if len(training) > 20_000:
        training = training.sort_values("feature_time").tail(20_000)
    LOGGER.info("Causal interval training rows: %d", len(training))
    interval_input = current.rename(columns={"ts_code": "symbol"})
    interval = IntervalLearningModel(
        IntervalLearningConfig(minimum_training_rows=5_000, transaction_cost=0.002, random_state=42)
    ).fit_predict(training, interval_input, list(MODEL_FEATURES), cutoff)
    LOGGER.info("Interval forecasts completed: rows=%d", len(interval))

    base = current.merge(index_factor, left_on="index_symbol", right_on="index_symbol", how="left")
    base = base.merge(interval, on="symbol", how="left")
    base["pre_news_score"] = base["index_momentum_score"] * 0.42 + base["interval_learning_score"] * 0.58
    base_gate = (
        base["buy_ready"]
        & base["index_momentum_score"].ge(0.60)
        & base["expected_edge"].gt(0.01)
        & base["q10"].gt(-0.05)
        & base["attribution_confidence"].ge(0.35)
    )
    base = base[base_gate].sort_values(
        ["pre_news_score", "average_amount_20d"], ascending=[False, False]
    )
    diversified = _diversified_candidates(base, news_candidates, max_per_industry=2)
    news_pool = diversified[
        ["symbol", "name", "index_symbol", "buy_ready", "average_amount_20d"]
    ].rename(columns={"index_symbol": "industry"})
    LOGGER.info("Pre-news candidates selected: rows=%d", len(news_pool))
    events, news_sources = _sentiment_events(news_pool, cutoff)
    LOGGER.info("Point-in-time news events prepared: rows=%d", len(events))
    sentiment = compute_semantic_sentiment(events, cutoff)
    watchlist = rank_three_factor_watchlist(
        current,
        index_factor,
        sentiment,
        interval,
        limit=10,
    )
    watchlist = _diversified_candidates(watchlist, 10, max_per_industry=2).reset_index(drop=True)
    LOGGER.info("Evening watchlist completed: rows=%d", len(watchlist))
    tickdb_rows, tickdb_error = (
        _tickdb_confirmation(watchlist["symbol"].tolist()) if not watchlist.empty else ({}, None)
    )
    watchlist["tickdb_confirmed"] = watchlist["symbol"].map(lambda symbol: symbol in tickdb_rows)
    watchlist["tickdb_last_done"] = watchlist["symbol"].map(
        lambda symbol: tickdb_rows.get(symbol, {}).get("last_done")
        or tickdb_rows.get(symbol, {}).get("last_price")
    )

    output = Path(report_root) / cutoff.strftime("%Y%m%d_%H%M%S")
    output.mkdir(parents=True, exist_ok=True)
    watchlist.to_csv(output / "evening_watchlist.csv", index=False, encoding="utf-8-sig")
    index_factor.to_csv(output / "index_factor.csv", index=False, encoding="utf-8-sig")
    sentiment.to_csv(output / "sentiment_factor.csv", index=False, encoding="utf-8-sig")
    interval.to_csv(output / "interval_factor.csv", index=False, encoding="utf-8-sig")
    payload = {
        "as_of": cutoff.isoformat(),
        "phase": "evening_pre_auction",
        "candidate_count": len(current),
        "news_candidate_count": len(news_pool),
        "watchlist_count": len(watchlist),
        "auto_buy_ready": False,
        "auction_status": "pending_next_session_09:25",
        "tickdb_error": tickdb_error,
        "news_sources": news_sources,
        "watchlist": watchlist.to_dict(orient="records"),
        "output_dir": str(output),
    }
    (output / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        f"# {cutoff_key} 四因子晚间观察名单",
        "",
        "当前阶段：前夜三因子排序，等待次日 09:25 真实竞价因子确认。",
        "",
        "| 排名 | 股票 | 行业 | 综合分 | 指数动量 | 舆情 | 区间学习 | q10/q50/q90 | TickDB |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for rank, row in enumerate(watchlist.itertuples(index=False), start=1):
        lines.append(
            f"| {rank} | {row.name}（{row.symbol}） | {row.industry} | {row.pre_auction_score:.3f} | "
            f"{row.index_momentum_score:.0%} | {row.semantic_sentiment_score:.0%} | "
            f"{row.interval_learning_score:.0%} | {row.q10:.1%}/{row.q50:.1%}/{row.q90:.1%} | "
            f"{'是' if row.tickdb_confirmed else '否'} |"
        )
    lines.extend(
        [
            "",
            "今晚不产生买入指令；下一交易日跳空、竞价量比、行业上涨广度和未匹配买卖盘全部通过后，"
            "才从中选择最多 5 只模拟买入。",
            "",
            "📡 数据由 TickDB.ai 提供",
            "",
            "仅用于模拟盘研究，不构成投资建议。",
        ]
    )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a live four-factor evening watchlist")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--cache-root", default="data/quant_cache/daily_chip_screen")
    parser.add_argument("--report-root", default="reports/quant/four_factor_live")
    parser.add_argument("--news-candidates", type=int, default=15)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = run_live_four_factor(args.as_of, args.cache_root, args.report_root, args.news_candidates)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
