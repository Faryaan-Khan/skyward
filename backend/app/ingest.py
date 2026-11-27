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
from sqlalchemy import and_, delete, func, insert, select, update

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


SEARCHED_MONTHS_AHEAD = 3        # routes added by users' searches
MAX_SEARCHED_ROUTES = 40          # most-searched routes kept fresh by the refresh job
SEARCH_CACHE_MINUTES = 15


def _month_bounds(month: str) -> tuple[dt.date, dt.date]:
    y, m = map(int, month.split("-"))
    start = dt.date(y, m, 1)
    end = dt.date(y + (m == 12), m % 12 + 1, 1)
    return start, end


def fetch_route_month(route: str, month: str, today: dt.date | None = None, client=None,
                      fetcher=None) -> int:
    """Fetch one route + month and replace today's snapshot for exactly that month."""
    today = today or dt.date.today()
    origin, dest = route.split("-")
    found = (fetcher or travelpayouts.fetch_month)(origin, dest, month, client=client)
    quotes: dict[dt.date, dict] = {}
    for q in found:
        if q["depart"] <= today:
            continue
        best = quotes.get(q["depart"])
        if best is None or q["price"] < best["price"]:
            quotes[q["depart"]] = q
    start, end = _month_bounds(month)
    f = db.fares.c
    now = dt.datetime.now(dt.timezone.utc)
    with db.connect() as c:
        c.execute(delete(db.fares).where(and_(
            f.route == route, f.observed == today, f.source == "travelpayouts", f.depart >= start, f.depart < end)))
        if quotes:
            c.execute(insert(db.fares), [
                {"route": route, "observed": today, "depart": q["depart"], "price": q["price"], "is_sale": False,
                 "source": "travelpayouts", "carrier": q.get("carrier"), "flight_number": q.get("flight_number"),
                 "transfers": q.get("transfers"), "link": q.get("link"), "fetched_at": now}
                for q in quotes.values()
            ])
    return len(quotes)


def month_is_fresh(route: str, month: str, minutes: int = SEARCH_CACHE_MINUTES) -> bool:
    """True if this route+month was fetched recently (saves API calls on repeat searches)."""
    start, end = _month_bounds(month)
    f = db.fares.c
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    with db.connect() as c:
        last = c.execute(select(func.max(f.fetched_at)).where(and_(
            f.route == route, f.source == "travelpayouts", f.depart >= start, f.depart < end))).scalar()
    if last is None:
        return False
    if last.tzinfo is None:  # SQLite returns naive datetimes
        last = last.replace(tzinfo=dt.timezone.utc)
    return last >= cutoff


def lookup_place(code: str) -> dict | None:
    """Name, country and coordinates for an IATA city/airport code."""
    try:
        for p in travelpayouts.autocomplete(code):
            if p["code"] == code:
                return p
    except Exception as e:
        log.warning("place lookup failed for %s: %s", code, e)
    return None


def _meta_values(route: str) -> dict:
    o, d = route.split("-")
    po, pd_ = lookup_place(o) or {}, lookup_place(d) or {}
    return {"origin_name": po.get("city") or po.get("name"), "dest_name": pd_.get("city") or pd_.get("name"),
            "origin_country": po.get("country"), "dest_country": pd_.get("country"),
            "origin_lat": po.get("lat"), "origin_lon": po.get("lon"),
            "dest_lat": pd_.get("lat"), "dest_lon": pd_.get("lon")}


