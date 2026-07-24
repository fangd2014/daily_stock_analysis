"""Three preregistered all-A stock selectors independent of chip peaks."""

from __future__ import annotations

from dataclasses import asdict, replace

import numpy as np
import pandas as pd

from .all_a_selector import AllACandidate, MonthlyPortfolioSelection, _active_universe
from .config import QuantConfig


STRATEGY_NAMES = ("quality_value", "industry_leader_momentum", "flow_confirmed_reversal")
_CANDIDATE_FIELDS = set(AllACandidate.__dataclass_fields__)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _candidate_template(
    strategy_name: str,
    symbol: str,
    metadata: pd.Series,
    selection_date: str,
    open_price: float,
    average_amount: float,
) -> dict:
    return {
        "symbol": symbol,
        "name": str(metadata["name"]),
        "industry": str(metadata.get("industry", "")),
        "selection_date": selection_date,
        "open_price": open_price,
        "lower_peak": None,
        "upper_peak": None,
        "valley": None,
        "chip_position": None,
        "distance_to_lower_pct": None,
        "momentum_20d": None,
        "volatility_20d": None,
        "average_amount_20d": average_amount,
        "score": None,
        "buy_ready": False,
        "eligible": False,
        "reason": "not_evaluated",
        "strategy_name": strategy_name,
    }


def _common_buy_ready(signal: pd.Series, limit_row: pd.Series | None) -> tuple[bool, str]:
    open_price = float(signal["open"])
    volume = float(signal.get("volume", 0.0))
    up_limit = None
    if limit_row is not None and pd.notna(limit_row.get("up_limit")):
        up_limit = float(limit_row["up_limit"])
    tolerance = max(open_price * 1e-6, 1e-6)
    if open_price <= 0 or volume <= 0:
        return False, "suspended_or_empty_session"
    if up_limit is not None and open_price >= up_limit - tolerance:
        return False, "locked_at_upper_limit"
    return True, "eligible"


def _price_features(history: pd.DataFrame) -> dict[str, float]:
    close = _numeric(history, "close").dropna()
    returns20 = close.tail(21).pct_change().dropna()
    downside = returns20[returns20 < 0]
    amount = _numeric(history.tail(20), "amount_yuan").dropna()
    amount_mean = float(amount.mean()) if len(amount) else np.nan
    amount_cv = float(amount.std(ddof=0) / amount_mean) if len(amount) and amount_mean > 0 else np.nan
    momentum20 = float(close.iloc[-1] / close.iloc[-21] - 1.0) if len(close) >= 21 else np.nan
    return5 = float(close.iloc[-1] / close.iloc[-6] - 1.0) if len(close) >= 6 else np.nan
    momentum60 = float(close.iloc[-1] / close.iloc[-61] - 1.0) if len(close) >= 61 else np.nan
    momentum120_skip5 = float(close.iloc[-6] / close.iloc[-121] - 1.0) if len(close) >= 121 else np.nan
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else np.nan
    drawdown = close.tail(60) / close.tail(60).cummax() - 1.0
    return {
        "momentum_20d": momentum20,
        "return_5d": return5,
        "momentum_60d_skip_5d": momentum60,
        "momentum_120d_skip_5d": momentum120_skip5,
        "volatility_20d": float(returns20.std(ddof=0)) if len(returns20) else np.nan,
        "downside_volatility_20d": float(downside.std(ddof=0)) if len(downside) else 0.0,
        "max_drawdown_20d": abs(float(drawdown.min())) if len(drawdown) else np.nan,
        "amount_cv_20d": amount_cv,
        "last_close": float(close.iloc[-1]) if len(close) else np.nan,
        "ma60": ma60,
    }


