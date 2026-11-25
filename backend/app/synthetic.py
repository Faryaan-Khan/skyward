"""Synthetic fare history generator.

Produces realistic-looking fare observations so the forecasting model and the
dashboard work out of the box without any paid API keys. The generator models
the effects that drive real airfare movement:

* base route cost (distance proxy)
* seasonality by departure month and day of week
* demand spikes around holidays / recurring yearly events
* the classic U-shaped days-to-departure curve
* random flash sales (special offers)
"""
from __future__ import annotations

import datetime as dt
import math
import random

ROUTES: dict[str, float] = {
    "KUL-SIN": 95,
    "KUL-BKK": 140,
    "KUL-NRT": 520,
    "KUL-LHR": 780,
    "KUL-SYD": 610,
    "SIN-HKG": 260,
    "JFK-LAX": 310,
    "LHR-CDG": 120,
}

# Recurring yearly events: (month, day, window_days, demand_multiplier)
EVENTS: dict[str, tuple[int, int, int, float]] = {
    "New Year": (1, 1, 7, 1.35),
    "Chinese New Year": (2, 10, 10, 1.45),
    "Hari Raya": (4, 10, 10, 1.40),
    "Summer peak": (7, 15, 30, 1.25),
    "Deepavali": (11, 1, 5, 1.15),
    "Christmas": (12, 25, 10, 1.40),
}

MONTH_SEASON = [1.10, 1.00, 0.95, 1.00, 0.92, 1.05, 1.18, 1.15, 0.90, 0.93, 0.97, 1.20]
DOW_FACTOR = [0.97, 0.94, 0.93, 0.98, 1.08, 1.06, 1.04]  # Mon..Sun departure


def event_factor(date: dt.date) -> tuple[float, str | None]:
    best, name = 1.0, None
    for ev, (m, d, win, mult) in EVENTS.items():
        for year in (date.year - 1, date.year, date.year + 1):
            try:
                center = dt.date(year, m, d)
            except ValueError:
                continue
            dist = abs((date - center).days)
            if dist <= win:
                f = 1 + (mult - 1) * (1 - dist / (win + 1))
                if f > best:
                    best, name = f, ev
    return best, name


def dtd_factor(days: int) -> float:
    """U-shaped curve: expensive very early, cheapest ~6-8 weeks out, spikes late."""
    late = 0.9 * math.exp(-days / 9)
    early = 0.0025 * max(0, days - 60)
    return 0.92 + late + early


def fare(route: str, depart: dt.date, observed: dt.date, rng: random.Random) -> tuple[float, bool]:
    base = ROUTES[route]
    days = max(0, (depart - observed).days)
    ev, _ = event_factor(depart)
    price = base * MONTH_SEASON[depart.month - 1] * DOW_FACTOR[depart.weekday()] * ev * dtd_factor(days)
    price *= rng.gauss(1.0, 0.05)
    is_sale = rng.random() < 0.03
    if is_sale:
        price *= rng.uniform(0.6, 0.8)
    return round(max(price, base * 0.4), 2), is_sale


def generate(seed: int = 42, start: dt.date | None = None, days_back: int = 730):
    """Yield fare observations: (route, observed, depart, price, is_sale)."""
    rng = random.Random(seed)
    today = start or dt.date.today()
    for route in ROUTES:
        for back in range(days_back, -1, -3):
            observed = today - dt.timedelta(days=back)
            for _ in range(6):
                depart = observed + dt.timedelta(days=rng.randint(1, 240))
                price, sale = fare(route, depart, observed, rng)
                yield route, observed, depart, price, sale
