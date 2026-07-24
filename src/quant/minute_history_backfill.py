"""Multi-source three-year minute history backfill and coverage reporting."""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.config import setup_env
from src.tushare_client import create_tushare_pro_api

from .data import MINUTE_COLUMNS, QuantDataError, normalize_minute_bars


LOGGER = logging.getLogger(__name__)
SOURCE_NAMES = ("tushare", "akshare", "baostock", "tdx2db")


@dataclass(frozen=True)
class MinuteHistoryConfig:
    symbols: list[str]
    start_date: str
    end_date: str
    frequency: str
    source_priority: list[str]
    cache_dir: str
    report_dir: str
    tushare_chunk_months: int = 6
    tushare_max_requests_per_run: int = 1
    request_pause_seconds: float = 61.0
    network_timeout_seconds: float = 30.0
    tdx_path: str = ""


@dataclass(frozen=True)
class SourceStatistics:
    source: str
    symbol: str
    status: str
    rows: int
    unique_bars: int
    trading_days: int
    actual_start: str | None
    actual_end: str | None
    duplicate_bars: int
    invalid_ohlc_rows: int
    null_value_rows: int
    bars_per_day_min: int
    bars_per_day_median: float
    bars_per_day_max: int
    bars_per_day_mode: int
    complete_day_ratio: float
    error: str | None = None


def load_minute_history_config(path: str | Path) -> MinuteHistoryConfig:
    config = MinuteHistoryConfig(**json.loads(Path(path).read_text(encoding="utf-8")))
    if not config.symbols:
        raise ValueError("At least one symbol is required")
    if config.frequency != "5min":
        raise ValueError("The multi-source backfill currently uses one comparable 5min frequency")
    if not config.source_priority or any(source not in SOURCE_NAMES for source in config.source_priority):
        raise ValueError(f"source_priority must contain only {SOURCE_NAMES}")
    if len(set(config.source_priority)) != len(config.source_priority):
        raise ValueError("source_priority must not contain duplicates")
    if pd.Timestamp(config.start_date) >= pd.Timestamp(config.end_date):
        raise ValueError("start_date must be earlier than end_date")
    return config


def minute_statistics(
    frame: pd.DataFrame,
    source: str,
    symbol: str,
    requested_start: str,
    requested_end: str,
    error: str | None = None,
) -> SourceStatistics:
    if frame is None or frame.empty:
        return SourceStatistics(
            source=source,
            symbol=symbol,
            status="failed" if error else "empty",
            rows=0,
            unique_bars=0,
            trading_days=0,
            actual_start=None,
            actual_end=None,
            duplicate_bars=0,
            invalid_ohlc_rows=0,
            null_value_rows=0,
            bars_per_day_min=0,
            bars_per_day_median=0.0,
            bars_per_day_max=0,
            bars_per_day_mode=0,
            complete_day_ratio=0.0,
            error=error,
        )
    values = frame.copy()
    values["datetime"] = pd.to_datetime(values["datetime"], errors="coerce")
    duplicates = int(values["datetime"].duplicated().sum())
    required = ["datetime", "open", "high", "low", "close", "volume", "amount"]
    null_rows = int(values[required].isna().any(axis=1).sum())
    invalid = int(
        (
            (values["high"] < values[["open", "close", "low"]].max(axis=1))
            | (values["low"] > values[["open", "close", "high"]].min(axis=1))
            | (values[["open", "high", "low", "close"]] <= 0).any(axis=1)
        ).sum()
    )
    per_day = values.groupby(values["datetime"].dt.normalize()).size()
    mode = int(per_day.mode().iloc[0]) if not per_day.empty else 0
    complete_ratio = float((per_day >= mode).mean()) if mode else 0.0
    actual_start = pd.Timestamp(values["datetime"].min())
    actual_end = pd.Timestamp(values["datetime"].max())
    start_ok = actual_start.normalize() <= pd.Timestamp(requested_start) + pd.Timedelta(days=7)
    end_ok = actual_end.normalize() >= pd.Timestamp(requested_end) - pd.Timedelta(days=7)
    status = "complete" if start_ok and end_ok and not duplicates and not invalid else "partial"
    return SourceStatistics(
        source=source,
        symbol=symbol,
        status=status,
        rows=len(values),
        unique_bars=int(values["datetime"].nunique()),
        trading_days=int(len(per_day)),
        actual_start=actual_start.isoformat(),
        actual_end=actual_end.isoformat(),
        duplicate_bars=duplicates,
        invalid_ohlc_rows=invalid,
        null_value_rows=null_rows,
        bars_per_day_min=int(per_day.min()),
        bars_per_day_median=float(per_day.median()),
        bars_per_day_max=int(per_day.max()),
        bars_per_day_mode=mode,
        complete_day_ratio=complete_ratio,
        error=error,
    )


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


