"""Tests for centralized Tushare client endpoint configuration."""

from functools import partial

import pytest
import tushare as ts

from src.tushare_client import DEFAULT_TUSHARE_API_URL, create_tushare_pro_api, get_tushare_api_url


class FakeDataApi:
    def __init__(self):
        self._DataApi__token = "test-token"
        self._DataApi__timeout = 30

    def __getattr__(self, name):
        return partial(self.query, name)


class FakeResponse:
    status_code = 200

    def __bool__(self):
        return True

    def json(self):
        return {"code": 0, "data": {"fields": ["cal_date"], "items": [["20250102"]]}}


def test_create_tushare_api_uses_new_default_url_without_appending_api_name(monkeypatch):
    api = FakeDataApi()
    tokens = []
    requests = []
    monkeypatch.delenv("TUSHARE_API_URL", raising=False)
    monkeypatch.setattr(ts, "pro_api", lambda token=None: tokens.append(token) or api)
    monkeypatch.setattr(
        "src.tushare_client.requests.post",
        lambda url, **kwargs: requests.append((url, kwargs)) or FakeResponse(),
    )

    result = create_tushare_pro_api("test-token")
    frame = result.trade_cal(exchange="SSE")

    assert result is api
    assert tokens == ["test-token"]
    assert getattr(result, "_DataApi__http_url") == DEFAULT_TUSHARE_API_URL
    assert requests[0][0] == "http://api.tushare.pro"
    assert requests[0][1]["json"]["api_name"] == "trade_cal"
    assert requests[0][1]["json"]["params"] == {"exchange": "SSE"}
    assert frame.iloc[0]["cal_date"] == "20250102"


def test_create_tushare_api_allows_environment_override(monkeypatch):
    api = FakeDataApi()
    monkeypatch.setenv("TUSHARE_API_URL", "https://proxy.example.test/tushare/")
    monkeypatch.setattr(ts, "pro_api", lambda token=None: api)

    result = create_tushare_pro_api("test-token")

    assert getattr(result, "_DataApi__http_url") == "https://proxy.example.test/tushare"


def test_tushare_api_url_rejects_invalid_values():
    with pytest.raises(ValueError, match="absolute HTTP"):
        get_tushare_api_url("api.tushare.pro")
