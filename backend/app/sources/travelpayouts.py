"""Travelpayouts / Aviasales Data API adapter (free with a Travelpayouts account).

Returns the cheapest real fares found by Aviasales users in roughly the last 48 hours
for each departure date in a month. Docs:
https://support.travelpayouts.com/hc/en-us/articles/203956163

Set TRAVELPAYOUTS_TOKEN (and optionally TRAVELPAYOUTS_MARKER for affiliate links).
Without a token, `fetch_month` returns [] and the app keeps using stored history.
"""
from __future__ import annotations

import datetime as dt
import os

import httpx

BASE = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
PLACES = "https://autocomplete.travelpayouts.com/places2"


def enabled() -> bool:
    return bool(os.getenv("TRAVELPAYOUTS_TOKEN"))


def autocomplete(term: str, locale: str = "en", client: httpx.Client | None = None) -> list[dict]:
    """Cities and airports worldwide matching `term` (free endpoint, no token needed)."""
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        r = client.get(PLACES, params=[("term", term), ("locale", locale),
                                       ("types[]", "city"), ("types[]", "airport")])
        r.raise_for_status()
        items = r.json()
    finally:
        if own:
            client.close()
    out = []
    for p in items[:12]:
        if not p.get("code"):
            continue
        coords = p.get("coordinates") or {}
        out.append({
            "code": p["code"],
            "type": p.get("type"),
            "name": p.get("name"),
            "city": p.get("city_name") or p.get("name"),
            "country": p.get("country_name"),
            "country_code": p.get("country_code"),
            "lat": coords.get("lat"),
            "lon": coords.get("lon"),
        })
    return out


def fetch_month(origin: str, dest: str, month: str, currency: str = "usd",
                client: httpx.Client | None = None) -> list[dict]:
    """Cheapest one-way fares per departure date for `month` (YYYY-MM)."""
    token = os.getenv("TRAVELPAYOUTS_TOKEN")
    if not token:
        return []
    params = {
        "origin": origin,
        "destination": dest,
        "departure_at": month,
        "one_way": "true",
        "currency": currency,
        "sorting": "price",
        "unique": "false",
        "limit": 1000,
    }
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        r = client.get(BASE, params=params, headers={"X-Access-Token": token})
        r.raise_for_status()
        body = r.json()
    finally:
        if own:
            client.close()
    marker = os.getenv("TRAVELPAYOUTS_MARKER")
    out = []
    for o in body.get("data", []) if body.get("success", True) else []:
        try:
            depart = dt.date.fromisoformat(o["departure_at"][:10])
            price = float(o["price"])
        except (KeyError, TypeError, ValueError):
            continue
        link = o.get("link")
        if link:
            link = "https://www.aviasales.com" + link + (f"&marker={marker}" if marker else "")
        out.append({
            "depart": depart,
            "price": price,
            "carrier": o.get("airline"),
            "flight_number": str(o.get("flight_number") or "") or None,
            "transfers": o.get("transfers"),
            "link": link,
        })
    return out
