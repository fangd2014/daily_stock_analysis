"""Minute-bar data loading, normalization, and local caching."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol

import pandas as pd

from src.config import setup_env
from src.tushare_client import create_tushare_pro_api

from .config import QuantConfig

logger = logging.getLogger(__name__)

MINUTE_COLUMNS = ["datetime", "symbol", "open", "high", "low", "close", "volume", "amount"]


class QuantDataError(RuntimeError):
    """Raised when quantitative market data cannot be loaded or validated."""


@dataclass
class QuantDataBundle:
    """Normalized inputs consumed by the event-driven backtester."""

    bars: pd.DataFrame
    limits: pd.DataFrame
    adjustments: pd.DataFrame
    dividends: pd.DataFrame


class QuantDataProvider(Protocol):
    """Common interface for online and offline quantitative data sources."""

    def fetch(self, force: bool = False) -> QuantDataBundle:
        """Refresh data when needed and return a normalized bundle."""

    def load(self) -> QuantDataBundle:
        """Load a previously cached or local bundle without forced refresh."""


def normalize_minute_bars(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Normalize Tushare or user-provided minute bars to the engine schema."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    result = frame.copy()
    result = result.rename(
        columns={
            "trade_time": "datetime",
            "date": "datetime",
            "ts_code": "symbol",
            "code": "symbol",
            "vol": "volume",
        }
    )
    if "datetime" not in result:
        raise QuantDataError("Minute data must include trade_time, datetime, or date")
    result["datetime"] = pd.to_datetime(result["datetime"], errors="coerce")
    result["symbol"] = result.get("symbol", symbol).fillna(symbol) if "symbol" in result else symbol
    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column not in result:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["datetime", "open", "high", "low", "close"])
    result = result[result["close"] > 0]
    clock = result["datetime"].dt.strftime("%H:%M")
    in_session = clock.between("09:30", "11:30") | clock.between("13:00", "15:00")
    result = result.loc[in_session, MINUTE_COLUMNS]
    return result.sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)


def normalize_limits(frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Normalize daily upper and lower price limits."""
    columns = ["trade_date", "up_limit", "down_limit"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    result = frame.rename(columns={"date": "trade_date"}).copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.normalize()
    for column in ("up_limit", "down_limit"):
        result[column] = pd.to_numeric(result.get(column), errors="coerce")
    return result.dropna(subset=["trade_date"])[columns].drop_duplicates("trade_date", keep="last")


def normalize_adjustments(frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Normalize daily adjustment factors."""
    columns = ["trade_date", "adj_factor"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    result = frame.rename(columns={"date": "trade_date"}).copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.normalize()
    result["adj_factor"] = pd.to_numeric(result.get("adj_factor"), errors="coerce")
    return result.dropna(subset=columns)[columns].drop_duplicates("trade_date", keep="last")


def normalize_dividends(frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Normalize implemented cash and stock distributions by ex-date."""
    columns = ["ex_date", "cash_div", "stock_ratio"]
    if frame is None or frame.empty or "ex_date" not in frame:
        return pd.DataFrame(columns=columns)
    result = frame.copy()
    if "div_proc" in result:
        implemented = result["div_proc"].astype(str).str.contains("实施|实施分配", regex=True, na=False)
        if implemented.any():
            result = result.loc[implemented]
    result["ex_date"] = pd.to_datetime(result["ex_date"], errors="coerce").dt.normalize()
    cash_source = "cash_div_tax" if "cash_div_tax" in result else "cash_div"
    result["cash_div"] = pd.to_numeric(result.get(cash_source, 0.0), errors="coerce").fillna(0.0)
    stock_columns = [column for column in ("stk_div", "stk_bo_rate", "stk_co_rate") if column in result]
    if "stock_ratio" in result:
        result["stock_ratio"] = pd.to_numeric(result["stock_ratio"], errors="coerce").fillna(0.0)
    elif stock_columns:
        result["stock_ratio"] = result[stock_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0).sum(axis=1)
    else:
        result["stock_ratio"] = 0.0
    result = result.dropna(subset=["ex_date"])
    return result.groupby("ex_date", as_index=False).agg(
        cash_div=("cash_div", "max"),
        stock_ratio=("stock_ratio", "max"),
    )


class TushareMinuteDataProvider:
    """Fetch Tushare minute data and persist deterministic monthly cache files."""

    def __init__(self, config: QuantConfig, api: object | None = None):
        self.config = config
        self.cache_root = Path(config.data.cache_dir) / config.symbol.replace(".", "_") / config.data.frequency
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._api = api

    def _get_api(self) -> object:
        if self._api is not None:
            return self._api
        setup_env()
        import os

        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token or token.startswith("your_"):
            raise QuantDataError("TUSHARE_TOKEN is required for minute data")
        self._api = create_tushare_pro_api(token)
        return self._api

    def _call(self, endpoint: str, **kwargs: object) -> pd.DataFrame:
        api = self._get_api()
        for attempt in range(5):
            try:
                return getattr(api, endpoint)(**kwargs)
            except Exception as exc:
                if attempt == 4:
                    raise
                message = str(exc)
                if "次/天" in message or "次/小时" in message:
                    raise QuantDataError(
                        f"The Tushare {endpoint} hourly or daily request quota is exhausted. "
                        "Cached partitions are safe; retry after the quota resets, upgrade the data permission, "
                        "or use a local file."
                    ) from exc
                delay = 61.0 if "频率超限" in message else min(2 ** (attempt + 1), 30)
                logger.warning("Tushare %s request failed; retrying in %.0fs: %s", endpoint, delay, message)
                time.sleep(delay)
        raise QuantDataError(f"Unexpected retry exit for {endpoint}")

    @staticmethod
    def _month_ranges(start_date: str, end_date: str) -> Iterable[tuple[pd.Timestamp, pd.Timestamp]]:
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
        for period in pd.period_range(start=start, end=end, freq="M"):
            month_start = max(start, period.start_time.normalize())
            month_end = min(end, period.end_time.normalize())
            yield month_start, month_end

    def fetch(self, force: bool = False) -> QuantDataBundle:
        """Fetch missing partitions and return the complete configured dataset."""
        missing_ranges = []
        for month_start, month_end in self._month_ranges(self.config.start_date, self.config.end_date):
            path = self.cache_root / f"bars_{month_start:%Y-%m}.parquet"
            if force or not path.exists():
                missing_ranges.append((month_start, month_end))
        chunk_size = max(1, self.config.data.minute_chunk_months)
        chunks = [missing_ranges[index : index + chunk_size] for index in range(0, len(missing_ranges), chunk_size)]
        if self.config.data.max_chunks_per_run > 0:
            chunks = chunks[: self.config.data.max_chunks_per_run]
        for chunk_index, chunk in enumerate(chunks):
            chunk_start = chunk[0][0]
            chunk_end = chunk[-1][1]
            logger.info(
                "Fetching %s to %s minute bars for %s",
                chunk_start.strftime("%Y-%m"),
                chunk_end.strftime("%Y-%m"),
                self.config.symbol,
            )
            frame = self._call(
                "stk_mins",
                ts_code=self.config.symbol,
                freq=self.config.data.frequency,
                start_date=f"{chunk_start:%Y-%m-%d} 09:00:00",
                end_date=f"{chunk_end:%Y-%m-%d} 15:30:00",
            )
            normalized = normalize_minute_bars(frame, self.config.symbol)
            if len(normalized) >= 7_990:
                raise QuantDataError(
                    f"Minute chunk {chunk_start:%Y-%m}..{chunk_end:%Y-%m} reached the 8000-row limit; "
                    "reduce data.minute_chunk_months"
                )
            for month_start, _ in chunk:
                month_period = normalized["datetime"].dt.to_period("M")
                partition = normalized[month_period == month_start.to_period("M")]
                cache_path = self.cache_root / f"bars_{month_start:%Y-%m}.parquet"
                partition.to_parquet(cache_path, index=False)
            if chunk_index < len(chunks) - 1:
                time.sleep(self.config.data.request_pause_seconds)
        self._fetch_metadata(force=force)
        return self.load()

    def _fetch_metadata(self, force: bool = False) -> None:
        date_args = {
            "ts_code": self.config.symbol,
            "start_date": self.config.start_date.replace("-", ""),
            "end_date": self.config.end_date.replace("-", ""),
        }
        endpoints = {
            "limits.parquet": ("stk_limit", normalize_limits),
            "adjustments.parquet": ("adj_factor", normalize_adjustments),
            "dividends.parquet": ("dividend", normalize_dividends),
        }
        for filename, (endpoint, normalizer) in endpoints.items():
            cache_path = self.cache_root / filename
            if cache_path.exists() and not force:
                continue
            try:
                call_args = {"ts_code": self.config.symbol} if endpoint == "dividend" else date_args
                frame = self._call(endpoint, **call_args)
                normalized = normalizer(frame)
                if endpoint == "dividend" and not normalized.empty:
                    start = pd.Timestamp(self.config.start_date)
                    end = pd.Timestamp(self.config.end_date)
                    normalized = normalized[(normalized["ex_date"] >= start) & (normalized["ex_date"] <= end)]
                normalized.to_parquet(cache_path, index=False)
            except Exception as exc:
                if endpoint == "dividend":
                    logger.warning("Dividend metadata unavailable: %s", exc)
                    normalizer(None).to_parquet(cache_path, index=False)
                else:
                    raise QuantDataError(f"Unable to fetch {endpoint}: {exc}") from exc
            time.sleep(min(self.config.data.request_pause_seconds, 0.2))

    def load(self) -> QuantDataBundle:
        """Load cached data without making network requests."""
        partitions = sorted(self.cache_root.glob("bars_*.parquet"))
        if not partitions:
            raise QuantDataError(f"No cached minute bars found under {self.cache_root}")
        bars = pd.concat((pd.read_parquet(path) for path in partitions), ignore_index=True)
        bars = normalize_minute_bars(bars, self.config.symbol)
        start = pd.Timestamp(self.config.start_date)
        end = pd.Timestamp(self.config.end_date) + pd.Timedelta(days=1)
        bars = bars[(bars["datetime"] >= start) & (bars["datetime"] < end)].reset_index(drop=True)

        def read_metadata(filename: str, columns: list[str]) -> pd.DataFrame:
            path = self.cache_root / filename
            return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=columns)

        return QuantDataBundle(
            bars=bars,
            limits=normalize_limits(read_metadata("limits.parquet", ["trade_date", "up_limit", "down_limit"])),
            adjustments=normalize_adjustments(read_metadata("adjustments.parquet", ["trade_date", "adj_factor"])),
            dividends=normalize_dividends(read_metadata("dividends.parquet", ["ex_date", "cash_div", "stock_ratio"])),
        )


def load_local_bundle(path: str | Path, symbol: str) -> QuantDataBundle:
    """Load an offline CSV or Parquet minute dataset for reproducible research."""
    data_path = Path(path)
    frame = pd.read_parquet(data_path) if data_path.suffix.lower() == ".parquet" else pd.read_csv(data_path)
    return QuantDataBundle(
        bars=normalize_minute_bars(frame, symbol),
        limits=pd.DataFrame(columns=["trade_date", "up_limit", "down_limit"]),
        adjustments=pd.DataFrame(columns=["trade_date", "adj_factor"]),
        dividends=pd.DataFrame(columns=["ex_date", "cash_div", "stock_ratio"]),
    )


class LocalMinuteDataProvider:
    """Read a user-supplied CSV or Parquet file through the provider interface."""

    def __init__(self, config: QuantConfig):
        if not config.data.local_path:
            raise QuantDataError("data.local_path is required when data.source is local")
        self.config = config

    def fetch(self, force: bool = False) -> QuantDataBundle:
        del force
        return self.load()

    def load(self) -> QuantDataBundle:
        return load_local_bundle(self.config.data.local_path, self.config.symbol)


def create_data_provider(config: QuantConfig) -> QuantDataProvider:
    """Create the configured market-data provider."""
    if config.data.source == "tushare":
        return TushareMinuteDataProvider(config)
    if config.data.source == "local":
        return LocalMinuteDataProvider(config)
    if config.data.source == "pytdx":
        from .tdx_data import FailoverQuantDataProvider, TdxQuantDataProvider

        primary = TdxQuantDataProvider(config)
        if config.data.fallback_source == "none":
            return primary
        fallback: QuantDataProvider
        if config.data.fallback_source == "tushare":
            fallback = TushareMinuteDataProvider(config)
        elif config.data.fallback_source == "local":
            fallback = LocalMinuteDataProvider(config)
        else:
            raise QuantDataError(f"Unsupported quant fallback source: {config.data.fallback_source}")
        status_path = primary.cache_root / "active_source.json"
        return FailoverQuantDataProvider(primary, fallback, status_path, config.data.fallback_source)
    raise QuantDataError(f"Unsupported quant data source: {config.data.source}")


def ensure_bundle_coverage(bundle: QuantDataBundle, start_date: str, end_date: str, tolerance_days: int = 7) -> None:
    """Fail before optimization when cached data does not cover the requested research period."""
    if bundle.bars.empty:
        raise QuantDataError("Minute dataset is empty")
    actual_start = pd.Timestamp(bundle.bars["datetime"].min()).normalize()
    actual_end = pd.Timestamp(bundle.bars["datetime"].max()).normalize()
    expected_start = pd.Timestamp(start_date).normalize()
    expected_end = pd.Timestamp(end_date).normalize()
    if actual_start > expected_start + pd.Timedelta(days=tolerance_days):
        raise QuantDataError(
            f"Minute data starts at {actual_start.date()}, expected coverage from {expected_start.date()}"
        )
    if actual_end < expected_end - pd.Timedelta(days=tolerance_days):
        raise QuantDataError(
            f"Minute data ends at {actual_end.date()}, expected coverage through {expected_end.date()}"
        )
