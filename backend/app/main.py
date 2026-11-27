"""Skyward API: worldwide flight search, live fares, ML forecasts, predicted drops and alerts.

Scaling design
* Stateless web workers: all state lives in the shared database (Postgres in production),
  so you can run as many workers/instances as traffic needs.
* Price refreshes and model training run in a background thread guarded by a DB lock, so only
  one server refreshes at a time. The trained model is stored in the database and every worker
  reloads it when a newer one appears.
* Read endpoints are cached in memory briefly, responses are gzip-compressed, and the endpoints
  that call the external fare API are rate-limited per visitor.
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import hmac
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, delete, func, insert, or_, select, update

from . import __version__, db, ingest, notify, security
from .model import FareForecaster
from .sources import rates as fx
from .sources import travelpayouts
from .synthetic import EVENTS

log = logging.getLogger("skyward")
logging.getLogger("skyward").setLevel(logging.INFO)          # our own messages show in Render's logs
if not logging.getLogger("skyward").handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.getLogger("skyward").addHandler(_h)
STATE: dict = {"model": None, "model_id": None, "model_created": None}
FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
MODEL_POLL_SECONDS = int(os.getenv("MODEL_POLL_SECONDS", "120"))
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "60"))
REFRESH_MINUTES = int(os.getenv("REFRESH_MINUTES", "15"))
AUTO_REFRESH = os.getenv("AUTO_REFRESH", "1") != "0"
SEARCH_LIMIT_PER_MIN = int(os.getenv("SEARCH_LIMIT_PER_MIN", "20"))
API_LIMIT_PER_MIN = int(os.getenv("API_LIMIT_PER_MIN", "240"))
MAX_BODY_BYTES = 16 * 1024
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "0") == "1"   # API docs are for developers; off on the public site
IATA = re.compile(r"^[A-Z]{3}$")
ROUTE_RE = re.compile(r"^[A-Z]{3}-[A-Z]{3}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, fn, seconds: int | None = None):
    ttl = CACHE_SECONDS if seconds is None else seconds
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.monotonic(), val)
    return val


# ---- simple per-visitor rate limit for endpoints that hit the external fare API ----
_hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_hits_lock = threading.Lock()


def _rate_limit(request: Request, bucket: str, per_minute: int) -> None:
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    with _hits_lock:
        q = _hits[f"{bucket}:{ip}"]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_minute:
            raise HTTPException(429, "Too many searches. Please wait a minute and try again.")
        q.append(now)


# ---- model lifecycle ----

def _load_latest_model() -> bool:
    """Load the newest stored model; train one if the database has none. Returns True if changed."""
    newest = db.latest_model_id()
    if newest is None:
        ingest.retrain()
        newest = db.latest_model_id()
    if newest == STATE["model_id"]:
        return False
    mid, created, mdl = db.load_model()
    if not hasattr(mdl, "bases"):  # a model saved by v1 (different features): retrain
        mdl = ingest.retrain()
        mid, created, _ = db.load_model()
    STATE.update(model=mdl, model_id=mid, model_created=created)
    _cache.clear()
    return True


async def _model_watcher():
    while True:
        await asyncio.sleep(MODEL_POLL_SECONDS)
        try:
            if await asyncio.to_thread(_load_latest_model):
                log.info("loaded newer model %s", STATE["model_id"])
        except Exception as e:  # keep serving with the current model
            log.warning("model reload failed: %s", e)


def _refresh_job() -> bool:
    """Run a price refresh if no worker/instance has started one in the last interval."""
    if not db.try_acquire("refresh", REFRESH_MINUTES * 60 - 60):
        return False
    try:
        ingest.run_refresh()
        _load_latest_model()
    except Exception as e:
        log.warning("refresh failed: %s", e)
    return True


async def _auto_refresh():
    """Every worker checks each minute; the shared DB lock lets only one run each refresh."""
    while True:
        try:
            await asyncio.to_thread(_refresh_job)
        except Exception as e:
            log.warning("auto refresh error: %s", e)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    ingest.seed_featured_routes()
    _load_latest_model()
    tasks = [asyncio.create_task(_model_watcher())]
    if notify.telegram_enabled():
        tasks.append(asyncio.create_task(asyncio.to_thread(notify.setup_telegram_webhook)))
    if travelpayouts.enabled():  # look up city names/coordinates for routes that lack them
        tasks.append(asyncio.create_task(asyncio.to_thread(ingest.fill_route_meta)))
    if AUTO_REFRESH:
        tasks.append(asyncio.create_task(_auto_refresh()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Skyward", version=__version__, lifespan=lifespan,
              description="Worldwide live flight prices, ML fare forecasts and price alerts.",
              docs_url="/docs" if ENABLE_DOCS else None, redoc_url=None,
              openapi_url="/openapi.json" if ENABLE_DOCS else None)
app.add_middleware(GZipMiddleware, minimum_size=1000)

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: blob: https://cdn.jsdelivr.net",
    "connect-src 'self' https://cdn.jsdelivr.net",
    "worker-src 'self' blob:",
    "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
])
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@app.middleware("http")
async def guard(request: Request, call_next):
    """Basic protection for every request: body size cap, per-visitor API rate limit, security headers."""
    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH") and int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    if path.startswith("/api/") and path != "/api/telegram/webhook":
        try:
            _rate_limit(request, "api", API_LIMIT_PER_MIN)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=429, headers={"Retry-After": "60"})
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        if k == "Content-Security-Policy" and path in ("/docs", "/openapi.json"):
            continue  # Swagger UI needs its own inline scripts (docs are off in production)
        resp.headers.setdefault(k, v)
    return resp


def model() -> FareForecaster:
    return STATE["model"]


# ---- helpers ----

def _check_route(route: str) -> str:
    route = route.upper()
    if not ROUTE_RE.match(route) or route[:3] == route[4:]:
        raise HTTPException(400, "Route must look like KUL-LHR (two different 3-letter airport or city codes)")
    return route


def _require_admin(token: str | None) -> None:
    expected = os.getenv("ADMIN_TOKEN")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "Admin token required")


def _live_base(route: str) -> float | None:
    """Typical price for a route from its real fares (median of the latest snapshot)."""
    f = db.fares.c
    with db.connect() as c:
        day = c.execute(select(func.max(f.observed)).where(and_(f.route == route, f.source != "synthetic"))).scalar()
        if day is None:
            return None
        prices = [r[0] for r in c.execute(select(f.price).where(and_(
            f.route == route, f.source != "synthetic", f.observed == day)))]
    return float(pd.Series(prices).median()) if prices else None


def _route_base(route: str) -> float | None:
    return model().base(route) or _live_base(route)


def _route_meta(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    with db.connect() as c:
        return {r["code"]: r for r in db.rows(c.execute(select(db.routes).where(db.routes.c.code.in_(codes))))}


def _meta_out(code: str, meta: dict | None) -> dict:
    m = meta or {}
    return {
        "code": code, "origin": code[:3], "dest": code[4:],
        "origin_name": m.get("origin_name") or code[:3], "dest_name": m.get("dest_name") or code[4:],
        "origin_country": m.get("origin_country"), "dest_country": m.get("dest_country"),
        "origin_coords": [m.get("origin_lat"), m.get("origin_lon")],
        "dest_coords": [m.get("dest_lat"), m.get("dest_lon")],
        "featured": bool(m.get("featured")), "searches": m.get("searches") or 0,
    }


def _iso(x):
    return x.isoformat() if x is not None else None


# ---- info endpoints ----

@app.get("/api/health")
def health():
    m = model()
    return {"ok": True, "version": __version__, "model_mae": m.mae, "model_id": STATE["model_id"]}


@app.get("/api/status")
def status():
    """When prices were last refreshed and how much live data there is."""
    def build():
        f = db.fares.c
        with db.connect() as c:
            last = c.execute(select(func.max(f.fetched_at)).where(f.source != "synthetic")).scalar()
            by_source = dict(c.execute(select(f.source, func.count()).group_by(f.source)).all())
            live_routes = c.execute(select(func.count(func.distinct(f.route))).where(f.source != "synthetic")).scalar()
            pop_updated = c.execute(select(func.max(db.popular.c.updated))).scalar()
            pop_count = c.execute(select(func.count()).select_from(db.popular)).scalar()
        return {
            "version": __version__,
            "live": bool(last),
            "last_updated": _iso(last),
            "rows_by_source": by_source,
            "routes_live": live_routes,
            "popular_routes": pop_count, "popular_updated": _iso(pop_updated),
            "model_trained_at": _iso(STATE["model_created"]),
            "model_mae": model().mae,
            "model_mape": getattr(model(), "mape", None),
        }
    return cached("status", build)


@app.get("/api/rates")
def rates(response: Response):
    """USD-based exchange rates for showing prices in any currency."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    return fx.get_rates()


