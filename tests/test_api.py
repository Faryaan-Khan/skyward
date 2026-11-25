import datetime as dt
import os
import tempfile

os.environ["SKYWARD_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("DATABASE_URL", None)
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["CACHE_SECONDS"] = "0"

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


def test_db_url_normalisation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host/db?sslmode=require")
    assert db._url() == "postgresql+psycopg://u:p@host/db?sslmode=require"
