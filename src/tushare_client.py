"""Centralized Tushare Pro client configuration."""

from __future__ import annotations

import os
from types import MethodType
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests

DEFAULT_TUSHARE_API_URL = "http://api.tushare.pro"


def get_tushare_api_url(api_url: str | None = None) -> str:
    """Return a validated Tushare base URL without a trailing slash."""
    value = (api_url or os.getenv("TUSHARE_API_URL") or DEFAULT_TUSHARE_API_URL).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("TUSHARE_API_URL must be an absolute HTTP or HTTPS URL")
    return value


def create_tushare_pro_api(token: str | None = None, api_url: str | None = None) -> Any:
    """Create a Tushare Pro API client using the project-wide endpoint."""
    import tushare as ts

    api = ts.pro_api(token) if token else ts.pro_api()
    endpoint = get_tushare_api_url(api_url)
    setattr(api, "_DataApi__http_url", endpoint)

    def query_exact_url(client: Any, api_name: str, fields: str = "", **kwargs: Any) -> pd.DataFrame:
        payload = {
            "api_name": api_name,
            "token": getattr(client, "_DataApi__token"),
            "params": kwargs,
            "fields": fields,
        }
        response = requests.post(
            endpoint,
            json=payload,
            timeout=getattr(client, "_DataApi__timeout", 30),
        )
        if not response:
            raise RuntimeError(f"Tushare request failed with HTTP status {response.status_code}")
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(str(result.get("msg", "Unknown Tushare API error")))
        data = result.get("data") or {}
        return pd.DataFrame(data.get("items") or [], columns=data.get("fields") or [])

    api.query = MethodType(query_exact_url, api)
    return api