@app.get("/api/places")
def places(q: str, request: Request, response: Response):
    """Autocomplete cities and airports anywhere in the world."""
    q = q.strip()
    if not 2 <= len(q) <= 40:
        return []
    _rate_limit(request, "places", 90)
    response.headers["Cache-Control"] = "public, max-age=86400"
    try:
        return cached(f"places:{q.lower()}", lambda: travelpayouts.autocomplete(q), seconds=86400)
    except Exception as e:
        log.warning("places lookup failed: %s", e)
        raise HTTPException(502, "Airport search is temporarily unavailable")


@app.get("/api/routes")
def routes():
    """This week's popular routes (Skyward searches + worldwide demand), then featured routes."""
    def build():
        with db.connect() as c:
            pop = db.rows(c.execute(select(db.popular).order_by(db.popular.c.rank)))
            meta = _route_meta([p["code"] for p in pop])
            featured = db.rows(c.execute(select(db.routes).where(db.routes.c.featured.is_(True))))
        out = [{**_meta_out(p["code"], meta.get(p["code"])), "rank": p["rank"],
                "reason": "Trending worldwide" if p["worldwide"] and not p["user_searches"]
                else f"{p['user_searches']} searches this week" if not p["worldwide"]
                else f"Trending · {p['user_searches']} searches", "updated": _iso(p["updated"])} for p in pop]
        seen = {o["code"] for o in out}
        out += [{**_meta_out(r["code"], r), "rank": None, "reason": "Featured", "updated": None}
                for r in featured if r["code"] not in seen]
        return out
    return cached("routes", build)


