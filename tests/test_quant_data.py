"""Tests for minute and corporate-action normalization."""

import pandas as pd
import pytest

from src.quant.config import DataConfig, QuantConfig
from src.quant.data import (
    QuantDataBundle,
    QuantDataError,
    TushareMinuteDataProvider,
    ensure_bundle_coverage,
    normalize_dividends,
    normalize_minute_bars,
)


def test_normalize_minute_bars_sorts_deduplicates_and_filters_sessions():
    frame = pd.DataFrame(
        {
            "trade_time": ["2026-01-05 10:00", "2026-01-05 09:00", "2026-01-05 10:00"],
            "ts_code": ["688008.SH"] * 3,
            "open": [100, 99, 101],
            "high": [101, 100, 102],
            "low": [99, 98, 100],
            "close": [100, 99, 101],
            "vol": [1000, 1000, 2000],
            "amount": [100000, 99000, 202000],
        }
    )
    result = normalize_minute_bars(frame, "688008.SH")

    assert len(result) == 1
    assert result.iloc[0]["close"] == 101
    assert result.iloc[0]["volume"] == 2000


def test_normalize_dividends_aggregates_implemented_plan():
    frame = pd.DataFrame(
        {
            "ex_date": ["20260105", "20260105", "20260201"],
            "div_proc": ["实施", "预案", "实施"],
            "cash_div_tax": [0.2, 0.8, 0.1],
            "stk_div": [0.1, 0.5, 0.0],
        }
    )
    result = normalize_dividends(frame)

    assert len(result) == 2
    first = result[result["ex_date"] == pd.Timestamp("2026-01-05")].iloc[0]
    assert first["cash_div"] == 0.2
    assert first["stock_ratio"] == 0.1


def test_coverage_check_rejects_partial_cache_before_optimization():
    bars = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2020-07-01 09:30", "2020-07-31 15:00"]),
            "open": [1, 1],
            "high": [1, 1],
            "low": [1, 1],
            "close": [1, 1],
            "volume": [1, 1],
            "amount": [1, 1],
            "symbol": ["688008.SH", "688008.SH"],
        }
    )
    bundle = QuantDataBundle(bars, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    with pytest.raises(QuantDataError, match="ends at"):
        ensure_bundle_coverage(bundle, "2020-07-01", "2026-06-30")


class DailyQuotaApi:
    def __init__(self):
        self.calls = 0

    def stk_mins(self, **kwargs):
        del kwargs
        self.calls += 1
        raise RuntimeError("接口频率超限(2次/天)")


def test_tushare_daily_quota_exhaustion_fails_without_sleeping(tmp_path):
    config = QuantConfig(
        symbol="688008.SH",
        name="Test",
        start_date="2026-01-01",
        end_date="2026-01-31",
        output_dir=str(tmp_path / "reports"),
        data=DataConfig(cache_dir=str(tmp_path)),
    )
    api = DailyQuotaApi()
    provider = TushareMinuteDataProvider(config, api=api)

    with pytest.raises(QuantDataError, match="quota is exhausted"):
        provider._call("stk_mins", ts_code="688008.SH")

    assert api.calls == 1
