"""Tests for nightly hot-sector commander screening."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.quant.hot_sector_screener import (
    HotSectorScreenConfig,
    evaluate_hot_sectors,
    run_hot_sector_pipeline,
    select_commanders,
)


def make_config(tmp_path: Path) -> HotSectorScreenConfig:
    return HotSectorScreenConfig(
        top_n=10,
        hot_sector_count=4,
        max_per_sector=3,
        lookback_days=60,
        calendar_days=150,
        min_sector_members=5,
        min_sector_pct_chg=0.8,
        min_sector_breadth=0.6,
        min_average_amount_20d=100_000_000.0,
        min_amount_ratio=1.0,
        max_stock_pct_chg=7.0,
        max_distance_ma20=0.08,
        exclude_st=True,
        output_dir=str(tmp_path / "reports"),
        cache_dir=str(tmp_path / "cache"),
    )


def make_market_frames():
    dates = pd.bdate_range("2026-04-28", periods=61)
    basic = []
    daily = []
    flow = []
    for sector_index in range(4):
        industry = f"Hot Sector {sector_index + 1}"
        for member_index in range(6):
            number = sector_index * 6 + member_index + 1
            symbol = f"{number:06d}.SZ"
            basic.append({"ts_code": symbol, "name": f"Commander {number}", "industry": industry})
            trend = np.linspace(80.0 + sector_index, 100.0 + sector_index, len(dates))
            for session_index, (trade_date, close) in enumerate(zip(dates, trend)):
                latest = session_index == len(dates) - 1
                amount = 260_000.0 + member_index * 30_000.0 if latest else 200_000.0
                daily.append(
                    {
                        "ts_code": symbol,
                        "trade_date": trade_date.strftime("%Y%m%d"),
                        "open": close * 0.995,
                        "high": close * 1.01,
                        "low": close * 0.99,
                        "close": close,
                        "pct_chg": 2.0 if latest else 0.3,
                        "vol": 1_000_000.0,
                        "amount": amount,
                    }
                )
            flow.append(
                {
                    "ts_code": symbol,
                    "trade_date": dates[-1].strftime("%Y%m%d"),
                    "buy_lg_amount": 2_000.0,
                    "buy_elg_amount": 1_000.0,
                    "sell_lg_amount": 500.0,
                    "sell_elg_amount": 300.0,
                }
            )
    return pd.DataFrame(basic), pd.DataFrame(daily), pd.DataFrame(flow)


class StaticProvider:
    def __init__(self, frames):
        self.frames = frames

    def load(self, as_of, config):
        return self.frames


class StaticGenerator:
    def analyze(self, stock, trade_date):
        return f"{stock['symbol']} 回踩买入，高开不追，跌破止损退出。"


class RecordingNotifier:
    def __init__(self):
        self.messages = []

    def send(self, content):
        self.messages.append(content)
        return True


def test_hot_sector_screen_selects_ten_liquid_commanders_with_sector_cap(tmp_path):
    candidates, sectors, trade_date = evaluate_hot_sectors(make_config(tmp_path), *make_market_frames())
    selected = select_commanders(candidates, top_n=10, max_per_sector=3)

    assert trade_date == "20260721"
    assert len(sectors) == 4
    assert len(selected) == 10
    assert all(candidate.eligible for candidate in selected)
    assert all(candidate.main_net_inflow > 0 for candidate in selected)
    assert max(sum(item.industry == industry for item in selected) for industry in sectors["industry"]) <= 3
    assert all("热度得分" in candidate.selection_reason for candidate in selected)


def test_hot_sector_pipeline_writes_reasons_logs_progress_and_pushes_one_feishu_message(tmp_path, caplog):
    notifier = RecordingNotifier()
    caplog.set_level("INFO", logger="src.quant.hot_sector_screener")

    result = run_hot_sector_pipeline(
        make_config(tmp_path),
        StaticProvider(make_market_frames()),
        StaticGenerator(),
        notifier,
        date(2026, 7, 21),
    )

    report = Path(result["guidance_report"])
    content = report.read_text(encoding="utf-8")
    assert result["selected_count"] == 10
    assert result["feishu_pushed"] is True
    assert len(notifier.messages) == 1
    assert "选股理由" in content
    assert "观察买入区" in content
    assert "高开超过3%不追" in content
    assert "板块门槛完成" in caplog.text
    assert "中军门槛完成" in caplog.text
    assert "候选排序完成" in caplog.text
    assert "入选#1" in caplog.text
    assert "飞书推送成功" in caplog.text

    repeated = run_hot_sector_pipeline(
        make_config(tmp_path),
        StaticProvider(make_market_frames()),
        StaticGenerator(),
        notifier,
        date(2026, 7, 21),
    )
    assert repeated["status"] == "skipped"
    assert len(notifier.messages) == 1
