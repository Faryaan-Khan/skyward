"""Amadeus Self-Service (free test tier) flight offers adapter.

Sign up at https://developers.amadeus.com, then set AMADEUS_CLIENT_ID and
AMADEUS_CLIENT_SECRET. Without keys, `fetch_offers` returns an empty list and
the app keeps using its historical/synthetic data.
"""
from __future__ import annotations

import os

import httpx

BASE = os.getenv("AMADEUS_BASE", "https://test.api.amadeus.com")


def _token() -> str | None:
    cid, secret = os.getenv("AMADEUS_CLIENT_ID"), os.getenv("AMADEUS_CLIENT_SECRET")
    if not cid or not secret:
        return None
    r = httpx.post(
        f"{BASE}/v1/security/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_offers(origin: str, dest: str, depart: str, adults: int = 1, currency: str = "USD") -> list[dict]:
    token = _token()
    if not token:
        return []
    r = httpx.get(
        f"{BASE}/v2/shopping/flight-offers",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "originLocationCode": origin,
            "destinationLocationCode": dest,
            "departureDate": depart,
            "adults": adults,
            "currencyCode": currency,
            "max": 20,
        },
        timeout=30,
    )
    r.raise_for_status()
    return [
        {
            "price": float(o["price"]["grandTotal"]),
            "carrier": o["validatingAirlineCodes"][0] if o.get("validatingAirlineCodes") else None,
            "source": "amadeus",
        }
        for o in r.json().get("data", [])
    ]
