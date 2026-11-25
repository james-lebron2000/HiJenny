from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hijenny import config, store
from hijenny.api.pilot import router


def test_pilot_receipt_survives_simultaneous_retries_and_only_cli_can_list(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "pilot.db")
    monkeypatch.setattr(store, "_ENGINE", None)
    monkeypatch.setattr(store, "_SESSION_FACTORY", None)
    store.init_db()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    request = dict(request_key=str(uuid4()), email="example@example.org", stage="II 期", pages="51–200 页",
                   meeting="2027-01-15", deployment="待讨论")
    url = "/api/pilot/inquiries"
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: client.post(url, json=request), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json()
    assert set(responses[0].json()) == {"receipt", "created_at"}
    assert client.get(url).status_code == 405
    assert client.post(url, json={**request, "pages": "待确认"}).status_code == 409
    with store.session_scope() as session:
        store.record_pilot_progress(session, responses[0].json()["receipt"], "已报价", "QUOTE-example")
        session.flush()
        rows = store.list_pilot_inquiries(session, responses[0].json()["receipt"])
        assert len(rows) == 1 and rows[0]["email"] == request["email"]
        assert rows[0]["progress"][0]["stage"] == "已报价"
    assert client.get("/api/pilot/sample-report").content.startswith(b"# ASD-101")
