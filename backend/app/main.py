"""Skyward API: live fares, ML forecasts, deals and price alerts.

Scaling design
* Stateless web workers: all state lives in the shared database (Postgres in production),
  so you can run as many workers/instances as traffic needs.
* Heavy work (fetching prices, training) runs in a separate job (`app.ingest`). The trained
  model is stored in the database; each worker reloads it when a newer one appears.
* Read endpoints are cached in memory for a short time and responses are gzip-compressed.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hmac
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, insert, select

from . import db, ingest
from .model import FareForecaster
from .synthetic import EVENTS, ROUTES

log = logging.getLogger("skyward")
STATE: dict = {"model": None, "model_id": None, "model_created": None}
FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
MODEL_POLL_SECONDS = int(os.getenv("MODEL_POLL_SECONDS", "120"))
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "60"))
REFRESH_MINUTES = int(os.getenv("REFRESH_MINUTES", "15"))
AUTO_REFRESH = os.getenv("AUTO_REFRESH", "1") != "0"
_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, fn):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    val = fn()
    _cache[key] = (time.monotonic(), val)
    return val


def _load_latest_model() -> bool:
    """Load the newest stored model; train one if the database has none. Returns True if changed."""
    newest = db.latest_model_id()
    if newest is None:
        ingest.retrain()
        newest = db.latest_model_id()
    if newest == STATE["model_id"]:
        return False
    mid, created, model = db.load_model()
    STATE.update(model=model, model_id=mid, model_created=created)
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
    _load_latest_model()
    tasks = [asyncio.create_task(_model_watcher())]
    if AUTO_REFRESH:
        tasks.append(asyncio.create_task(_auto_refresh()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Skyward", version="0.2.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)


def model() -> FareForecaster:
    return STATE["model"]


class WatchIn(BaseModel):
    route: str
    depart: dt.date
    target_price: float | None = Field(default=None, gt=0)
    email: str | None = Field(default=None, max_length=255)
    telegram_chat_id: str | None = Field(default=None, max_length=64)


def _check_route(route: str) -> None:
    if route not in ROUTES:
        raise HTTPException(404, f"Unknown route {route}. Try one of {list(ROUTES)}")


def _require_admin(token: str | None) -> None:
    expected = os.getenv("ADMIN_TOKEN")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "Admin token required")


@app.get("/api/health")
def health():
    return {"ok": True, "model_mae": model().mae, "model_id": STATE["model_id"]}


@app.get("/api/status")
def status():
    """When prices were last refreshed and where the data comes from."""
    def build():
        with db.connect() as c:
            last = c.execute(select(func.max(db.fares.c.fetched_at)).where(db.fares.c.source != "synthetic")).scalar()
            by_source = dict(c.execute(
                select(db.fares.c.source, func.count()).group_by(db.fares.c.source)).all())
        return {
            "live": bool(last),
            "last_updated": last.isoformat() if last else None,
            "rows_by_source": by_source,
            "model_trained_at": STATE["model_created"].isoformat() if STATE["model_created"] else None,
            "model_mae": model().mae,
        }
    return cached("status", build)


@app.get("/api/routes")
def routes():
    return [{"code": r, "origin": r[:3], "dest": r[4:]} for r in ROUTES]


@app.get("/api/events")
def events():
    return [{"name": k, "month": m, "day": d, "window_days": w, "multiplier": x} for k, (m, d, w, x) in EVENTS.items()]


@app.get("/api/history/{route}")
def history(route: str, response: Response):
    _check_route(route)
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
    _check_route(route)
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
        if latest:
            exp = model().predict_frame(pd.DataFrame(
                {"route": route, "depart": [r["depart"] for r in latest], "observed": dt.date.today()}))
        out = []
        for r, e in zip(latest, exp if latest else []):
            before = prev.get(r["depart"])
            out.append({
                "depart": r["depart"].isoformat(), "price": round(r["price"], 2), "carrier": r["carrier"],
                "flight_number": r["flight_number"], "transfers": r["transfers"], "link": r["link"],
                "change": round(r["price"] - before, 2) if before is not None else None,
                "expected": round(float(e), 2),
            })
        fetched = max((r["fetched_at"] for r in latest if r["fetched_at"]), default=None)
        return {"route": route, "live": True, "observed": days[0].isoformat(),
                "fetched_at": fetched.isoformat() if fetched else None, "fares": out}
    return cached(f"live:{route}:{limit}", build)


@app.get("/api/forecast/{route}")
def forecast(route: str, depart: dt.date, current_price: float | None = None):
    _check_route(route)
    if depart <= dt.date.today():
        raise HTTPException(400, "Departure must be in the future")
    if depart > dt.date.today() + dt.timedelta(days=365):
        raise HTTPException(400, "Departure must be within the next 12 months")
    if current_price is None:
        with db.connect() as c:
            current_price = ingest.latest_live_price(c, route, depart)
    return model().advise(route, depart, current_price)


@app.get("/api/deals")
def deals(response: Response, limit: int = 12):
    """Recent fares well below the model's expected price, plus flagged flash sales. Real fares first."""
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
        df["expected"] = model().predict_frame(df)
        df["discount_pct"] = (1 - df["price"] / df["expected"]) * 100
        df = df[df["discount_pct"] > 15].sort_values("discount_pct", ascending=False)
        df = df.drop_duplicates(["route", "depart"]).head(limit)
        return [{
            "route": r.route, "observed": r.observed.isoformat(), "depart": r.depart.isoformat(),
            "price": round(r.price, 2), "expected": round(r.expected, 2), "discount_pct": round(r.discount_pct, 1),
            "is_sale": bool(r.is_sale), "source": r.source, "carrier": r.carrier, "link": r.link,
        } for r in df.itertuples()]
    return cached(f"deals:{limit}", build)


