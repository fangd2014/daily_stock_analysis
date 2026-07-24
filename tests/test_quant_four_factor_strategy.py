"""Causality and selection tests for the four-factor strategy."""

import json

import numpy as np
import pandas as pd

from src.quant.four_factor_live import _diversified_candidates, _parse_news_date
from src.quant.four_factor_runner import run_four_factor_pipeline
from src.quant.four_factor_strategy import (
    IntervalLearningConfig,
    IntervalLearningModel,
    compute_auction_linkage,
    compute_index_momentum,
    compute_semantic_sentiment,
    rank_three_factor_watchlist,
    select_four_factor_portfolio,
)


def _index_bars(include_future=False):
    dates = pd.bdate_range("2025-01-01", periods=90)
    frames = []
    for position, symbol in enumerate(("000300.SH", "931643.CSI", "000905.SH")):
        close = 100 * np.cumprod(np.full(len(dates), 1.001 + position * 0.0005))
        frames.append(pd.DataFrame({"symbol": symbol, "date": dates, "close": close}))
    result = pd.concat(frames, ignore_index=True)
    if include_future:
        future = result.groupby("symbol", as_index=False).tail(1).copy()
        future["date"] = dates[-1] + pd.Timedelta(days=7)
        future["close"] = future["close"] * 10
        result = pd.concat([result, future], ignore_index=True)
    return result, dates[-1]


def test_index_momentum_ignores_bars_after_cutoff():
    clean, cutoff = _index_bars()
    contaminated, _ = _index_bars(include_future=True)

    expected = compute_index_momentum(clean, "000300.SH", cutoff)
    actual = compute_index_momentum(contaminated, "000300.SH", cutoff)

    pd.testing.assert_frame_equal(actual, expected)


def test_semantic_factor_ignores_future_events_and_deduplicates_content():
    cutoff = pd.Timestamp("2026-07-24 20:00:00")
    common = {
        "symbol": "688008.SH",
        "p_positive": 0.8,
        "p_negative": 0.1,
        "relevance": 1.0,
        "credibility": 0.9,
        "novelty": 0.8,
    }
    events = pd.DataFrame(
        [
            {**common, "published_at": cutoff - pd.Timedelta(hours=2), "content_hash": "same"},
            {**common, "published_at": cutoff - pd.Timedelta(hours=1), "content_hash": "same"},
            {
                **common,
                "published_at": cutoff + pd.Timedelta(hours=1),
                "content_hash": "future",
                "p_negative": 1.0,
            },
            {
                **common,
                "published_at": cutoff - pd.Timedelta(hours=3),
                "observed_at": cutoff + pd.Timedelta(minutes=1),
                "content_hash": "observed-in-future",
                "p_negative": 1.0,
            },
        ]
    )
    events["observed_at"] = events["observed_at"].fillna(events["published_at"])

    result = compute_semantic_sentiment(events, cutoff)

    assert result.loc[0, "event_count_24h"] == 1
    assert result.loc[0, "sentiment_24h"] > 0


def test_neutral_semantic_placeholders_do_not_count_as_news():
    cutoff = pd.Timestamp("2026-07-24 20:00:00")
    events = pd.DataFrame(
        {
            "symbol": ["600000.SH", "600001.SH"],
            "published_at": [cutoff, cutoff],
            "p_positive": [0.5, 0.5],
            "p_negative": [0.5, 0.5],
            "relevance": [0.0, 0.0],
            "credibility": [0.0, 0.0],
            "novelty": [0.0, 0.0],
        }
    )

    result = compute_semantic_sentiment(events, cutoff)

    assert result["event_count_24h"].eq(0).all()
    assert result["semantic_sentiment_score"].eq(0.5).all()


def _learning_frames():
    rows = 80
    dates = pd.bdate_range("2025-01-01", periods=rows)
    feature_a = np.linspace(-1, 1, rows)
    feature_b = np.sin(np.linspace(0, 6, rows))
    training = pd.DataFrame(
        {
            "feature_time": dates,
            "label_end": dates + pd.offsets.BDay(5),
            "feature_a": feature_a,
            "feature_b": feature_b,
            "forward_excess_return_5d": feature_a * 0.03 + feature_b * 0.01,
        }
    )
    current = pd.DataFrame(
        {
            "symbol": ["688008.SH", "600519.SH"],
            "feature_a": [0.8, -0.2],
            "feature_b": [0.5, 0.1],
        }
    )
    return training, current, dates[-1] + pd.offsets.BDay(10)


def test_interval_learning_is_causal_ordered_and_attributed():
    training, current, cutoff = _learning_frames()
    future = training.tail(10).copy()
    future["feature_time"] = cutoff + pd.offsets.BDay(1)
    future["label_end"] = cutoff + pd.offsets.BDay(6)
    future["forward_excess_return_5d"] = 9.0
    model = IntervalLearningModel(IntervalLearningConfig(minimum_training_rows=50, random_state=7))

    expected = model.fit_predict(training, current, ["feature_a", "feature_b"], cutoff)
    actual = model.fit_predict(pd.concat([training, future]), current, ["feature_a", "feature_b"], cutoff)

    np.testing.assert_allclose(actual[["q10", "q50", "q90"]], expected[["q10", "q50", "q90"]])
    assert (actual["q10"] <= actual["q50"]).all()
    assert (actual["q50"] <= actual["q90"]).all()
    assert set(json.loads(actual.loc[0, "feature_attribution"])) == {"feature_a", "feature_b"}


