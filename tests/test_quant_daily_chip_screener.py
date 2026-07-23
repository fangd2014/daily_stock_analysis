"""Tests for the nightly market-wide chip screening pipeline."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.quant.daily_chip_screener import (
    DailyChipScreenConfig,
    FeishuGuidanceNotifier,
    _main_net_inflow,
    evaluate_market,
    run_nightly_pipeline,
    select_top_ten,
)


def _config(tmp_path: Path) -> DailyChipScreenConfig:
    return DailyChipScreenConfig(
        top_n=10,
        lookback_days=60,
        calendar_days=150,
        selection_buy_position_max=0.45,
        min_average_amount_20d=100_000_000,
        min_sector_members=3,
        exclude_st=True,
        output_dir=str(tmp_path / "reports"),
        cache_dir=str(tmp_path / "cache"),
    )


def _market_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2026-04-28", periods=61)
    basic_rows = []
    daily_rows = []
    flow_rows = []
    for stock_index in range(12):
        symbol = f"{stock_index + 1:06d}.SZ"
        basic_rows.append(
            {
                "ts_code": symbol,
                "name": f"Name {stock_index + 1}",
                "industry": "Rising Tech",
            }
        )
        for history_index, trade_date in enumerate(dates[:-1]):
            if history_index % 3 == 0:
                price, volume = 90.0, 1_000_000
            elif history_index % 3 == 1:
                price, volume = 110.0, 900_000
            else:
                price, volume = 100.0, 20_000
            daily_rows.append(
                {
                    "ts_code": symbol,
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "high": price + 0.1,
                    "low": price - 0.1,
                    "close": price,
                    "pct_chg": 1.0,
                    "vol": volume,
                    "amount": 200_000,
                }
            )
        signal_price = 91.0 + stock_index * 0.15
        daily_rows.append(
            {
                "ts_code": symbol,
                "trade_date": dates[-1].strftime("%Y%m%d"),
                "high": signal_price + 0.1,
                "low": signal_price - 0.1,
                "close": signal_price,
                "pct_chg": 1.0,
                "vol": 1_000_000,
                "amount": 200_000,
            }
        )
        positive = stock_index < 10
        flow_rows.append(
            {
                "ts_code": symbol,
                "trade_date": dates[-1].strftime("%Y%m%d"),
                "buy_lg_amount": 100.0 if positive else 10.0,
                "sell_lg_amount": 10.0 if positive else 100.0,
                "buy_elg_amount": 50.0,
                "sell_elg_amount": 10.0,
            }
        )
    return pd.DataFrame(basic_rows), pd.DataFrame(daily_rows), pd.DataFrame(flow_rows)


class StaticProvider:
    def __init__(self, frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]):
        self.frames = frames

    def load(self, as_of: date, config: DailyChipScreenConfig):
        return self.frames


class RecordingGenerator:
    def __init__(self):
        self.symbols = []

    def analyze(self, stock, trade_date):
        self.symbols.append(stock["symbol"])
        return f"{stock['symbol']} next-session plan"


class RecordingNotifier:
    def __init__(self):
        self.messages = []

    def send(self, content):
        self.messages.append(content)
        return True


def test_main_fund_flow_uses_large_and_extra_large_orders():
    flow = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "buy_lg_amount": 100.0,
                "sell_lg_amount": 30.0,
                "buy_elg_amount": 50.0,
                "sell_elg_amount": 10.0,
            }
        ]
    )

    result = _main_net_inflow(flow)

    assert result.iloc[0]["main_net_inflow"] == 110.0


def test_market_screen_requires_rising_sector_positive_main_flow_and_buy_zone(tmp_path):
    results, trade_date = evaluate_market(_config(tmp_path), *_market_frames())
    selected = select_top_ten(results)

    assert trade_date == "20260721"
    assert len(selected) == 10
    assert [item.symbol for item in selected] == [f"{index:06d}.SZ" for index in range(1, 11)]
    assert all(item.sector_pct_chg > 0 for item in selected)
    assert all(item.main_net_inflow > 0 for item in selected)
    assert all(item.chip_position <= 0.45 for item in selected)
    assert all(
        selected[index].distance_to_lower_pct <= selected[index + 1].distance_to_lower_pct
        for index in range(len(selected) - 1)
    )
    assert {item.reason for item in results if not item.eligible} == {"main_funds_not_net_buying"}


def test_falling_sector_is_rejected_even_when_funds_are_positive(tmp_path):
    basic, daily, flow = _market_frames()
    latest_date = daily["trade_date"].max()
    daily.loc[daily["trade_date"] == latest_date, "pct_chg"] = -1.0

    results, _ = evaluate_market(_config(tmp_path), basic, daily, flow)

    assert not any(item.eligible for item in results)
    assert {item.reason for item in results} == {"sector_not_up"}


def test_nightly_pipeline_analyzes_all_ten_and_pushes_one_feishu_report(tmp_path):
    config = _config(tmp_path)
    generator = RecordingGenerator()
    notifier = RecordingNotifier()

    summary = run_nightly_pipeline(
        config,
        StaticProvider(_market_frames()),
        generator,
        notifier,
        date(2026, 7, 21),
    )

    assert summary["selected_count"] == 10
    assert summary["guidance_count"] == 10
    assert summary["feishu_pushed"] is True
    assert len(generator.symbols) == 10
    assert len(notifier.messages) == 1
    assert "次日模拟操作指导" in notifier.messages[0]
    assert (Path(config.output_dir) / "latest_guidance.md").exists()

    repeated = run_nightly_pipeline(
        config,
        StaticProvider(_market_frames()),
        generator,
        notifier,
        date(2026, 7, 21),
    )

    assert repeated["status"] == "skipped"
    assert repeated["reason"] == "trade_date_already_pushed"
    assert len(generator.symbols) == 10
    assert len(notifier.messages) == 1


def test_top_ten_never_backfills_an_ineligible_stock(tmp_path):
    results, _ = evaluate_market(_config(tmp_path), *_market_frames())
    eligible = [item for item in results if item.eligible]

    with pytest.raises(ValueError, match="10 are required"):
        select_top_ten([replace(item, eligible=False) for item in eligible[:1]] + eligible[1:9])


def test_feishu_notifier_sends_interactive_card_without_exposing_webhook(monkeypatch):
    import requests

    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"code": 0}

    def fake_post(url, json, timeout):
        captured.update({"url": url, "payload": json, "timeout": timeout})
        return Response()

    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/test-secret"
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", webhook)
    monkeypatch.setattr(requests, "post", fake_post)

    sent = FeishuGuidanceNotifier().send("# Test guidance")

    assert sent is True
    assert captured["url"] == webhook
    assert captured["payload"]["msg_type"] == "interactive"
    assert webhook not in str(captured["payload"])
