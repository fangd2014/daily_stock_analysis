"""Tests for multi-source minute history backfill and statistics."""

from pathlib import Path

import pandas as pd

from src.quant.minute_history_backfill import MinuteHistoryBackfill, MinuteHistoryConfig, minute_statistics


def make_frame(symbol: str, days: int = 3, start: str = "2026-07-20") -> pd.DataFrame:
    rows = []
    for day in pd.bdate_range(start, periods=days):
        for clock in ("09:35", "09:40", "09:45"):
            timestamp = pd.Timestamp(f"{day.date()} {clock}")
            rows.append(
                {
                    "datetime": timestamp,
                    "symbol": symbol,
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "volume": 1000.0,
                    "amount": 10_000.0,
                }
            )
    return pd.DataFrame(rows)


def make_config(tmp_path: Path) -> MinuteHistoryConfig:
    return MinuteHistoryConfig(
        symbols=["688008.SH"],
        start_date="2026-07-20",
        end_date="2026-07-22",
        frequency="5min",
        source_priority=["tushare", "akshare", "baostock", "tdx2db"],
        cache_dir=str(tmp_path / "cache"),
        report_dir=str(tmp_path / "reports"),
    )


def test_minute_statistics_reports_quality_and_coverage():
    stats = minute_statistics(make_frame("688008.SH"), "test", "688008.SH", "2026-07-20", "2026-07-22")

    assert stats.status == "complete"
    assert stats.rows == 9
    assert stats.trading_days == 3
    assert stats.bars_per_day_mode == 3
    assert stats.complete_day_ratio == 1.0
    assert stats.invalid_ohlc_rows == 0


def test_backfill_preserves_source_priority_and_records_failures(tmp_path):
    first = make_frame("688008.SH")
    second = first.copy()
    second.loc[second.index[-1], "close"] = 99.0

    def fail(symbol, force):
        del symbol, force
        raise RuntimeError("unavailable")

    runner = MinuteHistoryBackfill(
        make_config(tmp_path),
        source_handlers={
            "tushare": lambda symbol, force: first,
            "akshare": lambda symbol, force: second,
            "baostock": fail,
            "tdx2db": fail,
        },
    )
    result = runner.run()

    combined = pd.read_parquet(tmp_path / "cache" / "combined" / "688008_SH" / "bars.parquet")
    assert len(combined) == len(first)
    assert set(combined["source"]) == {"tushare"}
    assert result["source_statistics"][2]["status"] == "failed"
    assert Path(result["json_report"]).exists()
    assert Path(result["markdown_report"]).exists()
