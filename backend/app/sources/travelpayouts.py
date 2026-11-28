# Copyright (c) 2026 Faryaan Khan
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
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


def affiliate_marker() -> str | None:
    """Travelpayouts partner ID (TRAVELPAYOUTS_MARKER) for affiliate links; digits only, else None."""
    marker = (os.getenv("TRAVELPAYOUTS_MARKER") or "").strip()
    return marker if marker.isascii() and marker.isdigit() and len(marker) <= 12 else None


def with_marker(link: str | None) -> str | None:
    """Add the affiliate marker to an Aviasales link that doesn't have one yet (e.g. a fare
    stored before the marker was configured)."""
    marker = affiliate_marker()
    if not link or not marker or "aviasales." not in link or "marker=" in link:
        return link
    return link + ("&" if "?" in link else "?") + f"marker={marker}"


DIRECTIONS = "https://api.travelpayouts.com/v1/city-directions"
AIRPORTS = "https://api.travelpayouts.com/data/en/airports.json"
CITIES = "https://api.travelpayouts.com/data/en/cities.json"


def popular_directions(origin: str, client: httpx.Client | None = None) -> list[str]:
    """Most popular destinations from `origin` among Aviasales searches worldwide (best first)."""
    token = os.getenv("TRAVELPAYOUTS_TOKEN")
    if not token:
        return []
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = client.get(DIRECTIONS, params={"origin": origin, "currency": "usd"}, headers={"X-Access-Token": token})
        r.raise_for_status()
        data = r.json().get("data") or {}
    finally:
        if own:
            client.close()
    if isinstance(data, dict):        # documented shape: {"BKK": {...}, "SIN": {...}} in popularity order
        codes = list(data.keys())
    else:                             # tolerate a list of {"destination": "BKK", ...}
        codes = [d.get("destination") for d in data if isinstance(d, dict)]
    return [c for c in codes if isinstance(c, str) and len(c) == 3 and c.isalpha() and c != origin]


def airports(client: httpx.Client | None = None) -> list[dict]:
    """Every flightable airport in the world with city name and coordinates (public data, no token)."""
    own = client is None
    client = client or httpx.Client(timeout=60)
    try:
        aps = client.get(AIRPORTS).json()
        cities = {c["code"]: c.get("name") for c in client.get(CITIES).json() if c.get("code")}
    finally:
        if own:
            client.close()
    out = []
    for a in aps:
        co = a.get("coordinates") or {}
        if not (a.get("flightable") and a.get("iata_type") == "airport" and co.get("lat") is not None):
            continue
        out.append({"code": a["code"], "name": a.get("name") or a["code"], "city_code": a.get("city_code"),
                    "city": cities.get(a.get("city_code")) or a.get("name"), "country": a.get("country_code"),
                    "lat": round(float(co["lat"]), 4), "lon": round(float(co["lon"]), 4)})
    return out


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
    marker = affiliate_marker()
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