@app.get("/api/airports")
def airports(response: Response):
    """Every flightable airport in the world (for the interactive globe). Cached for a day."""
    response.headers["Cache-Control"] = "public, max-age=86400"
    try:
        return cached("airports", travelpayouts.airports, seconds=86400)
    except Exception as e:
        log.warning("airports load failed: %s", e)
        raise HTTPException(502, "Airport list is temporarily unavailable")


@app.get("/api/events")
def events():
    return [{"name": k, "month": m, "day": d, "window_days": w, "multiplier": x} for k, (m, d, w, x) in EVENTS.items()]


# ---- search ----

@app.get("/api/search")
def search(origin: str, destination: str, request: Request, month: str | None = None):
    """Live fares for any city pair for one month, with the model's view of each date."""
    o, d = origin.strip().upper(), destination.strip().upper()
    if not (IATA.match(o) and IATA.match(d)) or o == d:
        raise HTTPException(400, "Choose two different cities or airports")
    today = dt.date.today()
    month = month or f"{today.year:04d}-{today.month:02d}"
    if not MONTH_RE.match(month):
        raise HTTPException(400, "Month must look like 2027-01")
    start, end = ingest._month_bounds(month)
    if end <= today or start > today + dt.timedelta(days=366):
        raise HTTPException(400, "Choose a month within the next 12 months")
    route = f"{o}-{d}"

    _rate_limit(request, "search", SEARCH_LIMIT_PER_MIN)
    ingest.register_search(route)
    if travelpayouts.enabled() and not ingest.month_is_fresh(route, month):
        try:
            ingest.fetch_route_month(route, month)
        except Exception as e:
            log.warning("search fetch failed %s %s: %s", route, month, e)
    for k in [k for k in _cache if k.startswith(("routes", "drops", "deals", "status", f"live:{route}"))]:
        _cache.pop(k, None)

    f = db.fares.c
    with db.connect() as c:
        day = c.execute(select(func.max(f.observed)).where(and_(
            f.route == route, f.source != "synthetic", f.depart >= start, f.depart < end))).scalar()
        rows = db.rows(c.execute(
            select(f.depart, f.price, f.carrier, f.flight_number, f.transfers, f.link, f.fetched_at)
            .where(and_(f.route == route, f.source != "synthetic", f.observed == day,
                        f.depart >= max(start, today + dt.timedelta(days=1)), f.depart < end))
            .order_by(f.depart))) if day else []
    meta = _meta_out(route, _route_meta([route]).get(route))
    base = _route_base(route)
    out = {"route": meta, "month": month, "fares": [], "summary": None,
           "fetched_at": _iso(max((r["fetched_at"] for r in rows if r["fetched_at"]), default=None))}
    if not rows:
        return out
    m = model()
    expected = m.predict_frame(pd.DataFrame({"route": route, "depart": [r["depart"] for r in rows],
                                             "observed": today}), bases={route: base})
    outlooks = m.drop_outlook([(base, r["depart"]) for r in rows])
    for r, e, (drop, best_day) in zip(rows, expected, outlooks):
        out["fares"].append({
            "depart": r["depart"].isoformat(), "price": round(r["price"], 2), "carrier": r["carrier"],
            "flight_number": r["flight_number"], "transfers": r["transfers"], "link": r["link"],
            "expected": round(float(e), 2), "vs_expected_pct": round((r["price"] / e - 1) * 100, 1),
            "predicted_drop_pct": round(drop * 100, 1), "best_buy_date": best_day.isoformat(),
        })
    cheapest = min(out["fares"], key=lambda x: x["price"])
    adv = m.advise(route, dt.date.fromisoformat(cheapest["depart"]), cheapest["price"], base=base)
    out["summary"] = {
        "cheapest": cheapest, "typical_price": round(base, 2),
        "dates_found": len(out["fares"]), "advice": {k: v for k, v in adv.items() if k != "curve"},
    }
    return out


