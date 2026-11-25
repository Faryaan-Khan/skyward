"""Fare forecasting model.

A gradient-boosted regressor learns price as a function of route, departure
seasonality, holiday proximity and days-to-departure. For a given trip we sweep
every possible purchase day between today and departure to find the expected
lowest price and the best day to buy.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .synthetic import ROUTES, event_factor

FEATURES = ["route_idx", "dtd", "month", "dow", "doy_sin", "doy_cos", "event"]
ROUTE_IDX = {r: i for i, r in enumerate(ROUTES)}


def featurize(route: str, depart: pd.Series, observed: pd.Series) -> pd.DataFrame:
    depart = pd.to_datetime(depart)
    observed = pd.to_datetime(observed)
    doy = depart.dt.day_of_year
    return pd.DataFrame(
        {
            "route_idx": ROUTE_IDX.get(route, -1),
            "dtd": (depart - observed).dt.days.clip(lower=0),
            "month": depart.dt.month,
            "dow": depart.dt.day_of_week,
            "doy_sin": np.sin(2 * np.pi * doy / 365.25),
            "doy_cos": np.cos(2 * np.pi * doy / 365.25),
            "event": [event_factor(d.date())[0] for d in depart],
        }
    )


class FareForecaster:
    def __init__(self) -> None:
        self.model = HistGradientBoostingRegressor(
            max_iter=300, learning_rate=0.08, categorical_features=[0], random_state=0
        )
        self.mae: float | None = None

    def fit(self, df: pd.DataFrame) -> "FareForecaster":
        df = df[~df["is_sale"].astype(bool)]  # learn the normal market, not flash sales
        X = pd.concat(
            [featurize(r, g["depart"], g["observed"]) for r, g in df.groupby("route")], ignore_index=True
        )
        y = np.log(pd.concat([g["price"] for _, g in df.groupby("route")], ignore_index=True).values)
        n = len(X)
        idx = np.random.default_rng(0).permutation(n)
        split = int(n * 0.85)
        self.model.fit(X.iloc[idx[:split]], y[idx[:split]])
        pred = np.exp(self.model.predict(X.iloc[idx[split:]]))
        self.mae = float(np.mean(np.abs(pred - np.exp(y[idx[split:]]))))
        self.model.fit(X, y)
        return self

    def predict_frame(self, df: pd.DataFrame) -> np.ndarray:
        """Vectorised expected price for many (route, depart, observed) rows at once."""
        if df.empty:
            return np.array([])
        df = df.reset_index(drop=True)
        out = np.empty(len(df))
        for route, g in df.groupby("route"):
            X = featurize(route, g["depart"].reset_index(drop=True), g["observed"].reset_index(drop=True))
            out[g.index.to_numpy()] = np.exp(self.model.predict(X))
        return out

    def predict(self, route: str, depart: dt.date, observed: list[dt.date]) -> np.ndarray:
        X = featurize(route, pd.Series([depart] * len(observed)), pd.Series(observed))
        return np.exp(self.model.predict(X))

    def advise(self, route: str, depart: dt.date, current_price: float | None = None, today: dt.date | None = None):
        today = today or dt.date.today()
        days = max(1, (depart - today).days)
        buy_days = [today + dt.timedelta(days=i) for i in range(days)]
        curve = self.predict(route, depart, buy_days)
        best_i = int(np.argmin(curve))
        now = float(current_price if current_price is not None else curve[0])
        expected_low = float(curve[best_i])
        saving = now - expected_low
        band = self.mae or 0.0
        if best_i == 0 or saving <= band * 0.5:
            action, reason = "BUY", "Prices are forecast to rise from here."
        else:
            action, reason = "WAIT", f"Expected to drop about {saving:.0f} by {buy_days[best_i].isoformat()}."
        ev = event_factor(depart)[1]
        return {
            "route": route,
            "depart": depart.isoformat(),
            "current_price": round(now, 2),
            "expected_low": round(expected_low, 2),
            "best_buy_date": buy_days[best_i].isoformat(),
            "confidence_band": round(band, 2),
            "action": action,
            "reason": reason + (f" Departure falls in the {ev} demand window." if ev else ""),
            "curve": [{"date": d.isoformat(), "price": round(float(p), 2)} for d, p in zip(buy_days, curve)],
        }
