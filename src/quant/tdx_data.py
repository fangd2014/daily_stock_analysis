"""Resilient PyTDX market-data ingestion for quantitative research."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np
import pandas as pd

from src.config import setup_env

from .config import QuantConfig
from .data import (
    QuantDataBundle,
    QuantDataError,
    normalize_adjustments,
    normalize_dividends,
    normalize_limits,
    normalize_minute_bars,
)

logger = logging.getLogger(__name__)

TDX_CATEGORY = {"1min": 8, "5min": 0, "15min": 1, "30min": 2, "60min": 3}
EXPECTED_BARS_PER_DAY = {"1min": 240, "5min": 48, "15min": 16, "30min": 8, "60min": 4}


@dataclass(frozen=True)
class TdxEndpoint:
    """One reachable TDX quote server."""

    name: str
    host: str
    port: int
    latency_ms: float = float("inf")


@dataclass(frozen=True)
class TdxCalibration:
    """Unit calibration inferred from a trusted daily reference."""

    volume_multiplier: float
    amount_multiplier: float
    matched_days: int
    close_error_max: float
    volume_error_median: float
    amount_error_median: float
    reference_days: int = 0


@dataclass(frozen=True)
class TdxQualityReport:
    """Fail-closed quality checks for one cached dataset."""

    passed: bool
    row_count: int
    duplicate_count: int
    invalid_ohlc_count: int
    complete_day_ratio: float
    matched_reference_days: int
    close_error_max: float
    start: str
    end: str
    failures: list[str]


class TdxApi(Protocol):
    def connect(self, ip: str, port: int, time_out: float = 5.0) -> Any:
        """Connect to one quote server."""

    def disconnect(self) -> None:
        """Disconnect from the quote server."""

    def get_security_count(self, market: int) -> int:
        """Return the number of listed instruments for a market."""

    def get_security_bars(self, category: int, market: int, code: str, start: int, count: int) -> Any:
        """Return one page of bars."""


class MetadataProvider(Protocol):
    def fetch(self, symbol: str, start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
        """Fetch corporate-action metadata and a daily cross-check frame."""


def default_tdx_api_factory() -> TdxApi:
    """Create a strict PyTDX client that surfaces protocol failures."""
    from pytdx.hq import TdxHq_API

    return TdxHq_API(multithread=True, heartbeat=True, auto_retry=False, raise_exception=True)


def market_and_code(symbol: str) -> tuple[int, str]:
    """Map a canonical A-share symbol to the PyTDX market and six-digit code."""
    value = symbol.strip().upper()
    suffix = value[-3:] if len(value) >= 3 else ""
    code = value.replace(".SH", "").replace(".SZ", "").replace("SH", "").replace("SZ", "")
    if not code.isdigit() or len(code) != 6:
        raise QuantDataError(f"PyTDX only supports six-digit A-share symbols: {symbol}")
    if suffix == ".SH" or code.startswith(("5", "6", "9")):
        return 1, code
    if suffix == ".SZ" or code.startswith(("0", "1", "2", "3")):
        return 0, code
    raise QuantDataError(f"Unable to infer PyTDX market for {symbol}")


def _configured_endpoints(values: list[str]) -> list[TdxEndpoint]:
    endpoints = []
    for index, value in enumerate(values):
        host, separator, port = value.rpartition(":")
        if not separator or not host or not port.isdigit():
            raise QuantDataError(f"Invalid PyTDX endpoint: {value}")
        endpoints.append(TdxEndpoint(f"configured-{index + 1}", host, int(port)))
    return endpoints


def upstream_endpoints() -> list[TdxEndpoint]:
    """Load the endpoint catalog shipped by the installed PyTDX version."""
    from pytdx.config.hosts import hq_hosts

    return [TdxEndpoint(str(name), str(host), int(port)) for name, host, port in hq_hosts]


class TdxNodeSelector:
    """Probe, rank, and cache healthy TDX endpoints."""

    def __init__(
        self,
        cache_path: str | Path,
        endpoints: list[TdxEndpoint] | None = None,
        api_factory: Callable[[], TdxApi] = default_tdx_api_factory,
        timeout_seconds: float = 1.0,
        cache_ttl_seconds: int = 3600,
        max_workers: int = 12,
        clock: Callable[[], float] = time.time,
    ):
        self.cache_path = Path(cache_path)
        self.endpoints = endpoints or upstream_endpoints()
        self.api_factory = api_factory
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_workers = max_workers
        self.clock = clock
        if not self.endpoints:
            raise QuantDataError("PyTDX endpoint catalog is empty")

    def _load_cache(self) -> list[TdxEndpoint]:
        if not self.cache_path.exists():
            return []
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if self.clock() - float(payload["checked_at"]) > self.cache_ttl_seconds:
                return []
            return [TdxEndpoint(**item) for item in payload.get("nodes", [])]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return []

    def _probe(self, endpoint: TdxEndpoint) -> TdxEndpoint | None:
        api = self.api_factory()
        started = time.perf_counter()
        try:
            connected = api.connect(endpoint.host, endpoint.port, time_out=self.timeout_seconds)
            if not connected or int(api.get_security_count(0) or 0) <= 0:
                return None
            latency = (time.perf_counter() - started) * 1000
            return TdxEndpoint(endpoint.name, endpoint.host, endpoint.port, round(latency, 3))
        except Exception:
            return None
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

    def rank(self, force: bool = False) -> list[TdxEndpoint]:
        """Return healthy endpoints ordered by measured latency."""
        if not force:
            cached = self._load_cache()
            if cached:
                return cached
        healthy = []
        workers = min(max(1, self.max_workers), len(self.endpoints))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(self._probe, endpoint) for endpoint in self.endpoints]
            for future in as_completed(futures):
                endpoint = future.result()
                if endpoint is not None:
                    healthy.append(endpoint)
        healthy.sort(key=lambda item: item.latency_ms)
        if not healthy:
            raise QuantDataError("PyTDX cannot reach any quote server; check outbound TCP access")
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"checked_at": self.clock(), "nodes": [asdict(item) for item in healthy]}
        temporary = self.cache_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.cache_path)
        return healthy


class TdxBarPager:
    """Page backward through TDX history and fail over between healthy nodes."""

    def __init__(
        self,
        selector: TdxNodeSelector,
        api_factory: Callable[[], TdxApi] = default_tdx_api_factory,
        page_size: int = 800,
        max_pages: int = 500,
        timeout_seconds: float = 2.0,
    ):
        if not 1 <= page_size <= 800:
            raise ValueError("PyTDX page_size must be between 1 and 800")
        self.selector = selector
        self.api_factory = api_factory
        self.page_size = page_size
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds

    def fetch(self, symbol: str, frequency: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch a complete date range without assuming one server stays healthy."""
        if frequency not in TDX_CATEGORY:
            raise QuantDataError(f"Unsupported PyTDX frequency: {frequency}")
        market, code = market_and_code(symbol)
        requested_start = pd.Timestamp(start_date).normalize()
        requested_end = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)
        nodes = self.selector.rank()
        offset = 0
        pages = 0
        frames = []
        oldest = None
        errors = []
        for endpoint in nodes:
            api = self.api_factory()
            try:
                connected = api.connect(endpoint.host, endpoint.port, time_out=self.timeout_seconds)
                if not connected:
                    raise QuantDataError("connect returned false")
                while pages < self.max_pages:
                    raw = api.get_security_bars(TDX_CATEGORY[frequency], market, code, offset, self.page_size)
                    if raw is None:
                        raise QuantDataError("get_security_bars returned None")
                    page = pd.DataFrame(raw)
                    if page.empty:
                        break
                    if "datetime" not in page:
                        raise QuantDataError("PyTDX page has no datetime column")
                    page["datetime"] = pd.to_datetime(page["datetime"], errors="coerce")
                    page = page.dropna(subset=["datetime"])
                    if page.empty:
                        raise QuantDataError("PyTDX page contains no valid timestamps")
                    frames.append(page)
                    pages += 1
                    offset += len(raw)
                    oldest = page["datetime"].min()
                    if len(raw) < self.page_size:
                        break
                    if oldest <= requested_start:
                        break
                if oldest is not None and oldest <= requested_start:
                    break
            except Exception as exc:
                errors.append(f"{endpoint.host}:{endpoint.port}: {exc}")
            finally:
                try:
                    api.disconnect()
                except Exception:
                    pass
        if not frames:
            raise QuantDataError("PyTDX returned no bars; " + "; ".join(errors[-3:]))
        combined = pd.concat(frames, ignore_index=True)
        source_duplicate_count = int(combined["datetime"].duplicated().sum())
        combined = combined.drop_duplicates("datetime", keep="last").sort_values("datetime")
        combined = combined[(combined["datetime"] >= requested_start) & (combined["datetime"] < requested_end)]
        if combined.empty:
            raise QuantDataError("PyTDX bars do not overlap the requested date range")
        actual_start = combined["datetime"].min().normalize()
        if actual_start > requested_start + pd.Timedelta(days=7):
            raise QuantDataError(
                f"PyTDX history starts at {actual_start.date()}, requested {requested_start.date()}"
            )
        result = combined.reset_index(drop=True)
        result.attrs["source_duplicate_count"] = source_duplicate_count
        return result


