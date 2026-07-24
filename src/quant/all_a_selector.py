"""Causal monthly all-A chip double-peak portfolio selection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .config import QuantConfig
from .strategy import _select_double_peak


@dataclass(frozen=True)
class FactorWeights:
    """Cross-sectional weights selected only from the prior research window."""

    lower_peak_proximity: float = 0.40
    momentum_20d: float = 0.20
    low_volatility_20d: float = 0.20
    liquidity_20d: float = 0.20
    momentum_60d_skip_5d: float = 0.0
    reversal_5d: float = 0.0
    low_downside_volatility_20d: float = 0.0
    low_max_drawdown_20d: float = 0.0
    amount_stability_20d: float = 0.0

    def validate(self) -> None:
        values = asdict(self)
        if any(value < 0 for value in values.values()):
            raise ValueError("Factor weights must not be negative")
        if not np.isclose(sum(values.values()), 1.0):
            raise ValueError("Factor weights must sum to one")


@dataclass(frozen=True)
class AllACandidate:
    symbol: str
    name: str
    industry: str
    selection_date: str
    open_price: float
    lower_peak: Optional[float]
    upper_peak: Optional[float]
    valley: Optional[float]
    chip_position: Optional[float]
    distance_to_lower_pct: Optional[float]
    momentum_20d: Optional[float]
    volatility_20d: Optional[float]
    average_amount_20d: float
    score: Optional[float]
    buy_ready: bool
    eligible: bool
    reason: str
    momentum_60d_skip_5d: Optional[float] = None
    return_5d: Optional[float] = None
    downside_volatility_20d: Optional[float] = None
    max_drawdown_20d: Optional[float] = None
    amount_cv_20d: Optional[float] = None
    strategy_name: str = "chip_double_peak"
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    roe_dt: Optional[float] = None
    roa: Optional[float] = None
    grossprofit_margin: Optional[float] = None
    ocf_to_or: Optional[float] = None
    debt_to_assets: Optional[float] = None
    q_profit_yoy: Optional[float] = None
    momentum_120d_skip_5d: Optional[float] = None
    industry_momentum_60d: Optional[float] = None
    industry_positive_breadth: Optional[float] = None
    main_flow_ratio: Optional[float] = None


@dataclass(frozen=True)
class MonthlyPortfolioSelection:
    selection_date: str
    feature_cutoff: str
    candidates: list[AllACandidate]
    selected: list[AllACandidate]


def _number(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _active_universe(config: QuantConfig, universe: pd.DataFrame, selection_date: pd.Timestamp) -> pd.DataFrame:
    values = universe.copy()
    values["ts_code"] = values["ts_code"].astype(str)
    values["name"] = values["name"].fillna("").astype(str)
    if "industry" not in values:
        values["industry"] = ""
    values["industry"] = values["industry"].fillna("").astype(str)
    values["list_date"] = pd.to_datetime(values["list_date"].astype(str), format="%Y%m%d", errors="coerce")
    minimum_list_date = selection_date - pd.Timedelta(days=config.universe.min_listing_days)
    values = values[values["list_date"].le(minimum_list_date)]
    values = values[~values["ts_code"].isin(config.universe.excluded_symbols)]
    if config.universe.exclude_st:
        values = values[~values["name"].str.contains(r"^\*?ST(?![A-Z])|退", case=False, regex=True)]
    return values.drop_duplicates("ts_code", keep="last")


def _candidate(
    config: QuantConfig,
    symbol: str,
    metadata: pd.Series,
    history: pd.DataFrame,
    signal: pd.Series,
    limits: pd.Series | None,
    selection_date: str,
) -> AllACandidate:
    common = {
        "symbol": symbol,
        "name": str(metadata["name"]),
        "industry": str(metadata.get("industry", "")),
        "selection_date": selection_date,
        "open_price": float(signal["open"]),
    }
    if len(history) < max(config.strategy.chip_min_history_days, config.strategy.chip_bins):
        return AllACandidate(
            lower_peak=None,
            upper_peak=None,
            valley=None,
            chip_position=None,
            distance_to_lower_pct=None,
            momentum_20d=None,
            volatility_20d=None,
            average_amount_20d=0.0,
            score=None,
            buy_ready=False,
            eligible=False,
            reason="insufficient_history",
            **common,
        )
    average_amount = float(_number(history.tail(20), "amount_yuan").mean())
    if not np.isfinite(average_amount) or average_amount < config.universe.min_average_amount:
        return AllACandidate(
            lower_peak=None,
            upper_peak=None,
            valley=None,
            chip_position=None,
            distance_to_lower_pct=None,
            momentum_20d=None,
            volatility_20d=None,
            average_amount_20d=max(average_amount, 0.0) if np.isfinite(average_amount) else 0.0,
            score=None,
            buy_ready=False,
            eligible=False,
            reason="below_liquidity_floor",
            **common,
        )
    chip_history = history.tail(config.strategy.chip_lookback_days)
    typical = (
        (_number(chip_history, "high") + _number(chip_history, "low") + _number(chip_history, "close")) / 3.0
    ).to_numpy()
    volumes = _number(chip_history, "volume").fillna(0.0).to_numpy()
    peaks = _select_double_peak(typical, volumes, config.strategy)
    if peaks is None:
        return AllACandidate(
            lower_peak=None,
            upper_peak=None,
            valley=None,
            chip_position=None,
            distance_to_lower_pct=None,
            momentum_20d=None,
            volatility_20d=None,
            average_amount_20d=average_amount,
            score=None,
            buy_ready=False,
            eligible=False,
            reason="double_peak_not_confirmed",
            **common,
        )
    lower, upper, valley = peaks
    open_price = float(signal["open"])
    position = (open_price - lower) / (upper - lower)
    distance = open_price / lower - 1.0
    close_history = _number(history.tail(21), "close").dropna()
    returns = close_history.pct_change().dropna()
    momentum = float(close_history.iloc[-1] / close_history.iloc[0] - 1.0) if len(close_history) >= 2 else np.nan
    volatility = float(returns.std(ddof=0)) if len(returns) else np.nan
    extended_close = _number(history.tail(61), "close").dropna()
    momentum_60d_skip_5d = (
        float(extended_close.iloc[-6] / extended_close.iloc[0] - 1.0) if len(extended_close) >= 61 else np.nan
    )
    return_5d = float(extended_close.iloc[-1] / extended_close.iloc[-6] - 1.0) if len(extended_close) >= 6 else np.nan
    downside = returns[returns < 0]
    downside_volatility = float(downside.std(ddof=0)) if len(downside) else 0.0
    drawdown = close_history / close_history.cummax() - 1.0
    max_drawdown = abs(float(drawdown.min())) if len(drawdown) else np.nan
    amount = _number(history.tail(20), "amount_yuan").dropna()
    amount_mean = float(amount.mean()) if len(amount) else np.nan
    amount_cv = np.nan
    if len(amount) and np.isfinite(amount_mean) and amount_mean > 0:
        amount_cv = float(amount.std(ddof=0) / amount_mean)
    up_limit = float(limits["up_limit"]) if limits is not None and pd.notna(limits.get("up_limit")) else None
    tolerance = max(open_price * 1e-6, 1e-6)
    tradable = open_price > 0 and float(signal.get("volume", 0.0)) > 0
    not_limit_up = up_limit is None or open_price < up_limit - tolerance
    between_peaks = 0.0 <= position <= 1.0
    near_lower_peak = position <= config.strategy.chip_low_entry_position
    buy_ready = tradable and not_limit_up and between_peaks and near_lower_peak
    if not tradable:
        reason = "suspended_or_empty_session"
    elif not not_limit_up:
        reason = "locked_at_upper_limit"
    elif not between_peaks:
        reason = "price_outside_peaks"
    elif not near_lower_peak:
        reason = "above_selection_buy_zone"
    else:
        reason = "eligible"
    return AllACandidate(
        lower_peak=lower,
        upper_peak=upper,
        valley=valley,
        chip_position=position,
        distance_to_lower_pct=distance,
        momentum_20d=momentum,
        volatility_20d=volatility,
        average_amount_20d=average_amount,
        score=None,
        buy_ready=buy_ready,
        eligible=buy_ready,
        reason=reason,
        momentum_60d_skip_5d=momentum_60d_skip_5d,
        return_5d=return_5d,
        downside_volatility_20d=downside_volatility,
        max_drawdown_20d=max_drawdown,
        amount_cv_20d=amount_cv,
        **common,
    )


def _rank_candidates(candidates: list[AllACandidate], weights: FactorWeights) -> list[AllACandidate]:
    eligible = [candidate for candidate in candidates if candidate.eligible]
    if not eligible:
        return []
    frame = pd.DataFrame([asdict(candidate) for candidate in eligible])
    frame["proximity_rank"] = 1.0 - frame["chip_position"].rank(pct=True, ascending=True)
    frame["momentum_rank"] = frame["momentum_20d"].rank(pct=True, ascending=True)
    frame["volatility_rank"] = 1.0 - frame["volatility_20d"].rank(pct=True, ascending=True)
    frame["liquidity_rank"] = frame["average_amount_20d"].rank(pct=True, ascending=True)
    frame["momentum_60d_skip_5d_rank"] = frame["momentum_60d_skip_5d"].rank(pct=True, ascending=True)
    frame["reversal_5d_rank"] = 1.0 - frame["return_5d"].rank(pct=True, ascending=True)
    frame["downside_volatility_rank"] = 1.0 - frame["downside_volatility_20d"].rank(
        pct=True,
        ascending=True,
    )
    frame["max_drawdown_rank"] = 1.0 - frame["max_drawdown_20d"].rank(pct=True, ascending=True)
    frame["amount_stability_rank"] = 1.0 - frame["amount_cv_20d"].rank(pct=True, ascending=True)
    rank_columns = [column for column in frame if column.endswith("_rank")]
    frame[rank_columns] = frame[rank_columns].fillna(0.5)
    frame["score"] = (
        frame["proximity_rank"] * weights.lower_peak_proximity
        + frame["momentum_rank"] * weights.momentum_20d
        + frame["volatility_rank"] * weights.low_volatility_20d
        + frame["liquidity_rank"] * weights.liquidity_20d
        + frame["momentum_60d_skip_5d_rank"] * weights.momentum_60d_skip_5d
        + frame["reversal_5d_rank"] * weights.reversal_5d
        + frame["downside_volatility_rank"] * weights.low_downside_volatility_20d
        + frame["max_drawdown_rank"] * weights.low_max_drawdown_20d
        + frame["amount_stability_rank"] * weights.amount_stability_20d
    )
    ranked = frame.sort_values(
        ["score", "chip_position", "average_amount_20d", "symbol"],
        ascending=[False, True, False, True],
    )
    candidate_columns = list(asdict(eligible[0]))
    return [AllACandidate(**record) for record in ranked[candidate_columns].to_dict("records")]


def select_monthly_portfolio(
    config: QuantConfig,
    bars: pd.DataFrame,
    limits: pd.DataFrame,
    universe: pd.DataFrame,
    selection_date: str,
    weights: FactorWeights | None = None,
) -> MonthlyPortfolioSelection:
    """Select exactly the configured number of buy-ready stocks without future observations."""
    if config.universe.scope != "all_a":
        raise ValueError("Monthly all-A selection requires universe.scope=all_a")
    factor_weights = weights or FactorWeights()
    factor_weights.validate()
    selection_key = selection_date.replace("-", "")
    prices = bars.copy()
    prices["trade_date"] = prices["trade_date"].astype(str)
    signals = prices[prices["trade_date"] == selection_key].set_index("ts_code")
    history = prices[prices["trade_date"] < selection_key].sort_values(["ts_code", "trade_date"])
    if signals.empty or history.empty:
        raise ValueError(f"Selection bars or prior history are unavailable for {selection_key}")
    limit_frame = limits.copy()
    limit_frame["trade_date"] = limit_frame["trade_date"].astype(str)
    signal_limits = limit_frame[limit_frame["trade_date"] == selection_key].set_index("ts_code")
    selection_timestamp = pd.Timestamp(selection_date)
    active = _active_universe(config, universe, selection_timestamp).set_index("ts_code")
    history_by_symbol = {
        symbol: frame.tail(max(config.strategy.chip_lookback_days, 61))
        for symbol, frame in history.groupby("ts_code", sort=False)
    }
    candidates = []
    for symbol, metadata in active.iterrows():
        if symbol not in signals.index:
            continue
        limit_row = signal_limits.loc[symbol] if symbol in signal_limits.index else None
        candidates.append(
            _candidate(
                config,
                symbol,
                metadata,
                history_by_symbol.get(symbol, pd.DataFrame()),
                signals.loc[symbol],
                limit_row,
                selection_key,
            )
        )
    ranked = _rank_candidates(candidates, factor_weights)
    required = config.portfolio.max_positions
    if len(ranked) < required:
        raise ValueError(f"Only {len(ranked)} all-A stocks are buy-ready; {required} are required")
    feature_cutoff = history["trade_date"].max()
    return MonthlyPortfolioSelection(
        selection_date=selection_key,
        feature_cutoff=str(feature_cutoff),
        candidates=candidates,
        selected=ranked[:required],
    )
