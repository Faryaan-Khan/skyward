# Copyright (c) 2026 Faryaan Khan
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Currency exchange rates (USD base) from open.er-api.com: free, no key, ~160 currencies,
updated daily. Cached in memory for an hour. Rates by ExchangeRate-API (https://www.exchangerate-api.com).
"""
from __future__ import annotations

import time

import httpx

URL = "https://open.er-api.com/v6/latest/USD"
TTL = 3600
_cache: dict = {"at": 0.0, "data": None}


def get_rates(fetch=None) -> dict:
    if _cache["data"] and time.monotonic() - _cache["at"] < TTL:
        return _cache["data"]
    try:
        body = fetch() if fetch else httpx.get(URL, timeout=10).json()
        if body.get("result") != "success":
            raise ValueError(body.get("error-type", "bad response"))
        data = {"base": "USD", "updated": body.get("time_last_update_utc"), "rates": body["rates"]}
        _cache.update(at=time.monotonic(), data=data)
        return data
    except Exception:
        if _cache["data"]:
            return _cache["data"]  # serve stale rates rather than fail
        return {"base": "USD", "updated": None, "rates": {"USD": 1.0}}