# ---- per-route data ----

@app.get("/api/history/{route}")
def history(route: str, response: Response):
    route = _check_route(route)
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"

    def build():
        f = db.fares.c
        synthetic = (f.source == "synthetic").label("synthetic")
        with db.connect() as c:
            daily = pd.DataFrame(db.rows(c.execute(
                select(f.observed, synthetic, func.min(f.price).label("low"),
                       func.avg(f.price).label("avg"), func.count().label("n"))
                .where(f.route == route).group_by(f.observed, synthetic).order_by(f.observed)
            )))
        if daily.empty:
            return []
        # Put the synthetic baseline on the same level as real fares (same calibration as the model)
        real, synth = daily[~daily["synthetic"].astype(bool)], daily[daily["synthetic"].astype(bool)]
        if len(real) and len(synth):
            factor = (real["avg"] * real["n"]).sum() / real["n"].sum() / ((synth["avg"] * synth["n"]).sum() / synth["n"].sum())
            daily.loc[daily["synthetic"].astype(bool), ["low", "avg"]] *= factor
        daily["observed"] = pd.to_datetime(daily["observed"])
        daily["w"] = daily["avg"] * daily["n"]
        wk = daily.set_index("observed").resample("W-MON", label="left", closed="left").agg(
            {"low": "min", "w": "sum", "n": "sum"}).dropna()
        wk = wk[wk["n"] > 0]
        return [{"observed": d.date().isoformat(), "low": round(r.low, 2), "avg": round(r.w / r.n, 2)}
                for d, r in wk.iterrows()]
    return cached(f"history:{route}", build)


@app.get("/api/live/{route}")
def live(route: str, response: Response, limit: int = 60):
    """Latest real fares per departure date, with change vs the previous day's snapshot."""
    route = _check_route(route)
    limit = max(1, min(limit, 200))
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"

    def build():
        f = db.fares.c
        with db.connect() as c:
            days = [r[0] for r in c.execute(
                select(f.observed).where(and_(f.route == route, f.source != "synthetic"))
                .group_by(f.observed).order_by(f.observed.desc()).limit(2))]
            if not days:
                return {"route": route, "live": False, "fares": []}
            latest = db.rows(c.execute(
                select(f.depart, f.price, f.carrier, f.flight_number, f.transfers, f.link, f.fetched_at)
                .where(and_(f.route == route, f.source != "synthetic", f.observed == days[0],
                            f.depart > dt.date.today()))
                .order_by(f.depart).limit(limit)))
            prev = {}
            if len(days) > 1:
                prev = {r["depart"]: r["price"] for r in db.rows(c.execute(
                    select(f.depart, f.price).where(and_(f.route == route, f.source != "synthetic",
                                                         f.observed == days[1]))))}
        base = _route_base(route)
        exp = model().predict_frame(pd.DataFrame(
            {"route": route, "depart": [r["depart"] for r in latest], "observed": dt.date.today()}),
            bases={route: base} if base else None) if latest else []
        out = []
        for r, e in zip(latest, exp):
            before = prev.get(r["depart"])
            out.append({
                "depart": r["depart"].isoformat(), "price": round(r["price"], 2), "carrier": r["carrier"],
                "flight_number": r["flight_number"], "transfers": r["transfers"], "link": r["link"],
                "change": round(r["price"] - before, 2) if before is not None else None,
                "expected": None if pd.isna(e) else round(float(e), 2),
            })
        fetched = max((r["fetched_at"] for r in latest if r["fetched_at"]), default=None)
        return {"route": route, "live": True, "observed": days[0].isoformat(),
                "fetched_at": _iso(fetched), "fares": out}
    return cached(f"live:{route}:{limit}", build)