def register_search(route: str) -> None:
    """Count a search for a route (creating it with names and coordinates the first time)."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    r = db.routes.c
    with db.connect() as c:
        row = c.execute(select(r.code, r.origin_name).where(r.code == route)).first()
    if row is None:
        values = {"code": route, "searches": 1, "last_searched": now, "featured": False, **_meta_values(route)}
        try:
            with db.connect() as c:
                c.execute(insert(db.routes), values)
            return
        except Exception:
            pass  # created concurrently by another request: fall through to the update
    with db.connect() as c:
        c.execute(update(db.routes).where(r.code == route).values(searches=r.searches + 1, last_searched=now))
    if row is not None and not row.origin_name:
        fill_route_meta([route])


def seed_featured_routes() -> None:
    """Make sure the featured routes exist in the routes table."""
    r = db.routes.c
    with db.connect() as c:
        have = {x[0] for x in c.execute(select(r.code))}
        missing = [code for code in ROUTES if code not in have]
        if missing:
            c.execute(insert(db.routes), [{"code": code, "searches": 0, "featured": True} for code in missing])


def fill_route_meta(codes: list[str] | None = None) -> int:
    """Look up names/coordinates for routes that don't have them yet."""
    r = db.routes.c
    with db.connect() as c:
        q = select(r.code).where(r.origin_name.is_(None))
        if codes:
            q = q.where(r.code.in_(codes))
        todo = [x[0] for x in c.execute(q.limit(20))]
    for code in todo:
        values = _meta_values(code)
        if values["origin_name"] or values["dest_name"]:
            with db.connect() as c:
                c.execute(update(db.routes).where(r.code == code).values(**values))
    return len(todo)


def tracked_routes() -> dict[str, int]:
    """{route: months_ahead} for the refresh job: featured routes + users' most-searched routes."""
    out = {r: MONTHS_AHEAD for r in ROUTES}
    cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=14)
    with db.connect() as c:
        for (code,) in c.execute(select(db.routes.c.code).where(db.routes.c.last_searched >= cutoff)
                                 .order_by(db.routes.c.searches.desc()).limit(MAX_SEARCHED_ROUTES)):
            out.setdefault(code, SEARCHED_MONTHS_AHEAD)
    return out


def fetch_live(today: dt.date | None = None, fetcher=None) -> dict:
    """Pull fares for every tracked route and month; returns {route: rows_saved}."""
    today = today or dt.date.today()
    saved: dict[str, int] = {}
    with httpx.Client(timeout=30) as client:
        for route, months in tracked_routes().items():
            saved[route] = 0
            for month in _months(today, months):
                try:
                    saved[route] += fetch_route_month(route, month, today, client=client, fetcher=fetcher)
                except Exception as e:  # one bad route/month must not stop the job
                    log.warning("fetch failed %s %s: %s", route, month, e)
    return saved


def load_training_frame() -> pd.DataFrame:
    with db.connect() as c:
        df = pd.DataFrame(db.rows(c.execute(
            select(db.fares.c.route, db.fares.c.observed, db.fares.c.depart, db.fares.c.price,
                   db.fares.c.is_sale, db.fares.c.source)
        )))
    return calibrate_synthetic(df)


def calibrate_synthetic(df: pd.DataFrame) -> pd.DataFrame:
    """Scale each route's synthetic history to the level of its real fares.

    The synthetic data supplies the *shape* (seasonality, holidays, booking curve) and
    the live data supplies the *level*, so the model's expected prices match the real
    market instead of the generator's guesses. Routes without live data are unchanged.
    """
    if df.empty or "source" not in df:
        return df
    df = df.copy()
    dtd = (pd.to_datetime(df["depart"]) - pd.to_datetime(df["observed"])).dt.days
    window = dtd.between(1, 200)  # compare like with like: same booking horizon as live data
    live = df["source"] != "synthetic"
    for route in df.loc[live, "route"].unique():
        r = df["route"] == route
        real = df.loc[r & live & window, "price"]
        synth = df.loc[r & ~live & window & ~df["is_sale"].astype(bool), "price"]
        if len(real) >= 10 and len(synth) >= 10:
            df.loc[r & ~live, "price"] *= real.median() / synth.median()
    return df.drop(columns="source")


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
            try:
                adv = model.advise(w["route"], w["depart"], live, today=today)
            except ValueError:  # no price data for this route yet
                continue
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
    seed_featured_routes()
    try:
        fill_route_meta()
    except Exception as e:
        log.warning("route meta lookup failed: %s", e)
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
