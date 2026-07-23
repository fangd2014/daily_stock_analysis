"""Persistent five-minute paper trading runner for the T+0 strategy."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import logging
import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.realtime_types import UnifiedRealtimeQuote
from src.config import setup_env
from src.tushare_client import create_tushare_pro_api

from .broker import ExecutionBroker, Order
from .config import QuantConfig, load_quant_config
from .paper_config import PaperConfig, load_paper_config
from .paper_report import generate_weekly_report
from .strategy import PairState, VwapT0Strategy, create_strategy, prepare_features

logger = logging.getLogger(__name__)

TRADE_FIELDS = [
    "timestamp",
    "signal_time",
    "side",
    "quantity",
    "price",
    "gross_value",
    "commission",
    "stamp_tax",
    "transfer_fee",
    "total_fees",
    "cash_flow",
    "reason",
    "action",
    "status",
    "message",
]
BAR_FIELDS = ["datetime", "symbol", "open", "high", "low", "close", "volume", "amount", "source"]
EQUITY_FIELDS = ["timestamp", "cash", "shares", "sellable_shares", "price", "equity"]
PAIR_FIELDS = ["direction", "entry_time", "exit_time", "entry_price", "quantity", "pnl", "exit_reason"]


class QuoteProvider(Protocol):
    def get_quote(self, symbol: str) -> Optional[UnifiedRealtimeQuote]:
        """Return a current quote or None."""


class TencentQuoteProvider:
    """Lightweight single-symbol Tencent quote adapter."""

    def __init__(self) -> None:
        self.fetcher = AkshareFetcher()

    def get_quote(self, symbol: str) -> Optional[UnifiedRealtimeQuote]:
        return self.fetcher.get_realtime_quote(symbol, source="tencent")


class PaperStateStore:
    """Atomic JSON state plus append-only CSV journals."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / "runner.lock"

    @contextmanager
    def lock(self) -> Iterator[None]:
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self, initial_cash: float, start_date: str) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {
            "version": 1,
            "start_date": start_date,
            "initial_cash": initial_cash,
            "cash": initial_cash,
            "total_shares": 0,
            "sellable_shares": 0,
            "target_base_shares": 0,
            "base_initialized": False,
            "last_trade_date": None,
            "last_bucket": None,
            "last_snapshot": None,
            "last_price": 0.0,
            "last_equity": initial_cash,
            "last_tick": None,
            "pending_order": None,
            "active_pair": None,
            "pairs_today": 0,
            "daily_realized_pnl": 0.0,
            "daily_open_equity": initial_cash,
            "peak_equity": initial_cash,
            "daily_history_date": None,
            "last_signal": None,
        }

    def save(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def append(self, filename: str, fieldnames: list[str], values: dict[str, Any]) -> None:
        path = self.root / filename
        exists = path.exists() and path.stat().st_size > 0
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerow(values)


def _serialize_order(order: Optional[Order]) -> Optional[dict[str, Any]]:
    if order is None:
        return None
    return {
        "side": order.side,
        "quantity": order.quantity,
        "reason": order.reason,
        "action": order.action,
        "signal_time": order.signal_time.isoformat(),
    }


def _deserialize_order(values: Optional[dict[str, Any]]) -> Optional[Order]:
    if not values:
        return None
    return Order(
        side=values["side"],
        quantity=int(values["quantity"]),
        reason=values["reason"],
        action=values["action"],
        signal_time=pd.Timestamp(values["signal_time"]),
    )


def _serialize_pair(pair: Optional[PairState]) -> Optional[dict[str, Any]]:
    if pair is None:
        return None
    return {
        "direction": pair.direction,
        "entry_time": pair.entry_time.isoformat(),
        "entry_price": pair.entry_price,
        "original_quantity": pair.original_quantity,
        "remaining_quantity": pair.remaining_quantity,
        "cash_flow": pair.cash_flow,
        "holding_bars": pair.holding_bars,
        "exit_reason": pair.exit_reason,
    }


def _deserialize_pair(values: Optional[dict[str, Any]]) -> Optional[PairState]:
    if not values:
        return None
    return PairState(
        direction=values["direction"],
        entry_time=pd.Timestamp(values["entry_time"]),
        entry_price=float(values["entry_price"]),
        original_quantity=int(values["original_quantity"]),
        remaining_quantity=int(values["remaining_quantity"]),
        cash_flow=float(values["cash_flow"]),
        holding_bars=int(values.get("holding_bars", 0)),
        exit_reason=values.get("exit_reason", ""),
    )


def _is_market_session(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    current = now.time().replace(tzinfo=None)
    return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)


def _five_minute_bucket(timestamp: datetime) -> datetime:
    minute = timestamp.minute - timestamp.minute % 5
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def _parse_quote_time(quote: UnifiedRealtimeQuote, timezone: ZoneInfo) -> Optional[datetime]:
    value = quote.quote_time or ""
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone)
    except ValueError:
        return None


