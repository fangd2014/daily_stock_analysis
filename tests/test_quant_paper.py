"""Deterministic tests for the persistent paper-trading service."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pandas as pd

from data_provider.akshare_fetcher import _parse_tencent_stock_quote
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote
from src.quant import paper as paper_module
from src.quant.paper import PaperTradingService
from src.quant.paper_config import PaperConfig
from src.quant.paper_report import generate_weekly_report


class SequenceQuoteProvider:
    def __init__(self, quotes: list[UnifiedRealtimeQuote]):
        self.quotes = quotes
        self.calls = 0

    def get_quote(self, symbol: str) -> UnifiedRealtimeQuote:
        quote = self.quotes[min(self.calls, len(self.quotes) - 1)]
        self.calls += 1
        return quote


def make_quote(timestamp: str, volume: int = 1_000_000, price: float = 100.0) -> UnifiedRealtimeQuote:
    return UnifiedRealtimeQuote(
        code="688008",
        name="Montage Technology",
        source=RealtimeSource.TENCENT,
        price=price,
        volume=volume,
        amount=volume * price,
        open_price=price,
        high=price + 1,
        low=price - 1,
        pre_close=price,
        up_limit=120.0,
        down_limit=80.0,
        quote_time=timestamp,
    )


def make_config(tmp_path: Path) -> PaperConfig:
    quant_path = tmp_path / "quant.json"
    quant_path.write_text(
        json.dumps(
            {
                "symbol": "688008.SH",
                "name": "Montage Technology",
                "start_date": "2020-07-01",
                "end_date": "2026-06-30",
                "output_dir": str(tmp_path / "backtest"),
                "data": {"source": "local", "local_path": str(tmp_path / "unused.csv"), "frequency": "5min"},
                "portfolio": {
                    "initial_cash": 1_000_000,
                    "base_ratio": 0.6,
                    "lot_size": 100,
                    "max_volume_participation": 0.05,
                },
            }
        ),
        encoding="utf-8",
    )
    return PaperConfig(
        symbol="688008",
        name="Montage Technology",
        start_date="2026-07-21",
        quant_config=str(quant_path),
        state_dir=str(tmp_path / "state"),
        report_dir=str(tmp_path / "reports"),
    )


def make_service(
    tmp_path: Path, quotes: list[UnifiedRealtimeQuote]
) -> tuple[PaperTradingService, SequenceQuoteProvider]:
    provider = SequenceQuoteProvider(quotes)
    service = PaperTradingService(make_config(tmp_path), provider)
    service._refresh_daily_history = lambda state, today: None
    return service, provider


def test_tencent_parser_maps_volume_amount_prices_limits_and_time():
    fields = [""] * 50
    fields[1] = "澜起科技"
    fields[3] = "189.94"
    fields[4] = "183.60"
    fields[5] = "195.00"
    fields[30] = "20260720161500"
    fields[31] = "6.34"
    fields[32] = "3.45"
    fields[33] = "198.00"
    fields[34] = "180.25"
    fields[36] = "77142386"
    fields[37] = "1454517.5594"
    fields[47] = "220.32"
    fields[48] = "146.88"

    quote = _parse_tencent_stock_quote("688008", fields)

    assert quote.volume == 77_142_386
    assert quote.amount == 14_545_175_594
    assert quote.high == 198.0
    assert quote.low == 180.25
    assert quote.up_limit == 220.32
    assert quote.down_limit == 146.88
    assert quote.quote_time == "20260720161500"


def test_pre_start_and_outside_session_do_not_fetch_quotes(tmp_path):
    service, provider = make_service(tmp_path, [make_quote("20260721093000")])

    waiting = service.tick(datetime(2026, 7, 20, 10, 0))
    skipped = service.tick(datetime(2026, 7, 21, 12, 0))

    assert waiting["reason"] == "before_start_date"
    assert skipped["reason"] == "outside_market_session"
    assert provider.calls == 0


def test_base_purchase_is_queued_filled_and_settled_next_day(tmp_path):
    quotes = [
        make_quote("20260721093000", volume=1_000_000),
        make_quote("20260721093500", volume=2_000_000),
        make_quote("20260721093500", volume=2_000_000),
        make_quote("20260722093000", volume=1_000_000),
    ]
    service, _ = make_service(tmp_path, quotes)

    first = service.tick(datetime(2026, 7, 21, 9, 30))
    second = service.tick(datetime(2026, 7, 21, 9, 35))
    duplicate = service.tick(datetime(2026, 7, 21, 9, 36))
    next_day = service.tick(datetime(2026, 7, 22, 9, 30))

    assert first["shares"] == 0
    assert first["pending_order"]["action"] == "initial_base"
    assert second["shares"] == 6_000
    assert second["sellable_shares"] == 0
    assert second["base_initialized"] is True
    assert duplicate["reason"] == "duplicate_bucket"
    assert next_day["shares"] == 6_000
    assert next_day["sellable_shares"] == 6_000


def test_initial_base_waits_when_next_session_is_no_longer_buy_ready(tmp_path):
    quote = replace(make_quote("20260721093000", price=104.0), pre_close=100.0)
    config = replace(
        make_config(tmp_path),
        selection_lower_peak=90.0,
        selection_upper_peak=110.0,
        selection_buy_position_max=0.45,
        max_initial_entry_gap_pct=0.03,
    )
    service = PaperTradingService(config, SequenceQuoteProvider([quote]))
    service._refresh_daily_history = lambda state, today: None

    result = service.tick(datetime(2026, 7, 21, 9, 30))

    assert result["shares"] == 0
    assert result["pending_order"] is None
    assert result["last_signal"]["initial_base_allowed"] is False
    assert result["last_signal"]["reason"] == "entry_gap_too_high"


def test_initial_base_waits_above_selected_chip_buy_zone(tmp_path):
    quote = replace(make_quote("20260721093000", price=100.0), pre_close=100.0)
    config = replace(
        make_config(tmp_path),
        selection_lower_peak=90.0,
        selection_upper_peak=110.0,
        selection_buy_position_max=0.45,
    )
    service = PaperTradingService(config, SequenceQuoteProvider([quote]))
    service._refresh_daily_history = lambda state, today: None

    result = service.tick(datetime(2026, 7, 21, 9, 30))

    assert result["pending_order"] is None
    assert result["last_signal"]["reason"] == "price_above_selection_buy_zone"


def test_stale_quote_is_rejected_without_initializing_state(tmp_path):
    service, _ = make_service(tmp_path, [make_quote("20260720150000")])

    result = service.tick(datetime(2026, 7, 21, 9, 30))

    assert result["reason"] == "stale_quote"
    assert not service.store.state_path.exists()


def test_active_high_sell_pair_can_queue_repurchase_below_base_inventory(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, [make_quote("20260722093500", volume=2_000_000)])
    state = service.initialize()
    state.update(
        {
            "cash": 520_000.0,
            "total_shares": 4_800,
            "sellable_shares": 4_800,
            "target_base_shares": 6_000,
            "base_initialized": True,
            "last_trade_date": "2026-07-22",
            "last_bucket": "2026-07-22T09:30:00+08:00",
            "last_snapshot": {
                "trade_date": "2026-07-22",
                "price": 100.0,
                "volume": 1_000_000,
                "amount": 100_000_000.0,
            },
            "active_pair": {
                "direction": "high_sell",
                "entry_time": "2026-07-22T09:30:00+08:00",
                "entry_price": 101.0,
                "original_quantity": 1_200,
                "remaining_quantity": 1_200,
                "cash_flow": 121_000.0,
                "holding_bars": 0,
                "exit_reason": "",
            },
        }
    )
    service.store.save(state)
    service._feature_bars = lambda trade_date: pd.DataFrame([{"datetime": "2026-07-22 09:35:00"}])
    monkeypatch.setattr(
        paper_module,
        "prepare_features",
        lambda bars, strategy: pd.DataFrame(
            [
                {
                    "datetime": "2026-07-22 09:35:00",
                    "close": 100.0,
                    "zscore": 0.0,
                    "vwap": 100.0,
                    "vwap_drift": 0.0,
                    "prior_regime_allowed": True,
                }
            ]
        ),
    )

    result = service.tick(datetime(2026, 7, 22, 9, 35))

    assert result["pending_order"]["side"] == "buy"
    assert result["pending_order"]["action"] == "exit"


def test_weekly_report_handles_timezone_aware_journals(tmp_path):
    quotes = [
        make_quote("20260721093000", volume=1_000_000),
        make_quote("20260721093500", volume=2_000_000),
    ]
    service, _ = make_service(tmp_path, quotes)
    service.initialize()
    service.tick(datetime(2026, 7, 21, 9, 30))
    service.tick(datetime(2026, 7, 21, 9, 35))

    report_path = generate_weekly_report(service.config, datetime(2026, 7, 24, 15, 20))
    content = report_path.read_text(encoding="utf-8")

    assert "2026-07-20 至 2026-07-24" in content
    assert "模拟成交：1 笔" in content
    assert "该账户为模拟盘" in content
    assert (Path(service.config.report_dir) / "latest.md").exists()
