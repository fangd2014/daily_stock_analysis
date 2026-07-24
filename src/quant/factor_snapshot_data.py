"""Point-in-time valuation, money-flow, and financial snapshots for all-A research."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd

from .all_a_data import TushareAllADailyProvider, _is_a_share
from .config import QuantConfig


DAILY_BASIC_FIELDS = "ts_code,trade_date,turnover_rate_f,pe_ttm,pb,total_mv,circ_mv"
MONEYFLOW_FIELDS = (
    "ts_code,trade_date,buy_lg_amount,buy_elg_amount,sell_lg_amount,sell_elg_amount,net_mf_amount"
)
FINANCIAL_FIELDS = (
    "ts_code,ann_date,end_date,update_flag,roe_dt,roa,profit_dedt,grossprofit_margin,ocf_to_or,"
    "debt_to_assets,q_profit_yoy"
)


class FactorSnapshotProvider:
    """Cache factor snapshots and reject any attempt to substitute current values for history."""

    def __init__(
        self,
        config: QuantConfig,
        api: Any | None = None,
        min_symbols_per_snapshot: int = 1_000,
    ) -> None:
        self.config = config
        self.market_provider = TushareAllADailyProvider(config, api=api)
        self.api = self.market_provider.api
        self.root = Path(config.data.cache_dir) / "all_a_factors"
        self.pause_seconds = max(float(config.data.request_pause_seconds), 0.0)
        self.min_symbols = max(int(min_symbols_per_snapshot), 1)
        self._financial_cache: pd.DataFrame | None = None

    @staticmethod
    def _write(path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)

    @staticmethod
    def _read(path: Path) -> pd.DataFrame:
        return pd.read_parquet(path)

    def _snapshot_path(self, dataset: str, key: str) -> Path:
        return self.root / dataset / f"{key}.parquet"

    def required_monthly_cutoffs(self) -> list[str]:
        """Return the final session before each configured holdout month."""
        bars_dir = self.market_provider.cache_dir / "bars"
        trade_dates = sorted(path.stem for path in bars_dir.glob("*.parquet"))
        if not trade_dates:
            raise FileNotFoundError("All-A daily bars must be cached before factor snapshots")
        cutoffs = []
        months = pd.period_range(
            self.config.optimization.holdout_start,
            self.config.optimization.holdout_end,
            freq="M",
        )
        for month in months:
            month_key = month.strftime("%Y%m")
            month_dates = [trade_date for trade_date in trade_dates if trade_date.startswith(month_key)]
            if not month_dates:
                raise ValueError(f"No cached session exists for {month}")
            first_index = trade_dates.index(month_dates[0])
            if first_index == 0:
                raise ValueError(f"No feature cutoff exists before {month}")
            cutoffs.append(trade_dates[first_index - 1])
        return cutoffs

    def _fetch_cross_section(self, dataset: str, cutoff: str, force: bool) -> bool:
        path = self._snapshot_path(dataset, cutoff)
        if path.exists() and not force:
            return False
        if dataset == "daily_basic":
            frame = self.api.daily_basic(trade_date=cutoff, fields=DAILY_BASIC_FIELDS)
        elif dataset == "moneyflow":
            frame = self.api.moneyflow(trade_date=cutoff, fields=MONEYFLOW_FIELDS)
        else:
            raise ValueError(f"Unsupported cross-sectional factor dataset: {dataset}")
        frame = frame if frame is not None else pd.DataFrame()
        if "ts_code" not in frame or "trade_date" not in frame:
            raise ValueError(f"Incomplete {dataset} schema for {cutoff}")
        frame = frame[frame["ts_code"].map(_is_a_share)].copy()
        if len(frame) < self.min_symbols:
            raise ValueError(f"Incomplete {dataset} cross-section for {cutoff}: rows={len(frame)}")
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame = frame.sort_values("ts_code").drop_duplicates("ts_code", keep="last")
        self._write(path, frame)
        return True

    def required_financial_symbols(self, names_per_month: int = 150) -> list[str]:
        """Prefilter a bounded point-in-time value universe before per-symbol financial requests."""
        symbols: set[str] = set()
        for cutoff in self.required_monthly_cutoffs():
            frame = self.load_daily_basic(cutoff).copy()
            frame["pe_ttm"] = pd.to_numeric(frame["pe_ttm"], errors="coerce")
            frame["pb"] = pd.to_numeric(frame["pb"], errors="coerce")
            frame = frame[frame["pe_ttm"].gt(0) & frame["pb"].gt(0)].copy()
            frame["value_prefilter"] = (
                (1.0 - frame["pe_ttm"].rank(pct=True)) * 0.60
                + (1.0 - frame["pb"].rank(pct=True)) * 0.40
            )
            symbols.update(frame.nlargest(names_per_month, "value_prefilter")["ts_code"].astype(str))
        return sorted(symbols)

    def _financial_symbol_path(self, symbol: str) -> Path:
        return self.root / "fina_indicator_by_symbol" / f"{symbol.replace('.', '_')}.parquet"

    def _fetch_financial_symbol(self, symbol: str, force: bool) -> bool:
        path = self._financial_symbol_path(symbol)
        if path.exists() and not force:
            return False
        frame = self.api.fina_indicator(
            ts_code=symbol,
            start_date=(pd.Timestamp(self.config.start_date) - pd.DateOffset(years=2)).strftime("%Y%m%d"),
            end_date=self.config.end_date.replace("-", ""),
            fields=FINANCIAL_FIELDS,
        )
        frame = frame if frame is not None else pd.DataFrame()
        required = {"ts_code", "ann_date", "end_date"}
        if not required.issubset(frame.columns):
            if frame.empty:
                return False
            raise ValueError(f"Incomplete fina_indicator schema for {symbol}")
        frame = frame[frame["ts_code"].map(_is_a_share)].copy()
        if frame.empty:
            return False
        frame["ann_date"] = frame["ann_date"].fillna("").astype(str)
        frame["end_date"] = frame["end_date"].fillna("").astype(str)
        frame = frame.sort_values(["ts_code", "ann_date", "update_flag"], na_position="first")
        frame = frame.drop_duplicates(["ts_code", "ann_date", "end_date"], keep="last")
        self._write(path, frame)
        return True

    def fetch(self, force: bool = False) -> dict[str, Any]:
        """Fetch monthly market snapshots and all potentially observable quarterly reports."""
        fetched = []
        request_pause = max(self.pause_seconds, 0.7)
        for cutoff in self.required_monthly_cutoffs():
            for dataset in ("daily_basic", "moneyflow"):
                if self._fetch_cross_section(dataset, cutoff, force):
                    fetched.append(f"{dataset}:{cutoff}")
                    time.sleep(request_pause)
        financial_symbols = self.required_financial_symbols()
        for symbol in financial_symbols:
            if self._fetch_financial_symbol(symbol, force):
                fetched.append(f"fina_indicator:{symbol}")
                time.sleep(request_pause)
        return {
            "cutoffs": self.required_monthly_cutoffs(),
            "financial_symbols": financial_symbols,
            "fetched": fetched,
            "complete": True,
        }

    def load_daily_basic(self, cutoff: str) -> pd.DataFrame:
        path = self._snapshot_path("daily_basic", cutoff.replace("-", ""))
        if not path.exists():
            raise FileNotFoundError(f"daily_basic snapshot is missing for {cutoff}")
        return self._read(path)

    def load_moneyflow(self, cutoff: str) -> pd.DataFrame:
        path = self._snapshot_path("moneyflow", cutoff.replace("-", ""))
        if not path.exists():
            raise FileNotFoundError(f"moneyflow snapshot is missing for {cutoff}")
        return self._read(path)

    def load_financials(self, cutoff: str) -> pd.DataFrame:
        """Return the latest report that was publicly announced by the supplied cutoff."""
        cutoff_key = cutoff.replace("-", "")
        paths = sorted((self.root / "fina_indicator_by_symbol").glob("*.parquet"))
        if not paths:
            raise FileNotFoundError("No per-symbol fina_indicator cache is available")
        if self._financial_cache is None:
            self._financial_cache = pd.concat([self._read(path) for path in paths], ignore_index=True)
        frame = self._financial_cache.copy()
        frame["ann_date"] = frame["ann_date"].fillna("").astype(str)
        frame["end_date"] = frame["end_date"].fillna("").astype(str)
        frame = frame[frame["ann_date"].ne("") & frame["ann_date"].le(cutoff_key)].copy()
        if frame.empty:
            raise ValueError(f"No financial report was observable by {cutoff}")
        frame = frame.sort_values(["ts_code", "end_date", "ann_date", "update_flag"], na_position="first")
        return frame.drop_duplicates("ts_code", keep="last").reset_index(drop=True)
