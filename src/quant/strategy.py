"""VWAP mean-reversion strategy with compliant rolling T+0 inventory use."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time as clock_time
from typing import Any, Optional

import numpy as np
import pandas as pd

from .broker import Fill, Order, PortfolioLedger
from .config import StrategyConfig


def _parse_clock(value: str) -> clock_time:
    return pd.Timestamp(value).time()


def prepare_features(
    bars: pd.DataFrame,
    strategy: StrategyConfig,
    adjustments: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Build causal daily regime and intraday VWAP features."""
    frame = bars.copy().sort_values("datetime").reset_index(drop=True)
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    frame["trade_date"] = frame["datetime"].dt.normalize()
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    effective_volume = frame["volume"].clip(lower=0.0)
    numerator = (typical * effective_volume).groupby(frame["trade_date"]).cumsum()
    denominator = effective_volume.groupby(frame["trade_date"]).cumsum().replace(0.0, np.nan)
    frame["vwap"] = (numerator / denominator).fillna(frame["close"])
    frame["vwap_residual"] = frame["close"] / frame["vwap"] - 1.0

    grouped_residual = frame.groupby("trade_date", group_keys=False)["vwap_residual"]
    rolling_mean = grouped_residual.transform(
        lambda values: values.rolling(strategy.zscore_window, min_periods=strategy.zscore_window).mean()
    )
    rolling_std = grouped_residual.transform(
        lambda values: values.rolling(strategy.zscore_window, min_periods=strategy.zscore_window).std(ddof=0)
    )
    frame["zscore"] = ((frame["vwap_residual"] - rolling_mean) / rolling_std.replace(0.0, np.nan)).replace(
        [np.inf, -np.inf], np.nan
    )
    frame["vwap_drift"] = frame.groupby("trade_date")["vwap"].pct_change(6).fillna(0.0)

    daily = frame.groupby("trade_date", as_index=False).agg(
        close=("close", "last"),
        high=("high", "max"),
        low=("low", "min"),
    )
    if adjustments is not None and not adjustments.empty:
        factors = adjustments[["trade_date", "adj_factor"]].copy()
        factors["trade_date"] = pd.to_datetime(factors["trade_date"]).dt.normalize()
        daily = daily.merge(factors, on="trade_date", how="left")
        daily["adj_factor"] = daily["adj_factor"].ffill().bfill().fillna(1.0)
    else:
        daily["adj_factor"] = 1.0
    daily["adjusted_close"] = daily["close"] * daily["adj_factor"]
    daily["daily_return"] = daily["adjusted_close"].pct_change()
    daily["daily_trend"] = (
        daily["adjusted_close"].rolling(5, min_periods=5).mean()
        / daily["adjusted_close"].rolling(20, min_periods=20).mean()
        - 1.0
    )
    daily["daily_volatility"] = daily["daily_return"].rolling(20, min_periods=10).std(ddof=0)
    daily["regime_allowed"] = daily["daily_trend"].abs().le(strategy.daily_trend_limit) & daily["daily_volatility"].le(
        strategy.daily_volatility_limit
    )
    for column in ("daily_trend", "daily_volatility", "regime_allowed"):
        daily[f"prior_{column}"] = daily[column].shift(1)
    frame = frame.merge(
        daily[["trade_date", "prior_daily_trend", "prior_daily_volatility", "prior_regime_allowed"]],
        on="trade_date",
        how="left",
    )
    frame["prior_regime_allowed"] = frame["prior_regime_allowed"].astype("boolean").fillna(False).astype(bool)
    if strategy.strategy_type == "chip_double_peak":
        frame = _add_chip_double_peak_features(frame, strategy)
    return frame


