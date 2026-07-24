"""Point-in-time all-A daily data cache for portfolio research."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import setup_env
from src.tushare_client import create_tushare_pro_api

from .config import QuantConfig


A_SHARE_PATTERN = re.compile(r"^(?:[036]\d{5}|68\d{4}|[48]\d{5})\.(?:SH|SZ|BJ)$")


@dataclass(frozen=True)
class AllADailyBundle:
    """Normalized market-wide bars and board limits."""

    bars: pd.DataFrame
    limits: pd.DataFrame


def _is_a_share(symbol: str) -> bool:
    return bool(A_SHARE_PATTERN.fullmatch(str(symbol)))


def _frame_fingerprint(frame: pd.DataFrame, columns: list[str]) -> str:
    """Hash a stable projection without depending on a cache file format."""
    values = frame.loc[:, columns].copy().sort_values(columns).reset_index(drop=True)
    payload = values.to_csv(index=False, lineterminator="\n", float_format="%.10g")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TushareAllADailyProvider:
    """Fetch all-A daily bars, limits, and point-in-time historical listings."""

    def __init__(
        self,
        config: QuantConfig,
        api: Any | None = None,
        min_symbols_per_day: int = 1_000,
    ) -> None:
        if config.universe.scope != "all_a" or config.data.frequency != "daily":
            raise ValueError("TushareAllADailyProvider requires all-A daily research configuration")
        self.config = config
        self.cache_dir = Path(config.data.cache_dir) / "all_a_daily"
        self.pause_seconds = max(float(config.data.request_pause_seconds), 0.0)
        self.min_symbols_per_day = max(int(min_symbols_per_day), 1)
        if api is None:
            setup_env()
            token = os.getenv("TUSHARE_TOKEN", "").strip()
            if not token or token.startswith("your_"):
                raise ValueError("TUSHARE_TOKEN is required for all-A portfolio research")
            api = create_tushare_pro_api(token)
        self.api = api

    @staticmethod
    def _read(path: Path) -> pd.DataFrame:
        return pd.read_parquet(path)

    @staticmethod
    def _write(path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)

    def _calendar(self, start_date: str, end_date: str) -> list[str]:
        frame = self.api.trade_cal(
            exchange="",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            is_open="1",
            fields="cal_date",
        )
        if frame is None or frame.empty:
            raise ValueError("Tushare trading calendar is unavailable")
        return sorted(frame["cal_date"].astype(str).unique().tolist())

    def _daily_path(self, trade_date: str) -> Path:
        return self.cache_dir / "bars" / f"{trade_date}.parquet"

    def _limit_path(self, trade_date: str) -> Path:
        return self.cache_dir / "limits" / f"{trade_date}.parquet"

    def _universe_path(self, trade_date: str) -> Path:
        return Path(self.config.universe.snapshot_dir) / f"{trade_date}.parquet"

    def _master_path(self) -> Path:
        return Path(self.config.universe.snapshot_dir) / "source" / "stock_basic.parquet"

    def _namechange_path(self) -> Path:
        return Path(self.config.universe.snapshot_dir) / "source" / "namechange.parquet"

    def _fetch_day(self, trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        daily = self.api.daily(
            trade_date=trade_date,
            fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
        )
        limits = self.api.stk_limit(
            trade_date=trade_date,
            fields="ts_code,trade_date,pre_close,up_limit,down_limit",
        )
        daily = daily if daily is not None else pd.DataFrame()
        limits = limits if limits is not None else pd.DataFrame()
        daily = daily[daily["ts_code"].map(_is_a_share)].copy() if not daily.empty else daily
        limits = limits[limits["ts_code"].map(_is_a_share)].copy() if not limits.empty else limits
        required_daily = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}
        required_limits = {"ts_code", "trade_date", "up_limit", "down_limit"}
        if not required_daily.issubset(daily.columns) or not required_limits.issubset(limits.columns):
            raise ValueError(f"Incomplete all-A schema for {trade_date}")
        if len(daily) < self.min_symbols_per_day or len(limits) < self.min_symbols_per_day:
            raise ValueError(
                f"Incomplete all-A cross-section for {trade_date}: bars={len(daily)}, limits={len(limits)}"
            )
        daily["trade_date"] = daily["trade_date"].astype(str)
        limits["trade_date"] = limits["trade_date"].astype(str)
        numeric = ["open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
        for column in numeric:
            if column in daily:
                daily[column] = pd.to_numeric(daily[column], errors="coerce")
        daily["volume"] = daily["vol"].fillna(0.0) * 100.0
        daily["amount_yuan"] = daily["amount"].fillna(0.0) * 1_000.0
        if daily[["open", "high", "low", "close"]].isna().any().any():
            raise ValueError(f"Invalid OHLC values in all-A bars for {trade_date}")
        if not (
            daily["high"].ge(daily[["open", "close", "low"]].max(axis=1)).all()
            and daily["low"].le(daily[["open", "close", "high"]].min(axis=1)).all()
        ):
            raise ValueError(f"Invalid OHLC ordering in all-A bars for {trade_date}")
        return daily.sort_values("ts_code"), limits.sort_values("ts_code")

    def fetch(self, max_days: int = 0, force: bool = False) -> dict[str, Any]:
        """Incrementally cache complete daily cross-sections and fail closed on partial days."""
        trade_dates = self._calendar(self.config.start_date, self.config.end_date)
        pending = [
            day
            for day in trade_dates
            if force or not self._daily_path(day).exists() or not self._limit_path(day).exists()
        ]
        selected = pending[:max_days] if max_days > 0 else pending
        fetched = []
        for index, trade_date in enumerate(selected):
            daily, limits = self._fetch_day(trade_date)
            self._write(self._daily_path(trade_date), daily)
            self._write(self._limit_path(trade_date), limits)
            fetched.append(trade_date)
            if self.pause_seconds > 0 and index + 1 < len(selected):
                time.sleep(self.pause_seconds)
        fetched_set = set(fetched)
        remaining = [day for day in pending if day not in fetched_set]
        status = {
            "start_date": self.config.start_date,
            "end_date": self.config.end_date,
            "expected_days": len(trade_dates),
            "fetched_days": fetched,
            "remaining_days": remaining,
            "complete": not remaining,
        }
        status_path = self.cache_dir / "status.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        return status

    def fetch_universe(self, trade_date: str, force: bool = False) -> pd.DataFrame:
        """Cache Tushare's historical daily stock list for a point-in-time universe."""
        key = trade_date.replace("-", "")
        path = self._universe_path(key)
        if path.exists() and not force:
            return self._read(path)
        try:
            frame = self.api.bak_basic(
                trade_date=key,
                fields="trade_date,ts_code,name,industry,list_date,total_share,float_share",
            )
        except RuntimeError as error:
            if "bak_basic" not in str(error) or "权限" not in str(error):
                raise
            frame = self._reconstruct_universe(key, force=force)
        frame = frame if frame is not None else pd.DataFrame()
        required = {"trade_date", "ts_code", "name", "list_date"}
        if not required.issubset(frame.columns):
            raise ValueError(f"Historical all-A universe is unavailable for {key}")
        frame = frame[frame["ts_code"].map(_is_a_share)].copy()
        if len(frame) < self.min_symbols_per_day:
            raise ValueError(f"Historical all-A universe is incomplete for {key}: rows={len(frame)}")
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame["list_date"] = frame["list_date"].astype(str)
        frame = frame.sort_values("ts_code").drop_duplicates("ts_code", keep="last")
        self._write(path, frame)
        return frame

    def _stock_master(self, force: bool = False) -> pd.DataFrame:
        path = self._master_path()
        if path.exists() and not force:
            return self._read(path)
        frames = []
        for list_status in ("L", "D", "P"):
            frame = self.api.stock_basic(
                exchange="",
                list_status=list_status,
                fields="ts_code,name,industry,market,list_date,delist_date",
            )
            if frame is not None and not frame.empty:
                values = frame.copy()
                values["list_status"] = list_status
                frames.append(values)
        if not frames:
            raise ValueError("Tushare stock master is unavailable")
        master = pd.concat(frames, ignore_index=True)
        master = master[master["ts_code"].map(_is_a_share)].copy()
        master = master.sort_values(["ts_code", "list_status"]).drop_duplicates("ts_code", keep="first")
        if len(master) < self.min_symbols_per_day:
            raise ValueError(f"Tushare stock master is incomplete: rows={len(master)}")
        self._write(path, master)
        return master

    def _name_changes(self, force: bool = False) -> pd.DataFrame:
        path = self._namechange_path()
        if path.exists() and not force:
            return self._read(path)
        page_size = 10_000
        pages = []
        for offset in range(0, 100_000, page_size):
            frame = self.api.namechange(
                offset=offset,
                limit=page_size,
                fields="ts_code,name,start_date,end_date,ann_date,change_reason",
            )
            frame = frame if frame is not None else pd.DataFrame()
            if not frame.empty:
                pages.append(frame)
            if len(frame) < page_size:
                break
        else:
            raise ValueError("Tushare name-change pagination exceeded the safety limit")
        if not pages:
            raise ValueError("Tushare name-change history is unavailable")
        changes = pd.concat(pages, ignore_index=True)
        changes = changes[changes["ts_code"].map(_is_a_share)].copy()
        required = {"ts_code", "name", "start_date", "end_date"}
        if not required.issubset(changes.columns):
            raise ValueError("Tushare name-change history schema is incomplete")
        changes = changes.sort_values(["ts_code", "start_date", "end_date"], na_position="last")
        changes = changes.drop_duplicates(["ts_code", "name", "start_date", "end_date"], keep="last")
        self._write(path, changes)
        return changes

    def _reconstruct_universe(self, trade_date: str, force: bool = False) -> pd.DataFrame:
        """Rebuild historical membership and names without using today's listed set."""
        master = self._stock_master(force=force).copy()
        master["list_date"] = master["list_date"].fillna("").astype(str)
        master["delist_date"] = master["delist_date"].fillna("").astype(str)
        active = master[
            master["list_date"].le(trade_date)
            & (master["delist_date"].eq("") | master["delist_date"].ge(trade_date))
        ].copy()
        changes = self._name_changes(force=force).copy()
        changes["start_date"] = changes["start_date"].fillna("").astype(str)
        changes["end_date"] = changes["end_date"].fillna("").astype(str)
        valid_names = changes[
            changes["start_date"].le(trade_date)
            & (changes["end_date"].eq("") | changes["end_date"].ge(trade_date))
        ].copy()
        valid_names = valid_names.sort_values(["ts_code", "start_date"]).drop_duplicates("ts_code", keep="last")
        historical_names = valid_names.set_index("ts_code")["name"]
        active["name"] = active["ts_code"].map(historical_names).fillna(active["name"])
        active["trade_date"] = trade_date
        active["snapshot_source"] = "stock_basic_namechange_reconstruction"
        return active

    def load(self, start_date: str, end_date: str) -> AllADailyBundle:
        """Load an exact cached range and reject missing trading-day partitions."""
        trade_dates = self._calendar(start_date, end_date)
        missing = [
            day
            for day in trade_dates
            if not self._daily_path(day).exists() or not self._limit_path(day).exists()
        ]
        if missing:
            raise FileNotFoundError(f"All-A daily cache is missing {len(missing)} sessions; first={missing[0]}")
        bars = pd.concat([self._read(self._daily_path(day)) for day in trade_dates], ignore_index=True)
        limits = pd.concat([self._read(self._limit_path(day)) for day in trade_dates], ignore_index=True)
        return AllADailyBundle(bars=bars, limits=limits)

    def load_cached(self, start_date: str, end_date: str) -> AllADailyBundle:
        """Load cached partitions without making a calendar API request."""
        start_key = start_date.replace("-", "")
        end_key = end_date.replace("-", "")
        daily_paths = sorted(
            path for path in (self.cache_dir / "bars").glob("*.parquet") if start_key <= path.stem <= end_key
        )
        limit_paths = sorted(
            path for path in (self.cache_dir / "limits").glob("*.parquet") if start_key <= path.stem <= end_key
        )
        daily_dates = [path.stem for path in daily_paths]
        limit_dates = [path.stem for path in limit_paths]
        if not daily_paths or daily_dates != limit_dates:
            raise FileNotFoundError("All-A cached daily and limit partitions are incomplete or mismatched")
        bars = pd.concat([self._read(path) for path in daily_paths], ignore_index=True)
        limits = pd.concat([self._read(path) for path in limit_paths], ignore_index=True)
        return AllADailyBundle(bars=bars, limits=limits)

    def reconstruct_cached_universe(self, trade_date: str) -> pd.DataFrame:
        """Build and cache a historical universe from already cached master and name-change data."""
        key = trade_date.replace("-", "")
        path = self._universe_path(key)
        if path.exists():
            return self._read(path)
        if not self._master_path().exists() or not self._namechange_path().exists():
            raise FileNotFoundError("Historical stock master and name-change cache are required")
        frame = self._reconstruct_universe(key)
        if len(frame) < self.min_symbols_per_day:
            raise ValueError(f"Historical all-A universe is incomplete for {key}: rows={len(frame)}")
        frame = frame.sort_values("ts_code").drop_duplicates("ts_code", keep="last")
        self._write(path, frame)
        return frame

    @staticmethod
    def fingerprint(frame: pd.DataFrame, columns: list[str]) -> str:
        """Expose deterministic frame fingerprints for research manifests."""
        return _frame_fingerprint(frame, columns)