def _rank(frame: pd.DataFrame, strategy_name: str) -> pd.DataFrame:
    values = frame.copy()
    if strategy_name == "quality_value":
        values["score"] = (
            (1.0 - values["pe_ttm"].rank(pct=True)) * 0.20
            + (1.0 - values["pb"].rank(pct=True)) * 0.15
            + values["roe_dt"].rank(pct=True) * 0.20
            + values["roa"].rank(pct=True) * 0.10
            + values["grossprofit_margin"].rank(pct=True) * 0.10
            + values["ocf_to_or"].rank(pct=True) * 0.10
            + (1.0 - values["debt_to_assets"].rank(pct=True)) * 0.10
            + values["q_profit_yoy"].rank(pct=True) * 0.05
        )
    elif strategy_name == "industry_leader_momentum":
        values["score"] = (
            values["momentum_120d_skip_5d"].rank(pct=True) * 0.35
            + values["momentum_20d"].rank(pct=True) * 0.15
            + values["industry_momentum_60d"].rank(pct=True) * 0.20
            + values["industry_positive_breadth"].rank(pct=True) * 0.10
            + (1.0 - values["downside_volatility_20d"].rank(pct=True)) * 0.10
            + values["average_amount_20d"].rank(pct=True) * 0.10
        )
    else:
        values["score"] = (
            values["main_flow_ratio"].rank(pct=True) * 0.45
            + (1.0 - values["return_5d"].rank(pct=True)) * 0.25
            + (1.0 - values["downside_volatility_20d"].rank(pct=True)) * 0.10
            + (1.0 - values["amount_cv_20d"].rank(pct=True)) * 0.10
            + values["average_amount_20d"].rank(pct=True) * 0.10
        )
    values["score"] = values["score"].fillna(0.0)
    return values.sort_values(
        ["score", "average_amount_20d", "symbol"],
        ascending=[False, False, True],
    )