def _select_double_peak(
    prices: np.ndarray,
    volumes: np.ndarray,
    strategy: StrategyConfig,
) -> Optional[tuple[float, float, float]]:
    """Return the strongest separated volume-profile peaks and their valley."""
    valid = np.isfinite(prices) & np.isfinite(volumes) & (prices > 0) & (volumes > 0)
    prices = prices[valid]
    volumes = volumes[valid]
    if len(prices) < strategy.chip_bins or float(prices.max()) <= float(prices.min()):
        return None
    profile, edges = np.histogram(
        prices,
        bins=strategy.chip_bins,
        range=(float(prices.min()), float(prices.max())),
        weights=volumes,
    )
    smoothed = np.convolve(profile.astype(float), np.array([0.25, 0.50, 0.25]), mode="same")
    if not np.isfinite(smoothed).all() or float(smoothed.max()) <= 0:
        return None
    centers = (edges[:-1] + edges[1:]) / 2.0
    minimum_height = float(smoothed.max()) * strategy.chip_min_peak_height_ratio
    candidates: list[int] = []
    for index, height in enumerate(smoothed):
        left = smoothed[index - 1] if index > 0 else -np.inf
        right = smoothed[index + 1] if index < len(smoothed) - 1 else -np.inf
        if height >= minimum_height and height >= left and height >= right and (height > left or height > right):
            candidates.append(index)

    selected: Optional[tuple[float, float, float]] = None
    best_score = -np.inf
    for lower_index, upper_index in ((left, right) for left in candidates for right in candidates if left < right):
        lower_price = float(centers[lower_index])
        upper_price = float(centers[upper_index])
        separation = upper_price / lower_price - 1.0
        if separation < strategy.chip_min_peak_separation_pct or upper_index - lower_index < 2:
            continue
        valley_slice = smoothed[lower_index + 1 : upper_index]
        valley_offset = int(np.argmin(valley_slice))
        valley_index = lower_index + 1 + valley_offset
        weaker_peak = min(float(smoothed[lower_index]), float(smoothed[upper_index]))
        valley_ratio = float(smoothed[valley_index]) / weaker_peak if weaker_peak > 0 else 1.0
        if valley_ratio > strategy.chip_max_valley_ratio:
            continue
        score = weaker_peak * (1.0 - valley_ratio) * (1.0 + separation)
        if score > best_score:
            best_score = score
            selected = lower_price, upper_price, float(centers[valley_index])
    return selected


def _add_chip_double_peak_features(frame: pd.DataFrame, strategy: StrategyConfig) -> pd.DataFrame:
    """Attach prior-day rolling volume-profile peaks without look-ahead."""
    result = frame.copy()
    result["chip_lower_peak"] = np.nan
    result["chip_upper_peak"] = np.nan
    result["chip_valley"] = np.nan
    result["chip_double_peak"] = False
    trade_dates = list(pd.Index(result["trade_date"].drop_duplicates()).sort_values())
    typical = (result["high"] + result["low"] + result["close"]) / 3.0
    volumes = result["volume"].clip(lower=0.0)

    for date_index, trade_date in enumerate(trade_dates):
        history_start = max(0, date_index - strategy.chip_lookback_days)
        history_dates = trade_dates[history_start:date_index]
        if len(history_dates) < strategy.chip_min_history_days:
            continue
        history_mask = result["trade_date"].isin(history_dates)
        peaks = _select_double_peak(
            typical.loc[history_mask].to_numpy(dtype=float),
            volumes.loc[history_mask].to_numpy(dtype=float),
            strategy,
        )
        if peaks is None:
            continue
        current_mask = result["trade_date"] == trade_date
        result.loc[current_mask, "chip_lower_peak"] = peaks[0]
        result.loc[current_mask, "chip_upper_peak"] = peaks[1]
        result.loc[current_mask, "chip_valley"] = peaks[2]
        result.loc[current_mask, "chip_double_peak"] = True

    peak_span = (result["chip_upper_peak"] - result["chip_lower_peak"]).replace(0.0, np.nan)
    result["chip_position"] = (result["close"] - result["chip_lower_peak"]) / peak_span
    result["chip_price_between_peaks"] = (
        result["chip_double_peak"]
        & result["close"].ge(result["chip_lower_peak"])
        & result["close"].le(result["chip_upper_peak"])
    )
    return result


@dataclass
class PairState:
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    original_quantity: int
    remaining_quantity: int
    cash_flow: float
    holding_bars: int = 0
    exit_reason: str = ""


@dataclass
class ClosedPair:
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    quantity: int
    pnl: float
    exit_reason: str

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["entry_time"] = self.entry_time.isoformat()
        values["exit_time"] = self.exit_time.isoformat()
        return values