class PaperTradingService:
    """Execute one idempotent paper-trading tick and persist its state."""

    def __init__(self, config: PaperConfig, quote_provider: QuoteProvider | None = None):
        self.config = config
        self.quant_config: QuantConfig = load_quant_config(config.quant_config)
        portfolio = self.quant_config.portfolio
        if config.initial_cash is not None or config.base_ratio is not None:
            portfolio = replace(
                portfolio,
                initial_cash=config.initial_cash if config.initial_cash is not None else portfolio.initial_cash,
                base_ratio=config.base_ratio if config.base_ratio is not None else portfolio.base_ratio,
            )
        self.quant_config = replace(
            self.quant_config,
            symbol=config.quant_symbol or self.quant_config.symbol,
            name=config.name,
            portfolio=portfolio,
            strategy=(
                replace(self.quant_config.strategy, position_fraction=config.position_fraction)
                if config.position_fraction is not None
                else self.quant_config.strategy
            ),
        )
        self.timezone = ZoneInfo(config.timezone)
        self.store = PaperStateStore(config.state_dir)
        self.quote_provider = quote_provider or TencentQuoteProvider()

    def _broker(self, state: dict[str, Any]) -> ExecutionBroker:
        broker = ExecutionBroker(self.quant_config.portfolio, self.quant_config.costs, float(state["cash"]))
        broker.ledger.total_shares = int(state["total_shares"])
        broker.ledger.sellable_shares = int(state["sellable_shares"])
        return broker

    def _strategy(self, state: dict[str, Any]) -> VwapT0Strategy:
        strategy = create_strategy(self.quant_config.strategy, self.quant_config.portfolio.lot_size)
        strategy.active_pair = _deserialize_pair(state.get("active_pair"))
        strategy.pairs_today = int(state.get("pairs_today", 0))
        strategy.daily_realized_pnl = float(state.get("daily_realized_pnl", 0.0))
        strategy.daily_open_equity = float(state.get("daily_open_equity", state["initial_cash"]))
        strategy.peak_equity = float(state.get("peak_equity", state["initial_cash"]))
        if state.get("last_trade_date"):
            strategy.current_date = pd.Timestamp(state["last_trade_date"])
        return strategy

    def initialize(self) -> dict[str, Any]:
        """Create the account state without contacting a market data source."""
        try:
            with self.store.lock():
                state = self.store.load(self.quant_config.portfolio.initial_cash, self.config.start_date)
                if not self.store.state_path.exists():
                    self.store.save(state)
                return state
        except BlockingIOError:
            return {"status": "skipped", "reason": "another_tick_is_running"}

    def _refresh_daily_history(self, state: dict[str, Any], today: pd.Timestamp) -> None:
        path = self.store.root / "daily_history.csv"
        date_key = today.strftime("%Y-%m-%d")
        if state.get("daily_history_date") == date_key and path.exists():
            return
        setup_env()
        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token or token.startswith("your_"):
            if not path.exists():
                logger.warning("Daily regime history unavailable because TUSHARE_TOKEN is not configured")
            return
        try:
            start = today - pd.Timedelta(days=120)
            frame = create_tushare_pro_api(token).daily(
                ts_code=self.quant_config.symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=today.strftime("%Y%m%d"),
            )
            if frame is not None and not frame.empty:
                frame.to_csv(path, index=False)
                state["daily_history_date"] = date_key
        except Exception as exc:
            logger.warning("Unable to refresh daily regime history; using cache if present: %s", exc)

    def _feature_bars(self, trade_date: pd.Timestamp) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        daily_path = self.store.root / "daily_history.csv"
        if daily_path.exists():
            daily = pd.read_csv(daily_path)
            daily["trade_date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d", errors="coerce")
            daily = daily[daily["trade_date"] < trade_date].copy()
            if not daily.empty:
                frames.append(
                    pd.DataFrame(
                        {
                            "datetime": daily["trade_date"] + pd.Timedelta(hours=15),
                            "symbol": self.quant_config.symbol,
                            "open": daily["open"],
                            "high": daily["high"],
                            "low": daily["low"],
                            "close": daily["close"],
                            "volume": pd.to_numeric(daily["vol"], errors="coerce").fillna(0.0) * 100,
                            "amount": pd.to_numeric(daily["amount"], errors="coerce").fillna(0.0) * 1000,
                        }
                    )
                )
        bars_path = self.store.root / "bars.csv"
        if bars_path.exists():
            intraday = pd.read_csv(bars_path)
            intraday["datetime"] = pd.to_datetime(intraday["datetime"], errors="coerce")
            intraday = intraday[intraday["datetime"].dt.normalize() == trade_date]
            frames.append(intraday.drop(columns=["source"], errors="ignore"))
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).sort_values("datetime").reset_index(drop=True)

    def _bar_from_quote(
        self,
        quote: UnifiedRealtimeQuote,
        quote_time: datetime,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = state.get("last_snapshot") or {}
        same_day = snapshot.get("trade_date") == quote_time.date().isoformat()
        previous_price = (
            float(snapshot.get("price", quote.open_price or quote.price))
            if same_day
            else float(quote.open_price or quote.price)
        )
        cumulative_volume = int(quote.volume or 0)
        cumulative_amount = float(quote.amount or 0.0)
        previous_volume = int(snapshot.get("volume", 0)) if same_day else 0
        previous_amount = float(snapshot.get("amount", 0.0)) if same_day else 0.0
        price = float(quote.price or 0.0)
        return {
            "datetime": _five_minute_bucket(quote_time).isoformat(),
            "symbol": self.quant_config.symbol,
            "open": previous_price,
            "high": max(previous_price, price),
            "low": min(previous_price, price),
            "close": price,
            "volume": max(cumulative_volume - previous_volume, 0),
            "amount": max(cumulative_amount - previous_amount, 0.0),
            "source": quote.source.value,
        }

    def _append_fill(self, fill: Any) -> None:
        self.store.append("trades.csv", TRADE_FIELDS, fill.to_dict())

    def _save_strategy(self, state: dict[str, Any], strategy: VwapT0Strategy) -> None:
        state["active_pair"] = _serialize_pair(strategy.active_pair)
        state["pairs_today"] = strategy.pairs_today
        state["daily_realized_pnl"] = strategy.daily_realized_pnl
        state["daily_open_equity"] = strategy.daily_open_equity
        state["peak_equity"] = strategy.peak_equity
        for pair in strategy.closed_pairs:
            self.store.append("pairs.csv", PAIR_FIELDS, pair.to_dict())
        strategy.closed_pairs.clear()

    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        if now is None:
            current = datetime.now(self.timezone)
        elif now.tzinfo is None:
            current = now.replace(tzinfo=self.timezone)
        else:
            current = now.astimezone(self.timezone)
        if current.date() < pd.Timestamp(self.config.start_date).date():
            return {"status": "waiting", "reason": "before_start_date", "next_start": self.config.start_date}
        if not _is_market_session(current):
            return {"status": "skipped", "reason": "outside_market_session", "time": current.isoformat()}
        try:
            with self.store.lock():
                return self._tick_locked(current)
        except BlockingIOError:
            return {"status": "skipped", "reason": "another_tick_is_running"}

    def _tick_locked(self, current: datetime) -> dict[str, Any]:
        state = self.store.load(self.quant_config.portfolio.initial_cash, self.config.start_date)
        quote = self.quote_provider.get_quote(self.config.symbol)
        if quote is None or not quote.has_basic_data():
            return {"status": "error", "reason": "quote_unavailable"}
        quote_time = _parse_quote_time(quote, self.timezone)
        if quote_time is None or quote_time.date() != current.date():
            return {"status": "skipped", "reason": "stale_quote", "quote_time": quote.quote_time}
        if abs((current - quote_time).total_seconds()) > 20 * 60:
            return {"status": "skipped", "reason": "quote_too_old", "quote_time": quote.quote_time}
        bucket = _five_minute_bucket(quote_time)
        if state.get("last_bucket") == bucket.isoformat():
            return {"status": "skipped", "reason": "duplicate_bucket", "bucket": bucket.isoformat()}

        trade_date = pd.Timestamp(quote_time.date())
        broker = self._broker(state)
        strategy = self._strategy(state)
        if state.get("last_trade_date") != trade_date.strftime("%Y-%m-%d"):
            broker.ledger.settle_new_day()
            strategy.on_new_day(trade_date, broker.ledger.equity(float(quote.price)))
            state["last_trade_date"] = trade_date.strftime("%Y-%m-%d")
            state["last_snapshot"] = None
            state["last_bucket"] = None
            self._refresh_daily_history(state, trade_date)

        bar_values = self._bar_from_quote(quote, quote_time, state)
        self.store.append("bars.csv", BAR_FIELDS, bar_values)
        row = SimpleNamespace(**bar_values)
        pending = _deserialize_order(state.get("pending_order"))
        state["pending_order"] = None
        if pending is not None:
            fill = broker.execute(pending, row, quote.up_limit, quote.down_limit)
            self._append_fill(fill)
            if pending.action == "initial_base" and fill.status == "filled":
                desired = int(state["target_base_shares"])
                if broker.ledger.total_shares >= desired:
                    state["base_initialized"] = True
            else:
                strategy.on_fill(fill)

        if not state.get("base_initialized"):
            target_value = self.quant_config.portfolio.initial_cash * self.quant_config.portfolio.base_ratio
            lot_size = self.quant_config.portfolio.lot_size
            desired = int(target_value / float(quote.price)) // lot_size * lot_size
            state["target_base_shares"] = max(int(state.get("target_base_shares", 0)), desired)
            remaining = max(int(state["target_base_shares"]) - broker.ledger.total_shares, 0)
            if remaining > 0:
                state["pending_order"] = _serialize_order(
                    Order("buy", remaining, "initialize_base", "initial_base", pd.Timestamp(quote_time))
                )
            else:
                state["base_initialized"] = True
        elif strategy.active_pair is not None or broker.ledger.sellable_shares >= int(state["target_base_shares"]):
            feature_bars = self._feature_bars(trade_date)
            if not feature_bars.empty:
                features = prepare_features(feature_bars, self.quant_config.strategy)
                signal_row = next(features.tail(1).itertuples(index=False))
                if self.quant_config.strategy.strategy_type == "chip_double_peak":
                    state["last_signal"] = {
                        "timestamp": pd.Timestamp(signal_row.datetime).isoformat(),
                        "double_peak": bool(signal_row.chip_double_peak),
                        "price_between_peaks": bool(signal_row.chip_price_between_peaks),
                        "lower_peak": (
                            float(signal_row.chip_lower_peak) if pd.notna(signal_row.chip_lower_peak) else None
                        ),
                        "upper_peak": (
                            float(signal_row.chip_upper_peak) if pd.notna(signal_row.chip_upper_peak) else None
                        ),
                        "valley": float(signal_row.chip_valley) if pd.notna(signal_row.chip_valley) else None,
                        "position": float(signal_row.chip_position) if pd.notna(signal_row.chip_position) else None,
                    }
                order = strategy.on_bar(signal_row, broker.ledger, int(state["target_base_shares"]))
                state["pending_order"] = _serialize_order(order)

        equity = broker.ledger.equity(float(quote.price))
        state.update(
            {
                "cash": broker.ledger.cash,
                "total_shares": broker.ledger.total_shares,
                "sellable_shares": broker.ledger.sellable_shares,
                "last_bucket": bucket.isoformat(),
                "last_snapshot": {
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "price": float(quote.price),
                    "volume": int(quote.volume or 0),
                    "amount": float(quote.amount or 0.0),
                },
                "last_price": float(quote.price),
                "last_equity": equity,
                "last_tick": current.isoformat(),
            }
        )
        self._save_strategy(state, strategy)
        self.store.append(
            "equity.csv",
            EQUITY_FIELDS,
            {
                "timestamp": quote_time.isoformat(),
                "cash": broker.ledger.cash,
                "shares": broker.ledger.total_shares,
                "sellable_shares": broker.ledger.sellable_shares,
                "price": float(quote.price),
                "equity": equity,
            },
        )
        self.store.save(state)
        return {
            "status": "ok",
            "timestamp": quote_time.isoformat(),
            "price": float(quote.price),
            "equity": equity,
            "cash": broker.ledger.cash,
            "shares": broker.ledger.total_shares,
            "sellable_shares": broker.ledger.sellable_shares,
            "base_initialized": state["base_initialized"],
            "pending_order": state["pending_order"],
            "active_pair": state["active_pair"],
            "strategy_type": self.quant_config.strategy.strategy_type,
            "last_signal": state.get("last_signal"),
        }

    def status(self) -> dict[str, Any]:
        state = self.store.load(self.quant_config.portfolio.initial_cash, self.config.start_date)
        return state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent T+0 paper trading")
    parser.add_argument("--config", required=True, help="Path to a paper trading JSON configuration")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Initialize the paper account without fetching a quote")
    subparsers.add_parser("tick", help="Run one idempotent market tick")
    subparsers.add_parser("status", help="Print the persisted account state")
    report = subparsers.add_parser("report", help="Generate this week's Markdown report")
    report.add_argument("--at", help="Optional ISO timestamp for deterministic report generation")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_paper_config(args.config)
    service = PaperTradingService(config)
    if args.command == "init":
        result = service.initialize()
    elif args.command == "tick":
        result = service.tick()
    elif args.command == "status":
        result = service.status()
    else:
        report_time = datetime.fromisoformat(args.at) if args.at else datetime.now(ZoneInfo(config.timezone))
        path = generate_weekly_report(config, report_time)
        result = {"status": "ok", "report": str(path)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
