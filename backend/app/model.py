# Copyright (c) 2026 Faryaan Khan
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Fare forecasting model that works for any route in the world.

Instead of memorising each route, the model learns how a fare moves *relative to the
route's typical price* ("base"): the days-to-departure curve, seasonality, day of week
and holiday demand. A route's base is simply the median fare seen for it, so a brand-new
route only needs one batch of live prices before it can be forecast.

    price = base(route) * exp(model(features))

For a given trip we sweep every possible purchase day between today and departure to
find the expected lowest price and the best day to buy.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .synthetic import event_factor

FEATURES = ["log_base", "dtd", "month", "dow", "doy_sin", "doy_cos", "event"]


@lru_cache(maxsize=4096)
def _event(d: dt.date) -> float:
    return event_factor(d)[0]


def featurize(base: float | pd.Series, depart: pd.Series, observed: pd.Series) -> pd.DataFrame:
    depart = pd.to_datetime(pd.Series(depart).reset_index(drop=True))
    observed = pd.to_datetime(pd.Series(observed).reset_index(drop=True))
    doy = depart.dt.day_of_year
    base_arr = np.asarray(base, dtype=float) if not np.isscalar(base) else np.full(len(depart), float(base))
    return pd.DataFrame({
        "log_base": np.log(base_arr),
        "dtd": (depart - observed).dt.days.clip(lower=0).to_numpy(),
        "month": depart.dt.month.to_numpy(),
        "dow": depart.dt.day_of_week.to_numpy(),
        "doy_sin": np.sin(2 * np.pi * doy / 365.25).to_numpy(),
        "doy_cos": np.cos(2 * np.pi * doy / 365.25).to_numpy(),
        "event": [_event(d.date()) for d in depart],
    })


class FareForecaster:
    def __init__(self) -> None:
        self.model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, random_state=0)
        self.mae: float | None = None
        self.mape: float | None = None
        self.bases: dict[str, float] = {}

    # ---- training ----
    def fit(self, df: pd.DataFrame) -> "FareForecaster":
        df = df[~df["is_sale"].astype(bool)].reset_index(drop=True)  # learn the normal market
        self.bases = df.groupby("route")["price"].median().astype(float).to_dict()
        base = df["route"].map(self.bases).to_numpy()
        X = featurize(base, df["depart"], df["observed"])
        y = np.log(df["price"].to_numpy()) - np.log(base)
        n = len(X)
        idx = np.random.default_rng(0).permutation(n)
        split = int(n * 0.85)
        self.model.fit(X.iloc[idx[:split]], y[idx[:split]])
        test = idx[split:]
        pred = base[test] * np.exp(self.model.predict(X.iloc[test]))
        actual = df["price"].to_numpy()[test]
        self.mae = float(np.mean(np.abs(pred - actual)))
        self.mape = float(np.mean(np.abs(pred - actual) / actual))
        self.model.fit(X, y)
        return self

    # ---- prediction ----
    def base(self, route: str, fallback: float | None = None) -> float | None:
        return self.bases.get(route, fallback)

    def predict(self, base: float, depart: dt.date, observed: list[dt.date]) -> np.ndarray:
        X = featurize(base, [depart] * len(observed), observed)
        return base * np.exp(self.model.predict(X))

    def predict_frame(self, df: pd.DataFrame, bases: dict[str, float] | None = None) -> np.ndarray:
        """Expected price for many (route, depart, observed) rows. Unknown routes give NaN."""
        if df.empty:
            return np.array([])
        lookup = {**self.bases, **(bases or {})}
        base = df["route"].map(lookup).to_numpy(dtype=float)
        out = np.full(len(df), np.nan)
        ok = ~np.isnan(base)
        if ok.any():
            X = featurize(base[ok], df["depart"][ok], df["observed"][ok])
            out[ok] = base[ok] * np.exp(self.model.predict(X))
        return out

    def advise(self, route: str, depart: dt.date, current_price: float | None = None,
               today: dt.date | None = None, base: float | None = None) -> dict:
        today = today or dt.date.today()
        base = base or self.base(route)
        if not base:
            raise ValueError(f"No price data for {route} yet")
        days = max(1, (depart - today).days)
        buy_days = [today + dt.timedelta(days=i) for i in range(days)]
        curve = self.predict(base, depart, buy_days)
        best_i = int(np.argmin(curve))
        now = float(current_price if current_price is not None else curve[0])
        expected_low = float(curve[best_i])
        saving = now - expected_low
        band = self.mae or 0.0
        if best_i == 0 or saving <= max(band * 0.5, now * 0.03):
            action, reason = "BUY", "Prices are forecast to rise from here."
        else:
            action, reason = "WAIT", f"Expected to drop about {saving:.0f} USD by {buy_days[best_i].isoformat()}."
        ev = event_factor(depart)[1]
        return {
            "route": route,
            "depart": depart.isoformat(),
            "current_price": round(now, 2),
            "typical_price": round(float(base), 2),
            "expected_low": round(expected_low, 2),
            "best_buy_date": buy_days[best_i].isoformat(),
            "confidence_band": round(band, 2),
            "action": action,
            "reason": reason + (f" Departure falls in the {ev} demand window." if ev else ""),
            "curve": [{"date": d.isoformat(), "price": round(float(p), 2)} for d, p in zip(buy_days, curve)],
        }

    def drop_outlook(self, trips: list[tuple[float, dt.date]], today: dt.date | None = None) -> list[tuple[float, dt.date]]:
        """For each (base, depart): predicted relative fall from today's level to the cheapest
        future purchase day, and that day. One batched model call for all trips."""
        today = today or dt.date.today()
        bases, departs, observed, owner = [], [], [], []
        for k, (base, depart) in enumerate(trips):
            for i in range(max(1, (depart - today).days)):
                bases.append(base)
                departs.append(depart)
                observed.append(today + dt.timedelta(days=i))
                owner.append(k)
        if not owner:
            return []
        X = featurize(np.array(bases), departs, observed)
        rel = self.model.predict(X)  # log(price/base); same base within a trip, so compare directly
        frame = pd.DataFrame({"k": owner, "rel": rel, "day": observed})
        out = []
        for k, g in frame.groupby("k", sort=True):
            first = g["rel"].iloc[0]
            j = int(np.argmin(g["rel"].to_numpy()))
            out.append((float(1 - np.exp(g["rel"].iloc[j] - first)), g["day"].iloc[j]))
        return out