def normalize_tdx_bars(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Normalize raw PyTDX minute bars without guessing activity units."""
    result = normalize_minute_bars(frame.rename(columns={"vol": "volume"}), symbol)
    if "source_duplicate_count" in frame.attrs:
        result.attrs["source_duplicate_count"] = int(frame.attrs["source_duplicate_count"])
    elif "datetime" in frame:
        timestamps = pd.to_datetime(frame["datetime"], errors="coerce")
        result.attrs["source_duplicate_count"] = int(timestamps.duplicated().sum())
    return result


def _best_multiplier(source: pd.Series, target: pd.Series, candidates: tuple[float, ...]) -> tuple[float, float]:
    valid = source.gt(0) & target.gt(0)
    if not valid.any():
        return 1.0, float("inf")
    errors = {}
    for multiplier in candidates:
        relative = ((source[valid] * multiplier - target[valid]).abs() / target[valid]).replace([np.inf], np.nan)
        errors[multiplier] = float(relative.median())
    best = min(errors, key=errors.get)
    return float(best), errors[best]


def _activity_comparison(
    bars: pd.DataFrame,
    reference_daily: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    daily = bars.assign(trade_date=bars["datetime"].dt.normalize()).groupby("trade_date", as_index=False).agg(
        close=("close", "last"),
        volume=("volume", "sum"),
        amount=("amount", "sum"),
    )
    reference = reference_daily.rename(columns={"date": "trade_date", "vol": "volume"}).copy()
    reference["trade_date"] = pd.to_datetime(reference["trade_date"], errors="coerce").dt.normalize()
    for column in ("close", "volume", "amount"):
        reference[column] = pd.to_numeric(reference.get(column), errors="coerce")
    reference_days = int(reference["trade_date"].dropna().nunique())
    reference["volume"] = reference["volume"] * 100
    reference["amount"] = reference["amount"] * 1_000
    merged = daily.merge(
        reference[["trade_date", "close", "volume", "amount"]],
        on="trade_date",
        suffixes=("_tdx", "_ref"),
    )
    return merged, reference_days


def calibrate_activity_units(
    bars: pd.DataFrame,
    reference_daily: pd.DataFrame,
) -> tuple[pd.DataFrame, TdxCalibration]:
    """Infer PyTDX volume and amount units from Tushare daily totals."""
    if reference_daily is None or reference_daily.empty:
        return bars.copy(), TdxCalibration(1.0, 1.0, 0, float("inf"), float("inf"), float("inf"))
    merged, reference_days = _activity_comparison(bars, reference_daily)
    if merged.empty:
        return bars.copy(), TdxCalibration(
            1.0,
            1.0,
            0,
            float("inf"),
            float("inf"),
            float("inf"),
            reference_days,
        )
    volume_multiplier, volume_error = _best_multiplier(
        merged["volume_tdx"], merged["volume_ref"], (1.0, 10.0, 100.0)
    )
    amount_multiplier, amount_error = _best_multiplier(
        merged["amount_tdx"], merged["amount_ref"], (1.0, 10.0, 100.0, 1_000.0)
    )
    calibrated = bars.copy()
    calibrated["volume"] *= volume_multiplier
    calibrated["amount"] *= amount_multiplier
    close_error = ((merged["close_tdx"] - merged["close_ref"]).abs() / merged["close_ref"]).max()
    return calibrated, TdxCalibration(
        volume_multiplier=volume_multiplier,
        amount_multiplier=amount_multiplier,
        matched_days=len(merged),
        close_error_max=float(close_error),
        volume_error_median=volume_error,
        amount_error_median=amount_error,
        reference_days=reference_days,
    )


def evaluate_calibrated_activity(
    bars: pd.DataFrame,
    reference_daily: pd.DataFrame,
    unit_calibration: TdxCalibration,
) -> TdxCalibration:
    """Evaluate complete calibrated history while preserving the raw unit multipliers."""
    if reference_daily is None or reference_daily.empty:
        return TdxCalibration(
            unit_calibration.volume_multiplier,
            unit_calibration.amount_multiplier,
            0,
            float("inf"),
            float("inf"),
            float("inf"),
        )
    merged, reference_days = _activity_comparison(bars, reference_daily)
    if merged.empty:
        return TdxCalibration(
            unit_calibration.volume_multiplier,
            unit_calibration.amount_multiplier,
            0,
            float("inf"),
            float("inf"),
            float("inf"),
            reference_days,
        )

    def median_error(column: str) -> float:
        reference = merged[f"{column}_ref"]
        valid = reference.gt(0)
        error = ((merged.loc[valid, f"{column}_tdx"] - reference[valid]).abs() / reference[valid]).median()
        return float(error)

    close_error = ((merged["close_tdx"] - merged["close_ref"]).abs() / merged["close_ref"]).max()
    return TdxCalibration(
        volume_multiplier=unit_calibration.volume_multiplier,
        amount_multiplier=unit_calibration.amount_multiplier,
        matched_days=len(merged),
        close_error_max=float(close_error),
        volume_error_median=median_error("volume"),
        amount_error_median=median_error("amount"),
        reference_days=reference_days,
    )


def validate_tdx_bars(
    bars: pd.DataFrame,
    frequency: str,
    calibration: TdxCalibration,
    min_complete_day_ratio: float = 0.95,
    min_bars_per_day_ratio: float = 0.80,
    close_tolerance_bps: float = 20.0,
    require_cross_validation: bool = True,
    source_duplicate_count: int | None = None,
) -> TdxQualityReport:
    """Validate chronology, OHLC invariants, session density, and daily close agreement."""
    failures = []
    normalized_duplicates = int(bars["datetime"].duplicated().sum()) if not bars.empty else 0
    duplicate_count = normalized_duplicates if source_duplicate_count is None else source_duplicate_count
    invalid = (
        (
            bars["high"].lt(bars[["open", "close", "low"]].max(axis=1))
            | bars["low"].gt(bars[["open", "close", "high"]].min(axis=1))
            | bars[["open", "high", "low", "close"]].le(0).any(axis=1)
        )
        if not bars.empty
        else pd.Series(dtype=bool)
    )
    invalid_count = int(invalid.sum())
    if bars.empty:
        failures.append("empty_bars")
        complete_day_ratio = 0.0
    else:
        counts = bars.assign(trade_date=bars["datetime"].dt.normalize()).groupby("trade_date").size()
        threshold = EXPECTED_BARS_PER_DAY[frequency] * min_bars_per_day_ratio
        complete_days = int(counts.ge(threshold).sum())
        if calibration.reference_days:
            complete_days = min(complete_days, calibration.matched_days)
        denominator = calibration.reference_days or len(counts)
        complete_day_ratio = float(complete_days / denominator) if denominator else 0.0
    if duplicate_count:
        failures.append("duplicate_timestamps")
    if invalid_count:
        failures.append("invalid_ohlc")
    if complete_day_ratio < min_complete_day_ratio:
        failures.append("incomplete_sessions")
    if require_cross_validation and calibration.matched_days == 0:
        failures.append("cross_validation_unavailable")
    if calibration.matched_days and calibration.close_error_max > close_tolerance_bps / 10_000:
        failures.append("cross_source_close_mismatch")
    return TdxQualityReport(
        passed=not failures,
        row_count=len(bars),
        duplicate_count=duplicate_count,
        invalid_ohlc_count=invalid_count,
        complete_day_ratio=complete_day_ratio,
        matched_reference_days=calibration.matched_days,
        close_error_max=calibration.close_error_max,
        start=str(bars["datetime"].min()) if not bars.empty else "",
        end=str(bars["datetime"].max()) if not bars.empty else "",
        failures=failures,
    )


class TushareTdxMetadataProvider:
    """Fetch authoritative adjustment, limit, dividend, and daily comparison data."""

    def __init__(
        self,
        api: Any | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api = api
        self.max_attempts = max_attempts
        self.sleep = sleep

    def _get_api(self) -> Any:
        if self.api is not None:
            return self.api
        setup_env()
        import os

        import tushare as ts

        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token or token.startswith("your_"):
            raise QuantDataError("TUSHARE_TOKEN is required for PyTDX metadata and cross-validation")
        self.api = ts.pro_api(token)
        return self.api

    def _call(self, endpoint: str, **kwargs: Any) -> pd.DataFrame:
        api = self._get_api()
        for attempt in range(self.max_attempts):
            try:
                return getattr(api, endpoint)(**kwargs)
            except Exception as exc:
                if attempt + 1 >= self.max_attempts:
                    raise QuantDataError(f"Unable to fetch Tushare {endpoint}: {exc}") from exc
                delay = min(2 ** attempt, 8)
                logger.warning("Tushare %s metadata request failed; retrying in %ss: %s", endpoint, delay, exc)
                self.sleep(delay)
        raise QuantDataError(f"Unexpected retry exit for Tushare {endpoint}")

    def fetch(self, symbol: str, start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
        date_args = {
            "ts_code": symbol,
            "start_date": start_date.replace("-", ""),
            "end_date": end_date.replace("-", ""),
        }
        return {
            "limits": normalize_limits(self._call("stk_limit", **date_args)),
            "adjustments": normalize_adjustments(self._call("adj_factor", **date_args)),
            "dividends": normalize_dividends(self._call("dividend", ts_code=symbol)),
            "reference_daily": self._call("daily", **date_args),
        }


class TdxQuantDataProvider:
    """Fetch, validate, partition, and load PyTDX minute data."""

    def __init__(
        self,
        config: QuantConfig,
        selector: TdxNodeSelector | None = None,
        pager: TdxBarPager | None = None,
        metadata_provider: MetadataProvider | None = None,
    ):
        self.config = config
        self.cache_root = (
            Path(config.data.cache_dir)
            / config.symbol.replace(".", "_")
            / config.data.frequency
            / "pytdx"
        )
        endpoint_values = config.data.tdx_endpoints
        endpoints = _configured_endpoints(endpoint_values) if endpoint_values else None
        self.selector = selector or TdxNodeSelector(
            self.cache_root / "healthy_nodes.json",
            endpoints=endpoints,
            timeout_seconds=config.data.tdx_connect_timeout_seconds,
            cache_ttl_seconds=config.data.tdx_health_ttl_seconds,
            max_workers=config.data.tdx_probe_workers,
        )
        self.pager = pager or TdxBarPager(
            self.selector,
            page_size=config.data.tdx_page_size,
            max_pages=config.data.tdx_max_pages,
            timeout_seconds=config.data.tdx_connect_timeout_seconds,
        )
        self.metadata_provider = metadata_provider or TushareTdxMetadataProvider()

    def _write_parquet(self, frame: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp.parquet")
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)

    def _write_json(self, payload: dict[str, Any], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _request_spec(self) -> dict[str, str]:
        return {
            "symbol": self.config.symbol,
            "frequency": self.config.data.frequency,
            "start_date": self.config.start_date,
            "end_date": self.config.end_date,
        }

    def _quality_payload(self) -> dict[str, Any]:
        path = self.cache_root / "quality.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _read_cached_bars(self) -> pd.DataFrame:
        partitions = sorted(self.cache_root.glob("bars_*.parquet"))
        if not partitions:
            return pd.DataFrame(columns=normalize_minute_bars(pd.DataFrame(), self.config.symbol).columns)
        return normalize_minute_bars(
            pd.concat((pd.read_parquet(path) for path in partitions), ignore_index=True),
            self.config.symbol,
        )

    def _needs_refresh(self, force: bool) -> bool:
        if force:
            return True
        payload = self._quality_payload()
        if not payload.get("quality", {}).get("passed", False):
            return True
        if payload.get("request") != self._request_spec():
            return True
        if not list(self.cache_root.glob("bars_*.parquet")):
            return True
        configured_end = pd.Timestamp(self.config.end_date).normalize()
        return configured_end >= pd.Timestamp.now().normalize()

    @staticmethod
    def _same_units(previous: dict[str, Any], current: TdxCalibration) -> bool:
        return (
            float(previous.get("volume_multiplier", current.volume_multiplier)) == current.volume_multiplier
            and float(previous.get("amount_multiplier", current.amount_multiplier)) == current.amount_multiplier
        )

    def fetch(self, force: bool = False) -> QuantDataBundle:
        """Refresh missing data and fail closed when validation does not pass."""
        self.cache_root.mkdir(parents=True, exist_ok=True)
        if not self._needs_refresh(force):
            return self.load()

        requested_start = pd.Timestamp(self.config.start_date).normalize()
        cached = pd.DataFrame()
        previous_payload = self._quality_payload()
        if not force:
            cached = self._read_cached_bars()
        can_increment = (
            not cached.empty
            and cached["datetime"].min().normalize() <= requested_start + pd.Timedelta(days=7)
        )
        refresh_start = self.config.start_date
        if can_increment:
            overlap_start = cached["datetime"].max().normalize() - pd.Timedelta(days=3)
            refresh_start = str(max(requested_start, overlap_start).date())

        raw = self.pager.fetch(
            self.config.symbol,
            self.config.data.frequency,
            refresh_start,
            self.config.end_date,
        )
        source_duplicate_count = int(raw.attrs.get("source_duplicate_count", 0))
        fresh = normalize_tdx_bars(raw, self.config.symbol)
        metadata = self.metadata_provider.fetch(
            self.config.symbol,
            self.config.start_date,
            self.config.end_date,
        )
        fresh, unit_calibration = calibrate_activity_units(fresh, metadata["reference_daily"])
        if can_increment and not self._same_units(previous_payload.get("calibration", {}), unit_calibration):
            raw = self.pager.fetch(
                self.config.symbol,
                self.config.data.frequency,
                self.config.start_date,
                self.config.end_date,
            )
            source_duplicate_count = int(raw.attrs.get("source_duplicate_count", 0))
            fresh = normalize_tdx_bars(raw, self.config.symbol)
            fresh, unit_calibration = calibrate_activity_units(fresh, metadata["reference_daily"])
            cached = pd.DataFrame()

        merge_frames = [fresh] if cached.empty else [cached, fresh]
        bars = normalize_minute_bars(pd.concat(merge_frames, ignore_index=True), self.config.symbol)
        start = pd.Timestamp(self.config.start_date)
        end = pd.Timestamp(self.config.end_date) + pd.Timedelta(days=1)
        bars = bars[(bars["datetime"] >= start) & (bars["datetime"] < end)].reset_index(drop=True)
        calibration = evaluate_calibrated_activity(bars, metadata["reference_daily"], unit_calibration)
        quality = validate_tdx_bars(
            bars,
            self.config.data.frequency,
            calibration,
            min_complete_day_ratio=self.config.data.tdx_min_complete_day_ratio,
            min_bars_per_day_ratio=self.config.data.tdx_min_bars_per_day_ratio,
            close_tolerance_bps=self.config.data.tdx_close_tolerance_bps,
            require_cross_validation=self.config.data.tdx_require_cross_validation,
            source_duplicate_count=source_duplicate_count,
        )
        quality_path = self.cache_root / "quality.json"
        payload = {
            "request": self._request_spec(),
            "quality": asdict(quality),
            "calibration": asdict(calibration),
            "validated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        }
        if not quality.passed:
            self._write_json(payload, quality_path)
            raise QuantDataError(f"PyTDX quality validation failed: {', '.join(quality.failures)}")

        pending = dict(payload)
        pending["quality"] = dict(payload["quality"], passed=False, failures=["cache_write_in_progress"])
        self._write_json(pending, quality_path)
        month = bars["datetime"].dt.to_period("M")
        for period in sorted(month.unique()):
            self._write_parquet(bars[month == period], self.cache_root / f"bars_{period}.parquet")
        self._write_parquet(metadata["limits"], self.cache_root / "limits.parquet")
        self._write_parquet(metadata["adjustments"], self.cache_root / "adjustments.parquet")
        self._write_parquet(metadata["dividends"], self.cache_root / "dividends.parquet")
        self._write_parquet(metadata["reference_daily"], self.cache_root / "reference_daily.parquet")
        self._write_json(payload, quality_path)
        return self.load()

    def load(self) -> QuantDataBundle:
        """Load only a previously passed PyTDX cache."""
        quality_path = self.cache_root / "quality.json"
        if not quality_path.exists():
            raise QuantDataError("PyTDX cache has no quality report")
        quality = json.loads(quality_path.read_text(encoding="utf-8")).get("quality", {})
        if not quality.get("passed", False):
            raise QuantDataError("PyTDX cache quality report is not passing")
        partitions = sorted(self.cache_root.glob("bars_*.parquet"))
        if not partitions:
            raise QuantDataError("PyTDX cache has no bar partitions")
        bars = normalize_minute_bars(
            pd.concat((pd.read_parquet(path) for path in partitions), ignore_index=True),
            self.config.symbol,
        )
        start = pd.Timestamp(self.config.start_date)
        end = pd.Timestamp(self.config.end_date) + pd.Timedelta(days=1)
        bars = bars[(bars["datetime"] >= start) & (bars["datetime"] < end)].reset_index(drop=True)

        def read(filename: str) -> pd.DataFrame:
            path = self.cache_root / filename
            return pd.read_parquet(path) if path.exists() else pd.DataFrame()

        return QuantDataBundle(
            bars=bars,
            limits=normalize_limits(read("limits.parquet")),
            adjustments=normalize_adjustments(read("adjustments.parquet")),
            dividends=normalize_dividends(read("dividends.parquet")),
        )


class FailoverQuantDataProvider:
    """Use a primary provider and record an explicit fallback when it is unavailable."""

    def __init__(self, primary: Any, fallback: Any, status_path: str | Path, fallback_name: str = "fallback"):
        self.primary = primary
        self.fallback = fallback
        self.status_path = Path(status_path)
        self.fallback_name = fallback_name

    def _write_status(self, source: str, error: str = "") -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(
            json.dumps(
                {"source": source, "error": error, "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def fetch(self, force: bool = False) -> QuantDataBundle:
        try:
            bundle = self.primary.fetch(force=force)
            self._write_status("pytdx")
            return bundle
        except Exception as exc:
            logger.warning("PyTDX primary failed; using explicit fallback: %s", exc)
            self._write_status(self.fallback_name, str(exc))
            return self.fallback.fetch(force=force)

    def load(self) -> QuantDataBundle:
        try:
            bundle = self.primary.load()
            self._write_status("pytdx")
            return bundle
        except Exception as exc:
            self._write_status(self.fallback_name, str(exc))
            return self.fallback.load()
