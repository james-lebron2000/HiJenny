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


def test_sample_source_downloads_match_files_used_to_render_previews():
    from hashlib import sha256

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    # Updating these pinned synthetic sources also requires refreshing the matching page images.
    for source, digest in [
        ("csr", "0aed7bb9d6c29a447a4ff3fc2c2696fd9d54165160ca1f33c987d399e711e255"),
        ("forecast", "5a5783823bd3fc7ed7c7a33172a5ee29c7d2142a35c5cf11bc68ed05e7bdd77d"),
    ]:
        response = client.get(f"/api/pilot/sources/{source}")
        assert response.status_code == 200 and sha256(response.content).hexdigest() == digest
    assert client.get("/api/pilot/sources/projects").status_code == 422


def test_metrics_distinguish_sessions_receipts_and_recorded_sales_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "pilot.db")
    monkeypatch.setattr(store, "_ENGINE", None)
    monkeypatch.setattr(store, "_SESSION_FACTORY", None)
    store.init_db()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    anon = {"session_id": str(uuid4()), "sample_version": "asd101-v1"}
    request = dict(request_key=str(uuid4()), email="sales@example.invalid", stage="II 期", pages="待确认",
                   meeting="2027-05-01", deployment="待讨论", analytics=anon)
    for _ in range(2):
        for event in ("pilot_open", "sample_open", "source_open"):
            assert client.post("/api/pilot/events", json={**anon, "event": event}).status_code == 204
        assert client.get("/api/pilot/sample-report", params=anon).status_code == 200
        receipt = client.post("/api/pilot/inquiries", json=request).json()["receipt"]
    assert client.post("/api/pilot/events", json={**anon, "event": "inquiry_accepted"}).status_code == 422
    assert client.post("/api/pilot/events", json={**anon, "event": "sample_open", "email": "x"}).status_code == 422
    with store.session_scope() as session:
        for ref in ("quote1", "quote2"):
            store.record_pilot_progress(session, receipt, "已报价", ref)
        session.flush()
        metrics = store.pilot_metrics(session)
        sessions, receipts, sales, _ = metrics.values()
        assert sessions == {"asd101-v1": dict.fromkeys(
            ["pilot_open", "sample_open", "source_open", "report_requested", "inquiry_accepted"], 1)}
        assert receipts == 1 and sales == {"已报价": 1}
        from datetime import date
        assert store.pilot_metrics(session, date(2000, 1, 1))["受理编号数"] == 1
        future = store.pilot_metrics(session, date(9999, 1, 1))
        assert list(future.values())[:3] == [{}, 0, {}]
