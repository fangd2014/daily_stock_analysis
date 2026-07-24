"""Causal four-factor computation and constrained A-share selection."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


INDEX_FACTOR_COLUMNS = (
    "index_symbol",
    "feature_cutoff",
    "return_5d",
    "return_20d",
    "return_60d",
    "relative_strength_20d",
    "breadth_20d",
    "index_momentum_raw",
    "index_momentum_score",
)
SENTIMENT_FACTOR_COLUMNS = (
    "symbol",
    "feature_cutoff",
    "sentiment_24h",
    "sentiment_surprise",
    "sentiment_volume_shock",
    "negative_event_risk",
    "semantic_sentiment_raw",
    "event_count_24h",
    "semantic_sentiment_score",
)


def _number(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _percentile(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() <= 1 or numeric.dropna().nunique() <= 1:
        return pd.Series(0.5, index=values.index, dtype=float).where(numeric.notna())
    return numeric.rank(pct=True, method="average")


def compute_index_momentum(
    index_bars: pd.DataFrame,
    benchmark_symbol: str,
    as_of: str | pd.Timestamp,
    member_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute risk-adjusted multi-horizon index momentum using completed daily bars."""
    required = {"symbol", "date", "close"}
    missing = required - set(index_bars.columns)
    if missing:
        raise ValueError("Index bars are missing fields: " + ",".join(sorted(missing)))
    cutoff = pd.Timestamp(as_of).normalize()
    bars = index_bars.copy()
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce").dt.normalize()
    if bars["date"].max() > cutoff:
        bars = bars[bars["date"] <= cutoff]
    bars["close"] = _number(bars, "close")
    series = {
        symbol: frame.sort_values("date").drop_duplicates("date", keep="last").tail(121)
        for symbol, frame in bars.groupby("symbol", sort=False)
    }
    benchmark = series.get(benchmark_symbol)
    if benchmark is None or len(benchmark) < 61:
        raise ValueError("Benchmark requires at least 61 completed daily bars")
    benchmark_returns = benchmark.set_index("date")["close"].pct_change().dropna()
    breadth: dict[str, float] = {}
    if member_bars is not None and not member_bars.empty:
        members = member_bars.copy()
        members["date"] = pd.to_datetime(members["date"], errors="coerce").dt.normalize()
        members = members[members["date"] <= cutoff].sort_values(["index_symbol", "symbol", "date"])
        for index_symbol, frame in members.groupby("index_symbol", sort=False):
            changes = []
            for _, stock in frame.groupby("symbol", sort=False):
                close = _number(stock.tail(21), "close").dropna()
                if len(close) >= 21:
                    changes.append(float(close.iloc[-1] / close.iloc[-21] - 1.0))
            breadth[index_symbol] = float(np.mean(np.array(changes) > 0)) if changes else np.nan
    records = []
    for symbol, frame in series.items():
        close = frame["close"].dropna()
        if len(close) < 61:
            continue
        returns = frame.set_index("date")["close"].pct_change().dropna()
        aligned = pd.concat([returns.rename("index"), benchmark_returns.rename("market")], axis=1).dropna().tail(60)
        beta = float(aligned.cov().iloc[0, 1] / aligned["market"].var()) if aligned["market"].var() > 0 else 1.0
        return_5d = float(close.iloc[-1] / close.iloc[-6] - 1.0)
        return_20d = float(close.iloc[-1] / close.iloc[-21] - 1.0)
        return_60d = float(close.iloc[-1] / close.iloc[-61] - 1.0)
        benchmark_close = benchmark["close"].dropna()
        benchmark_20d = float(benchmark_close.iloc[-1] / benchmark_close.iloc[-21] - 1.0)
        volatility_20d = float(returns.tail(20).std(ddof=0))
        volatility_60d = float(returns.tail(60).std(ddof=0))
        breadth_value = breadth.get(symbol, np.nan)
        raw = (
            0.25 * return_5d / max(volatility_20d, 1e-6)
            + 0.30 * return_20d / max(volatility_60d, 1e-6)
            + 0.20 * return_60d / max(volatility_60d * np.sqrt(3.0), 1e-6)
            + 0.15 * (return_20d - beta * benchmark_20d)
            + 0.10 * (2.0 * breadth_value - 1.0 if np.isfinite(breadth_value) else 0.0)
        )
        records.append(
            {
                "index_symbol": symbol,
                "feature_cutoff": cutoff.isoformat(),
                "return_5d": return_5d,
                "return_20d": return_20d,
                "return_60d": return_60d,
                "relative_strength_20d": return_20d - beta * benchmark_20d,
                "breadth_20d": breadth_value,
                "index_momentum_raw": raw,
            }
        )
    result = pd.DataFrame(records)
    if result.empty:
        return pd.DataFrame(columns=INDEX_FACTOR_COLUMNS)
    result["index_momentum_score"] = _percentile(result["index_momentum_raw"])
    return result