# ---- Price watches: private to whoever created them (manage token) ----

def _public_watch(w: dict) -> dict:
    return {"id": w["id"], "route": w["route"], "depart": w["depart"].isoformat(),
            "target_price": w["target_price"], "has_email": bool(w["email"]),
            "has_telegram": bool(w["telegram_chat_id"])}


@app.post("/api/watches", status_code=201)
def create_watch(w: WatchIn):
    _check_route(w.route)
    if w.depart <= dt.date.today():
        raise HTTPException(400, "Departure must be in the future")
    token = secrets.token_urlsafe(24)
    with db.connect() as c:
        wid = c.execute(insert(db.watches).values(
            token=token, route=w.route, depart=w.depart, target_price=w.target_price,
            email=w.email, telegram_chat_id=w.telegram_chat_id).returning(db.watches.c.id)).scalar()
    return {"id": wid, "token": token, **w.model_dump(mode="json")}


@app.get("/api/watches")
def list_watches(tokens: str = ""):
    """Watches for the given comma-separated manage tokens (the browser keeps them)."""
    toks = [t for t in tokens.split(",") if t][:50]
    if not toks:
        return []
    with db.connect() as c:
        ws = db.rows(c.execute(select(db.watches).where(db.watches.c.token.in_(toks)).order_by(db.watches.c.id.desc())))
        ids = [w["id"] for w in ws]
        al = db.rows(c.execute(select(db.alerts).where(db.alerts.c.watch_id.in_(ids))
                               .order_by(db.alerts.c.id.desc()).limit(50))) if ids else []
    return {"watches": [_public_watch(w) for w in ws],
            "alerts": [{"watch_id": a["watch_id"], "kind": a["kind"], "message": a["message"],
                        "created": a["created"].isoformat() if a["created"] else None} for a in al]}


@app.delete("/api/watches/{token}", status_code=204)
def delete_watch(token: str):
    with db.connect() as c:
        c.execute(delete(db.watches).where(db.watches.c.token == token))


@app.post("/api/watches/check")
def check_watches(x_admin_token: str | None = Header(default=None)):
    """Evaluate every watch and fire alerts. Normally run by the ingestion job; admin only."""
    _require_admin(x_admin_token)
    return ingest.check_watches(model())


@app.post("/api/refresh", status_code=202)
async def refresh():
    """Wake-up / refresh hook for the scheduler. Safe to call publicly: it runs at most once
    per refresh interval across all servers, and returns immediately."""
    last = await asyncio.to_thread(db.last_job_start, "refresh")
    asyncio.get_running_loop().run_in_executor(None, _refresh_job)
    return {"accepted": True, "last_started": last.isoformat() if last else None,
            "interval_minutes": REFRESH_MINUTES}


@app.post("/api/admin/reload-model")
def reload_model(x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    return {"changed": _load_latest_model(), "model_id": STATE["model_id"]}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


if os.path.isdir(FRONTEND):
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND, "index.html"))