@app.get("/api/forecast/{route}")
def forecast(route: str, depart: dt.date, request: Request, current_price: float | None = None):
    route = _check_route(route)
    today = dt.date.today()
    if depart <= today:
        raise HTTPException(400, "Departure must be in the future")
    if depart > today + dt.timedelta(days=365):
        raise HTTPException(400, "Departure must be within the next 12 months")
    base = _route_base(route)
    if base is None and travelpayouts.enabled():
        _rate_limit(request, "search", SEARCH_LIMIT_PER_MIN)
        try:
            ingest.fetch_route_month(route, f"{depart.year:04d}-{depart.month:02d}")
        except Exception as e:
            log.warning("forecast fetch failed %s: %s", route, e)
        base = _route_base(route)
    if base is None:
        raise HTTPException(404, f"No price data for {route} yet. Search it first.")
    if current_price is None:
        with db.connect() as c:
            current_price = ingest.latest_live_price(c, route, depart)
    return model().advise(route, depart, current_price, base=base)


# ---- discovery: deals and predicted drops ----

@app.get("/api/deals")
def deals(response: Response, limit: int = 12):
    """Real fares well below the model's expected price for that route and date."""
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    limit = max(1, min(limit, 50))

    def build():
        today = dt.date.today()
        f = db.fares.c
        with db.connect() as c:
            has_live = c.execute(select(func.count()).where(and_(
                f.source != "synthetic", f.observed >= today - dt.timedelta(days=3)))).scalar() > 0
            cond = [f.depart > today]
            cond += ([f.source != "synthetic", f.observed >= today - dt.timedelta(days=3)] if has_live
                     else [f.observed >= today - dt.timedelta(days=21)])
            df = pd.DataFrame(db.rows(c.execute(
                select(f.route, f.observed, f.depart, f.price, f.is_sale, f.source, f.carrier, f.link)
                .where(and_(*cond)))))
        if df.empty:
            return []
        extra = df.groupby("route")["price"].median().to_dict()  # bases for routes the model hasn't seen
        df["expected"] = model().predict_frame(df, bases={k: v for k, v in extra.items() if not model().base(k)})
        df = df.dropna(subset=["expected"])
        df["discount_pct"] = (1 - df["price"] / df["expected"]) * 100
        df["saving"] = df["expected"] - df["price"]
        df = df[df["discount_pct"] > 15].sort_values(["discount_pct", "saving"], ascending=False)
        df = df.drop_duplicates(["route", "depart"]).head(limit)
        meta = _route_meta(df["route"].unique().tolist())
        return [{
            **{k: v for k, v in _meta_out(r.route, meta.get(r.route)).items() if k in ("origin_name", "dest_name")},
            "route": r.route, "observed": r.observed.isoformat(), "depart": r.depart.isoformat(),
            "price": round(r.price, 2), "expected": round(r.expected, 2), "discount_pct": round(r.discount_pct, 1),
            "saving": round(r.saving, 2), "is_sale": bool(r.is_sale), "source": r.source, "carrier": r.carrier,
            "link": r.link,
        } for r in df.itertuples()]
    return cached(f"deals:{limit}", build)


