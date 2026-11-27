"""Ingestion job: fetch live fares, retrain the model, send due alerts.

Runs separately from the web app (GitHub Actions cron every 15 minutes), so the
website never slows down while prices are being pulled:

    cd backend
    python -m app.ingest            # fetch + retrain + alerts
    python -m app.ingest --no-train # fetch only

Each run replaces today's live snapshot per route with the newest prices, so the
database keeps one up-to-date snapshot per day (small enough for a free Postgres
tier) while `fetched_at` records exactly when prices were last refreshed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

import httpx
import pandas as pd
from sqlalchemy import and_, delete, func, insert, select

from . import db, notify
from .model import FareForecaster
from .sources import travelpayouts
from .synthetic import ROUTES

log = logging.getLogger("skyward.ingest")
MONTHS_AHEAD = 6
ALERT_COOLDOWN_HOURS = 24


def _months(today: dt.date, n: int) -> list[str]:
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def fetch_live(today: dt.date | None = None, fetcher=travelpayouts.fetch_month) -> dict:
    """Pull fares for every route and month; returns {route: rows_saved}."""
    today = today or dt.date.today()
    now = dt.datetime.now(dt.timezone.utc)
    saved: dict[str, int] = {}
    with httpx.Client(timeout=30) as client:
        for route in ROUTES:
            origin, dest = route.split("-")
            quotes: dict[dt.date, dict] = {}
            for month in _months(today, MONTHS_AHEAD):
                try:
                    found = fetcher(origin, dest, month, client=client)
                except Exception as e:  # one bad route/month must not stop the job
                    log.warning("fetch failed %s %s: %s", route, month, e)
                    continue
                for q in found:
                    if q["depart"] <= today:
                        continue
                    best = quotes.get(q["depart"])
                    if best is None or q["price"] < best["price"]:
                        quotes[q["depart"]] = q
            if not quotes:
                saved[route] = 0
                continue
            with db.connect() as c:
                c.execute(delete(db.fares).where(and_(
                    db.fares.c.route == route, db.fares.c.observed == today, db.fares.c.source == "travelpayouts")))
                c.execute(insert(db.fares), [
                    {"route": route, "observed": today, "depart": q["depart"], "price": q["price"], "is_sale": False,
                     "source": "travelpayouts", "carrier": q.get("carrier"), "flight_number": q.get("flight_number"),
                     "transfers": q.get("transfers"), "link": q.get("link"), "fetched_at": now}
                    for q in quotes.values()
                ])
            saved[route] = len(quotes)
    return saved


def load_training_frame() -> pd.DataFrame:
    with db.connect() as c:
        df = pd.DataFrame(db.rows(c.execute(
            select(db.fares.c.route, db.fares.c.observed, db.fares.c.depart, db.fares.c.price, db.fares.c.is_sale)
        )))
    return df


def retrain() -> FareForecaster:
    df = load_training_frame()
    model = FareForecaster().fit(df)
    db.save_model(model, len(df))
    return model


def check_watches(model: FareForecaster, today: dt.date | None = None) -> list[dict]:
    """Evaluate every watch; alert at most once per cooldown window per watch."""
    today = today or dt.date.today()
    # naive UTC works for both SQLite (CURRENT_TIMESTAMP is UTC) and Postgres (UTC session)
    cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=ALERT_COOLDOWN_HOURS)
    fired = []
    with db.connect() as c:
        recent = {r[0] for r in c.execute(
            select(db.alerts.c.watch_id).where(db.alerts.c.created >= cutoff).distinct())}
        for w in db.rows(c.execute(select(db.watches).where(db.watches.c.depart > today))):
            if w["id"] in recent:
                continue
            live = latest_live_price(c, w["route"], w["depart"])
            adv = model.advise(w["route"], w["depart"], live, today=today)
            hit_target = bool(w["target_price"]) and adv["current_price"] <= w["target_price"]
            if adv["action"] == "BUY" or hit_target:
                kind = "target" if hit_target else "buy_now"
                msg = (f"{w['route']} on {w['depart']}: {adv['current_price']:.0f} now. "
                       f"Expected low {adv['expected_low']:.0f}. {adv['reason']}")
                try:
                    notify.dispatch(w, f"Skyward: time to book {w['route']}", msg)
                except Exception as e:
                    log.warning("notify failed for watch %s: %s", w["id"], e)
                c.execute(insert(db.alerts), {"watch_id": w["id"], "kind": kind, "message": msg})
                fired.append({"watch_id": w["id"], "kind": kind, "message": msg})
    return fired


def latest_live_price(conn, route: str, depart: dt.date) -> float | None:
    """Most recent real price seen for this exact trip, if any."""
    r = conn.execute(
        select(db.fares.c.price).where(and_(
            db.fares.c.route == route, db.fares.c.depart == depart, db.fares.c.source != "synthetic"))
        .order_by(db.fares.c.observed.desc()).limit(1)
    ).first()
    return float(r[0]) if r else None


def run_refresh(train: bool = True) -> dict:
    """One full refresh: live fares -> retrain -> alerts. Used by the web app and the CLI."""
    out: dict = {"fetched": None, "trained": False, "alerts": 0}
    if travelpayouts.enabled():
        saved = fetch_live()
        out["fetched"] = sum(saved.values())
        log.info("live fares saved: %s (total %d)", saved, out["fetched"])
    else:
        log.warning("TRAVELPAYOUTS_TOKEN not set: skipping live fetch")
    if not train:
        return out
    model = retrain()
    with db.connect() as c:
        n = c.execute(select(func.count()).select_from(db.fares)).scalar()
    log.info("model retrained on %d rows, MAE %.2f", n, model.mae)
    out["trained"] = True
    out["alerts"] = len(check_watches(model))
    log.info("alerts fired: %d", out["alerts"])
    return out


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-train", action="store_true")
    args = ap.parse_args(argv)
    db.init()
    run_refresh(train=not args.no_train)


if __name__ == "__main__":
    main()
