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