class VwapT0Strategy:
    """Generate paired intraday orders while restoring the overnight base inventory."""

    def __init__(self, config: StrategyConfig, lot_size: int):
        self.config = config
        self.lot_size = lot_size
        self.active_pair: Optional[PairState] = None
        self.closed_pairs: list[ClosedPair] = []
        self.current_date: Optional[pd.Timestamp] = None
        self.pairs_today = 0
        self.daily_realized_pnl = 0.0
        self.daily_open_equity = 0.0
        self.peak_equity = 0.0

    def _round_lot(self, quantity: float) -> int:
        return max(0, int(quantity // self.lot_size) * self.lot_size)

    def on_new_day(self, trade_date: pd.Timestamp, opening_equity: float) -> None:
        self.current_date = trade_date
        self.pairs_today = 0
        self.daily_realized_pnl = 0.0
        self.daily_open_equity = opening_equity
        self.peak_equity = max(self.peak_equity, opening_equity)

    def _account_drawdown_halted(self, ledger: PortfolioLedger, mark_price: float) -> bool:
        equity = ledger.equity(mark_price)
        self.peak_equity = max(self.peak_equity, equity)
        return equity <= self.peak_equity * (1.0 - self.config.account_drawdown_limit)

    def on_fill(self, fill: Fill) -> None:
        """Update pair state only from actual broker fills."""
        if fill.status != "filled" or fill.quantity <= 0:
            return
        if fill.action in {"entry_high", "entry_low"}:
            direction = "high_sell" if fill.action == "entry_high" else "low_buy"
            self.active_pair = PairState(
                direction=direction,
                entry_time=fill.timestamp,
                entry_price=fill.price,
                original_quantity=fill.quantity,
                remaining_quantity=fill.quantity,
                cash_flow=fill.cash_flow,
            )
            self.pairs_today += 1
            return
        if fill.action != "exit" or self.active_pair is None:
            return
        self.active_pair.cash_flow += fill.cash_flow
        self.active_pair.remaining_quantity -= fill.quantity
        if self.active_pair.remaining_quantity > 0:
            return
        pair = ClosedPair(
            direction=self.active_pair.direction,
            entry_time=self.active_pair.entry_time,
            exit_time=fill.timestamp,
            entry_price=self.active_pair.entry_price,
            quantity=self.active_pair.original_quantity,
            pnl=self.active_pair.cash_flow,
            exit_reason=self.active_pair.exit_reason or fill.reason,
        )
        self.closed_pairs.append(pair)
        self.daily_realized_pnl += pair.pnl
        self.active_pair = None

    def _exit_order(self, row: Any, reason: str) -> Order:
        assert self.active_pair is not None
        self.active_pair.exit_reason = reason
        side = "buy" if self.active_pair.direction == "high_sell" else "sell"
        return Order(
            side=side,
            quantity=self.active_pair.remaining_quantity,
            reason=reason,
            action="exit",
            signal_time=pd.Timestamp(row.datetime),
        )

    def on_bar(self, row: Any, ledger: PortfolioLedger, target_base_shares: int) -> Optional[Order]:
        """Return an order to execute at the next bar, if any."""
        timestamp = pd.Timestamp(row.datetime)
        time_value = timestamp.time()
        zscore = float(row.zscore) if pd.notna(row.zscore) else np.nan
        drawdown_halted = self._account_drawdown_halted(ledger, float(row.close))
        if self.active_pair is not None:
            self.active_pair.holding_bars += 1
            forced = time_value >= _parse_clock(self.config.force_exit_at)
            timed_out = self.active_pair.holding_bars >= self.config.max_holding_bars
            if self.active_pair.direction == "high_sell":
                mean_reverted = pd.notna(zscore) and zscore <= self.config.exit_z
                stopped = float(row.close) >= self.active_pair.entry_price * (1.0 + self.config.stop_loss_pct)
            else:
                mean_reverted = pd.notna(zscore) and zscore >= -self.config.exit_z
                stopped = float(row.close) <= self.active_pair.entry_price * (1.0 - self.config.stop_loss_pct)
            if forced:
                return self._exit_order(row, "forced_end_of_day")
            if drawdown_halted:
                return self._exit_order(row, "account_drawdown_stop")
            if stopped:
                return self._exit_order(row, "stop_loss")
            if timed_out:
                return self._exit_order(row, "time_stop")
            if mean_reverted:
                return self._exit_order(row, "mean_reversion")
            return None

        if not (_parse_clock(self.config.no_entry_before) <= time_value <= _parse_clock(self.config.no_entry_after)):
            return None
        if self.pairs_today >= self.config.max_pairs_per_day:
            return None
        if drawdown_halted:
            return None
        if self.daily_realized_pnl <= -self.daily_open_equity * self.config.daily_loss_limit:
            return None
        if not bool(row.prior_regime_allowed):
            return None
        if abs(float(row.vwap_drift)) > self.config.intraday_vwap_drift_limit:
            return None
        if pd.isna(zscore) or float(row.vwap) <= 0:
            return None
        edge_bps = abs(float(row.close) / float(row.vwap) - 1.0) * 10_000.0
        if edge_bps < self.config.min_edge_bps:
            return None
        quantity = self._round_lot(target_base_shares * self.config.position_fraction)
        if quantity <= 0:
            return None
        if zscore >= self.config.entry_z and ledger.sellable_shares >= quantity:
            return Order("sell", quantity, "zscore_high", "entry_high", timestamp)
        estimated_cost = quantity * float(row.close)
        if zscore <= -self.config.entry_z and ledger.cash >= estimated_cost:
            return Order("buy", quantity, "zscore_low", "entry_low", timestamp)
        return None


class ChipDoublePeakStrategy(VwapT0Strategy):
    """Trade reversals between two prominent rolling volume-profile peaks."""

    def on_bar(self, row: Any, ledger: PortfolioLedger, target_base_shares: int) -> Optional[Order]:
        """Return a paired order based on the price location between chip peaks."""
        timestamp = pd.Timestamp(row.datetime)
        time_value = timestamp.time()
        close = float(row.close)
        position = float(row.chip_position) if pd.notna(row.chip_position) else np.nan
        lower_peak = float(row.chip_lower_peak) if pd.notna(row.chip_lower_peak) else np.nan
        upper_peak = float(row.chip_upper_peak) if pd.notna(row.chip_upper_peak) else np.nan
        drawdown_halted = self._account_drawdown_halted(ledger, close)

        if self.active_pair is not None:
            self.active_pair.holding_bars += 1
            forced = time_value >= _parse_clock(self.config.force_exit_at)
            timed_out = self.active_pair.holding_bars >= self.config.max_holding_bars
            if self.active_pair.direction == "high_sell":
                mean_reverted = pd.notna(position) and position <= self.config.chip_exit_position
                peak_broken = pd.notna(upper_peak) and close >= upper_peak * (
                    1.0 + self.config.chip_breakout_buffer_pct
                )
                price_stopped = close >= self.active_pair.entry_price * (1.0 + self.config.stop_loss_pct)
            else:
                mean_reverted = pd.notna(position) and position >= self.config.chip_exit_position
                peak_broken = pd.notna(lower_peak) and close <= lower_peak * (
                    1.0 - self.config.chip_breakout_buffer_pct
                )
                price_stopped = close <= self.active_pair.entry_price * (1.0 - self.config.stop_loss_pct)
            if forced:
                return self._exit_order(row, "forced_end_of_day")
            if drawdown_halted:
                return self._exit_order(row, "account_drawdown_stop")
            if peak_broken:
                return self._exit_order(row, "chip_peak_breakout")
            if price_stopped:
                return self._exit_order(row, "stop_loss")
            if timed_out:
                return self._exit_order(row, "time_stop")
            if mean_reverted:
                return self._exit_order(row, "chip_midpoint_reversion")
            return None

        if not (_parse_clock(self.config.no_entry_before) <= time_value <= _parse_clock(self.config.no_entry_after)):
            return None
        if self.pairs_today >= self.config.max_pairs_per_day or drawdown_halted:
            return None
        if self.daily_realized_pnl <= -self.daily_open_equity * self.config.daily_loss_limit:
            return None
        if not bool(row.prior_regime_allowed):
            return None
        if not bool(row.chip_double_peak) or not bool(row.chip_price_between_peaks):
            return None
        if pd.isna(position):
            return None

        quantity = self._round_lot(target_base_shares * self.config.position_fraction)
        if quantity <= 0:
            return None
        if position >= self.config.chip_high_entry_position and ledger.sellable_shares >= quantity:
            return Order("sell", quantity, "chip_upper_zone", "entry_high", timestamp)
        estimated_cost = quantity * close
        if position <= self.config.chip_low_entry_position and ledger.cash >= estimated_cost:
            return Order("buy", quantity, "chip_lower_zone", "entry_low", timestamp)
        return None


def create_strategy(config: StrategyConfig, lot_size: int) -> VwapT0Strategy:
    """Build the configured strategy implementation."""
    if config.strategy_type == "chip_double_peak":
        return ChipDoublePeakStrategy(config, lot_size)
    return VwapT0Strategy(config, lot_size)
