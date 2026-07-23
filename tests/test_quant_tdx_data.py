"""Offline tests for resilient PyTDX quantitative data ingestion."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.quant.config import DataConfig, QuantConfig, load_quant_config
from src.quant.data import QuantDataBundle, QuantDataError
from src.quant.tdx_data import (
    FailoverQuantDataProvider,
    HybridQuantDataProvider,
    TdxBarPager,
    TdxCalibration,
    TdxEndpoint,
    TdxNodeSelector,
    TdxQuantDataProvider,
    TushareTdxMetadataProvider,
    calibrate_activity_units,
    evaluate_calibrated_activity,
    market_and_code,
    normalize_tdx_bars,
    validate_tdx_bars,
)


def make_five_minute_day(day: str = "2026-01-05", close: float = 10.0) -> pd.DataFrame:
    morning = pd.date_range(f"{day} 09:35", f"{day} 11:30", freq="5min")
    afternoon = pd.date_range(f"{day} 13:05", f"{day} 15:00", freq="5min")
    timestamps = morning.append(afternoon)
    return pd.DataFrame(
        {
            "datetime": timestamps,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "vol": 100.0,
            "amount": 1_000.0,
        }
    )


def empty_bundle() -> QuantDataBundle:
    return QuantDataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())


def make_config(tmp_path: Path, **data_changes: Any) -> QuantConfig:
    data = replace(
        DataConfig(),
        source="pytdx",
        fallback_source="none",
        cache_dir=str(tmp_path),
        tdx_require_cross_validation=True,
        **data_changes,
    )
    return QuantConfig(
        symbol="688008.SH",
        name="Test",
        start_date="2026-01-05",
        end_date="2026-01-05",
        output_dir=str(tmp_path / "reports"),
        data=data,
    )


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("688008.SH", (1, "688008")),
        ("600519", (1, "600519")),
        ("300750.SZ", (0, "300750")),
        ("000001", (0, "000001")),
    ],
)
def test_market_and_code_maps_a_share_symbols(symbol: str, expected: tuple[int, str]):
    assert market_and_code(symbol) == expected


def test_market_and_code_rejects_non_a_share_symbol():
    with pytest.raises(QuantDataError, match="six-digit"):
        market_and_code("AAPL")


class ProbeApi:
    def __init__(self, calls: list[str]):
        self.calls = calls
        self.host = ""

    def connect(self, ip: str, port: int, time_out: float = 5.0) -> bool:
        del port, time_out
        self.host = ip
        self.calls.append(ip)
        if ip == "slow":
            time.sleep(0.02)
        return ip != "down"

    def disconnect(self) -> None:
        return None

    def get_security_count(self, market: int) -> int:
        del market
        return 1


def test_node_selector_ranks_healthy_nodes_and_reuses_cache(tmp_path: Path):
    calls: list[str] = []
    endpoints = [
        TdxEndpoint("slow", "slow", 7709),
        TdxEndpoint("fast", "fast", 7709),
        TdxEndpoint("down", "down", 7709),
    ]
    selector = TdxNodeSelector(
        tmp_path / "nodes.json",
        endpoints=endpoints,
        api_factory=lambda: ProbeApi(calls),
        max_workers=3,
    )

    ranked = selector.rank()

    assert [node.host for node in ranked] == ["fast", "slow"]
    assert sorted(calls) == ["down", "fast", "slow"]
    cached_selector = TdxNodeSelector(
        tmp_path / "nodes.json",
        endpoints=endpoints,
        api_factory=lambda: pytest.fail("A valid node cache should avoid probing"),
    )
    assert [node.host for node in cached_selector.rank()] == ["fast", "slow"]

    payload = json.loads((tmp_path / "nodes.json").read_text(encoding="utf-8"))
    payload["checked_at"] = 0
    (tmp_path / "nodes.json").write_text(json.dumps(payload), encoding="utf-8")
    expired_calls: list[str] = []
    expired_selector = TdxNodeSelector(
        tmp_path / "nodes.json",
        endpoints=[TdxEndpoint("fast", "fast", 7709)],
        api_factory=lambda: ProbeApi(expired_calls),
        cache_ttl_seconds=1,
        clock=lambda: 100.0,
    )
    expired_selector.rank()
    assert expired_calls == ["fast"]


class StaticSelector:
    def rank(self) -> list[TdxEndpoint]:
        return [TdxEndpoint("first", "first", 7709), TdxEndpoint("second", "second", 7709)]


class PagingApi:
    def __init__(self, bars: list[dict[str, Any]], fail_at_offset: int | None = None):
        self.bars = bars
        self.fail_at_offset = fail_at_offset
        self.calls: list[int] = []

    def connect(self, ip: str, port: int, time_out: float = 5.0) -> bool:
        del ip, port, time_out
        return True

    def disconnect(self) -> None:
        return None

    def get_security_bars(self, category: int, market: int, code: str, start: int, count: int) -> Any:
        del category, market, code
        self.calls.append(start)
        if self.fail_at_offset == start:
            raise RuntimeError("simulated node failure")
        return self.bars[start : start + count]


def test_bar_pager_continues_same_offset_after_node_failure():
    timestamps = pd.date_range("2026-01-05 09:35", periods=5, freq="5min")[::-1]
    bars = [
        {"datetime": str(timestamp), "open": 10, "high": 10, "low": 10, "close": 10, "vol": 1, "amount": 10}
        for timestamp in timestamps
    ]
    first = PagingApi(bars, fail_at_offset=2)
    second = PagingApi(bars)
    apis = iter([first, second])
    pager = TdxBarPager(StaticSelector(), api_factory=lambda: next(apis), page_size=2, max_pages=10)

    result = pager.fetch("688008.SH", "5min", "2026-01-05", "2026-01-05")

    assert len(result) == 5
    assert first.calls == [0, 2]
    assert second.calls == [2, 4]
    assert result["datetime"].is_monotonic_increasing


def test_activity_calibration_matches_tushare_units():
    raw = make_five_minute_day().iloc[:2].copy()
    raw["vol"] = 5.0
    raw["amount"] = 500.0
    bars = normalize_tdx_bars(raw, "688008.SH")
    reference = pd.DataFrame(
        {"trade_date": ["20260105"], "close": [10.0], "vol": [10.0], "amount": [1.0]}
    )

    calibrated, report = calibrate_activity_units(bars, reference)

    assert report.volume_multiplier == 100.0
    assert report.amount_multiplier == 1.0
    assert calibrated["volume"].sum() == 1_000.0
    assert calibrated["amount"].sum() == 1_000.0


def test_incremental_calibration_evaluates_complete_cached_history():
    first = normalize_tdx_bars(make_five_minute_day("2026-01-05"), "688008.SH")
    second = normalize_tdx_bars(make_five_minute_day("2026-01-06"), "688008.SH")
    bars = pd.concat([first, second], ignore_index=True)
    reference = pd.DataFrame(
        {
            "trade_date": ["20260105", "20260106"],
            "close": [10.0, 10.0],
            "vol": [48.0, 48.0],
            "amount": [48.0, 48.0],
        }
    )
    unit_calibration = TdxCalibration(1.0, 1.0, 1, 0.0, 0.0, 0.0, reference_days=2)

    report = evaluate_calibrated_activity(bars, reference, unit_calibration)

    assert report.matched_days == 2
    assert report.reference_days == 2
    assert report.volume_multiplier == 1.0
    assert report.volume_error_median == 0.0


def test_quality_validation_fails_on_source_duplicates_and_close_mismatch():
    bars = normalize_tdx_bars(make_five_minute_day(), "688008.SH")
    bars.loc[0, "high"] = 9.0
    calibration = TdxCalibration(1.0, 1.0, 1, 0.01, 0.0, 0.0)

    report = validate_tdx_bars(
        bars,
        "5min",
        calibration,
        source_duplicate_count=1,
        close_tolerance_bps=20,
    )

    assert not report.passed
    assert report.duplicate_count == 1
    assert "duplicate_timestamps" in report.failures
    assert "invalid_ohlc" in report.failures
    assert "cross_source_close_mismatch" in report.failures


def test_quality_validation_counts_missing_reference_trading_days():
    bars = normalize_tdx_bars(make_five_minute_day(), "688008.SH")
    calibration = TdxCalibration(1.0, 1.0, 1, 0.0, 0.0, 0.0, reference_days=2)

    report = validate_tdx_bars(bars, "5min", calibration, min_complete_day_ratio=0.95)

    assert report.complete_day_ratio == 0.5
    assert "incomplete_sessions" in report.failures


class FakePager:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.calls: list[tuple[str, str]] = []

    def fetch(self, symbol: str, frequency: str, start_date: str, end_date: str) -> pd.DataFrame:
        del symbol, frequency
        self.calls.append((start_date, end_date))
        return self.frame.copy()


class FakeMetadataProvider:
    def fetch(self, symbol: str, start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
        del symbol, start_date, end_date
        return {
            "limits": pd.DataFrame(
                {"trade_date": ["20260105"], "up_limit": [12.0], "down_limit": [8.0]}
            ),
            "adjustments": pd.DataFrame({"trade_date": ["20260105"], "adj_factor": [1.0]}),
            "dividends": pd.DataFrame(columns=["ex_date", "cash_div", "stock_ratio"]),
            "reference_daily": pd.DataFrame(
                {"trade_date": ["20260105"], "close": [10.0], "vol": [48.0], "amount": [48.0]}
            ),
        }


def test_provider_writes_validated_cache_and_reuses_historical_partition(tmp_path: Path):
    pager = FakePager(make_five_minute_day())
    provider = TdxQuantDataProvider(
        make_config(tmp_path),
        pager=pager,
        metadata_provider=FakeMetadataProvider(),
    )

    first = provider.fetch()
    second = provider.fetch()

    assert len(first.bars) == 48
    assert len(second.bars) == 48
    assert pager.calls == [("2026-01-05", "2026-01-05")]
    quality = json.loads((provider.cache_root / "quality.json").read_text(encoding="utf-8"))
    assert quality["quality"]["passed"] is True
    assert quality["request"]["symbol"] == "688008.SH"


class FailingProvider:
    def fetch(self, force: bool = False) -> QuantDataBundle:
        del force
        raise QuantDataError("primary unavailable")

    def load(self) -> QuantDataBundle:
        raise QuantDataError("primary cache unavailable")


class SuccessfulProvider:
    def fetch(self, force: bool = False) -> QuantDataBundle:
        del force
        return empty_bundle()

    def load(self) -> QuantDataBundle:
        return empty_bundle()


def test_failover_provider_records_actual_source_and_error(tmp_path: Path):
    status_path = tmp_path / "active_source.json"
    provider = FailoverQuantDataProvider(
        FailingProvider(),
        SuccessfulProvider(),
        status_path,
        fallback_name="tushare",
    )

    provider.fetch()

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["source"] == "tushare"
    assert "primary unavailable" in status["error"]


class RetryingApi:
    def __init__(self):
        self.attempts = 0

    def daily(self, **kwargs: Any) -> pd.DataFrame:
        del kwargs
        self.attempts += 1
        if self.attempts < 3:
            raise RuntimeError("temporary error")
        return pd.DataFrame({"trade_date": ["20260105"]})


def test_metadata_provider_retries_with_bounded_backoff():
    api = RetryingApi()
    delays: list[float] = []
    provider = TushareTdxMetadataProvider(api=api, max_attempts=3, sleep=delays.append)

    result = provider._call("daily", ts_code="688008.SH")

    assert len(result) == 1
    assert api.attempts == 3
    assert delays == [1, 2]


def test_config_accepts_pytdx_and_rejects_oversized_pages(tmp_path: Path):
    config_path = tmp_path / "config.json"
    payload = {
        "symbol": "688008.SH",
        "start_date": "2026-01-05",
        "end_date": "2026-01-05",
        "output_dir": str(tmp_path / "reports"),
        "data": {"source": "pytdx", "fallback_source": "none", "tdx_page_size": 800},
    }
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_quant_config(config_path).data.source == "pytdx"

    payload["data"]["tdx_page_size"] = 801
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="between 1 and 800"):
        load_quant_config(config_path)


class BundleProvider:
    def __init__(self, bundle: QuantDataBundle):
        self.bundle = bundle
        self.fetch_calls = 0

    def fetch(self, force: bool = False) -> QuantDataBundle:
        del force
        self.fetch_calls += 1
        return self.bundle

    def load(self) -> QuantDataBundle:
        return self.bundle


def make_single_bar_bundle(timestamp: str) -> QuantDataBundle:
    bars = pd.DataFrame(
        {
            "datetime": [pd.Timestamp(timestamp)],
            "symbol": ["688008.SH"],
            "open": [10.0],
            "high": [10.0],
            "low": [10.0],
            "close": [10.0],
            "volume": [100.0],
            "amount": [1_000.0],
        }
    )
    return QuantDataBundle(bars, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())


def make_hybrid_config(tmp_path: Path) -> QuantConfig:
    data = replace(
        DataConfig(),
        source="hybrid",
        fallback_source="none",
        cache_dir=str(tmp_path),
        tdx_history_start_date="2026-03-01",
    )
    return QuantConfig(
        symbol="688008.SH",
        name="Hybrid Test",
        start_date="2026-01-01",
        end_date="2026-03-31",
        output_dir=str(tmp_path / "reports"),
        data=data,
    )


def test_hybrid_provider_rejects_incomplete_historical_months(tmp_path: Path):
    historical = BundleProvider(make_single_bar_bundle("2026-01-05 09:35"))
    recent = BundleProvider(make_single_bar_bundle("2026-03-02 09:35"))
    provider = HybridQuantDataProvider(make_hybrid_config(tmp_path), historical, recent)

    with pytest.raises(QuantDataError, match=r"2 month\(s\) missing"):
        provider.fetch()

    assert historical.fetch_calls == 1
    assert recent.fetch_calls == 0
    status = json.loads(provider.status_path.read_text(encoding="utf-8"))
    assert status["ready_for_research"] is False


def test_hybrid_provider_merges_history_and_recent_data(tmp_path: Path):
    historical = BundleProvider(make_single_bar_bundle("2026-02-27 15:00"))
    recent = BundleProvider(make_single_bar_bundle("2026-03-02 09:35"))
    provider = HybridQuantDataProvider(make_hybrid_config(tmp_path), historical, recent)
    provider.cache_root.mkdir(parents=True, exist_ok=True)
    for month in ("2026-01", "2026-02"):
        (provider.cache_root / f"bars_{month}.parquet").touch()

    bundle = provider.fetch()

    assert len(bundle.bars) == 2
    assert bundle.bars["datetime"].is_monotonic_increasing
    status = json.loads(provider.status_path.read_text(encoding="utf-8"))
    assert status["ready_for_research"] is True
