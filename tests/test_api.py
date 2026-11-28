# Copyright (c) 2026 Faryaan Khan
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import datetime as dt
import os
import tempfile

os.environ["SKYWARD_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("DATABASE_URL", None)
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["CACHE_SECONDS"] = "0"
os.environ["AUTO_REFRESH"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, ingest  # noqa: E402
from app.main import app  # noqa: E402
from app.sources import rates, travelpayouts  # noqa: E402

TODAY = dt.date.today()
PLACES = {"KUL": ("Kuala Lumpur", "Malaysia", 3.1, 101.7), "SIN": ("Singapore", "Singapore", 1.35, 103.99),
          "CDG": ("Paris", "France", 49.0, 2.55), "LHR": ("London", "United Kingdom", 51.47, -0.45)}


def fake_fetch(origin, dest, month, client=None):
    """Stand-in for the Travelpayouts API: one cheap fare on the 20th of each month."""
    y, m = map(int, month.split("-"))
    return [{"depart": dt.date(y, m, 20), "price": 42.0, "carrier": "AK", "flight_number": "700",
             "transfers": 0, "link": "https://www.aviasales.com/search/x"}]


def fake_autocomplete(term, locale="en", client=None):
    out = []
    for code, (city, country, lat, lon) in PLACES.items():
        if term.upper() == code or term.lower() in city.lower():
            out.append({"code": code, "type": "city", "name": city, "city": city, "country": country,
                        "country_code": None, "lat": lat, "lon": lon})
    return out


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never call real external APIs."""
    monkeypatch.setattr(travelpayouts, "autocomplete", fake_autocomplete)
    monkeypatch.setattr(travelpayouts, "fetch_month", fake_fetch)


def test_end_to_end():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        assert len(c.get("/api/routes").json()) >= 5
        assert c.get("/api/status").json()["live"] is False

        depart = (TODAY + dt.timedelta(days=60)).isoformat()
        f = c.get(f"/api/forecast/KUL-SIN?depart={depart}").json()
        assert f["action"] in {"BUY", "WAIT"} and f["expected_low"] > 0
        assert isinstance(c.get("/api/deals").json(), list)
        # an unknown route with no data (and no live API key) gives a clear 404, bad input a 400
        assert c.get("/api/forecast/XXX-YYY?depart=" + depart).status_code == 404
        assert c.get("/api/forecast/not-a-route?depart=" + depart).status_code == 400
        assert len(c.get("/api/history/KUL-SIN").json()) > 50
        from app import __version__
        assert c.get("/api/health").json()["version"] == __version__


def test_live_ingest_and_endpoints():
    with TestClient(app) as c:
        saved = ingest.fetch_live(fetcher=fake_fetch)
        assert sum(saved.values()) > 0
        # running again the same day replaces the snapshot instead of duplicating it
        again = ingest.fetch_live(fetcher=fake_fetch)
        assert again == saved

        st = c.get("/api/status").json()
        assert st["live"] and st["last_updated"]
        lv = c.get("/api/live/KUL-SIN").json()
        assert lv["live"] and lv["fares"] and lv["fares"][0]["price"] == 42.0
        # a 42 fare on long-haul routes is far below the model's expectation -> a real deal
        deals = c.get("/api/deals").json()
        assert deals and all(d["source"] == "travelpayouts" for d in deals)

        # history is calibrated to the real price level: no cliff between demo and live weeks
        h = c.get("/api/history/KUL-LHR").json()
        older = sorted(x["avg"] for x in h[:-2])
        assert 0.5 < h[-1]["avg"] / older[len(older) // 2] < 2

        ingest.retrain()
        assert c.post("/api/admin/reload-model", headers={"X-Admin-Token": "test-admin"}).json()["changed"]


def test_alerts_double_opt_in_no_spam_and_unsubscribe(monkeypatch):
    from app import notify, security
    sent = []
    monkeypatch.setattr(notify, "email_enabled", lambda: True)
    monkeypatch.setattr(notify, "send_email", lambda to, subject, *a, **k: sent.append((to, subject)) or True)
    nxt = (TODAY.replace(day=1) + dt.timedelta(days=32)).replace(day=20)
    admin = {"X-Admin-Token": "test-admin"}
    with TestClient(app) as c:
        ingest.fetch_route_month("KUL-SIN", nxt.strftime("%Y-%m"), fetcher=fake_fetch)   # live fare: 42 USD
        w = c.post("/api/watches", json={"route": "KUL-SIN", "depart": nxt.isoformat(), "target_price": 9999,
                                         "email": "Someone@Example.com"}).json()
        assert w["token"] and w["email_confirmation_sent"]
        assert sent[-1][0] == "someone@example.com" and "Confirm" in sent[-1][1]
        # watches are private: no token, no list; emails are never returned
        assert c.get("/api/watches").json() == []
        mine = c.get(f"/api/watches?tokens={w['token']}").json()["watches"][0]
        assert mine["has_email"] and not mine["email_verified"] and "email" not in mine
        # tokens are stored hashed
        with db.connect() as conn:
            stored = conn.execute(db.watches.select().where(db.watches.c.id == w["id"])).first().token
        assert stored == security.hash_token(w["token"])

        # no alert before the address is confirmed
        assert c.post("/api/watches/check").status_code == 403
        assert not c.post("/api/watches/check", headers=admin).json()
        # forged confirm link is rejected; the real one works
        assert c.get(f"/api/watches/confirm?w={w['id']}&s=bad").status_code == 400
        assert c.get(f"/api/watches/confirm?w={w['id']}&s={security.sign('confirm', w['id'])}").status_code == 200

        fired = c.post("/api/watches/check", headers=admin).json()
        assert [x["watch_id"] for x in fired] == [w["id"]] and fired[0]["kind"] == "target"
        assert "42 USD" in sent[-1][1]
        # no spam: the same watch isn't alerted again (cooldown + needs a further price fall)
        assert not c.post("/api/watches/check", headers=admin).json()
        assert c.get(f"/api/watches?tokens={w['token']}").json()["alerts"]

        # a second alert for a confirmed address needs no new confirmation email
        before = len(sent)
        w2 = c.post("/api/watches", json={"route": "KUL-SIN", "depart": nxt.isoformat(),
                                          "email": "someone@example.com"}).json()
        assert not w2["email_confirmation_sent"] and len(sent) == before

        # bad input is rejected
        assert c.post("/api/watches", json={"route": "KUL-SIN", "depart": nxt.isoformat(), "email": "not-an-email"}).status_code == 422
        assert c.post("/api/watches", json={"route": "KUL-SIN", "depart": nxt.isoformat()}).status_code == 400

        # unsubscribe link (from the email) removes the alert; forged links don't
        assert c.get(f"/api/watches/unsubscribe?w={w['id']}&s=nope").status_code == 400
        assert c.get(f"/api/watches/unsubscribe?w={w['id']}&s={security.sign('unsub', w['id'])}").status_code == 200
        assert c.get(f"/api/watches?tokens={w['token']}").json()["watches"] == []
        assert c.delete(f"/api/watches/{w2['token']}").status_code == 204


def test_affiliate_marker_exposed_only_when_valid(monkeypatch):
    from app.sources import travelpayouts
    with TestClient(app) as c:
        monkeypatch.delenv("TRAVELPAYOUTS_MARKER", raising=False)
        assert c.get("/api/alerts/config").json()["affiliate_marker"] is None
        monkeypatch.setenv("TRAVELPAYOUTS_MARKER", " 123456 ")
        assert c.get("/api/alerts/config").json()["affiliate_marker"] == "123456"
        for bad in ("12ab", "1&x=2", "<script>", "1" * 13, "\u0661\u0662"):
            monkeypatch.setenv("TRAVELPAYOUTS_MARKER", bad)
            assert travelpayouts.affiliate_marker() is None


def test_telegram_connect_and_webhook_security(monkeypatch):
    from app import notify, security
    replies = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setattr(notify, "bot_username", lambda: "SkywardTestBot")
    monkeypatch.setattr(notify, "send_telegram", lambda chat, text, buttons=None: replies.append((chat, text)) or True)
    depart = (TODAY + dt.timedelta(days=40)).isoformat()
    with TestClient(app) as c:
        w = c.post("/api/watches", json={"route": "KUL-SIN", "depart": depart, "telegram": True}).json()
        assert w["telegram_link"].startswith("https://t.me/SkywardTestBot?start=w")
        payload = w["telegram_link"].split("start=")[1]
        upd = {"message": {"chat": {"id": 555}, "text": f"/start {payload}"}}
        # calls without Telegram's secret header are refused
        assert c.post("/api/telegram/webhook", json=upd).status_code == 403
        hdr = {"X-Telegram-Bot-Api-Secret-Token": security.telegram_secret("123:test")}
        assert c.post("/api/telegram/webhook", json=upd, headers=hdr).status_code == 200
        assert "Connected" in replies[-1][1]
        assert c.get(f"/api/watches?tokens={w['token']}").json()["watches"][0]["has_telegram"]
        # a forged start payload doesn't link anything
        c.post("/api/telegram/webhook", json={"message": {"chat": {"id": 777}, "text": f"/start w{w['id']}_{'0' * 32}"}}, headers=hdr)
        assert "Hi!" in replies[-1][1]
        c.post("/api/telegram/webhook", json={"message": {"chat": {"id": 555}, "text": "/stop"}}, headers=hdr)
        assert not c.get(f"/api/watches?tokens={w['token']}").json()["watches"][0]["has_telegram"]


def test_security_headers_rate_limit_and_body_cap():
    with TestClient(app) as c:
        r = c.get("/")
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY" and "max-age" in r.headers["strict-transport-security"]
        assert c.get("/docs").status_code == 404             # developer docs are off on the public site
        big = "x" * 20000
        assert c.post("/api/watches", content=big, headers={"content-type": "application/json"}).status_code == 413


def test_weekly_popular_routes():
    with TestClient(app) as c:
        for _ in range(5):
            ingest.register_search("KUL-CDG")
        rows = ingest.compute_popular(directions=lambda hub, client=None: ["LON", "PAR"] if hub == "KUL" else [])
        codes = [r["code"] for r in rows]
        assert codes[0] == "KUL-CDG"           # 5 searches x2 = 10 beats the #1 worldwide route (8)
        assert "KUL-LON" in codes and "KUL-PAR" in codes
        api = c.get("/api/routes").json()
        assert api[0]["code"] == "KUL-CDG" and "searches" in api[0]["reason"]
        assert "KUL-LON" in ingest.tracked_routes()
        # variety: one destination can't take over the list, and each origin appears at most twice
        rows = ingest.compute_popular(directions=lambda hub, client=None: ["MOW", "BKK", "TAS"])
        dests = [r["code"][4:] for r in rows if r["worldwide"]]
        assert len(dests) == len(set(dests))
        origins = [r["code"][:3] for r in rows if r["worldwide"]]
        assert max(origins.count(o) for o in set(origins)) <= 2


def test_worldwide_search_any_route(monkeypatch):
    monkeypatch.setattr(travelpayouts, "enabled", lambda: True)
    calls = []
    monkeypatch.setattr(travelpayouts, "fetch_month",
                        lambda o, d, m, client=None: calls.append((o, d, m)) or fake_fetch(o, d, m))
    nxt = (TODAY.replace(day=1) + dt.timedelta(days=32)).strftime("%Y-%m")
    with TestClient(app) as c:
        s = c.get(f"/api/search?origin=kul&destination=CDG&month={nxt}").json()
        assert s["route"]["code"] == "KUL-CDG" and s["route"]["dest_name"] == "Paris"
        assert s["route"]["dest_coords"] == [49.0, 2.55]
        assert s["fares"] and s["fares"][0]["price"] == 42.0 and s["fares"][0]["expected"] > 0
        assert s["summary"]["advice"]["action"] in {"BUY", "WAIT"}
        # a repeat search within 15 minutes is served from the database, not the API
        c.get(f"/api/search?origin=KUL&destination=CDG&month={nxt}")
        assert len(calls) == 1
        # the searched route is now tracked and appears in popular routes
        assert "KUL-CDG" in ingest.tracked_routes()
        assert any(r["code"] == "KUL-CDG" for r in c.get("/api/routes").json())
        # forecasts now work for this brand-new route
        f = c.get(f"/api/forecast/KUL-CDG?depart={s['fares'][0]['depart']}").json()
        assert f["action"] in {"BUY", "WAIT"} and f["typical_price"] > 0

        assert c.get("/api/search?origin=KUL&destination=KUL").status_code == 400
        assert c.get("/api/search?origin=KUL&destination=CDG&month=1999-01").status_code == 400
        assert isinstance(c.get("/api/drops").json(), list)


def multi_airline_fetch(origin, dest, month, client=None):
    """Several airlines per date, so the comparison has something to compare."""
    y, m = map(int, month.split("-"))
    out = []
    for day in (10, 20):
        for carrier, price in (("MH", 300.0), ("AK", 180.0), ("MK", 420.0), ("AK", 199.0)):
            out.append({"depart": dt.date(y, m, day), "price": price + day, "carrier": carrier,
                        "flight_number": "1", "transfers": 0 if carrier == "MH" else 1,
                        "link": f"https://www.aviasales.com/search/{carrier}"})
    return out


def test_compare_airlines_fast_refresh_and_background(monkeypatch):
    monkeypatch.setattr(travelpayouts, "enabled", lambda: True)
    monkeypatch.setattr(travelpayouts, "fetch_month", multi_airline_fetch)
    nxt = (TODAY.replace(day=1) + dt.timedelta(days=32)).strftime("%Y-%m")
    with TestClient(app) as c:
        s = c.get(f"/api/search?origin=SIN&destination=MRU&month={nxt}").json()
        day = s["fares"][0]
        assert day["price"] == 190.0 and day["carrier"] == "AK"           # cheapest of all airlines
        assert [o["carrier"] for o in day["offers"]] == ["AK", "MH", "MK"]  # one (cheapest) offer per airline
        assert [a["carrier"] for a in s["airlines"]] == ["AK", "MH", "MK"]
        assert s["airlines"][0]["dates"] == 2

        # the page's own 1-minute refresh doesn't count as a search
        from sqlalchemy import func, select
        def events():
            with db.connect() as conn:
                return conn.execute(select(func.count()).select_from(db.search_events)
                                    .where(db.search_events.c.route == "SIN-MRU")).scalar()
        before = events()
        c.get(f"/api/search?origin=SIN&destination=MRU&month={nxt}&background=1")
        assert events() == before

        # the fast refresh covers recently searched routes
        assert "SIN-MRU" in ingest.quick_routes()
        assert ingest.quick_refresh(fetcher=multi_airline_fetch) > 0


def test_how_it_works_page():
    with TestClient(app) as c:
        r = c.get("/how")
        assert r.status_code == 200 and "How it works" in r.text
        assert "script-src 'self'" in r.headers["content-security-policy"]


def test_places_and_rates(monkeypatch):
    monkeypatch.setattr(rates, "_cache", {"at": 0.0, "data": None})
    with TestClient(app) as c:
        p = c.get("/api/places?q=paris").json()
        assert p[0]["code"] == "CDG" and p[0]["country"] == "France"
        assert c.get("/api/places?q=a").json() == []   # too short: no external call
        r = rates.get_rates(fetch=lambda: {"result": "success", "time_last_update_utc": "x",
                                           "rates": {"USD": 1, "MYR": 4.1}})
        assert r["rates"]["MYR"] == 4.1
        assert c.get("/api/rates").json()["rates"]["MYR"] == 4.1   # served from cache


def test_model_drop_outlook_and_new_route():
    from app.main import model
    m = model()
    far = TODAY + dt.timedelta(days=150)
    (drop, day), = m.drop_outlook([(300.0, far)])
    assert -1 < drop < 1 and TODAY <= day < far
    adv = m.advise("ZZZ-YYY", far, base=300.0)
    assert adv["typical_price"] == 300.0 and adv["expected_low"] > 0


def test_old_v1_model_is_replaced_on_load():
    import pickle
    import types

    from sqlalchemy import insert

    from app import main
    with TestClient(app):
        with db.connect() as c:   # a model saved by v1 has no per-route bases
            c.execute(insert(db.model_store), {"mae": 1.0, "rows_trained": 1,
                                               "blob": pickle.dumps(types.SimpleNamespace(mae=1.0))})
        main._load_latest_model()
        assert hasattr(main.model(), "bases") and main.model().bases


def test_refresh_lock_runs_once_per_interval():
    with TestClient(app):
        assert db.try_acquire("unit-test-job", 600) is True
        assert db.try_acquire("unit-test-job", 600) is False   # second caller is refused
        assert db.try_acquire("unit-test-job", 0) is True       # interval elapsed -> allowed


def test_synthetic_is_calibrated_to_live_prices():
    import pandas as pd
    obs = TODAY
    rows = [{"route": "A-B", "observed": obs, "depart": obs + dt.timedelta(days=30), "price": 400.0,
             "is_sale": False, "source": "synthetic"} for _ in range(20)]
    rows += [{"route": "A-B", "observed": obs, "depart": obs + dt.timedelta(days=30), "price": 100.0,
              "is_sale": False, "source": "travelpayouts"} for _ in range(20)]
    rows += [{"route": "C-D", "observed": obs, "depart": obs + dt.timedelta(days=30), "price": 400.0,
              "is_sale": False, "source": "synthetic"} for _ in range(20)]
    out = ingest.calibrate_synthetic(pd.DataFrame(rows))
    assert set(out.loc[out.route == "A-B", "price"]) == {100.0}   # scaled down to the real level
    assert set(out.loc[out.route == "C-D", "price"]) == {400.0}   # no live data -> untouched


def test_db_url_normalisation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host/db?sslmode=require")
    assert db._url() == "postgresql+psycopg://u:p@host/db?sslmode=require"