def select_strategy_portfolio(
    config: QuantConfig,
    strategy_name: str,
    bars: pd.DataFrame,
    limits: pd.DataFrame,
    universe: pd.DataFrame,
    selection_date: str,
    factor_cutoff: str,
    daily_basic: pd.DataFrame,
    financials: pd.DataFrame,
    moneyflow: pd.DataFrame,
) -> MonthlyPortfolioSelection:
    """Select five buy-ready stocks using one preregistered non-chip strategy."""
    if strategy_name not in STRATEGY_NAMES:
        raise ValueError(f"Unsupported stock-selection strategy: {strategy_name}")
    selection_key = selection_date.replace("-", "")
    cutoff_key = factor_cutoff.replace("-", "")
    prices = bars.copy()
    prices["trade_date"] = prices["trade_date"].astype(str)
    history = prices[prices["trade_date"].lt(selection_key)].sort_values(["ts_code", "trade_date"])
    signals = prices[prices["trade_date"].eq(selection_key)].set_index("ts_code")
    if history.empty or signals.empty:
        raise ValueError(f"Selection bars or history are unavailable for {selection_key}")
    feature_cutoff = str(history["trade_date"].max())
    if cutoff_key > feature_cutoff:
        raise ValueError("Factor snapshot cutoff is later than the price feature cutoff")
    active = _active_universe(config, universe, pd.Timestamp(selection_date)).set_index("ts_code")
    limit_frame = limits.copy()
    limit_frame["trade_date"] = limit_frame["trade_date"].astype(str)
    signal_limits = limit_frame[limit_frame["trade_date"].eq(selection_key)].set_index("ts_code")
    basic = daily_basic.drop_duplicates("ts_code", keep="last").set_index("ts_code")
    finance = financials.drop_duplicates("ts_code", keep="last").set_index("ts_code")
    flow = moneyflow.drop_duplicates("ts_code", keep="last").set_index("ts_code")
    history_by_symbol = {symbol: frame.tail(121) for symbol, frame in history.groupby("ts_code", sort=False)}
    records = []
    for symbol, metadata in active.iterrows():
        if symbol not in signals.index:
            continue
        stock_history = history_by_symbol.get(symbol, pd.DataFrame())
        average_amount = float(_numeric(stock_history.tail(20), "amount_yuan").mean())
        template = _candidate_template(
            strategy_name,
            symbol,
            metadata,
            selection_key,
            float(signals.loc[symbol, "open"]),
            average_amount if np.isfinite(average_amount) else 0.0,
        )
        if len(stock_history) < 20 or average_amount < config.universe.min_average_amount:
            records.append(asdict(AllACandidate(**{**template, "reason": "below_history_or_liquidity_floor"})))
            continue
        features = _price_features(stock_history)
        limit_row = signal_limits.loc[symbol] if symbol in signal_limits.index else None
        buy_ready, reason = _common_buy_ready(signals.loc[symbol], limit_row)
        values = {
            **template,
            **{key: value for key, value in features.items() if key in _CANDIDATE_FIELDS},
        }
        values.update(
            {
                "momentum_20d": features["momentum_20d"],
                "return_5d": features["return_5d"],
                "momentum_60d_skip_5d": features["momentum_60d_skip_5d"],
                "momentum_120d_skip_5d": features["momentum_120d_skip_5d"],
                "volatility_20d": features["volatility_20d"],
                "downside_volatility_20d": features["downside_volatility_20d"],
                "max_drawdown_20d": features["max_drawdown_20d"],
                "amount_cv_20d": features["amount_cv_20d"],
            }
        )
        eligible = buy_ready
        if strategy_name == "quality_value":
            if symbol not in basic.index or symbol not in finance.index:
                eligible, reason = False, "point_in_time_fundamentals_missing"
            else:
                basic_row = basic.loc[symbol]
                finance_row = finance.loc[symbol]
                for column in ("pe_ttm", "pb"):
                    values[column] = float(basic_row[column]) if pd.notna(basic_row.get(column)) else np.nan
                for column in (
                    "roe_dt",
                    "roa",
                    "grossprofit_margin",
                    "ocf_to_or",
                    "debt_to_assets",
                    "q_profit_yoy",
                ):
                    values[column] = float(finance_row[column]) if pd.notna(finance_row.get(column)) else np.nan
                required = [values[column] for column in ("pe_ttm", "pb", "roe_dt", "roa", "debt_to_assets")]
                eligible = eligible and all(np.isfinite(value) for value in required)
                eligible = eligible and values["pe_ttm"] > 0 and values["pb"] > 0 and values["roe_dt"] > 0
                if not eligible and buy_ready:
                    reason = "quality_value_gate_failed"
        elif strategy_name == "industry_leader_momentum":
            eligible = eligible and len(stock_history) >= 121
        else:
            if symbol not in flow.index:
                eligible, reason = False, "moneyflow_missing"
            else:
                flow_row = flow.loc[symbol]
                net_main = sum(
                    float(flow_row.get(column, 0.0) or 0.0)
                    for column in ("buy_lg_amount", "buy_elg_amount")
                ) - sum(
                    float(flow_row.get(column, 0.0) or 0.0)
                    for column in ("sell_lg_amount", "sell_elg_amount")
                )
                cutoff_amount = float(_numeric(stock_history.tail(1), "amount_yuan").iloc[-1]) / 10_000.0
                values["main_flow_ratio"] = net_main / cutoff_amount if cutoff_amount > 0 else np.nan
                eligible = (
                    eligible
                    and np.isfinite(values["main_flow_ratio"])
                    and values["main_flow_ratio"] > 0
                    and features["return_5d"] < 0
                    and features["momentum_60d_skip_5d"] > 0
                    and features["last_close"] > features["ma60"]
                )
                if not eligible and buy_ready:
                    reason = "flow_reversal_gate_failed"
        values["buy_ready"] = bool(eligible)
        values["eligible"] = bool(eligible)
        values["reason"] = "eligible" if eligible else reason
        records.append(asdict(AllACandidate(**values)))
    if not records:
        raise ValueError(f"No active stocks are available for {strategy_name} on {selection_key}")
    frame = pd.DataFrame(records)
    if strategy_name == "industry_leader_momentum" and not frame.empty:
        eligible_price = frame[frame["eligible"]].copy()
        industry_stats = eligible_price.groupby("industry")["momentum_60d_skip_5d"].agg(
            industry_momentum_60d="median",
            industry_positive_breadth=lambda values: float((values > 0).mean()),
        )
        frame = frame.merge(industry_stats, left_on="industry", right_index=True, how="left", suffixes=("", "_new"))
        for column in ("industry_momentum_60d", "industry_positive_breadth"):
            replacement = f"{column}_new"
            if replacement in frame:
                frame[column] = frame[replacement]
                frame = frame.drop(columns=replacement)
        trend_gate = (
            frame["momentum_120d_skip_5d"].gt(0)
            & frame["momentum_60d_skip_5d"].gt(0)
            & frame["industry_momentum_60d"].gt(0)
            & frame["industry_positive_breadth"].ge(0.5)
        )
        frame["eligible"] = frame["eligible"] & trend_gate
        frame["buy_ready"] = frame["eligible"]
        frame.loc[~frame["eligible"] & frame["reason"].eq("eligible"), "reason"] = "industry_trend_gate_failed"
    eligible = frame[frame["eligible"]].copy()
    required = config.portfolio.max_positions
    if len(eligible) < required:
        raise ValueError(f"Only {len(eligible)} {strategy_name} stocks are buy-ready; {required} are required")
    ranked = _rank(eligible, strategy_name)
    candidate_columns = list(asdict(AllACandidate(**records[0])))
    selected = [AllACandidate(**record) for record in ranked[candidate_columns].head(required).to_dict("records")]
    candidates = [AllACandidate(**record) for record in frame[candidate_columns].to_dict("records")]
    return MonthlyPortfolioSelection(
        selection_date=selection_key,
        feature_cutoff=feature_cutoff,
        candidates=candidates,
        selected=selected,
    )