def compute_semantic_sentiment(
    events: pd.DataFrame,
    as_of: str | pd.Timestamp,
    half_life_hours: float = 24.0,
) -> pd.DataFrame:
    """Aggregate point-in-time semantic event probabilities into a decayed surprise factor."""
    required = {
        "symbol",
        "published_at",
        "p_positive",
        "p_negative",
        "relevance",
        "credibility",
        "novelty",
    }
    missing = required - set(events.columns)
    if missing:
        if events.empty:
            return pd.DataFrame(columns=SENTIMENT_FACTOR_COLUMNS)
        raise ValueError("Sentiment events are missing fields: " + ",".join(sorted(missing)))
    cutoff = pd.Timestamp(as_of)
    values = events.copy()
    values["published_at"] = pd.to_datetime(values["published_at"], errors="coerce")
    values = values[values["published_at"].notna() & values["published_at"].le(cutoff)].copy()
    if "observed_at" in values:
        values["observed_at"] = pd.to_datetime(values["observed_at"], errors="coerce")
        values = values[values["observed_at"].notna() & values["observed_at"].le(cutoff)].copy()
    if "content_hash" in values:
        values = values.sort_values("published_at").drop_duplicates(["symbol", "content_hash"], keep="last")
    for column in ("p_positive", "p_negative", "relevance", "credibility", "novelty"):
        values[column] = _number(values, column).clip(0.0, 1.0)
    age_hours = (cutoff - values["published_at"]).dt.total_seconds() / 3600.0
    values["decay"] = np.exp(-np.log(2.0) * age_hours / max(half_life_hours, 1e-6))
    values["weight"] = values["relevance"] * values["credibility"] * values["novelty"] * values["decay"]
    values["weighted_polarity"] = (values["p_positive"] - values["p_negative"]) * values["weight"]
    records = []
    for symbol, frame in values.groupby("symbol", sort=False):
        current = frame[frame["published_at"].gt(cutoff - pd.Timedelta(hours=24))]
        history = frame[frame["published_at"].le(cutoff - pd.Timedelta(hours=24))].copy()
        current_count = int(current["weight"].gt(0).sum())
        denominator = float(current["weight"].abs().sum())
        sentiment_24h = float(current["weighted_polarity"].sum() / denominator) if denominator > 0 else 0.0
        if history.empty:
            surprise = sentiment_24h
            volume_shock = float(current_count > 0)
        else:
            history["event_day"] = history["published_at"].dt.normalize()
            daily = history.groupby("event_day")["weighted_polarity"].sum().tail(20)
            std = float(daily.std(ddof=0))
            surprise = float((sentiment_24h - daily.mean()) / max(std, 0.10))
            daily_counts = history.groupby("event_day").size().tail(20)
            volume_shock = float(current_count / max(float(daily_counts.median()), 1.0) - 1.0)
        negative_risk = float((current["p_negative"] * current["weight"]).sum() / max(denominator, 1e-9))
        raw = 0.50 * sentiment_24h + 0.30 * np.tanh(surprise) + 0.20 * np.tanh(volume_shock) * np.sign(
            sentiment_24h
        )
        records.append(
            {
                "symbol": symbol,
                "feature_cutoff": cutoff.isoformat(),
                "sentiment_24h": sentiment_24h,
                "sentiment_surprise": surprise,
                "sentiment_volume_shock": volume_shock,
                "negative_event_risk": negative_risk,
                "semantic_sentiment_raw": raw,
                "event_count_24h": current_count,
            }
        )
    result = pd.DataFrame(records)
    if result.empty:
        return pd.DataFrame(columns=SENTIMENT_FACTOR_COLUMNS)
    result["semantic_sentiment_score"] = _percentile(result["semantic_sentiment_raw"])
    return result


@dataclass(frozen=True)
class IntervalLearningConfig:
    minimum_training_rows: int = 120
    transaction_cost: float = 0.002
    random_state: int = 42


