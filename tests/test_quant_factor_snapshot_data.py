"""Tests for point-in-time all-A factor snapshots."""

from dataclasses import replace

import pandas as pd

from src.quant.config import DataConfig, OptimizationConfig
from src.quant.factor_snapshot_data import FactorSnapshotProvider
from tests.test_quant_all_a_selector import make_config


class FakeFactorApi:
    def daily_basic(self, **kwargs):
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "trade_date": [kwargs["trade_date"]] * 2,
                "turnover_rate_f": [1.0, 2.0],
                "pe_ttm": [10.0, 12.0],
                "pb": [1.0, 1.2],
                "total_mv": [100_000.0, 200_000.0],
                "circ_mv": [80_000.0, 160_000.0],
            }
        )

    def moneyflow(self, **kwargs):
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "trade_date": [kwargs["trade_date"]] * 2,
                "buy_lg_amount": [100.0, 200.0],
                "buy_elg_amount": [50.0, 60.0],
                "sell_lg_amount": [80.0, 100.0],
                "sell_elg_amount": [20.0, 40.0],
                "net_mf_amount": [50.0, 120.0],
            }
        )

    def fina_indicator(self, **kwargs):
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "600000.SH"],
                "ann_date": ["20230420", "20230820", "20230425"],
                "end_date": ["20221231", "20230630", "20221231"],
                "update_flag": ["0", "0", "0"],
                "roe_dt": [10.0, 12.0, 8.0],
                "roa": [5.0, 6.0, 4.0],
                "profit_dedt": [100.0, 120.0, 80.0],
                "grossprofit_margin": [30.0, 31.0, 20.0],
                "ocf_to_or": [0.2, 0.3, 0.1],
                "debt_to_assets": [40.0, 38.0, 50.0],
                "q_profit_yoy": [5.0, 8.0, 3.0],
                "requested_symbol": [kwargs["ts_code"]] * 3,
            }
        ).drop(columns="requested_symbol")


def make_snapshot_provider(tmp_path) -> FactorSnapshotProvider:
    config = make_config()
    config = replace(
        config,
        start_date="2023-01-01",
        end_date="2023-08-31",
        data=DataConfig(frequency="daily", cache_dir=str(tmp_path), request_pause_seconds=0.0),
        optimization=OptimizationConfig(
            holdout_start="2023-07-01",
            holdout_end="2023-08-31",
        ),
    )
    bars_dir = tmp_path / "all_a_daily" / "bars"
    bars_dir.mkdir(parents=True)
    for trade_date in ("20230629", "20230630", "20230703", "20230731", "20230801"):
        (bars_dir / f"{trade_date}.parquet").touch()
    return FactorSnapshotProvider(config, api=FakeFactorApi(), min_symbols_per_snapshot=2)


def test_required_cutoffs_are_sessions_before_each_holdout_month(tmp_path):
    provider = make_snapshot_provider(tmp_path)

    assert provider.required_monthly_cutoffs() == ["20230630", "20230731"]


def test_factor_snapshots_are_cached_and_financials_obey_announcement_cutoff(tmp_path):
    provider = make_snapshot_provider(tmp_path)

    result = provider.fetch()
    early = provider.load_financials("2023-06-30")
    late = provider.load_financials("2023-08-31")

    assert result["complete"] is True
    assert result["financial_symbols"] == ["000001.SZ", "600000.SH"]
    assert len(provider.load_daily_basic("20230630")) == 2
    assert len(provider.load_moneyflow("20230630")) == 2
    assert early.set_index("ts_code").loc["000001.SZ", "roe_dt"] == 10.0
    assert late.set_index("ts_code").loc["000001.SZ", "roe_dt"] == 12.0