@app.get("/api/drops")
def drops(response: Response, limit: int = 12):
    """Fares the model expects to get cheaper before departure: how much, and the best day to buy."""
    response.headers["Cache-Control"] = "public, max-age=300"
    limit = max(1, min(limit, 50))

    def build():
        today = dt.date.today()
        f = db.fares.c
        with db.connect() as c:
            latest = select(f.route, func.max(f.observed).label("day")).where(
                f.source != "synthetic").group_by(f.route).subquery()
            df = pd.DataFrame(db.rows(c.execute(
                select(f.route, f.depart, f.price, f.carrier, f.link)
                .join(latest, and_(f.route == latest.c.route, f.observed == latest.c.day))
                .where(and_(f.source != "synthetic", f.depart >= today + dt.timedelta(days=21),
                            f.depart <= today + dt.timedelta(days=180))))))
        if df.empty:
            return []
        # keep it light: up to 8 dates per route, spread across the window
        df = df.sort_values(["route", "depart"]).reset_index(drop=True)
        pos = df.groupby("route").cumcount()
        step = df.groupby("route")["depart"].transform("size").floordiv(8).clip(lower=1)
        df = df[(pos % step == 0) & (pos // step < 8)]
        bases = {r: _route_base(r) for r in df["route"].unique()}
        df = df[df["route"].map(bases).notna()]
        if df.empty:
            return []
        outlook = model().drop_outlook([(bases[r.route], r.depart) for r in df.itertuples()])
        df["drop"] = [o[0] for o in outlook]
        df["best_day"] = [o[1] for o in outlook]
        mae = model().mae or 0
        df = df[(df["drop"] >= 0.05) & (df["price"] * df["drop"] >= mae * 0.5)]
        df = df.sort_values("drop", ascending=False).drop_duplicates("route").head(limit)
        meta = _route_meta(df["route"].tolist())
        return [{
            **_meta_out(r.route, meta.get(r.route)),
            "route": r.route, "depart": r.depart.isoformat(), "price": round(r.price, 2),
            "predicted_price": round(r.price * (1 - r.drop), 2), "drop_pct": round(r.drop * 100, 1),
            "best_buy_date": r.best_day.isoformat(), "carrier": r.carrier, "link": r.link,
        } for r in df.itertuples()]
    return cached(f"drops:{limit}", build, seconds=300)


# ---- Price watches: private to whoever created them (manage token) ----

MAX_WATCHES_PER_EMAIL = 20
MAX_CONFIRM_EMAILS_PER_DAY = 3     # stops anyone using Skyward to flood someone else's inbox


class WatchIn(BaseModel):
    route: str
    depart: dt.date
    target_price: float | None = Field(default=None, gt=0, lt=1_000_000)  # USD
    email: EmailStr | None = None
    telegram: bool = False


def _public_watch(w: dict) -> dict:
    return {"id": w["id"], "route": w["route"], "depart": w["depart"].isoformat(),
            "target_price": w["target_price"], "has_email": bool(w["email"]),
            "email_verified": bool(w["email_verified"]), "has_telegram": bool(w["telegram_chat_id"]),
            "telegram_link": _telegram_link(w["id"]) if not w["telegram_chat_id"] else None,
            "alerts_sent": w["alert_count"]}


def _telegram_link(watch_id: int) -> str | None:
    bot = notify.bot_username()
    return f"https://t.me/{bot}?start=w{watch_id}_{security.sign('tg', watch_id)}" if bot else None


def _send_confirmation(watch_id: int, email: str, route: str, depart: dt.date) -> None:
    base = notify.public_url()
    confirm = f"{base}/api/watches/confirm?w={watch_id}&s={security.sign('confirm', watch_id)}"
    unsub = f"{base}/api/watches/unsubscribe?w={watch_id}&s={security.sign('unsub', watch_id)}"
    trip = f"{route[:3]} → {route[4:]} on {depart:%a %d %b %Y}"
    notify.send_email(
        email, "Confirm your Skyward price alert",
        f"Please confirm your price alert for {trip}: {confirm}\n\nIf you didn't ask for this, ignore this email.",
        notify.email_html("Confirm your price alert", [f"You asked Skyward to watch <b>{trip}</b>.",
                          "Confirm and we'll email you only when the real price drops: never more than once a day."],
                          ("Confirm alert", confirm), "Didn't ask for this? Just ignore this email; nothing will be sent."),
        unsub)


def _token_filter(tokens: list[str]):
    """Match manage tokens (stored hashed; plain-text from v2.0 still accepted)."""
    return or_(db.watches.c.token.in_([security.hash_token(t) for t in tokens]), db.watches.c.token.in_(tokens))


@app.post("/api/watches", status_code=201)
def create_watch(w: WatchIn, request: Request, background: BackgroundTasks):
    _rate_limit(request, "watch-create", 10)
    w.route = _check_route(w.route)
    if w.depart <= dt.date.today():
        raise HTTPException(400, "Departure must be in the future")
    if not w.email and not w.telegram:
        raise HTTPException(400, "Add an email address or choose Telegram so we can reach you")
    email = str(w.email).lower() if w.email else None
    send_confirm = False
    if email:
        if not notify.email_enabled():
            raise HTTPException(503, "Email alerts aren't switched on yet. Use Telegram for now.")
        day_ago = ingest._utcnow() - dt.timedelta(days=1)
        with db.connect() as c:
            W = db.watches.c
            total = c.execute(select(func.count()).where(W.email == email)).scalar()
            verified = c.execute(select(func.count()).where(and_(W.email == email, W.email_verified.is_(True)))).scalar()
            recent = c.execute(select(func.count()).where(and_(W.email == email, W.email_verified.is_(False),
                                                               W.created >= day_ago))).scalar()
        if total >= MAX_WATCHES_PER_EMAIL:
            raise HTTPException(429, "This email already has the maximum number of alerts")
        send_confirm = not verified
        if send_confirm and recent >= MAX_CONFIRM_EMAILS_PER_DAY:
            raise HTTPException(429, "Too many unconfirmed alerts for this email today. Check your inbox.")
    if w.telegram and not notify.telegram_enabled():
        raise HTTPException(503, "Telegram alerts aren't switched on yet")
    token = secrets.token_urlsafe(24)
    with db.connect() as c:
        wid = c.execute(insert(db.watches).values(
            token=security.hash_token(token), route=w.route, depart=w.depart, target_price=w.target_price,
            email=email, email_verified=bool(email) and not send_confirm).returning(db.watches.c.id)).scalar()
    if send_confirm:
        background.add_task(_send_confirmation, wid, email, w.route, w.depart)
    ingest.register_search(w.route)  # keep watched routes fresh
    return {"id": wid, "token": token, "route": w.route, "depart": w.depart.isoformat(),
            "target_price": w.target_price, "email_confirmation_sent": send_confirm,
            "telegram_link": _telegram_link(wid) if w.telegram else None}


def _page(title: str, message: str, status: int = 200) -> HTMLResponse:
    import html
    return HTMLResponse(status_code=status, content=(
        f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(title)} · Skyward</title><link rel="stylesheet" href="/static/styles.css">'
        f'<body><main class="notice"><p class="eyebrow">Skyward</p><h1>{html.escape(title)}</h1>'
        f'<p>{html.escape(message)}</p><a class="btn gold" href="/">Back to Skyward</a></main></body>'))


@app.get("/api/watches/confirm", response_class=HTMLResponse)
def confirm_watch(w: int, s: str):
    if not security.verify("confirm", w, s):
        return _page("Link not valid", "This confirmation link is invalid or has been changed.", 400)
    with db.connect() as c:
        row = c.execute(select(db.watches.c.email).where(db.watches.c.id == w)).first()
        if row is None:
            return _page("Alert not found", "This alert was removed or the trip has already departed.", 404)
        # confirming one alert verifies the address for all of that person's alerts
        c.execute(update(db.watches).where(db.watches.c.email == row.email).values(email_verified=True))
    return _page("Alert confirmed", "You'll get an email only when the real price drops: at most once a day per trip.")


@app.get("/api/watches/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(w: int, s: str):
    if not security.verify("unsub", w, s):
        return _page("Link not valid", "This unsubscribe link is invalid.", 400)
    with db.connect() as c:
        c.execute(delete(db.watches).where(db.watches.c.id == w))
    return _page("Alert stopped", "You won't receive any more messages for this alert.")


@app.post("/api/watches/unsubscribe")
def unsubscribe_one_click(w: int, s: str):
    """RFC 8058 one-click unsubscribe (Gmail/Outlook 'Unsubscribe' button)."""
    if not security.verify("unsub", w, s):
        raise HTTPException(400, "Invalid link")
    with db.connect() as c:
        c.execute(delete(db.watches).where(db.watches.c.id == w))
    return {"ok": True}


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    """Messages to the Skyward bot. '/start w<id>_<sig>' links a chat to an alert; '/stop' removes it."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not x_telegram_bot_api_secret_token or not hmac.compare_digest(
            x_telegram_bot_api_secret_token, security.telegram_secret(token)):
        raise HTTPException(403, "Forbidden")
    update_ = await request.json()
    msg = update_.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id") or "")
    text = (msg.get("text") or "").strip()
    if not chat_id or not re.fullmatch(r"-?\d{1,20}", chat_id):
        return {"ok": True}
    m = re.fullmatch(r"/start w(\d{1,10})_([0-9a-f]{32})", text)
    if m and security.verify("tg", int(m[1]), m[2]):
        with db.connect() as c:
            row = c.execute(select(db.watches.c.route, db.watches.c.depart).where(db.watches.c.id == int(m[1]))).first()
            if row:
                c.execute(update(db.watches).where(db.watches.c.id == int(m[1])).values(telegram_chat_id=chat_id))
        reply = (f"✅ Connected. I'll message you when {row.route[:3]} → {row.route[4:]} on {row.depart:%d %b %Y} "
                 f"gets cheaper (at most once a day). Send /stop to stop all alerts." if row
                 else "That alert no longer exists.")
    elif text == "/stop":
        with db.connect() as c:
            c.execute(update(db.watches).where(db.watches.c.telegram_chat_id == chat_id).values(telegram_chat_id=None))
        reply = "🛑 Done. You won't get any more Skyward alerts here."
    else:
        reply = "Hi! Set a price alert on Skyward and tap 'Connect Telegram' to link it here. Send /stop to stop alerts."
    await asyncio.to_thread(notify.send_telegram, chat_id, reply)
    return {"ok": True}


@app.get("/api/alerts/config")
def alerts_config():
    """Which alert channels are switched on (so the page only offers what works)."""
    return {"email": notify.email_enabled(), "telegram": notify.telegram_enabled()}


@app.get("/api/watches")
def list_watches(tokens: str = ""):
    """Watches for the given comma-separated manage tokens (the browser keeps them)."""
    toks = [t for t in tokens.split(",") if 10 <= len(t) <= 64][:50]
    if not toks:
        return []
    with db.connect() as c:
        ws = db.rows(c.execute(select(db.watches).where(_token_filter(toks)).order_by(db.watches.c.id.desc())))
        ids = [w["id"] for w in ws]
        al = db.rows(c.execute(select(db.alerts).where(db.alerts.c.watch_id.in_(ids))
                               .order_by(db.alerts.c.id.desc()).limit(50))) if ids else []
    return {"watches": [_public_watch(w) for w in ws],
            "alerts": [{"watch_id": a["watch_id"], "kind": a["kind"], "message": a["message"],
                        "created": _iso(a["created"])} for a in al]}


@app.delete("/api/watches/{token}", status_code=204)
def delete_watch(token: str):
    if not 10 <= len(token) <= 64:
        raise HTTPException(400, "Invalid token")
    with db.connect() as c:
        c.execute(delete(db.watches).where(_token_filter([token])))


@app.post("/api/watches/check")
def check_watches(x_admin_token: str | None = Header(default=None)):
    """Evaluate every watch and fire alerts. Normally run by the refresh job; admin only."""
    _require_admin(x_admin_token)
    return ingest.check_watches(model())


@app.post("/api/refresh", status_code=202)
async def refresh():
    """Wake-up / refresh hook for the scheduler. Safe to call publicly: it runs at most once
    per refresh interval across all servers, and returns immediately."""
    last = await asyncio.to_thread(db.last_job_start, "refresh")
    asyncio.get_running_loop().run_in_executor(None, _refresh_job)
    return {"accepted": True, "last_started": _iso(last), "interval_minutes": REFRESH_MINUTES}


@app.post("/api/admin/reload-model")
def reload_model(x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    return {"changed": _load_latest_model(), "model_id": STATE["model_id"]}


if os.path.isdir(FRONTEND):
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND, "index.html"))