class IntervalLearningModel:
    """Predict causal return intervals and explain the median forecast by feature occlusion."""

    def __init__(self, config: IntervalLearningConfig | None = None) -> None:
        self.config = config or IntervalLearningConfig()

    def fit_predict(
        self,
        training: pd.DataFrame,
        current: pd.DataFrame,
        feature_columns: list[str],
        cutoff: str | pd.Timestamp,
        target_column: str = "forward_excess_return_5d",
    ) -> pd.DataFrame:
        from sklearn.ensemble import GradientBoostingRegressor
        from sklearn.impute import SimpleImputer

        feature_cutoff = pd.Timestamp(cutoff)
        train = training.copy()
        train["feature_time"] = pd.to_datetime(train["feature_time"], errors="coerce")
        train["label_end"] = pd.to_datetime(train["label_end"], errors="coerce")
        train = train[train["feature_time"].le(feature_cutoff) & train["label_end"].le(feature_cutoff)].copy()
        missing_features = set(feature_columns) - set(train.columns)
        missing_current = ({"symbol"} | set(feature_columns)) - set(current.columns)
        if missing_features:
            raise ValueError("Training data is missing features: " + ",".join(sorted(missing_features)))
        if missing_current:
            raise ValueError("Current data is missing fields: " + ",".join(sorted(missing_current)))
        if target_column not in train:
            raise ValueError(f"Training data is missing target: {target_column}")
        x_train = train[feature_columns].apply(pd.to_numeric, errors="coerce")
        y_train = pd.to_numeric(train[target_column], errors="coerce")
        valid = y_train.notna()
        x_train = x_train.loc[valid]
        y_train = y_train.loc[valid]
        if len(y_train) < self.config.minimum_training_rows:
            raise ValueError(
                f"Only {len(y_train)} causally observable labeled rows are available; "
                f"{self.config.minimum_training_rows} are required"
            )
        imputer = SimpleImputer(strategy="median")
        train_values = imputer.fit_transform(x_train)
        current_values = imputer.transform(current[feature_columns].apply(pd.to_numeric, errors="coerce"))
        models = {}
        predictions = {}
        for name, alpha in (("q10", 0.10), ("q50", 0.50), ("q90", 0.90)):
            model = GradientBoostingRegressor(
                loss="quantile",
                alpha=alpha,
                n_estimators=120,
                max_depth=3,
                learning_rate=0.04,
                min_samples_leaf=15,
                random_state=self.config.random_state,
            )
            model.fit(train_values, y_train.to_numpy())
            models[name] = model
            predictions[name] = model.predict(current_values)
        q10 = np.minimum(predictions["q10"], predictions["q50"])
        q90 = np.maximum(predictions["q90"], predictions["q50"])
        uncertainty = np.maximum(q90 - q10, 1e-6)
        median_model = models["q50"]
        medians = np.nanmedian(train_values, axis=0)
        base_prediction = predictions["q50"]
        contributions: list[dict[str, float]] = []
        confidence = []
        for row_index, row in enumerate(current_values):
            row_contributions = {}
            for feature_index, feature in enumerate(feature_columns):
                occluded = row.copy()
                occluded[feature_index] = medians[feature_index]
                row_contributions[feature] = float(
                    base_prediction[row_index] - median_model.predict(occluded.reshape(1, -1))[0]
                )
            total = sum(abs(value) for value in row_contributions.values())
            concentration = max((abs(value) for value in row_contributions.values()), default=0.0) / max(total, 1e-9)
            confidence.append(float(np.clip(1.0 - concentration, 0.0, 1.0)))
            contributions.append(row_contributions)
        result = current[["symbol"]].copy().reset_index(drop=True)
        result["feature_cutoff"] = feature_cutoff.isoformat()
        result["q10"] = q10
        result["q50"] = predictions["q50"]
        result["q90"] = q90
        result["uncertainty"] = uncertainty
        result["expected_edge"] = predictions["q50"] - self.config.transaction_cost
        result["interval_learning_raw"] = result["expected_edge"] / uncertainty
        result["interval_learning_score"] = _percentile(result["interval_learning_raw"])
        result["attribution_confidence"] = confidence
        result["feature_attribution"] = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in contributions]
        return result


