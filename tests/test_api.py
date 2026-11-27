import datetime as dt
import os
import tempfile

os.environ["SKYWARD_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("DATABASE_URL", None)
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["CACHE_SECONDS"] = "0"
os.environ["AUTO_REFRESH"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import db, ingest  # noqa: E402
from app.main import app  # noqa: E402

TODAY = dt.date.today()


def fake_fetch(origin, dest, month, client=None):
    """Stand-in for the Travelpayouts API: one cheap fare on the 20th of each month."""
    y, m = map(int, month.split("-"))
    return [{"depart": dt.date(y, m, 20), "price": 42.0, "carrier": "AK", "flight_number": "700",
             "transfers": 0, "link": "https://www.aviasales.com/search/x"}]


def test_end_to_end():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        assert len(c.get("/api/routes").json()) >= 5
        assert c.get("/api/status").json()["live"] is False

        depart = (TODAY + dt.timedelta(days=60)).isoformat()
        f = c.get(f"/api/forecast/KUL-SIN?depart={depart}").json()
        assert f["action"] in {"BUY", "WAIT"} and f["expected_low"] > 0
        assert isinstance(c.get("/api/deals").json(), list)
        assert c.get("/api/forecast/XXX-YYY?depart=" + depart).status_code == 404
        assert len(c.get("/api/history/KUL-SIN").json()) > 50


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
