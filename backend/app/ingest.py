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


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def ensure_route(route: str) -> None:
    """Create a route row with names and coordinates if it doesn't exist yet."""
    r = db.routes.c
    with db.connect() as c:
        row = c.execute(select(r.code, r.origin_name).where(r.code == route)).first()
    if row is None:
        try:
            with db.connect() as c:
                c.execute(insert(db.routes), {"code": route, "searches": 0, "featured": False, **_meta_values(route)})
        except Exception:
            pass  # created concurrently by another request
    elif not row.origin_name:
        fill_route_meta([route])


def register_search(route: str) -> None:
    """Count a search for a route (creating it the first time) and log it for weekly popularity."""
    ensure_route(route)
    now = _utcnow()
    r = db.routes.c
    with db.connect() as c:
        c.execute(update(db.routes).where(r.code == route).values(searches=r.searches + 1, last_searched=now))
        c.execute(insert(db.search_events), {"route": route, "at": now})


# ---- weekly popular routes: what Skyward users search + what the world flies ----
HUBS = ["KUL", "SIN", "BKK", "HKG", "TYO", "SEL", "DXB", "LON", "PAR", "NYC", "LAX", "SYD", "IST", "DEL", "JKT"]
POPULAR_SIZE = 12
POPULAR_JOB = "popular-v2"   # bump to force a recompute after changing the ranking rules


def compute_popular(directions=None) -> list[dict]:
    """Rank routes for the coming week. Score = 2 x Skyward searches in the last 7 days
    + worldwide popularity (top destinations from 15 global hubs, by Aviasales search volume)."""
    directions = directions or travelpayouts.popular_directions
    now = _utcnow()
    with db.connect() as c:
        users = dict(c.execute(select(db.search_events.c.route, func.count())
                               .where(db.search_events.c.at >= now - dt.timedelta(days=7))
                               .group_by(db.search_events.c.route)).all())
        c.execute(delete(db.search_events).where(db.search_events.c.at < now - dt.timedelta(days=30)))
    worldwide: dict[str, float] = {}
    with httpx.Client(timeout=20) as client:
        for hub in HUBS:
            try:
                dests = directions(hub, client=client)[:8]
            except Exception as e:
                log.warning("popular directions failed for %s: %s", hub, e)
                continue
            for i, dest in enumerate(dests):
                worldwide[f"{hub}-{dest}"] = 8 - i
    log.info("popular: %d worldwide routes, %d routes searched on Skyward this week", len(worldwide), len(users))
    scores = {code: 2 * users.get(code, 0) + worldwide.get(code, 0) for code in set(users) | set(worldwide)}
    # Variety: Aviasales demand leans towards a few destinations (e.g. Moscow), so keep at most one route
    # per destination and two per origin; Skyward users' own searches are never filtered out.
    top, per_dest, per_origin = [], {}, {}
    for code, s in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        o, d = code.split("-")
        if code not in users and (per_dest.get(d, 0) >= 1 or per_origin.get(o, 0) >= 2):
            continue
        top.append((code, s))
        per_dest[d] = per_dest.get(d, 0) + 1
        per_origin[o] = per_origin.get(o, 0) + 1
        if len(top) == POPULAR_SIZE:
            break
    rows = [{"code": code, "rank": i + 1, "score": float(s), "user_searches": int(users.get(code, 0)),
             "worldwide": code in worldwide, "updated": now} for i, (code, s) in enumerate(top) if s > 0]
    with db.connect() as c:
        c.execute(delete(db.popular))
        if rows:
            c.execute(insert(db.popular), rows)
    for row in rows:
        ensure_route(row["code"])
    log.info("popular routes updated: %s", [r["code"] for r in rows])
    return rows


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
        for (code,) in c.execute(select(db.popular.c.code).order_by(db.popular.c.rank)):
            out.setdefault(code, SEARCHED_MONTHS_AHEAD)
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


# ---- price alerts: real prices only, opted-in channels only, no spam ----
MAX_ALERTS_PER_WATCH = 8          # a watch goes quiet after this many alerts
MAX_EMAILS_PER_ADDRESS_PER_DAY = 5
DEAL_THRESHOLD = 0.90             # without a target, alert only when a fare is >= 10% under typical
REPEAT_DROP = 0.95                # a repeat alert needs a further >= 5% fall vs the last alert


def _alert_text(w: dict, price: float, adv: dict, kind: str, link: str | None) -> tuple[str, str, str]:
    route = f"{w['route'][:3]} → {w['route'][4:]}"
    why = (f"It's at or below your target of {w['target_price']:.0f} USD." if kind == "target"
           else f"That's {100 * (1 - price / adv['typical_price']):.0f}% below the usual price for this route.")
    subject = f"✈ {route} on {w['depart']:%d %b}: now {price:.0f} USD"
    text = (f"{route} on {w['depart']:%a %d %b %Y} is now {price:.0f} USD. {why} "
            f"Model call: {adv['action']}. {adv['reason']}")
    return subject, text, link or f"{notify.public_url()}/?from={w['route'][:3]}&to={w['route'][4:]}&month={w['depart']:%Y-%m}"


