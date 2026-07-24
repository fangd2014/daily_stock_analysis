"""Point-in-time TickDB snapshots for four-factor research and paper trading."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .tickdb_client import TickDBClient, TickDBError


LOGGER = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class TickDBSnapshotConfig:
    root: str = "data/quant_factor_snapshots/tickdb"
    kline_interval: str = "1d"
    kline_limit: int = 121
    include_capital_flow: bool = True


class TickDBFactorSnapshotStore:
    """Capture raw API responses with an observable timestamp and no credentials."""

    def __init__(
        self,
        client: TickDBClient | None = None,
        config: TickDBSnapshotConfig | None = None,
    ) -> None:
        self.client = client or TickDBClient()
        self.config = config or TickDBSnapshotConfig()
        self.root = Path(self.config.root)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _symbol_file(symbol: str) -> str:
        return symbol.replace(".", "_").replace("/", "_") + ".json"

    def capture(
        self,
        symbols: str | Iterable[str],
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Capture one immutable market snapshot and return its manifest."""
        values = self.client.normalize_symbols(symbols)
        if not values:
            raise ValueError("At least one symbol is required")
        current = observed_at or datetime.now(SHANGHAI)
        if current.tzinfo is None:
            current = current.replace(tzinfo=SHANGHAI)
        else:
            current = current.astimezone(SHANGHAI)
        snapshot_id = current.strftime("%Y%m%d/%H%M%S")
        output = self.root / snapshot_id
        LOGGER.info("TickDB snapshot started: symbols=%d output=%s", len(values), output)

        successes: list[str] = []
        errors: dict[str, str] = {}
        common_requests = (
            ("ticker", lambda: self.client.get_ticker(values)),
            ("intraday", lambda: self.client.get_intraday(values)),
            ("market_metrics", lambda: self.client.get_market_metrics(values)),
        )
        for dataset, request in common_requests:
            try:
                self._write_json(output / f"{dataset}.json", request())
            except TickDBError as exc:
                errors[dataset] = str(exc)
                LOGGER.error("TickDB snapshot dataset failed: dataset=%s error=%s", dataset, exc)
        for symbol in values:
            LOGGER.info("TickDB snapshot symbol: %s", symbol)
            try:
                kline = self.client.get_kline(
                    symbol,
                    interval=self.config.kline_interval,
                    limit=self.config.kline_limit,
                )
                self._write_json(output / "kline" / self._symbol_file(symbol), kline)
                if self.config.include_capital_flow:
                    capital_flow = self.client.get_capital_flow(symbol)
                    self._write_json(output / "capital_flow" / self._symbol_file(symbol), capital_flow)
                successes.append(symbol)
            except TickDBError as exc:
                errors[f"symbol:{symbol}"] = str(exc)
                LOGGER.error("TickDB snapshot failed: symbol=%s error=%s", symbol, exc)

        manifest = {
            "provider": "TickDB.ai",
            "observed_at": current.isoformat(),
            "symbols_requested": values,
            "symbols_completed": successes,
            "errors": errors,
            "kline_interval": self.config.kline_interval,
            "kline_limit": self.config.kline_limit,
            "includes_capital_flow": self.config.include_capital_flow,
            "credential_persisted": False,
            "complete": len(successes) == len(values) and not errors,
        }
        self._write_json(output / "manifest.json", manifest)
        LOGGER.info("TickDB snapshot completed: completed=%d errors=%d", len(successes), len(errors))
        return {**manifest, "snapshot_dir": str(output)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture a point-in-time TickDB market snapshot")
    parser.add_argument("--symbols", required=True, help="Comma-delimited TickDB symbols")
    parser.add_argument("--root", default=TickDBSnapshotConfig.root)
    parser.add_argument("--interval", default=TickDBSnapshotConfig.kline_interval)
    parser.add_argument("--limit", type=int, default=TickDBSnapshotConfig.kline_limit)
    parser.add_argument("--skip-capital-flow", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    store = TickDBFactorSnapshotStore(
        config=TickDBSnapshotConfig(
            root=args.root,
            kline_interval=args.interval,
            kline_limit=args.limit,
            include_capital_flow=not args.skip_capital_flow,
        )
    )
    result = store.capture(args.symbols)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