def compute_auction_linkage(
    snapshots: pd.DataFrame,
    auction_history: pd.DataFrame,
    sentiment: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute a true 09:25 auction factor from indicative quotes and unmatched orders."""
    required = {
        "symbol",
        "industry",
        "timestamp",
        "indicative_price",
        "prior_close",
        "matched_volume",
        "unmatched_bid",
        "unmatched_ask",
    }
    missing = required - set(snapshots.columns)
    if missing:
        raise ValueError("Auction snapshots are missing fields: " + ",".join(sorted(missing)))
    values = snapshots.copy()
    values["timestamp"] = pd.to_datetime(values["timestamp"], errors="coerce")
    values = values[values["timestamp"].dt.strftime("%H:%M").between("09:15", "09:25")]
    if values.empty:
        raise ValueError("No 09:15-09:25 auction snapshot is available")
    for column in required - {"symbol", "industry", "timestamp"}:
        values[column] = _number(values, column)
    history = auction_history.copy()
    if not {"symbol", "matched_volume"}.issubset(history.columns):
        raise ValueError("Auction history requires symbol and matched_volume")
    history["matched_volume"] = _number(history, "matched_volume")
    median_volume = history.groupby("symbol")["matched_volume"].median()
    records = []
    for symbol, frame in values.groupby("symbol", sort=False):
        frame = frame.sort_values("timestamp")
        final = frame.iloc[-1]
        final_clock = final["timestamp"].strftime("%H:%M")
        gap = float(final["indicative_price"] / final["prior_close"] - 1.0)
        typical_volume = float(median_volume.get(symbol, np.nan))
        volume_ratio = float(final["matched_volume"] / typical_volume) if typical_volume > 0 else np.nan
        denominator = float(final["unmatched_bid"] + final["unmatched_ask"])
        imbalance = float((final["unmatched_bid"] - final["unmatched_ask"]) / max(denominator, 1e-9))
        cancellable = frame[frame["timestamp"].dt.strftime("%H:%M").le("09:20")]
        peak_bid = float(cancellable["unmatched_bid"].max()) if not cancellable.empty else float(final["unmatched_bid"])
        cancel_risk = float(max(peak_bid - final["unmatched_bid"], 0.0) / max(peak_bid, 1e-9))
        records.append(
            {
                "symbol": symbol,
                "industry": final["industry"],
                "feature_cutoff": final["timestamp"].isoformat(),
                "auction_final_clock": final_clock,
                "auction_gap": gap,
                "auction_volume_ratio": volume_ratio,
                "auction_imbalance": imbalance,
                "auction_cancel_risk": cancel_risk,
            }
        )
    result = pd.DataFrame(records)
    industry_breadth = result.groupby("industry")["auction_gap"].transform(lambda values: float((values > 0).mean()))
    result["auction_sector_breadth"] = industry_breadth
    sentiment_map = {}
    if sentiment is not None and not sentiment.empty:
        sentiment_map = sentiment.set_index("symbol")["semantic_sentiment_score"].to_dict()
    alignment = [
        np.sign(gap) * (float(sentiment_map.get(symbol, 0.5)) - 0.5) * 2.0
        for symbol, gap in zip(result["symbol"], result["auction_gap"])
    ]
    gap_component = _percentile(result["auction_gap"].clip(-0.03, 0.03))
    volume_component = _percentile(np.log1p(result["auction_volume_ratio"].clip(lower=0)))
    imbalance_component = _percentile(result["auction_imbalance"])
    breadth_component = _percentile(result["auction_sector_breadth"])
    result["auction_linkage_raw"] = (
        0.30 * gap_component
        + 0.20 * volume_component
        + 0.20 * imbalance_component
        + 0.15 * breadth_component
        + 0.15 * pd.Series(alignment, index=result.index)
        - 0.20 * result["auction_cancel_risk"]
    )
    result["auction_linkage_score"] = _percentile(result["auction_linkage_raw"])
    result["auction_buy_ready"] = (
        result["auction_final_clock"].ge("09:25")
        & result["auction_gap"].between(0.0, 0.03)
        & result["auction_volume_ratio"].ge(1.5)
        & result["auction_sector_breadth"].ge(0.60)
        & result["auction_imbalance"].gt(0)
    )
    return result


@dataclass(frozen=True)
class FourFactorSelection:
    symbol: str
    name: str
    industry: str
    score: float
    index_momentum_score: float
    semantic_sentiment_score: float
    interval_learning_score: float
    auction_linkage_score: float | None
    q10: float
    q50: float
    q90: float
    reason: str


def rank_three_factor_watchlist(
    stocks: pd.DataFrame,
    index_factor: pd.DataFrame,
    sentiment_factor: pd.DataFrame,
    interval_factor: pd.DataFrame,
    limit: int = 20,
) -> pd.DataFrame:
    """Rank an evening watchlist without claiming that the next auction gate has passed."""
    values = stocks.merge(index_factor, on="index_symbol", how="left")
    values = values.merge(sentiment_factor, on="symbol", how="left", suffixes=("", "_sentiment"))
    values = values.merge(interval_factor, on="symbol", how="left", suffixes=("", "_interval"))
    gate = (
        values["buy_ready"].fillna(False)
        & values["index_momentum_score"].ge(0.60)
        & values["semantic_sentiment_score"].ge(0.45)
        & values["negative_event_risk"].lt(0.70)
        & values["expected_edge"].gt(0.01)
        & values["q10"].gt(-0.05)
        & values["attribution_confidence"].ge(0.35)
    )
    values["pre_auction_score"] = (
        values["index_momentum_score"] * 0.30
        + values["semantic_sentiment_score"] * 0.30
        + values["interval_learning_score"] * 0.40
    )
    ranked = values[gate].sort_values(
        ["pre_auction_score", "average_amount_20d", "symbol"],
        ascending=[False, False, True],
    )
    ranked = ranked.head(max(int(limit), 0)).copy()
    ranked["auction_status"] = "pending_09:25_confirmation"
    ranked["auto_buy_ready"] = False
    return ranked.reset_index(drop=True)


def select_four_factor_portfolio(
    stocks: pd.DataFrame,
    index_factor: pd.DataFrame,
    sentiment_factor: pd.DataFrame,
    interval_factor: pd.DataFrame,
    auction_factor: pd.DataFrame | None = None,
    max_positions: int = 5,
    max_per_industry: int = 2,
) -> list[FourFactorSelection]:
    """Select up to five liquid stocks; missing true auction data keeps them in observation only."""
    values = stocks.merge(index_factor, on="index_symbol", how="left")
    values = values.merge(sentiment_factor, on="symbol", how="left", suffixes=("", "_sentiment"))
    values = values.merge(interval_factor, on="symbol", how="left", suffixes=("", "_interval"))
    has_auction = auction_factor is not None and not auction_factor.empty
    if has_auction:
        values = values.merge(auction_factor, on="symbol", how="left", suffixes=("", "_auction"))
    else:
        values["auction_linkage_score"] = np.nan
        values["auction_buy_ready"] = False
    base_gate = (
        values["buy_ready"].fillna(False)
        & values["index_momentum_score"].ge(0.60)
        & values["semantic_sentiment_score"].ge(0.45)
        & values["negative_event_risk"].lt(0.70)
        & values["expected_edge"].gt(0.01)
        & values["q10"].gt(-0.05)
        & values["attribution_confidence"].ge(0.35)
    )
    if has_auction:
        base_gate &= values["auction_buy_ready"].fillna(False)
        values["four_factor_score"] = (
            values["index_momentum_score"] * 0.25
            + values["semantic_sentiment_score"] * 0.20
            + values["interval_learning_score"] * 0.35
            + values["auction_linkage_score"] * 0.20
        )
    else:
        values["four_factor_score"] = (
            values["index_momentum_score"] * 0.30
            + values["semantic_sentiment_score"] * 0.30
            + values["interval_learning_score"] * 0.40
        )
        base_gate &= False
    ranked = values[base_gate].sort_values(
        ["four_factor_score", "average_amount_20d", "symbol"],
        ascending=[False, False, True],
    )
    selected = []
    industry_counts: dict[str, int] = {}
    for row in ranked.itertuples(index=False):
        if industry_counts.get(row.industry, 0) >= max_per_industry:
            continue
        reason = (
            f"指数动量{row.index_momentum_score:.0%}、语义情绪{row.semantic_sentiment_score:.0%}、"
            f"区间学习{row.interval_learning_score:.0%}、竞价联动{row.auction_linkage_score:.0%}；"
            f"未来5日区间[{row.q10:.1%},{row.q90:.1%}]，中位{row.q50:.1%}。"
        )
        selected.append(
            FourFactorSelection(
                symbol=row.symbol,
                name=row.name,
                industry=row.industry,
                score=float(row.four_factor_score),
                index_momentum_score=float(row.index_momentum_score),
                semantic_sentiment_score=float(row.semantic_sentiment_score),
                interval_learning_score=float(row.interval_learning_score),
                auction_linkage_score=float(row.auction_linkage_score),
                q10=float(row.q10),
                q50=float(row.q50),
                q90=float(row.q90),
                reason=reason,
            )
        )
        industry_counts[row.industry] = industry_counts.get(row.industry, 0) + 1
        if len(selected) == max_positions:
            break
    return selected
