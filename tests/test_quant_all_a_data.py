"""Tests for the point-in-time all-A daily cache."""

import pandas as pd
import pytest

from src.quant.all_a_data import TushareAllADailyProvider
from src.quant.config import DataConfig, PortfolioConfig, QuantConfig, UniverseConfig


class FakeAllAApi:
    def __init__(self) -> None:
        self.daily_calls = []
        self.limit_calls = []
        self.universe_calls = []

    def trade_cal(self, **kwargs):
        return pd.DataFrame({"cal_date": ["20230703", "20230704"]})

    def daily(self, trade_date: str, **kwargs):
        self.daily_calls.append(trade_date)
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "trade_date": [trade_date, trade_date],
                "open": [10.0, 20.0],
                "high": [10.5, 20.5],
                "low": [9.8, 19.8],
                "close": [10.2, 20.2],
                "pre_close": [10.0, 20.0],
                "pct_chg": [2.0, 1.0],
                "vol": [1_000.0, 2_000.0],
                "amount": [10_000.0, 40_000.0],
            }
        )

    def stk_limit(self, trade_date: str, **kwargs):
        self.limit_calls.append(trade_date)
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "trade_date": [trade_date, trade_date],
                "pre_close": [10.0, 20.0],
                "up_limit": [11.0, 22.0],
                "down_limit": [9.0, 18.0],
            }
        )

    def bak_basic(self, trade_date: str, **kwargs):
        self.universe_calls.append(trade_date)
        return pd.DataFrame(
            {
                "trade_date": [trade_date, trade_date, trade_date],
                "ts_code": ["000001.SZ", "600000.SH", "900001.SH"],
                "name": ["Alpha", "Beta", "B Share"],
                "industry": ["Bank", "Bank", "Other"],
                "list_date": ["19910403", "19991110", "19920101"],
                "total_share": [1.0, 2.0, 3.0],
                "float_share": [1.0, 2.0, 3.0],
            }
        )


class FakeReconstructedUniverseApi(FakeAllAApi):
    def bak_basic(self, trade_date: str, **kwargs):
        raise RuntimeError("抱歉，您没有接口(bak_basic)访问权限")

    def stock_basic(self, list_status: str, **kwargs):
        if list_status == "L":
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "300001.SZ"],
                    "name": ["Alpha", "Future"],
                    "industry": ["Bank", "Tech"],
                    "market": ["Main", "GEM"],
                    "list_date": ["19910403", "20250101"],
                    "delist_date": [None, None],
                }
            )
        if list_status == "D":
            return pd.DataFrame(
                {
                    "ts_code": ["600000.SH"],
                    "name": ["Beta Delisted"],
                    "industry": ["Bank"],
                    "market": ["Main"],
                    "list_date": ["19991110"],
                    "delist_date": ["20240101"],
                }
            )
        return pd.DataFrame()

    def namechange(self, offset: int, limit: int, **kwargs):
        if offset:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "name": ["ST Alpha", "Beta Historical"],
                "start_date": ["20200101", "19991110"],
                "end_date": ["20240101", "20240101"],
                "ann_date": ["20200101", "19991110"],
                "change_reason": ["ST", "rename"],
            }
        )


def make_config(tmp_path) -> QuantConfig:
    return QuantConfig(
        symbol="ALL_A_PORTFOLIO",
        name="All-A Test",
        start_date="2023-07-03",
        end_date="2023-07-04",
        output_dir=str(tmp_path / "reports"),
        data=DataConfig(
            source="tushare",
            fallback_source="none",
            frequency="daily",
            cache_dir=str(tmp_path / "cache"),
            request_pause_seconds=0.0,
        ),
        portfolio=PortfolioConfig(max_positions=5),
        universe=UniverseConfig(scope="all_a", snapshot_dir=str(tmp_path / "universe")),
    )


def test_provider_incrementally_caches_complete_cross_sections(tmp_path):
    api = FakeAllAApi()
    provider = TushareAllADailyProvider(make_config(tmp_path), api=api, min_symbols_per_day=2)

    first = provider.fetch(max_days=1)
    second = provider.fetch(max_days=1)
    bundle = provider.load("2023-07-03", "2023-07-04")

    assert first["complete"] is False
    assert second["complete"] is True
    assert api.daily_calls == ["20230703", "20230704"]
    assert len(bundle.bars) == 4
    assert bundle.bars.iloc[0]["volume"] == 100_000.0
    assert bundle.bars.iloc[0]["amount_yuan"] == 10_000_000.0


def test_provider_uses_historical_snapshot_and_excludes_non_a_shares(tmp_path):
    api = FakeAllAApi()
    provider = TushareAllADailyProvider(make_config(tmp_path), api=api, min_symbols_per_day=2)

    first = provider.fetch_universe("2023-07-03")
    second = provider.fetch_universe("2023-07-03")

    assert first["ts_code"].tolist() == ["000001.SZ", "600000.SH"]
    assert second.equals(first)
    assert api.universe_calls == ["20230703"]


def test_provider_rejects_partial_market_day(tmp_path):
    api = FakeAllAApi()
    provider = TushareAllADailyProvider(make_config(tmp_path), api=api, min_symbols_per_day=3)

    with pytest.raises(ValueError, match="Incomplete all-A cross-section"):
        provider.fetch(max_days=1)


def test_provider_reconstructs_membership_and_historical_st_names_when_bak_basic_is_unavailable(tmp_path):
    api = FakeReconstructedUniverseApi()
    provider = TushareAllADailyProvider(make_config(tmp_path), api=api, min_symbols_per_day=2)

    universe = provider.fetch_universe("2023-07-03")

    names = universe.set_index("ts_code")["name"].to_dict()
    assert names == {"000001.SZ": "ST Alpha", "600000.SH": "Beta Historical"}
    assert set(universe["snapshot_source"]) == {"stock_basic_namechange_reconstruction"}
