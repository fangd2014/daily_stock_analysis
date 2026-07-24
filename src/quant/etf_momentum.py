"""Weekly ETF momentum rotation research, recommendation, and Feishu delivery."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from src.config import setup_env

from .tickdb_client import TickDBClient, TickDBError


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ETFSpec:
    symbol: str
    name: str
    asset_class: str


@dataclass(frozen=True)
class ETFMomentumConfig:
    name: str
    data_source: str
    rebalance_frequency: str
    initial_cash: float
    backtest_start: str
    backtest_end: str
    data_start: str
    max_positions: int
    max_per_asset_class: int
    target_exposure: float
    reduced_exposure: float
    momentum_windows: tuple[int, ...]
    momentum_weights: tuple[float, ...]
    trend_ma_window: int
    volatility_window: int
    drawdown_window: int
    volatility_penalty: float
    drawdown_penalty: float
    single_stop_loss: float
    trailing_stop: float
    portfolio_reduce_drawdown: float
    portfolio_stop_drawdown: float
    risk_cooldown_sessions: int
    max_entry_gap: float
    commission_rate: float
    minimum_commission: float
    slippage_rate: float
    lot_size: int
    cache_dir: str
    report_dir: str
    universe: tuple[ETFSpec, ...]


@dataclass(frozen=True)
class MomentumCandidate:
    symbol: str
    name: str
    asset_class: str
    close: float
    score: float
    return_20d: float
    return_60d: float
    return_120d: float
    return_250d: float
    volatility_60d: float
    drawdown_120d: float
    ma120: float
    trend_pass: bool
    selected: bool


class WeeklyNotifier(Protocol):
    def send(self, content: str) -> bool:
        """Send one completed weekly ETF plan."""


def load_etf_momentum_config(path: str | Path) -> ETFMomentumConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["momentum_windows"] = tuple(int(value) for value in payload["momentum_windows"])
    payload["momentum_weights"] = tuple(float(value) for value in payload["momentum_weights"])
    payload["universe"] = tuple(ETFSpec(**item) for item in payload["universe"])
    config = ETFMomentumConfig(**payload)
    if len(config.momentum_windows) != len(config.momentum_weights):
        raise ValueError("Momentum windows and weights must have equal length")
    if set(config.momentum_windows) != {20, 60, 120, 250}:
        raise ValueError("ETF momentum windows must be exactly 20, 60, 120, and 250 sessions")
    if not math.isclose(sum(config.momentum_weights), 1.0, abs_tol=1e-9):
        raise ValueError("Momentum weights must sum to one")
    if config.max_positions < 1 or config.max_per_asset_class < 1:
        raise ValueError("Position limits must be positive")
    if not 0 < config.target_exposure <= 1 or not 0 <= config.reduced_exposure <= config.target_exposure:
        raise ValueError("Exposure settings are invalid")
    if len({item.symbol for item in config.universe}) != len(config.universe):
        raise ValueError("ETF symbols must be unique")
    if config.data_source not in {"tickdb", "tushare"}:
        raise ValueError("data_source must be tickdb or tushare")
    if config.rebalance_frequency not in {"daily", "weekly"}:
        raise ValueError("rebalance_frequency must be daily or weekly")
    return config


class TickDBETFProvider:
    """Fetch and atomically cache completed ETF daily bars from TickDB."""

    def __init__(self, config: ETFMomentumConfig, client: TickDBClient | None = None) -> None:
        self.config = config
        self.client = client or TickDBClient()
        self.root = Path(config.cache_dir) / "tickdb_daily"

    def _path(self, symbol: str) -> Path:
        return self.root / f"{symbol.replace('.', '_')}.csv"

    @staticmethod
    def _normalize(symbol: str, payload: Any) -> pd.DataFrame:
        rows = payload.get("klines", []) if isinstance(payload, dict) else []
        frame = pd.DataFrame(rows)
        required = {"time", "open", "high", "low", "close", "volume", "quote_volume"}
        if not required.issubset(frame.columns):
            raise ValueError(f"Incomplete TickDB ETF kline schema for {symbol}")
        frame["date"] = (
            pd.to_datetime(frame["time"], unit="ms", utc=True)
            .dt.tz_convert("Asia/Shanghai")
            .dt.tz_localize(None)
            .dt.normalize()
        )
        for column in ("open", "high", "low", "close", "volume", "quote_volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["symbol"] = symbol
        frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
        return frame[["symbol", "date", "open", "high", "low", "close", "volume", "quote_volume"]]

    @staticmethod
    def _write(path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        values = frame.copy()
        values["date"] = pd.to_datetime(values["date"]).dt.strftime("%Y-%m-%d")
        values.to_csv(temporary, index=False)
        temporary.replace(path)

    @staticmethod
    def _read(path: Path) -> pd.DataFrame:
        frame = pd.read_csv(path)
        frame["date"] = pd.to_datetime(frame["date"])
        return frame

    def _fetch_one(self, spec: ETFSpec, limit: int, force: bool) -> tuple[str, pd.DataFrame, str | None]:
        path = self._path(spec.symbol)
        try:
            payload = self.client.get_kline(spec.symbol, "1d", limit=limit)
            frame = self._normalize(spec.symbol, payload)
            self._write(path, frame)
            LOGGER.info("ETF data fetched: symbol=%s rows=%d", spec.symbol, len(frame))
            return spec.symbol, frame, None
        except (TickDBError, ValueError) as exc:
            if path.exists() and not force:
                frame = self._read(path)
                LOGGER.warning("ETF data fallback to cache: symbol=%s error=%s", spec.symbol, exc)
                return spec.symbol, frame, str(exc)
            return spec.symbol, pd.DataFrame(), str(exc)

    def fetch(self, limit: int = 700, force: bool = False, workers: int = 3) -> tuple[pd.DataFrame, dict[str, str]]:
        frames: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as executor:
            futures = {
                executor.submit(self._fetch_one, spec, limit, force): spec.symbol for spec in self.config.universe
            }
            for future in as_completed(futures):
                symbol, frame, error = future.result()
                if not frame.empty:
                    frames[symbol] = frame
                if error:
                    errors[symbol] = error
        minimum = max(8, math.ceil(len(self.config.universe) * 0.8))
        if len(frames) < minimum:
            raise ValueError(f"Only {len(frames)} ETF histories are available; {minimum} are required")
        bars = pd.concat(frames.values(), ignore_index=True)
        cutoff = pd.Timestamp(self.config.backtest_end)
        bars = bars[pd.to_datetime(bars["date"]).le(cutoff)].copy()
        return bars, errors


class TushareETFProvider:
    """Fetch complete ETF daily histories when TickDB plan retention is insufficient."""

    def __init__(self, config: ETFMomentumConfig, client: TickDBClient | None = None) -> None:
        from src.tushare_client import create_tushare_pro_api

        setup_env()
        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token or token.startswith("your_"):
            raise ValueError("TUSHARE_TOKEN is required for ETF history")
        self.config = config
        self.client = client or TickDBClient()
        self.api = create_tushare_pro_api(token)
        self.root = Path(config.cache_dir) / "tushare_qfq_daily"

    def _path(self, symbol: str) -> Path:
        return self.root / f"{symbol.replace('.', '_')}.csv"

    @staticmethod
    def _normalize(symbol: str, frame: pd.DataFrame, adjustment: pd.DataFrame) -> pd.DataFrame:
        required = {"trade_date", "open", "high", "low", "close", "vol", "amount"}
        if frame is None or not required.issubset(frame.columns):
            raise ValueError(f"Incomplete Tushare ETF daily schema for {symbol}")
        if adjustment is None or not {"trade_date", "adj_factor"}.issubset(adjustment.columns):
            raise ValueError(f"Incomplete Tushare ETF adjustment schema for {symbol}")
        values = frame.copy()
        factors = adjustment[["trade_date", "adj_factor"]].copy()
        factors["adj_factor"] = pd.to_numeric(factors["adj_factor"], errors="coerce")
        values = values.merge(factors, on="trade_date", how="left", validate="one_to_one")
        if values["adj_factor"].isna().any() or not np.isfinite(values["adj_factor"]).all():
            raise ValueError(f"Tushare ETF adjustment coverage is incomplete for {symbol}")
        values["date"] = pd.to_datetime(values["trade_date"], format="%Y%m%d", errors="coerce")
        for column in ("open", "high", "low", "close", "vol", "amount"):
            values[column] = pd.to_numeric(values[column], errors="coerce")
        latest_factor = float(values.sort_values("date")["adj_factor"].iloc[-1])
        relative_factor = values["adj_factor"] / latest_factor
        for column in ("open", "high", "low", "close"):
            values[column] = values[column] * relative_factor
        values["symbol"] = symbol
        values["volume"] = values["vol"] * 100.0
        values["quote_volume"] = values["amount"] * 1_000.0
        values = values.dropna(subset=["date", "open", "high", "low", "close"])
        values = values.sort_values("date").drop_duplicates("date", keep="last")
        return values[["symbol", "date", "open", "high", "low", "close", "volume", "quote_volume"]]

    @staticmethod
    def _write(path: Path, frame: pd.DataFrame) -> None:
        TickDBETFProvider._write(path, frame)

    @staticmethod
    def _read(path: Path) -> pd.DataFrame:
        return TickDBETFProvider._read(path)

    def _fetch_one(self, spec: ETFSpec, force: bool) -> tuple[str, pd.DataFrame, str | None]:
        path = self._path(spec.symbol)
        try:
            frame = self.api.fund_daily(
                ts_code=spec.symbol,
                start_date=pd.Timestamp(self.config.data_start).strftime("%Y%m%d"),
                end_date=pd.Timestamp(self.config.backtest_end).strftime("%Y%m%d"),
            )
            adjustment = self.api.fund_adj(
                ts_code=spec.symbol,
                start_date=pd.Timestamp(self.config.data_start).strftime("%Y%m%d"),
                end_date=pd.Timestamp(self.config.backtest_end).strftime("%Y%m%d"),
            )
            values = self._normalize(spec.symbol, frame, adjustment)
            self._write(path, values)
            LOGGER.info("ETF data fetched: source=tushare symbol=%s rows=%d", spec.symbol, len(values))
            return spec.symbol, values, None
        except (RuntimeError, ValueError) as exc:
            if path.exists() and not force:
                values = self._read(path)
                LOGGER.warning("ETF data fallback to cache: source=tushare symbol=%s error=%s", spec.symbol, exc)
                return spec.symbol, values, str(exc)
            return spec.symbol, pd.DataFrame(), str(exc)

    def fetch(self, limit: int = 700, force: bool = False, workers: int = 3) -> tuple[pd.DataFrame, dict[str, str]]:
        del limit
        frames: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as executor:
            futures = {executor.submit(self._fetch_one, spec, force): spec.symbol for spec in self.config.universe}
            for future in as_completed(futures):
                symbol, frame, error = future.result()
                if not frame.empty:
                    frames[symbol] = frame
                if error:
                    errors[symbol] = error
        minimum = max(8, math.ceil(len(self.config.universe) * 0.8))
        if len(frames) < minimum:
            raise ValueError(f"Only {len(frames)} ETF histories are available; {minimum} are required")
        return pd.concat(frames.values(), ignore_index=True), errors

    def load(self) -> tuple[pd.DataFrame, dict[str, str]]:
        frames = []
        for spec in self.config.universe:
            path = self._path(spec.symbol)
            if path.exists():
                frames.append(self._read(path))
        minimum = max(8, math.ceil(len(self.config.universe) * 0.8))
        if len(frames) < minimum:
            raise ValueError(f"Only {len(frames)} cached ETF histories are available; {minimum} are required")
        return pd.concat(frames, ignore_index=True), {}


def _candidate_table(
    bars: pd.DataFrame,
    config: ETFMomentumConfig,
    signal_date: pd.Timestamp,
) -> list[MomentumCandidate]:
    metadata = {item.symbol: item for item in config.universe}
    candidates = []
    for symbol, history in bars[bars["date"].le(signal_date)].groupby("symbol", sort=False):
        history = history.sort_values("date")
        if pd.Timestamp(history["date"].iloc[-1]).normalize() != signal_date.normalize():
            LOGGER.warning(
                "ETF excluded because its latest bar is stale: symbol=%s date=%s",
                symbol,
                signal_date.date(),
            )
            continue
        close = pd.to_numeric(history["close"], errors="coerce").dropna()
        needed = max(max(config.momentum_windows), config.trend_ma_window, config.drawdown_window) + 1
        if len(close) < needed:
            continue
        returns = {
            window: float(close.iloc[-1] / close.iloc[-window - 1] - 1.0) for window in config.momentum_windows
        }
        daily_return = close.pct_change(fill_method=None)
        volatility = float(daily_return.tail(config.volatility_window).std(ddof=0) * np.sqrt(252.0))
        trailing = close.tail(config.drawdown_window)
        drawdown = float(trailing.iloc[-1] / trailing.max() - 1.0)
        ma = float(close.tail(config.trend_ma_window).mean())
        score = sum(
            weight * returns[window] for window, weight in zip(config.momentum_windows, config.momentum_weights)
        )
        score -= config.volatility_penalty * volatility
        score -= config.drawdown_penalty * abs(drawdown)
        trend_pass = bool(close.iloc[-1] > ma and returns[120] > 0)
        spec = metadata[symbol]
        candidates.append(
            MomentumCandidate(
                symbol=symbol,
                name=spec.name,
                asset_class=spec.asset_class,
                close=float(close.iloc[-1]),
                score=float(score),
                return_20d=returns[20],
                return_60d=returns[60],
                return_120d=returns[120],
                return_250d=returns[250],
                volatility_60d=volatility,
                drawdown_120d=drawdown,
                ma120=ma,
                trend_pass=trend_pass,
                selected=False,
            )
        )
    ranked = sorted(candidates, key=lambda item: (-item.score, item.symbol))
    selected_symbols = []
    class_counts: dict[str, int] = {}
    for item in ranked:
        if not item.trend_pass or item.score <= 0:
            continue
        if class_counts.get(item.asset_class, 0) >= config.max_per_asset_class:
            continue
        selected_symbols.append(item.symbol)
        class_counts[item.asset_class] = class_counts.get(item.asset_class, 0) + 1
        if len(selected_symbols) >= config.max_positions:
            break
    return [
        MomentumCandidate(**{**asdict(item), "selected": item.symbol in selected_symbols}) for item in ranked
    ]


def _signal_dates(
    bars: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    frequency: str,
) -> list[pd.Timestamp]:
    dates = pd.Series(sorted(pd.to_datetime(bars["date"]).drop_duplicates()))
    dates = dates[dates.le(end)]
    if frequency == "daily":
        selected = dates.tolist()
    else:
        selected = dates.groupby(dates.dt.to_period("W-FRI")).max().tolist()
    return [pd.Timestamp(value) for value in selected if pd.Timestamp(value) >= start - pd.Timedelta(days=7)]


def _commission(config: ETFMomentumConfig, gross: float) -> float:
    return max(config.minimum_commission, gross * config.commission_rate) if gross > 0 else 0.0


def run_backtest(bars: pd.DataFrame, config: ETFMomentumConfig) -> dict[str, Any]:
    values = bars.copy()
    values["date"] = pd.to_datetime(values["date"]).dt.normalize()
    values = values.sort_values(["date", "symbol"])
    sessions = sorted(values["date"].drop_duplicates())
    start = pd.Timestamp(config.backtest_start)
    end = pd.Timestamp(config.backtest_end)
    signal_dates = _signal_dates(values, start, end, config.rebalance_frequency)
    next_session = {date: sessions[index + 1] for index, date in enumerate(sessions[:-1])}
    schedules: dict[pd.Timestamp, list[str]] = {}
    signal_records = []
    for signal_date in signal_dates:
        candidates = _candidate_table(values, config, signal_date)
        selected = [item.symbol for item in candidates if item.selected]
        execute_date = next_session.get(signal_date)
        if execute_date is not None and execute_date <= end:
            schedules[execute_date] = selected
        signal_records.append(
            {
                "signal_date": signal_date.strftime("%Y-%m-%d"),
                "execute_date": execute_date.strftime("%Y-%m-%d") if execute_date is not None else None,
                "selected": selected,
                "candidates": [asdict(item) for item in candidates],
            }
        )

    by_date = {
        date: frame.set_index("symbol") for date, frame in values.groupby("date", sort=False)
    }
    positions: dict[str, int] = {}
    entry_price: dict[str, float] = {}
    highest_close: dict[str, float] = {}
    forced_exits: set[str] = set()
    cash = float(config.initial_cash)
    peak_equity = cash
    reduced = False
    cooldown = 0
    trades = []
    equity_rows = []
    previous_close: dict[str, float] = {}

    for session in sessions:
        if session < start or session > end:
            frame = by_date[session]
            previous_close.update(pd.to_numeric(frame["close"], errors="coerce").dropna().to_dict())
            continue
        frame = by_date[session]
        selected = schedules.get(session)
        if cooldown > 0:
            cooldown -= 1
            selected = [] if selected is not None else None
            if cooldown == 0 and not positions:
                peak_equity = cash
                reduced = False
        if forced_exits:
            selected = [symbol for symbol in (selected or positions.keys()) if symbol not in forced_exits]

        if selected is not None:
            open_prices = pd.to_numeric(frame["open"], errors="coerce").to_dict()
            open_equity = cash + sum(
                shares * float(open_prices.get(symbol, previous_close.get(symbol, 0.0)))
                for symbol, shares in positions.items()
            )
            exposure = config.reduced_exposure if reduced else config.target_exposure
            per_position_weight = exposure / config.max_positions
            targets = {symbol: open_equity * per_position_weight for symbol in selected}

            for symbol in list(positions):
                price = float(open_prices.get(symbol, np.nan))
                if not np.isfinite(price):
                    continue
                target_shares = int(targets.get(symbol, 0.0) / price / config.lot_size) * config.lot_size
                if positions[symbol] <= target_shares:
                    continue
                quantity = positions[symbol] - target_shares
                execution_price = price * (1.0 - config.slippage_rate)
                gross = quantity * execution_price
                fee = _commission(config, gross)
                cash += gross - fee
                positions[symbol] = target_shares
                trades.append(
                    {
                        "date": session.strftime("%Y-%m-%d"),
                        "symbol": symbol,
                        "side": "sell",
                        "quantity": quantity,
                        "price": execution_price,
                        "fee": fee,
                        "reason": "risk_or_weekly_rebalance" if target_shares == 0 else "weekly_trim",
                    }
                )
                if target_shares == 0:
                    positions.pop(symbol, None)
                    entry_price.pop(symbol, None)
                    highest_close.pop(symbol, None)

            for symbol, target_value in targets.items():
                price = float(open_prices.get(symbol, np.nan))
                prior = previous_close.get(symbol)
                if not np.isfinite(price) or prior is None or price / prior - 1.0 > config.max_entry_gap:
                    continue
                target_shares = int(target_value / price / config.lot_size) * config.lot_size
                current_shares = positions.get(symbol, 0)
                quantity = max(target_shares - current_shares, 0)
                if quantity <= 0:
                    continue
                execution_price = price * (1.0 + config.slippage_rate)
                affordable = int(
                    max(cash - config.minimum_commission, 0.0)
                    / execution_price
                    / config.lot_size
                ) * config.lot_size
                quantity = min(quantity, affordable)
                if quantity <= 0:
                    continue
                gross = quantity * execution_price
                fee = _commission(config, gross)
                cash -= gross + fee
                positions[symbol] = current_shares + quantity
                entry_price[symbol] = (
                    (entry_price.get(symbol, execution_price) * current_shares + execution_price * quantity)
                    / positions[symbol]
                )
                highest_close[symbol] = max(highest_close.get(symbol, execution_price), execution_price)
                trades.append(
                    {
                        "date": session.strftime("%Y-%m-%d"),
                        "symbol": symbol,
                        "side": "buy",
                        "quantity": quantity,
                        "price": execution_price,
                        "fee": fee,
                        "reason": "weekly_momentum_entry",
                    }
                )
            forced_exits.clear()

        close_prices = pd.to_numeric(frame["close"], errors="coerce").to_dict()
        market_value = 0.0
        for symbol, shares in positions.items():
            close = float(close_prices.get(symbol, previous_close.get(symbol, 0.0)))
            market_value += shares * close
            highest_close[symbol] = max(highest_close.get(symbol, close), close)
            if close / entry_price[symbol] - 1.0 <= -config.single_stop_loss:
                forced_exits.add(symbol)
            elif close / highest_close[symbol] - 1.0 <= -config.trailing_stop:
                forced_exits.add(symbol)
        equity = cash + market_value
        peak_equity = max(peak_equity, equity)
        drawdown = equity / peak_equity - 1.0
        reduced = drawdown <= -config.portfolio_reduce_drawdown
        if drawdown <= -config.portfolio_stop_drawdown and cooldown == 0 and positions:
            forced_exits.update(positions)
            cooldown = config.risk_cooldown_sessions
        equity_rows.append(
            {
                "date": session.strftime("%Y-%m-%d"),
                "equity": equity,
                "cash": cash,
                "market_value": market_value,
                "positions": len(positions),
                "drawdown": drawdown,
            }
        )
        previous_close.update(close_prices)

    equity = pd.DataFrame(equity_rows)
    if equity.empty:
        raise ValueError("Backtest produced no equity observations")
    equity["risk_drawdown"] = equity["drawdown"]
    equity["drawdown"] = equity["equity"] / equity["equity"].cummax() - 1.0
    daily_returns = equity["equity"].pct_change(fill_method=None).fillna(0.0)
    total_return = float(equity["equity"].iloc[-1] / config.initial_cash - 1.0)
    years = max((pd.Timestamp(equity["date"].iloc[-1]) - pd.Timestamp(equity["date"].iloc[0])).days / 365.25, 1 / 252)
    annualized = float((1.0 + total_return) ** (1.0 / years) - 1.0)
    volatility = float(daily_returns.std(ddof=0) * np.sqrt(252.0))
    sharpe = (
        float(daily_returns.mean() / daily_returns.std(ddof=0) * np.sqrt(252.0))
        if daily_returns.std() > 0
        else 0.0
    )
    metrics = {
        "initial_cash": config.initial_cash,
        "ending_equity": float(equity["equity"].iloc[-1]),
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": abs(float(equity["drawdown"].min())),
        "annualized_volatility": volatility,
        "sharpe": sharpe,
        "trade_count": len(trades),
        "total_fees": float(sum(item["fee"] for item in trades)),
        "average_exposure": float((equity["market_value"] / equity["equity"]).mean()),
    }
    month_ends = equity.assign(month=pd.to_datetime(equity["date"]).dt.to_period("M")).groupby("month")[
        "equity"
    ].last()
    monthly_returns = month_ends.pct_change(fill_method=None)
    if not monthly_returns.empty:
        monthly_returns.iloc[0] = month_ends.iloc[0] / config.initial_cash - 1.0
    monthly_records = [
        {"month": str(month), "return": float(value)} for month, value in monthly_returns.items()
    ]
    metrics["monthly_return_median"] = float(monthly_returns.median())
    metrics["positive_month_ratio"] = float(monthly_returns.gt(0).mean())
    return {
        "metrics": metrics,
        "equity": equity,
        "trades": pd.DataFrame(trades),
        "signals": signal_records,
        "monthly_returns": monthly_records,
    }


def _benchmark_metrics(bars: pd.DataFrame, config: ETFMomentumConfig, symbol: str = "510300.SH") -> dict[str, float]:
    frame = bars[bars["symbol"].eq(symbol)].sort_values("date")
    frame = frame[frame["date"].between(pd.Timestamp(config.backtest_start), pd.Timestamp(config.backtest_end))]
    if len(frame) < 2:
        return {}
    returns = frame["close"].pct_change(fill_method=None).fillna(0.0) * config.target_exposure
    curve = config.initial_cash * (1.0 + returns).cumprod()
    drawdown = curve / curve.cummax() - 1.0
    return {
        "symbol": symbol,
        "total_return": float(curve.iloc[-1] / config.initial_cash - 1.0),
        "max_drawdown": abs(float(drawdown.min())),
    }


def _next_trade_day(client: TickDBClient, signal_date: pd.Timestamp) -> str:
    data = client.get_trade_days(
        signal_date.strftime("%Y%m%d"),
        (signal_date + pd.Timedelta(days=10)).strftime("%Y%m%d"),
    )
    days = sorted(str(value) for value in data.get("trade_days", []))
    later = [value for value in days if value > signal_date.strftime("%Y%m%d")]
    if not later:
        raise ValueError("No next CN trading session is available")
    return later[0]


def build_next_week_plan(
    bars: pd.DataFrame,
    config: ETFMomentumConfig,
    client: TickDBClient | None = None,
) -> dict[str, Any]:
    signal_date = pd.to_datetime(bars["date"]).max().normalize()
    candidates = _candidate_table(bars, config, signal_date)
    selected = [item for item in candidates if item.selected]
    next_day = _next_trade_day(client or TickDBClient(), signal_date)
    per_position_cash = config.initial_cash * config.target_exposure / config.max_positions
    plans = []
    for item in selected:
        shares = int(per_position_cash / item.close / config.lot_size) * config.lot_size
        plans.append(
            {
                **asdict(item),
                "target_weight": config.target_exposure / config.max_positions,
                "reference_cash": per_position_cash,
                "reference_shares": shares,
                "max_open_price": item.close * (1.0 + config.max_entry_gap),
                "initial_stop": item.close * (1.0 - config.single_stop_loss),
            }
        )
    return {
        "signal_date": signal_date.strftime("%Y-%m-%d"),
        "execute_date": pd.Timestamp(next_day).strftime("%Y-%m-%d"),
        "initial_cash": config.initial_cash,
        "target_exposure": config.target_exposure,
        "selected": plans,
        "ranking": [asdict(item) for item in candidates],
        "rules": {
            "entry": "Next-session open; skip when the opening gap exceeds 3%",
            "single_stop_loss": config.single_stop_loss,
            "trailing_stop": config.trailing_stop,
            "portfolio_reduce_drawdown": config.portfolio_reduce_drawdown,
            "portfolio_stop_drawdown": config.portfolio_stop_drawdown,
        },
    }


def _report_markdown(
    config: ETFMomentumConfig,
    backtest: dict[str, Any],
    benchmark: dict[str, float],
    plan: dict[str, Any],
) -> str:
    metrics = backtest["metrics"]
    frequency_label = "每日收盘重算、下一交易日开盘成交" if config.rebalance_frequency == "daily" else "周末重算"
    lines = [
        f"# {config.name}",
        "",
        f"回测区间：{config.backtest_start} 至 {config.backtest_end}；初始资金：¥{config.initial_cash:,.0f}",
        f"价格历史：{config.data_source.upper()}（前复权）；中国市场交易日历：TickDB",
        f"历史信号频率：{frequency_label}",
        "",
        "## 回测结果",
        "",
        f"- 期末权益：¥{metrics['ending_equity']:,.2f}",
        f"- 总收益率：{metrics['total_return']:.2%}",
        f"- 年化收益率：{metrics['annualized_return']:.2%}",
        f"- 最大回撤：{metrics['max_drawdown']:.2%}",
        f"- 夏普比率：{metrics['sharpe']:.2f}",
        f"- 月收益中位数/正收益月份占比：{metrics['monthly_return_median']:.2%} / "
        f"{metrics['positive_month_ratio']:.1%}",
        f"- 平均仓位：{metrics['average_exposure']:.2%}",
        f"- 成交笔数/总费用：{metrics['trade_count']} / ¥{metrics['total_fees']:,.2f}",
    ]
    if benchmark:
        lines.extend(
            [
                f"- 对照（60%沪深300ETF）：收益{benchmark['total_return']:.2%}，"
                f"最大回撤{benchmark['max_drawdown']:.2%}",
            ]
        )
    lines.extend(["", "| 月份 | 收益率 |", "| --- | ---: |"])
    lines.extend(
        f"| {item['month']} | {item['return']:.2%} |" for item in backtest["monthly_returns"]
    )
    lines.extend(
        [
            "",
            f"## {plan['execute_date']} 模拟操作计划",
            "",
            "| ETF | 类别 | 收盘 | 动量分 | 20/60/120/250日 | 目标仓位 | 高开放弃价 | 初始止损 |",
            "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for item in plan["selected"]:
        lines.append(
            f"| {item['name']}（{item['symbol']}） | {item['asset_class']} | {item['close']:.3f} | "
            f"{item['score']:.3f} | {item['return_20d']:.1%}/{item['return_60d']:.1%}/"
            f"{item['return_120d']:.1%}/{item['return_250d']:.1%} | {item['target_weight']:.0%} | "
            f"{item['max_open_price']:.3f} | {item['initial_stop']:.3f} |"
        )
    if not plan["selected"]:
        lines.append("没有ETF通过绝对趋势门槛，本周保持现金。")
    lines.extend(
        [
            "",
            "执行纪律：开盘高于放弃价不追；单只固定止损7%、从持有期高点回撤6%退出；"
            "组合回撤8%降仓至30%，回撤12%清仓并冷静20个交易日。",
            "",
            "回测采用下一交易日开盘成交，计入佣金、最低佣金和滑点；使用前复权价格收益，"
            "未将现金分红重复计为现金流。",
            "仅用于模拟盘研究，不构成投资建议，也不保证未来收益。",
            "",
            "ETF价格及复权因子由Tushare提供，中国市场交易日历由TickDB提供。",
            "📡 数据由 TickDB.ai 提供",
        ]
    )
    return "\n".join(lines) + "\n"


class FeishuETFNotifier:
    """Push a weekly ETF rotation plan through the configured Feishu webhook."""

    def __init__(self) -> None:
        setup_env()
        self.webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not self.webhook or "your_key_here" in self.webhook:
            raise ValueError("FEISHU_WEBHOOK_URL is required for ETF rotation push")

    def send(self, content: str) -> bool:
        import requests

        from src.formatters import format_feishu_markdown

        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "ETF动量每周复盘与操作策略"},
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


def run_etf_research(
    config_path: str | Path,
    push: bool = False,
    provider: TickDBETFProvider | TushareETFProvider | None = None,
    notifier: WeeklyNotifier | None = None,
    rolling_dates: bool = False,
    refresh: bool = True,
) -> dict[str, Any]:
    config = load_etf_momentum_config(config_path)
    if rolling_dates:
        end = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
        start = end - pd.DateOffset(years=1)
        config = replace(
            config,
            backtest_start=start.strftime("%Y-%m-%d"),
            backtest_end=end.strftime("%Y-%m-%d"),
            data_start=(start - pd.Timedelta(days=450)).strftime("%Y-%m-%d"),
        )
    if provider is None:
        provider = (
            TushareETFProvider(config)
            if config.data_source == "tushare"
            else TickDBETFProvider(config)
        )
    if refresh or not isinstance(provider, TushareETFProvider):
        bars, data_errors = provider.fetch()
    else:
        bars, data_errors = provider.load()
    backtest = run_backtest(bars, config)
    benchmark = _benchmark_metrics(bars, config)
    plan = build_next_week_plan(bars, config, client=provider.client)
    markdown = _report_markdown(config, backtest, benchmark, plan)
    output = Path(config.report_dir)
    output.mkdir(parents=True, exist_ok=True)
    backtest["equity"].to_csv(output / "equity.csv", index=False)
    backtest["trades"].to_csv(output / "trades.csv", index=False)
    (output / "signals.json").write_text(
        json.dumps(backtest["signals"], ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    payload = {
        "metrics": backtest["metrics"],
        "benchmark": benchmark,
        "plan": plan,
        "monthly_returns": backtest["monthly_returns"],
        "data_errors": data_errors,
        "pushed": False,
    }
    (output / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (output / "latest.md").write_text(markdown, encoding="utf-8")
    if push:
        sender = notifier or FeishuETFNotifier()
        if not sender.send(markdown):
            raise RuntimeError("Feishu rejected the ETF rotation report")
        payload["pushed"] = True
        (output / "latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    LOGGER.info(
        "ETF research completed: return=%.2f%% drawdown=%.2f%% selected=%d pushed=%s",
        backtest["metrics"]["total_return"] * 100.0,
        backtest["metrics"]["max_drawdown"] * 100.0,
        len(plan["selected"]),
        payload["pushed"],
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="ETF momentum rotation research and weekly plan")
    parser.add_argument("--config", default="configs/quant/etf_momentum_rotation.json")
    parser.add_argument("command", choices=("research", "daily", "weekly"), nargs="?", default="research")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = run_etf_research(
        args.config,
        push=args.push or args.command in {"daily", "weekly"},
        rolling_dates=args.command in {"daily", "weekly"},
        refresh=not args.cache_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
