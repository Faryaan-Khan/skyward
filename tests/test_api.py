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
        assert c.get("/api/health").json()["version"] == "2.0.0"


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


def test_watches_are_private_and_alerts_fire():
    with TestClient(app) as c:
        depart = (TODAY + dt.timedelta(days=60)).isoformat()
        w = c.post("/api/watches", json={"route": "KUL-SIN", "depart": depart, "target_price": 9999,
                                         "email": "someone@example.com"}).json()
        assert w["token"]
        # nobody can list watches without the token, and emails are never returned
        assert c.get("/api/watches").json() == []
        mine = c.get(f"/api/watches?tokens={w['token']}").json()
        assert mine["watches"][0]["has_email"] and "email" not in mine["watches"][0]

        assert c.post("/api/watches/check").status_code == 403
        fired = c.post("/api/watches/check", headers={"X-Admin-Token": "test-admin"}).json()
        assert any(x["watch_id"] == w["id"] for x in fired)
        # cooldown: the same watch is not alerted twice in a row
        fired2 = c.post("/api/watches/check", headers={"X-Admin-Token": "test-admin"}).json()
        assert not any(x["watch_id"] == w["id"] for x in fired2)
        assert c.get(f"/api/watches?tokens={w['token']}").json()["alerts"]

        assert c.delete(f"/api/watches/{w['token']}").status_code == 204
        assert c.get(f"/api/watches?tokens={w['token']}").json()["watches"] == []


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