class MinuteHistoryBackfill:
    def __init__(
        self,
        config: MinuteHistoryConfig,
        source_handlers: dict[str, Callable[[str, bool], pd.DataFrame]] | None = None,
    ) -> None:
        self.config = config
        self.cache_root = Path(config.cache_dir)
        self.report_root = Path(config.report_dir)
        self.tushare_requests = 0
        self.source_handlers = source_handlers or {
            "tushare": self._fetch_tushare,
            "akshare": self._fetch_akshare,
            "baostock": self._fetch_baostock,
            "tdx2db": self._fetch_tdx2db,
        }

    def _source_path(self, source: str, symbol: str) -> Path:
        return self.cache_root / source / symbol.replace(".", "_") / "bars.parquet"

    def _fetch_tushare(self, symbol: str, force: bool) -> pd.DataFrame:
        setup_env()
        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token or token.startswith("your_"):
            raise QuantDataError("TUSHARE_TOKEN is not configured")
        api = create_tushare_pro_api(token)
        root = self.cache_root / "tushare" / symbol.replace(".", "_") / "partitions"
        periods = list(pd.period_range(self.config.start_date, self.config.end_date, freq="M"))
        chunk_size = self.config.tushare_chunk_months
        frames = []
        for index in range(0, len(periods), chunk_size):
            chunk = periods[index : index + chunk_size]
            start = max(pd.Timestamp(self.config.start_date), chunk[0].start_time)
            end = min(pd.Timestamp(self.config.end_date), chunk[-1].end_time)
            path = root / f"{start:%Y-%m}_{end:%Y-%m}.parquet"
            if path.exists() and not force:
                frames.append(pd.read_parquet(path))
                continue
            if self.tushare_requests >= self.config.tushare_max_requests_per_run:
                LOGGER.info("本轮Tushare请求预算已用完，保留现有分区并等待下次续跑")
                break
            LOGGER.info("Tushare拉取 %s：%s至%s", symbol, start.date(), end.date())
            for attempt in range(5):
                try:
                    self.tushare_requests += 1
                    raw = api.stk_mins(
                        ts_code=symbol,
                        freq=self.config.frequency,
                        start_date=f"{start:%Y-%m-%d} 09:00:00",
                        end_date=f"{end:%Y-%m-%d} 15:30:00",
                    )
                    break
                except Exception as exc:
                    if "次/小时" in str(exc) or "次/天" in str(exc):
                        if frames:
                            LOGGER.warning("Tushare配额已耗尽，保留现有分区：%s", exc)
                            return normalize_minute_bars(pd.concat(frames, ignore_index=True), symbol)
                        raise QuantDataError(f"Tushare quota exhausted: {exc}") from exc
                    if attempt == 4:
                        raise
                    delay = self.config.request_pause_seconds if "频率超限" in str(exc) else min(2 ** attempt, 15)
                    LOGGER.warning("Tushare请求失败，%.0f秒后重试：%s", delay, exc)
                    time.sleep(delay)
            normalized = normalize_minute_bars(raw, symbol)
            if len(normalized) >= 7_990:
                raise QuantDataError("Tushare response reached its 8000-row limit; reduce tushare_chunk_months")
            _atomic_parquet(path, normalized)
            frames.append(normalized)
            if index + chunk_size < len(periods):
                if self.tushare_requests < self.config.tushare_max_requests_per_run:
                    time.sleep(self.config.request_pause_seconds)
        if not frames:
            raise QuantDataError("No Tushare partition is available in this run")
        return normalize_minute_bars(pd.concat(frames, ignore_index=True), symbol)

    def _fetch_akshare(self, symbol: str, force: bool) -> pd.DataFrame:
        del force
        import akshare as ak

        LOGGER.info("AKShare拉取 %s：%s至%s", symbol, self.config.start_date, self.config.end_date)
        raw = ak.stock_zh_a_hist_min_em(
            symbol=symbol.split(".")[0],
            start_date=f"{self.config.start_date} 09:30:00",
            end_date=f"{self.config.end_date} 15:00:00",
            period="5",
            adjust="",
        ).rename(
            columns={
                "时间": "datetime",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
            }
        )
        return normalize_minute_bars(raw, symbol)

    def _fetch_baostock(self, symbol: str, force: bool) -> pd.DataFrame:
        del force
        import baostock as bs

        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(self.config.network_timeout_seconds)
        login = None
        try:
            login = bs.login()
            if login.error_code != "0":
                raise QuantDataError(f"Baostock login failed: {login.error_msg}")
            prefix = "sh" if symbol.endswith(".SH") else "sz"
            code = f"{prefix}.{symbol.split('.')[0]}"
            LOGGER.info("Baostock拉取 %s：%s至%s", symbol, self.config.start_date, self.config.end_date)
            result = bs.query_history_k_data_plus(
                code,
                "date,time,code,open,high,low,close,volume,amount,adjustflag",
                start_date=self.config.start_date,
                end_date=self.config.end_date,
                frequency="5",
                adjustflag="3",
            )
            if result.error_code != "0":
                raise QuantDataError(f"Baostock query failed: {result.error_msg}")
            rows = []
            while result.next():
                rows.append(result.get_row_data())
            raw = pd.DataFrame(rows, columns=result.fields)
            raw["datetime"] = pd.to_datetime(raw["time"].astype(str).str[:14], format="%Y%m%d%H%M%S")
            return normalize_minute_bars(raw, symbol)
        finally:
            if login is not None:
                try:
                    bs.logout()
                except Exception:
                    LOGGER.warning("Baostock logout failed", exc_info=True)
            socket.setdefaulttimeout(previous_timeout)

    def _fetch_tdx2db(self, symbol: str, force: bool) -> pd.DataFrame:
        del force
        tdx_path = self.config.tdx_path or os.getenv("TDX_PATH", "").strip()
        if not tdx_path:
            raise QuantDataError("TDX_PATH is not configured; tdx2db requires local vipdoc files")
        from tdx2db.reader import TdxDataReader

        code = symbol.split(".")[0]
        market = 1 if symbol.endswith(".SH") else 0
        LOGGER.info("tdx2db读取本地5分钟文件：%s", symbol)
        raw = TdxDataReader(tdx_path).read_5min_data(market, code)
        normalized = normalize_minute_bars(raw, symbol)
        start = pd.Timestamp(self.config.start_date)
        end = pd.Timestamp(self.config.end_date) + pd.Timedelta(days=1)
        return normalized[(normalized["datetime"] >= start) & (normalized["datetime"] < end)].reset_index(drop=True)

    def run(
        self,
        symbols: list[str] | None = None,
        sources: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        selected_symbols = symbols or self.config.symbols
        selected_sources = sources or self.config.source_priority
        statistics = []
        combined_stats = []
        for symbol in selected_symbols:
            source_frames = []
            for priority, source in enumerate(selected_sources):
                path = self._source_path(source, symbol)
                try:
                    frame = (
                        pd.read_parquet(path)
                        if path.exists() and not force
                        else self.source_handlers[source](symbol, force)
                    )
                    frame = normalize_minute_bars(frame, symbol)
                    if not frame.empty:
                        _atomic_parquet(path, frame)
                        values = frame.copy()
                        values["source"] = source
                        values["source_priority"] = priority
                        source_frames.append(values)
                    stats = minute_statistics(
                        frame,
                        source,
                        symbol,
                        self.config.start_date,
                        self.config.end_date,
                    )
                except Exception as exc:
                    LOGGER.error("%s获取%s失败：%s", source, symbol, exc)
                    stats = minute_statistics(
                        pd.DataFrame(columns=MINUTE_COLUMNS),
                        source,
                        symbol,
                        self.config.start_date,
                        self.config.end_date,
                        f"{type(exc).__name__}: {exc}",
                    )
                statistics.append(asdict(stats))
                LOGGER.info(
                    "数据源统计：%s %s status=%s rows=%d days=%d",
                    source,
                    symbol,
                    stats.status,
                    stats.rows,
                    stats.trading_days,
                )
            if source_frames:
                combined = pd.concat(source_frames, ignore_index=True).sort_values(["datetime", "source_priority"])
                combined = combined.drop_duplicates("datetime", keep="first").sort_values("datetime")
                combined_path = self.cache_root / "combined" / symbol.replace(".", "_") / "bars.parquet"
                _atomic_parquet(combined_path, combined)
                stats = minute_statistics(
                    combined,
                    "combined",
                    symbol,
                    self.config.start_date,
                    self.config.end_date,
                )
            else:
                stats = minute_statistics(
                    pd.DataFrame(columns=MINUTE_COLUMNS),
                    "combined",
                    symbol,
                    self.config.start_date,
                    self.config.end_date,
                    "All configured sources failed",
                )
            combined_stats.append(asdict(stats))
        return self._write_report(statistics, combined_stats)

    def _write_report(self, statistics: list[dict[str, Any]], combined: list[dict[str, Any]]) -> dict[str, Any]:
        self.report_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "requested_start": self.config.start_date,
            "requested_end": self.config.end_date,
            "frequency": self.config.frequency,
            "source_priority": self.config.source_priority,
            "source_statistics": statistics,
            "combined_statistics": combined,
        }
        json_path = self.report_root / "minute_history_statistics.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [
            "# 最近三年分钟行情数据统计",
            "",
            f"请求区间：{self.config.start_date} 至 {self.config.end_date}；统一频率：{self.config.frequency}。",
            "",
            "| 数据源 | 股票 | 状态 | K线数 | 交易日 | 实际起点 | 实际终点 | 完整日比例 | 错误 |",
            "|---|---|---|---:|---:|---|---|---:|---|",
        ]
        for item in statistics:
            error = (item["error"] or "").replace("|", "/").replace("\n", " ")
            lines.append(
                f"| {item['source']} | {item['symbol']} | {item['status']} | {item['rows']} | "
                f"{item['trading_days']} | {item['actual_start'] or '-'} | {item['actual_end'] or '-'} | "
                f"{item['complete_day_ratio']:.1%} | {error} |"
            )
        markdown_path = self.report_root / "minute_history_statistics.md"
        markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        payload["json_report"] = str(json_path)
        payload["markdown_report"] = str(markdown_path)
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill and audit three-year A-share 5-minute history")
    parser.add_argument("--config", required=True)
    parser.add_argument("--symbol", action="append", help="Limit this run to one or more symbols")
    parser.add_argument("--source", action="append", choices=SOURCE_NAMES, help="Limit this run to selected sources")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_minute_history_config(args.config)
    result = MinuteHistoryBackfill(config).run(args.symbol, args.source, args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