def _auction_factor(symbols):
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        industry = ("半导体", "软件", "通信")[min(symbol_index // 2, 2)]
        for clock, bid in (("09:15", 3000), ("09:20", 2600), ("09:25", 2200)):
            rows.append(
                {
                    "symbol": symbol,
                    "industry": industry,
                    "timestamp": f"2026-07-25 {clock}:00",
                    "indicative_price": 10.1 + symbol_index * 0.01,
                    "prior_close": 10.0,
                    "matched_volume": 2000,
                    "unmatched_bid": bid,
                    "unmatched_ask": 1000,
                }
            )
    history = pd.DataFrame({"symbol": symbols, "matched_volume": [1000] * len(symbols)})
    return compute_auction_linkage(pd.DataFrame(rows), history)


def _selection_inputs(symbols):
    industries = [("半导体", "软件", "通信")[min(index // 2, 2)] for index in range(len(symbols))]
    stocks = pd.DataFrame(
        {
            "symbol": symbols,
            "name": [f"股票{i}" for i in range(len(symbols))],
            "industry": industries,
            "index_symbol": ["931643.CSI"] * len(symbols),
            "buy_ready": [True] * len(symbols),
            "average_amount_20d": np.arange(len(symbols), 0, -1) * 1e8,
        }
    )
    index = pd.DataFrame({"index_symbol": ["931643.CSI"], "index_momentum_score": [0.8]})
    sentiment = pd.DataFrame(
        {
            "symbol": symbols,
            "semantic_sentiment_score": np.linspace(0.9, 0.6, len(symbols)),
            "negative_event_risk": [0.1] * len(symbols),
        }
    )
    interval = pd.DataFrame(
        {
            "symbol": symbols,
            "expected_edge": [0.03] * len(symbols),
            "q10": [-0.02] * len(symbols),
            "q50": [0.032] * len(symbols),
            "q90": [0.08] * len(symbols),
            "attribution_confidence": [0.7] * len(symbols),
            "interval_learning_score": np.linspace(0.95, 0.65, len(symbols)),
        }
    )
    return stocks, index, sentiment, interval


def test_real_auction_selects_five_and_limits_each_industry_to_two():
    symbols = [f"60000{i}.SH" for i in range(6)]
    stocks, index, sentiment, interval = _selection_inputs(symbols)
    auction = _auction_factor(symbols)

    selected = select_four_factor_portfolio(stocks, index, sentiment, interval, auction)

    assert len(selected) == 5
    industry_counts = {
        industry: sum(item.industry == industry for item in selected)
        for industry in {item.industry for item in selected}
    }
    assert max(industry_counts.values()) <= 2


def test_missing_auction_creates_watchlist_but_never_an_auto_buy():
    symbols = [f"60000{i}.SH" for i in range(6)]
    stocks, index, sentiment, interval = _selection_inputs(symbols)

    watchlist = rank_three_factor_watchlist(stocks, index, sentiment, interval, limit=5)
    selected = select_four_factor_portfolio(stocks, index, sentiment, interval, auction_factor=None)

    assert len(watchlist) == 5
    assert not watchlist["auto_buy_ready"].any()
    assert selected == []


def test_pipeline_without_auction_writes_observation_only_report(tmp_path):
    clean_bars, _ = _index_bars()
    symbols = [f"60000{i}.SH" for i in range(6)]
    stocks, _, _, _ = _selection_inputs(symbols)
    training, current, learning_cutoff = _learning_frames()
    current = pd.concat([current] * 3, ignore_index=True)
    current["symbol"] = symbols
    events = pd.DataFrame(
        {
            "symbol": symbols,
            "published_at": [learning_cutoff - pd.Timedelta(hours=1)] * len(symbols),
            "p_positive": np.linspace(0.9, 0.6, len(symbols)),
            "p_negative": [0.1] * len(symbols),
            "relevance": [1.0] * len(symbols),
            "credibility": [0.9] * len(symbols),
            "novelty": [0.8] * len(symbols),
        }
    )
    config = {
        "name": "test four-factor strategy",
        "benchmark_symbol": "000300.SH",
        "max_positions": 5,
        "max_per_industry": 2,
        "watchlist_size": 20,
        "interval_learning": {
            "feature_columns": ["feature_a", "feature_b"],
            "minimum_training_rows": 50,
            "transaction_cost": 0.002,
            "random_state": 7,
        },
        "risk": {"target_exposure": 0.6},
    }
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    stocks.to_csv(input_dir / "stocks.csv", index=False)
    clean_bars.to_csv(input_dir / "index_bars.csv", index=False)
    events.to_csv(input_dir / "sentiment_events.csv", index=False)
    training.to_csv(input_dir / "interval_training.csv", index=False)
    current.to_csv(input_dir / "current_features.csv", index=False)

    result = run_four_factor_pipeline(config_path, input_dir, tmp_path / "reports", learning_cutoff)

    report_dir = tmp_path / "reports" / pd.Timestamp(learning_cutoff).strftime("%Y%m%d_%H%M%S")
    assert result["auction_status"] == "missing"
    assert result["selected_count"] == 0
    assert result["auto_trade_enabled"] is False
    assert (report_dir / "selection.json").exists()
    assert "未生成自动买入信号" in (report_dir / "selection.md").read_text(encoding="utf-8")


def test_live_candidates_are_diversified_and_news_dates_are_causal():
    ranked = pd.DataFrame(
        {
            "symbol": ["A", "B", "C", "D", "E", "F"],
            "industry": ["银行", "银行", "银行", "港口", "港口", "铁路"],
            "score": [6, 5, 4, 3, 2, 1],
        }
    )

    selected = _diversified_candidates(ranked, limit=5, max_per_industry=2)

    assert selected["symbol"].tolist() == ["A", "B", "D", "E", "F"]
    cutoff = pd.Timestamp("2026-07-24 20:00:00")
    assert _parse_news_date("2026-07-24", cutoff) == pd.Timestamp("2026-07-24 12:00:00")
    assert _parse_news_date("2026-07-25", cutoff) is None
