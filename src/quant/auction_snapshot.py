"""Interfaces and local storage for genuine A-share call-auction snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd


AUCTION_COLUMNS = (
    "symbol",
    "industry",
    "timestamp",
    "indicative_price",
    "prior_close",
    "matched_volume",
    "unmatched_bid",
    "unmatched_ask",
)


class AuctionSnapshotProvider(Protocol):
    def load(self, trade_date: str | pd.Timestamp) -> pd.DataFrame:
        """Load genuine 09:15-09:25 snapshots for one session."""


@dataclass(frozen=True)
class LocalAuctionSnapshotProvider:
    """Read immutable auction snapshots exported by a licensed market-data source."""

    root: str = "data/quant_factor_snapshots/auction"

    def load(self, trade_date: str | pd.Timestamp) -> pd.DataFrame:
        date_key = pd.Timestamp(trade_date).strftime("%Y%m%d")
        root = Path(self.root)
        parquet = root / f"{date_key}.parquet"
        csv = root / f"{date_key}.csv"
        if parquet.exists():
            frame = pd.read_parquet(parquet)
        elif csv.exists():
            frame = pd.read_csv(csv)
        else:
            raise FileNotFoundError(f"No genuine auction snapshot exists for {date_key}")
        missing = set(AUCTION_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError("Auction snapshot is missing fields: " + ",".join(sorted(missing)))
        values = frame.loc[:, AUCTION_COLUMNS].copy()
        values["timestamp"] = pd.to_datetime(values["timestamp"], errors="coerce")
        values = values[values["timestamp"].notna()].copy()
        values = values[values["timestamp"].dt.strftime("%Y%m%d").eq(date_key)]
        values = values[values["timestamp"].dt.strftime("%H:%M").between("09:15", "09:25")]
        if values.empty:
            raise ValueError(f"No valid 09:15-09:25 auction rows exist for {date_key}")
        return values.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
