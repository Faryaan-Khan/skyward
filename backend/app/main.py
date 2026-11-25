"""Skyward API: fare history, ML forecasts, deals and price alerts."""
from __future__ import annotations

import datetime as dt
import os
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, notify
from .model import FareForecaster
from .synthetic import EVENTS, ROUTES

STATE: dict = {}
FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")


def load_history() -> pd.DataFrame:
    with db.connect() as c:
        return pd.read_sql_query("SELECT route, observed, depart, price, is_sale FROM fares", c)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    STATE["model"] = FareForecaster().fit(load_history())
    yield


app = FastAPI(title="Skyward", version="0.1.0", lifespan=lifespan)


class WatchIn(BaseModel):
    route: str
    depart: dt.date
    target_price: float | None = None
    email: str | None = None
    telegram_chat_id: str | None = None


def _check_route(route: str) -> None:
    if route not in ROUTES:
        raise HTTPException(404, f"Unknown route {route}. Try one of {list(ROUTES)}")


@app.get("/api/health")
def health():
    return {"ok": True, "model_mae": STATE["model"].mae}


@app.get("/api/routes")
def routes():
    return [{"code": r, "origin": r[:3], "dest": r[4:]} for r in ROUTES]


@app.get("/api/events")
def events():
    return [{"name": k, "month": m, "day": d, "window_days": w, "multiplier": x} for k, (m, d, w, x) in EVENTS.items()]


@app.get("/api/history/{route}")
def history(route: str):
    _check_route(route)
    with db.connect() as c:
        rows = c.execute(
            "SELECT observed, ROUND(MIN(price),2) low, ROUND(AVG(price),2) avg FROM fares WHERE route=? "
            "GROUP BY strftime('%Y-%W', observed) ORDER BY observed",
            (route,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/forecast/{route}")
def forecast(route: str, depart: dt.date, current_price: float | None = None):
    _check_route(route)
    if depart <= dt.date.today():
        raise HTTPException(400, "Departure must be in the future")
    return STATE["model"].advise(route, depart, current_price)


@app.get("/api/deals")
def deals(limit: int = 12):
    """Recent fares well below the model's expected price, plus flagged flash sales."""
    since = (dt.date.today() - dt.timedelta(days=21)).isoformat()
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT route, observed, depart, price, is_sale FROM fares WHERE observed>=? AND depart>?",
            (since, dt.date.today().isoformat()),
        )]
    out = []
    m = STATE["model"]
    for r in rows:
        exp = float(m.predict(r["route"], dt.date.fromisoformat(r["depart"]), [dt.date.fromisoformat(r["observed"])])[0])
        disc = 1 - r["price"] / exp
        if disc > 0.15:
            out.append({**r, "expected": round(exp, 2), "discount_pct": round(disc * 100, 1), "is_sale": bool(r["is_sale"])})
    out.sort(key=lambda x: -x["discount_pct"])
    return out[:limit]


@app.get("/api/watches")
def list_watches():
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM watches ORDER BY id DESC")]


@app.post("/api/watches", status_code=201)
def create_watch(w: WatchIn):
    _check_route(w.route)
    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO watches(route, depart, target_price, email, telegram_chat_id) VALUES (?,?,?,?,?)",
            (w.route, w.depart.isoformat(), w.target_price, w.email, w.telegram_chat_id),
        )
        return {"id": cur.lastrowid, **w.model_dump(mode="json")}


@app.delete("/api/watches/{watch_id}", status_code=204)
def delete_watch(watch_id: int):
    with db.connect() as c:
        c.execute("DELETE FROM watches WHERE id=?", (watch_id,))


@app.post("/api/watches/check")
def check_watches():
    """Evaluate every watch and fire alerts. Run on a schedule (cron / GitHub Actions)."""
    fired = []
    with db.connect() as c:
        for w in [dict(r) for r in c.execute("SELECT * FROM watches")]:
            depart = dt.date.fromisoformat(w["depart"])
            if depart <= dt.date.today():
                continue
            adv = STATE["model"].advise(w["route"], depart)
            hit_target = w["target_price"] and adv["current_price"] <= w["target_price"]
            if adv["action"] == "BUY" or hit_target:
                kind = "target" if hit_target else "buy_now"
                msg = (f"{w['route']} on {w['depart']}: {adv['current_price']:.0f} now. "
                       f"Expected low {adv['expected_low']:.0f}. {adv['reason']}")
                notify.dispatch(w, f"Skyward: time to book {w['route']}", msg)
                c.execute("INSERT INTO alerts(watch_id, kind, message) VALUES (?,?,?)", (w["id"], kind, msg))
                fired.append({"watch_id": w["id"], "kind": kind, "message": msg})
    return fired


@app.get("/api/alerts")
def alerts():
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 50")]


if os.path.isdir(FRONTEND):
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND, "index.html"))