def deliver_alert(w: dict, price: float, adv: dict, kind: str, link: str | None) -> list[str]:
    """Send one alert on every channel the user confirmed. Returns the channels used."""
    from . import security
    subject, text, book = _alert_text(w, price, adv, kind, link)
    unsub = f"{notify.public_url()}/api/watches/unsubscribe?w={w['id']}&s={security.sign('unsub', w['id'])}"
    sent = []
    if w.get("email") and w.get("email_verified"):
        esc = html_escape
        body = notify.email_html(subject, [esc(text)], ("View & book this flight", book),
                                 f'You get this because you set a price alert on Skyward. '
                                 f'<a href="{esc(unsub)}" style="color:#8c6d3a">Stop these alerts</a>.')
        if notify.send_email(w["email"], subject, f"{text}\n\nBook: {book}\n\nStop these alerts: {unsub}", body, unsub):
            sent.append("email")
    if w.get("telegram_chat_id"):
        if notify.send_telegram(w["telegram_chat_id"], f"<b>{html_escape(subject)}</b>\n{html_escape(text)}",
                                [("View & book", book), ("Stop this alert", unsub)]):
            sent.append("telegram")
    return sent


def check_watches(model: FareForecaster, today: dt.date | None = None, deliver=None) -> list[dict]:
    """Evaluate every watch against the latest REAL price and alert without spamming:
    * only channels the user confirmed (verified email / connected Telegram)
    * at most one alert per watch per 24 h, and at most 8 per watch overall
    * a repeat alert needs the price to fall at least 5% further than the last alert
    * at most 5 emails per address per day across all their watches
    """
    deliver = deliver or deliver_alert
    today = today or dt.date.today()
    now = _utcnow()
    cooldown = now - dt.timedelta(hours=ALERT_COOLDOWN_HOURS)
    fired = []
    W = db.watches.c
    with db.connect() as c:
        c.execute(delete(db.watches).where(W.depart <= today))  # trips in the past: clean up
        sent_today: dict[str, int] = dict(c.execute(
            select(W.email, func.count()).select_from(db.alerts.join(db.watches, db.alerts.c.watch_id == W.id))
            .where(and_(db.alerts.c.created >= now - dt.timedelta(days=1), W.email.is_not(None)))
            .group_by(W.email)).all())
        watches = db.rows(c.execute(select(db.watches)))
    for w in watches:
        if not ((w["email"] and w["email_verified"]) or w["telegram_chat_id"]):
            continue
        if w["alert_count"] >= MAX_ALERTS_PER_WATCH or (w["last_alert_at"] and w["last_alert_at"] > cooldown):
            continue
        with db.connect() as c:
            fare = latest_live_fare(c, w["route"], w["depart"])
        if not fare:
            continue  # never alert on a model guess, only on a price you can actually buy
        price = fare["price"]
        try:
            adv = model.advise(w["route"], w["depart"], price, today=today)
        except ValueError:
            continue
        if w["last_alert_price"] and price > w["last_alert_price"] * REPEAT_DROP:
            continue
        if w["target_price"] and price <= w["target_price"]:
            kind = "target"
        elif not w["target_price"] and price <= adv["typical_price"] * DEAL_THRESHOLD and adv["action"] == "BUY":
            kind = "deal"
        else:
            continue
        if w["email"] and sent_today.get(w["email"], 0) >= MAX_EMAILS_PER_ADDRESS_PER_DAY:
            continue
        channels = deliver(w, price, adv, kind, fare.get("link"))
        subject, text, _ = _alert_text(w, price, adv, kind, fare.get("link"))
        with db.connect() as c:
            c.execute(insert(db.alerts), {"watch_id": w["id"], "kind": kind, "message": text[:1000]})
            c.execute(update(db.watches).where(W.id == w["id"]).values(
                last_alert_price=price, last_alert_at=now, alert_count=W.alert_count + 1))
        if w["email"]:
            sent_today[w["email"]] = sent_today.get(w["email"], 0) + 1
        fired.append({"watch_id": w["id"], "kind": kind, "message": text, "channels": channels})
    return fired


def html_escape(s: str) -> str:
    import html
    return html.escape(str(s), quote=True)


def latest_live_fare(conn, route: str, depart: dt.date) -> dict | None:
    """Most recent real fare for this exact trip (price + booking link), if any."""
    f = db.fares.c
    r = conn.execute(
        select(f.price, f.link).where(and_(f.route == route, f.depart == depart, f.source != "synthetic"))
        .order_by(f.observed.desc()).limit(1)
    ).first()
    return {"price": float(r.price), "link": r.link} if r else None


def latest_live_price(conn, route: str, depart: dt.date) -> float | None:
    fare = latest_live_fare(conn, route, depart)
    return fare["price"] if fare else None


def run_refresh(train: bool = True) -> dict:
    """One full refresh: live fares -> retrain -> alerts. Used by the web app and the CLI."""
    out: dict = {"fetched": None, "trained": False, "alerts": 0}
    seed_featured_routes()
    try:
        fill_route_meta()
    except Exception as e:
        log.warning("route meta lookup failed: %s", e)
    if travelpayouts.enabled() and db.try_acquire(POPULAR_JOB, 7 * 86400 - 3600):  # weekly
        try:
            rows = compute_popular()
            if not any(r["worldwide"] for r in rows):
                log.warning("popular routes: no worldwide data this time, will retry next refresh")
                db.release(POPULAR_JOB)
        except Exception as e:
            log.warning("popular routes update failed: %s", e)
            db.release(POPULAR_JOB)
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
