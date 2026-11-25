import datetime as dt
import os
import tempfile

os.environ["SKYWARD_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_end_to_end():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        assert len(c.get("/api/routes").json()) >= 5
        depart = (dt.date.today() + dt.timedelta(days=60)).isoformat()
        f = c.get(f"/api/forecast/KUL-SIN?depart={depart}").json()
        assert f["action"] in {"BUY", "WAIT"} and f["expected_low"] > 0
        assert isinstance(c.get("/api/deals").json(), list)
        w = c.post("/api/watches", json={"route": "KUL-SIN", "depart": depart, "target_price": 9999}).json()
        fired = c.post("/api/watches/check").json()
        assert any(x["watch_id"] == w["id"] for x in fired)
        assert c.get("/api/forecast/XXX-YYY?depart=" + depart).status_code == 404
