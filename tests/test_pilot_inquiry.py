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


def test_sales_queue_covers_unassigned_reassignment_and_stages(tmp_path, monkeypatch):
    from datetime import date
    from hijenny import pilot_sales

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "sales.db")
    monkeypatch.setattr(store, "_ENGINE", None)
    monkeypatch.setattr(store, "_SESSION_FACTORY", None)
    with store.session_scope() as session:
        fields = dict(email="sales@example.invalid", stage="II 期", pages="待确认", meeting="2027-05-01", deployment="待讨论")
        receipt = store.submit_pilot_inquiry(session, str(uuid4()), fields)["receipt"]
        assert pilot_sales.queue(session, waiting_quote=True)[0]["owner"] is None
        due = date(2027, 2, 1)
        pilot_sales.assign(session, receipt, "甲", due, "确认范围")
        pilot_sales.assign(session, receipt, "乙", due, "准备报价")
        assert not pilot_sales.queue(session, owner="甲")
        assert pilot_sales.queue(session, owner="乙", due_by=due)[0]["next_action"] == "准备报价"
        store.record_pilot_progress(session, receipt, "已报价", "SYNTHETIC")
        assert not pilot_sales.queue(session, waiting_quote=True)
        store.record_pilot_progress(session, receipt, "已关闭", "SYNTHETIC")
        assert not pilot_sales.queue(session)
        assert pilot_sales.queue(session, include_finished=True)[0]["sales_stage"] == "已关闭"


def test_quote_uses_intake_and_explicit_terms_without_recording_sale(tmp_path, monkeypatch):
    from hijenny.cli import main
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "quote.db")
    monkeypatch.setattr(store, "_ENGINE", None)
    monkeypatch.setattr(store, "_SESSION_FACTORY", None)
    with store.session_scope() as s:
        receipt = store.submit_pilot_inquiry(s, str(uuid4()), dict(email="pilot@example.invalid", stage="II 期",
            pages="待确认", meeting="2099-01-01", deployment="待讨论"))["receipt"]
    path = tmp_path / "quote.html"
    args = ["pilot-quote", receipt, "--currency", "CNY", "--amount", "1234.50", "--scope", "<交付样例>",
            "--delivery", "资料齐备后商定", "--tax", "含税", "--valid-until", "2099-01-01", "--out", str(path)]
    assert main(args) == 0
    text = path.read_text()
    assert all(v in text for v in ("CNY 1,234.50", "&lt;交付样例&gt;", receipt, "pilot@example.invalid", "含税"))
    assert main(args) == 2 and path.read_text() == text
    with store.session_scope() as s:
        assert store.list_pilot_inquiries(s, receipt)[0]["progress"] == []


def test_delivery_versions_preserve_acceptance_and_retry_safely(tmp_path, monkeypatch):
    import pytest
    from hijenny import pilot_sales
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "delivery.db")
    monkeypatch.setattr(store, "_ENGINE", None)
    monkeypatch.setattr(store, "_SESSION_FACTORY", None)
    with store.session_scope() as s:
        receipt = store.submit_pilot_inquiry(s, str(uuid4()), dict(email="example@example.invalid", stage="II 期",
            pages="待确认", meeting="2099-01-01", deployment="待讨论"))["receipt"]
    file = tmp_path / "brief.txt"
    for version, decision in [("v1", "需返工"), ("v2", "已验收")]:
        file.write_text(version)
        for _ in range(2):
            with store.session_scope() as s:
                pilot_sales.delivery_record(s, receipt, version, file, "SYNTHETIC")
            with store.session_scope() as s:
                pilot_sales.delivery_feedback(s, receipt, version, decision, "反馈", "SYNTHETIC")
    with pytest.raises(ValueError), store.session_scope() as s:
        pilot_sales.delivery_record(s, receipt, "v1", file, "SYNTHETIC")
    with pytest.raises(ValueError), store.session_scope() as s:
        pilot_sales.delivery_feedback(s, receipt, "v1", "已验收", "改写", "SYNTHETIC")
    with store.session_scope() as s:
        rows = pilot_sales.deliveries(s, receipt)
        assert [r["outcome"] for r in rows] == ["需返工", "已验收"]
        assert rows[0]["digest"] != rows[1]["digest"]
        assert not store.list_pilot_inquiries(s, receipt)[0]["progress"]
