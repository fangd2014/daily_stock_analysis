"""Tests for causal exits in the monthly prequential portfolio runner."""

import pandas as pd

from src.quant.all_a_data import AllADailyBundle
from src.quant.all_a_selector import AllACandidate, MonthlyPortfolioSelection
from src.quant.config import PortfolioConfig, QuantConfig, UniverseConfig
from src.quant.factor_experiments import preregistered_exit_policies
from src.quant.prequential_portfolio import PrequentialPortfolioRunner


class FakeProvider:
    def reconstruct_cached_universe(self, trade_date: str) -> pd.DataFrame:
        raise AssertionError(f"Universe reconstruction is not expected for {trade_date}")


def make_runner() -> tuple[PrequentialPortfolioRunner, str, list[str]]:
    symbols = [f"00000{index}.SZ" for index in range(1, 6)]
    dates = pd.bdate_range("2023-02-01", periods=70)
    records = []
    limit_records = []
    for trade_date in dates:
        key = trade_date.strftime("%Y%m%d")
        for symbol in symbols:
            records.append(
                {
                    "ts_code": symbol,
                    "trade_date": key,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1_000_000.0,
                    "amount_yuan": 100_000_000.0,
                }
            )
            limit_records.append(
                {
                    "ts_code": symbol,
                    "trade_date": key,
                    "up_limit": 110.0,
                    "down_limit": 90.0,
                }
            )
    bars = pd.DataFrame(records)
    limits = pd.DataFrame(limit_records)
    config = QuantConfig(
        symbol="ALL_A_PORTFOLIO",
        name="Exit Test",
        start_date="2023-02-01",
        end_date="2023-05-31",
        output_dir="reports/quant/test",
        portfolio=PortfolioConfig(max_positions=5),
        universe=UniverseConfig(scope="all_a", min_average_amount=0.0),
    )
    runner = PrequentialPortfolioRunner(config, FakeProvider(), AllADailyBundle(bars=bars, limits=limits))
    entry_date = runner._month_dates("2023-05")[0]
    return runner, entry_date, symbols


def make_selection(entry_date: str, symbols: list[str]) -> MonthlyPortfolioSelection:
    candidates = [
        AllACandidate(
            symbol=symbol,
            name=symbol,
            industry="Test",
            selection_date=entry_date,
            open_price=100.0,
            lower_peak=90.0,
            upper_peak=120.0,
            valley=105.0,
            chip_position=1.0 / 3.0,
            distance_to_lower_pct=1.0 / 9.0,
            momentum_20d=0.0,
            volatility_20d=0.01,
            average_amount_20d=100_000_000.0,
            score=1.0,
            buy_ready=True,
            eligible=True,
            reason="eligible",
        )
        for symbol in symbols
    ]
    return MonthlyPortfolioSelection(
        selection_date=entry_date,
        feature_cutoff="20230428",
        candidates=candidates,
        selected=candidates,
    )


def set_session_prices(
    runner: PrequentialPortfolioRunner,
    trade_date: str,
    *,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
) -> None:
    rows = runner.bars["trade_date"].eq(trade_date)
    runner.bars.loc[rows, ["open", "high", "low", "close"]] = [
        open_price,
        high_price,
        low_price,
        close_price,
    ]


def test_stop_is_t_plus_one_and_uses_conservative_intraday_precedence():
    runner, entry_date, symbols = make_runner()
    month_dates = runner._month_dates("2023-05")
    selection = make_selection(entry_date, symbols)
    policy = next(policy for policy in preregistered_exit_policies() if policy.name == "mid_peak_stop_6")
    set_session_prices(runner, month_dates[0], open_price=100.0, high_price=106.0, low_price=90.0, close_price=100.0)
    set_session_prices(runner, month_dates[1], open_price=100.0, high_price=106.0, low_price=93.0, close_price=100.0)

    curve = runner._month_curve("2023-05", selection, 1.0, policy)

    assert curve.iloc[0] > 0.99
    assert 0.93 < curve.iloc[1] < 0.95
    assert curve.iloc[-1] == curve.iloc[1]


def test_mid_peak_target_turns_each_position_into_cash_after_fill():
    runner, entry_date, symbols = make_runner()
    month_dates = runner._month_dates("2023-05")
    selection = make_selection(entry_date, symbols)
    policy = next(policy for policy in preregistered_exit_policies() if policy.name == "mid_peak_stop_6")
    set_session_prices(runner, month_dates[1], open_price=101.0, high_price=106.0, low_price=99.0, close_price=102.0)

    curve = runner._month_curve("2023-05", selection, 1.0, policy)

    assert 1.04 < curve.iloc[1] < 1.05
    assert curve.iloc[-1] == curve.iloc[1]
