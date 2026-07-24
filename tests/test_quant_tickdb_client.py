"""Tests for the credential-safe TickDB client and snapshot store."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

from src.quant.tickdb_client import (
    TickDBClient,
    TickDBError,
    load_tickdb_key_from_shell,
)
from src.quant.tickdb_factor_snapshot import TickDBFactorSnapshotStore, TickDBSnapshotConfig


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self.payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, endpoint_payload=None):
        self.endpoint_payload = endpoint_payload or {"code": 0, "data": [{"close": 1.0}]}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/claw-keys"):
            return FakeResponse({"apiKey": "temporary-trial-key"})
        return FakeResponse(self.endpoint_payload)


def test_formal_key_takes_precedence_and_is_sent_only_as_a_header(monkeypatch):
    monkeypatch.setenv("TICKDB_API_KEY", "formal-key")
    session = FakeSession()
    client = TickDBClient(session=session, api_key="explicit-key")

    client.get_ticker("688008.SH")

    assert len(session.calls) == 1
    _, kwargs = session.calls[0]
    assert kwargs["headers"] == {"X-API-Key": "explicit-key"}
    assert "explicit-key" not in str(kwargs["params"])


def test_trial_key_is_ephemeral_and_rejects_symbols_outside_trial_list(tmp_path, monkeypatch):
    monkeypatch.delenv("TICKDB_API_KEY", raising=False)
    monkeypatch.setenv("TICKDB_ZSHRC_PATH", str(tmp_path / "missing-zshrc"))
    session = FakeSession()
    client = TickDBClient(session=session)

    client.get_ticker("600519.SH")
    client.get_market_metrics("600519.SH")

    assert len(session.calls) == 3
    assert session.calls[1][1]["headers"]["X-API-Key"] == "temporary-trial-key"
    assert session.calls[2][1]["headers"]["X-API-Key"] == "temporary-trial-key"
    with pytest.raises(TickDBError, match="formal TICKDB_API_KEY"):
        client.get_ticker("688008.SH")


def test_api_error_code_is_not_silently_accepted():
    session = FakeSession({"code": 42901, "message": "quota exceeded"})
    client = TickDBClient(session=session, api_key="formal-key")

    with pytest.raises(TickDBError, match="quota exceeded"):
        client.get_ticker("688008.SH")


def test_formal_key_is_loaded_from_zshrc_without_executing_it(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    profile = tmp_path / ".zshrc"
    profile.write_text(
        "export TICKDB_API_KEY=$(touch must-not-exist)\n"
        "export TICKDB_API_KEY='formal-key-from-zshrc'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TICKDB_API_KEY", raising=False)
    monkeypatch.setenv("TICKDB_ZSHRC_PATH", str(profile))
    session = FakeSession()

    assert load_tickdb_key_from_shell() == "formal-key-from-zshrc"
    TickDBClient(session=session).get_ticker("688008.SH")

    assert len(session.calls) == 1
    assert session.calls[0][1]["headers"] == {"X-API-Key": "formal-key-from-zshrc"}
    assert not marker.exists()


class FakeTickDBClient:
    normalize_symbols = staticmethod(TickDBClient.normalize_symbols)

    def get_ticker(self, symbols):
        return {"symbols": list(symbols)}

    def get_intraday(self, symbols):
        return {"symbols": list(symbols)}

    def get_market_metrics(self, symbols):
        return {"symbols": list(symbols)}

    def get_kline(self, symbol, interval, limit):
        return {"symbol": symbol, "interval": interval, "limit": limit}

    def get_capital_flow(self, symbol):
        return {"symbol": symbol, "main_net_inflow": 1.0}


def test_snapshot_manifest_never_persists_credentials(tmp_path):
    store = TickDBFactorSnapshotStore(
        client=FakeTickDBClient(),
        config=TickDBSnapshotConfig(root=str(tmp_path)),
    )

    result = store.capture(
        ["688008.SH"],
        observed_at=datetime(2026, 7, 24, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    manifest = (tmp_path / "20260724" / "200000" / "manifest.json").read_text(encoding="utf-8")
    assert result["complete"] is True
    assert result["credential_persisted"] is False
    assert "api_key" not in manifest.lower()
