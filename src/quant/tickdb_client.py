"""Safe TickDB REST client for reproducible quantitative snapshots."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests

from src.config import setup_env


TRIAL_A_SHARE_SYMBOLS = frozenset(
    {
        "600519.SH",
        "601318.SH",
        "600036.SH",
        "000858.SZ",
        "000333.SZ",
        "600900.SH",
        "601012.SH",
        "000002.SZ",
        "600276.SH",
        "002594.SZ",
    }
)
TICKDB_KEY_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?TICKDB_API_KEY\s*=\s*(['\"]?)([A-Za-z0-9._-]+)\1\s*(?:#.*)?$"
)


class TickDBError(RuntimeError):
    """Raised when TickDB authentication, transport, or response validation fails."""


def load_tickdb_key_from_shell() -> str:
    """Read a literal TickDB key assignment without executing a shell profile."""
    configured_path = os.getenv("TICKDB_ZSHRC_PATH", "").strip()
    if configured_path:
        candidates = [Path(configured_path).expanduser()]
    else:
        candidates = [Path.home() / ".zshrc", Path.home() / "zshrc"]
    for path in candidates:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError, UnicodeError):
            continue
        for line in reversed(lines):
            match = TICKDB_KEY_ASSIGNMENT.fullmatch(line)
            if match:
                return match.group(2)
    return ""


@dataclass(frozen=True)
class TickDBClientConfig:
    base_url: str = "https://api.tickdb.ai"
    trial_key_url: str = "https://tickdb.ai/api/public/claw-keys"
    timeout_seconds: float = 20.0
    allow_trial_key: bool = True


class TickDBClient:
    """Call documented TickDB endpoints without persisting API credentials."""

    def __init__(
        self,
        config: TickDBClientConfig | None = None,
        session: requests.Session | None = None,
        api_key: str | None = None,
    ) -> None:
        self.config = config or TickDBClientConfig()
        self.session = session or requests.Session()
        self.explicit_api_key = (api_key or "").strip()
        self._ephemeral_trial_key = ""

    @staticmethod
    def normalize_symbols(values: str | Iterable[str]) -> list[str]:
        """Normalize comma-delimited or iterable symbol input."""
        if isinstance(values, str):
            return [item.strip() for item in values.split(",") if item.strip()]
        return [str(item).strip() for item in values if str(item).strip()]

    def _formal_key(self) -> str:
        if self.explicit_api_key:
            return self.explicit_api_key
        setup_env()
        return os.getenv("TICKDB_API_KEY", "").strip() or load_tickdb_key_from_shell()

    def _trial_key(self, symbols: list[str]) -> str:
        unsupported = sorted(set(symbols) - TRIAL_A_SHARE_SYMBOLS)
        if unsupported:
            raise TickDBError(
                "A formal TICKDB_API_KEY is required for symbols outside the trial A-share list: "
                + ",".join(unsupported)
            )
        if not self.config.allow_trial_key:
            raise TickDBError("TICKDB_API_KEY is required")
        if self._ephemeral_trial_key:
            return self._ephemeral_trial_key
        try:
            response = self.session.get(self.config.trial_key_url, timeout=self.config.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise TickDBError(f"TickDB trial key request failed: {exc}") from exc
        key = str(payload.get("apiKey", "")).strip()
        if not key:
            raise TickDBError("TickDB trial key response does not contain apiKey")
        self._ephemeral_trial_key = key
        return self._ephemeral_trial_key

    def _request(self, path: str, params: dict[str, Any], symbols: list[str]) -> Any:
        key = self._formal_key() or self._trial_key(symbols)
        try:
            response = self.session.get(
                self.config.base_url.rstrip("/") + path,
                params={name: value for name, value in params.items() if value is not None},
                headers={"X-API-Key": key},
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TickDBError(f"TickDB network error on {path}: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise TickDBError(f"TickDB returned non-JSON content on {path}") from exc
        if not response.ok or payload.get("code", 0) not in (0, "0"):
            code = payload.get("code", response.status_code)
            message = payload.get("message", "Unknown TickDB error")
            raise TickDBError(f"TickDB error {code} on {path}: {message}")
        return payload.get("data", payload)

    def get_kline(
        self,
        symbol: str,
        interval: str,
        limit: int = 1000,
        start_time: int | None = None,
        end_time: int | None = None,
        asset_type: str | None = None,
    ) -> Any:
        return self._request(
            "/v1/market/kline",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
                "type": asset_type,
            },
            [symbol],
        )

    def get_ticker(self, symbols: str | Iterable[str]) -> Any:
        values = self.normalize_symbols(symbols)
        return self._request("/v1/market/ticker", {"symbols": ",".join(values)}, values)

    def get_intraday(self, symbols: str | Iterable[str]) -> Any:
        values = self.normalize_symbols(symbols)
        return self._request("/v1/market/intraday", {"symbols": ",".join(values)}, values)

    def get_market_metrics(self, symbols: str | Iterable[str]) -> Any:
        values = self.normalize_symbols(symbols)
        return self._request("/v1/market/calc-index", {"symbols": ",".join(values)}, values)

    def get_capital_flow(self, symbol: str) -> Any:
        return self._request("/v1/market/capital-flow", {"symbol": symbol}, [symbol])

    def get_trade_days(self, start_date: str, end_date: str) -> Any:
        formal_key = self._formal_key()
        if not formal_key:
            raise TickDBError("A formal TICKDB_API_KEY is required for market-wide calendar queries")
        return self._request(
            "/v1/market/trade-days",
            {"market": "CN", "beg_day": start_date, "end_day": end_date},
            [],
        )
