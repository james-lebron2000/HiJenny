"""API tests for HiJenny (app-dev owned).

Isolation: each test gets a fresh SQLite DB (``store.configure``) and a fresh
DATA_DIR (``config.DATA_DIR`` monkeypatched), so the shared ``data/hijenny.db``
is never touched.
"""
from __future__ import annotations

import io
import sys
import types

import pytest
from fastapi.testclient import TestClient

from hijenny import config, pipeline, store
from hijenny.api.main import app
import hijenny.api.main as api_main
from hijenny.schemas import (
    CoverageDraft,
    CoverageStatus,
    EvidenceCategory,
    EvidenceDraft,
    FieldName,
    FindingDraft,
    FindingType,
    Owner,
    Priority,
    QuestionDraft,
    Severity,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    store.configure(db_path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    return {"data_dir": data_dir, "db_path": db_path, "root": tmp_path}


@pytest.fixture()
def client(env):
    with TestClient(app) as test_client:
        yield test_client


def make_pdf_bytes(title: str = "Clinical Study Report", body: str = "ORR was 42.0% in the ITT population.") -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 90), title, fontsize=16)
    page.insert_text((72, 130), body, fontsize=11)
    page.insert_text((72, 160), "Data cutoff: 15 January 2026", fontsize=11)
    payload = document.tobytes()
    document.close()
    return payload


def create_project(client, name: str = "Test Asset DD") -> dict:
    response = client.post(
        "/api/projects",
        json={
            "name": name,
            "asset_name": "ABC-101",
            "indication": "NSCLC",
            "phase": "Phase 2",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["project"]


def upload_pdf(client, project_id: str, filename: str = "CSR.pdf", *, parse: bool = False, content: bytes | None = None):
    files = [("files", (filename, content or make_pdf_bytes(), "application/pdf"))]
    return client.post(
        f"/api/projects/{project_id}/documents?parse={'true' if parse else 'false'}",
        files=files,
    )


def seed_review_data(project_id: str) -> dict:
    """Seed evidence / findings / questions / coverage directly via the store."""
    with store.session_scope() as session:
        document = store.create_document(
            session,
            project_id=project_id,
            filename="CSR.pdf",
            sha256="a" * 64,
            stored_path=f"projects/{project_id}/documents/CSR.pdf",
            doc_type="CSR",
        )[0]
        store.add_pages(
            session,
            document["document_id"],
            [
                {
                    "page_no": 1,
                    "text": "ORR was 42.0% in the ITT population. Data cutoff 15 January 2026.",
                    "width": 612,
                    "height": 792,
                    "blocks": [
                        {"text": "ORR was 42.0% in the ITT population.", "bbox": [72, 120, 420, 140], "type": "paragraph"}
                    ],
                }
            ],
        )
        evidence, _ = store.save_evidence_batch(
            session,
            project_id,
            document,
            [
                EvidenceDraft(
                    field_name=FieldName.ORR,
                    category=EvidenceCategory.CLINICAL_EFFICACY,
                    value_num=42.0,
                    unit="%",
                    population="ITT",
                    arm="ABC-101",
                    trial_key="NCT01234567",
                    page=1,
                    quote="ORR was 42.0% in the ITT population.",
                )
            ],
        )
        evidence_id = evidence[0]["evidence_id"]
        finding_result = store.save_findings(
            session,
            project_id,
            [
                FindingDraft(
                    finding_type=FindingType.DATA_INCONSISTENCY,
                    domain="clinical",
                    title="ORR differs between CSR and commercial deck",
                    explanation="CSR reports 42.0% while the deck reports 48.2% for the same ITT population.",
                    severity=Severity.CRITICAL,
                    evidence_ids=[evidence_id],
                    possible_causes=["transcription error"],
                    missing_information=["reconciliation memo"],
                    dedup_key="finding.orr",
                )
            ],
        )
        finding_id = finding_result["findings"][0]["finding_id"]
        question_result = store.save_questions(
            session,
            project_id,
            [
                QuestionDraft(
                    question="Please reconcile the ORR of 42.0% in the CSR with the 48.2% shown in the commercial deck.",
                    rationale="Same population and data cutoff were used.",
                    category="Clinical",
                    priority=Priority.HIGH,
                    owner=Owner.CLINICAL,
                    required_documents=["CSR", "analysis output"],
                    evidence_ids=[evidence_id],
                    finding_ids=[finding_id],
                    dedup_key="question.orr",
                )
            ],
        )
        question_id = question_result["questions"][0]["question_id"]
        coverage = store.replace_coverage(
            session,
            project_id,
            [
                CoverageDraft(
                    rule_id="coverage.pp",
                    dimension="Population",
                    requirement="Per-protocol population analysis",
                    status=CoverageStatus.NOT_FOUND,
                    priority=Priority.HIGH,
                    note="No PP analysis in the package.",
                ),
                CoverageDraft(
                    rule_id="coverage.multiplicity",
                    dimension="Statistics",
                    requirement="Multiplicity control",
                    status=CoverageStatus.SUFFICIENT,
                    evidence_ids=[evidence_id],
                ),
            ],
        )
    return {
        "document_id": document["document_id"],
        "evidence_id": evidence_id,
        "finding_id": finding_id,
        "question_id": question_id,
        "coverage": coverage,
    }


# ---------------------------------------------------------------------------
# Health / UI
# ---------------------------------------------------------------------------


def test_health_and_llm_status(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["service"].startswith("HiJenny")
    assert "version" in body and "storage" not in body

    llm = client.get("/api/llm-status")
    assert llm.status_code == 200
    payload = llm.json()
    assert isinstance(payload["available"], bool)
    assert payload["mode"]
    assert "offline_default" in payload


def test_ui_is_served(client):
    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "HiJenny" in index.text
    assert "/static/app.js" in index.text

    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "renderEvidence" in script.text

    css = client.get("/static/styles.css")
    assert css.status_code == 200
    assert "ws-grid" in css.text


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def test_projects_crud_and_404(client):
    project = create_project(client)
    project_id = project["project_id"]
    assert project["status"] == "CREATED"
    assert project["org_id"] == "ORG_DEFAULT"

    listing = client.get("/api/projects")
    assert listing.status_code == 200
    assert listing.json()["count"] >= 1
    assert any(p["project_id"] == project_id for p in listing.json()["projects"])

    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["project"]["name"] == "Test Asset DD"
    assert "stats" in body and "outputs" in body
    assert body["stats"]["documents"] == 0

    patched = client.patch(f"/api/projects/{project_id}", json={"phase": "Phase 3", "actor": "alice"})
    assert patched.status_code == 200
    assert patched.json()["project"]["phase"] == "Phase 3"
    events = client.get(f"/api/projects/{project_id}/review-events").json()["events"]
    assert any(e["entity_type"] == "project" and e["actor"] == "alice" for e in events)

    assert client.get("/api/projects/prj_does_not_exist").status_code == 404
    assert client.get("/api/projects/prj_does_not_exist/documents").status_code == 404
    assert client.post("/api/projects", json={}).status_code == 422

    scoped = client.get(f"/api/projects/{project_id}", headers={"X-Org-Id": "OTHER_ORG"})
    assert scoped.status_code == 404


def test_gateway_mode_requires_org_context_for_private_resources(client, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_ORG_HEADER", True)
    headers = {"X-Org-Id": "PILOT"}
    project = client.post("/api/projects", headers=headers, json={"name": "QA"}).json()["project"]
    assert project["org_id"] == "PILOT"
    base = "/api/projects/" + project["project_id"]
    doc = seed_review_data(project["project_id"])
    for path in ("/api/projects", base, base + "/outputs", "/api/questions/" + doc["question_id"],
                 "/api/files/projects/" + project["project_id"] + "/documents/CSR.pdf"):
        assert client.get(path).status_code == 403
    assert client.post("/api/projects", json={"name": "QA", "org_id": "PILOT"}).status_code == 403
    assert client.get("/api/projects", params={"org_id": "PILOT"}).status_code == 403
    assert client.get(base, headers=headers).status_code == 200
    assert client.get("/api/public-events/count").status_code == 200


def test_gateway_reader_can_review_but_only_editor_can_change_projects(client, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_PROJECT_ROLE", True)
    monkeypatch.setattr(config, "REQUIRE_ORG_HEADER", True)
    org = {"X-Org-Id": "QA"}
    editor = dict(org, **{"X-Project-Role": "editor"})
    reader = dict(org, **{"X-Project-Role": "reader"})
    for headers in (org, reader, {"X-Project-Role": "editor"}, dict(org, **{"X-Project-Role": "unknown"})):
        assert client.post("/api/projects", headers=headers, json={"name": "QA"}).status_code == 403
    project = client.post("/api/projects", headers=editor, json={"name": "QA"}).json()["project"]["project_id"]
    base = "/api/projects/" + project
    assert client.get(base, headers=reader).status_code == 200
    assert client.patch(base, headers=reader, json={"name": "revised"}).status_code == 403
    assert client.get(base, headers=reader).json()["project"]["name"] == "QA"
    assert client.patch(base, headers=editor, json={"name": "revised"}).status_code == 200
    assert client.get(base, headers=org).status_code == 403
    assert client.get(base, headers=dict(reader, **{"X-Org-Id": "second"})).status_code == 404
    assert client.get("/api/public-events/count").status_code == 200


def test_asset_project_grants_apply_within_one_organization(client, monkeypatch):
    a, b = [create_project(client, name)["project_id"] for name in ("Asset A", "Asset B")]
    resources = [(pid, seed_review_data(pid)["question_id"], upload_pdf(client, pid).json()["documents"][0]["file_url"])
                 for pid in (a, b)]
    for flag in ("REQUIRE_PROJECT_GRANTS", "REQUIRE_PROJECT_ROLE", "REQUIRE_ORG_HEADER"):
        monkeypatch.setattr(config, flag, True)
    gateway = {"X-Org-Id": "ORG_DEFAULT", "X-Project-Role": "editor"}
    headers = {**gateway, "X-Project-Ids": a}
    assert client.get("/api/projects", headers=gateway).status_code == 403
    assert [p["project_id"] for p in client.get("/api/projects", headers=headers).json()["projects"]] == [a]
    assert client.get("/api/projects", headers=headers).json()["permissions"] == {"edit": True, "create": False}
    for role, can_edit in [("editor", True), ("reader", False)]:
        access = client.get("/api/projects", headers={**gateway, "X-Project-Ids": "*", "X-Project-Role": role})
        assert access.json()["permissions"] == {"edit": can_edit, "create": can_edit}
    for pid, question, url in resources:
        expected = 200 if pid == a else 404
        for path in (f"/api/projects/{pid}", f"/api/questions/{question}", url):
            assert client.get(path, headers=headers).status_code == expected
    assert client.post("/api/projects", json={"name": "Third"}, headers=headers).status_code == 403
    for grant in ("*", f"{a}, {b}"):
        assert client.get("/api/projects", headers={**gateway, "X-Project-Ids": grant}).json()["count"] == 2


def test_org_scope_follows_project_resources_and_downloads(client):
    project = create_project(client)["project_id"]
    rows = seed_review_data(project)
    assert client.patch(f"/api/questions/{rows['question_id']}", json={"status": "approved"}).is_success
    uploaded = upload_pdf(client, project, "appendix.pdf").json()["documents"][0]
    routes = [uploaded["file_url"], f"/api/projects/{project}/outputs",
              f"/api/projects/{project}/exports/seller-reply.xlsx",
              f"/api/documents/{rows['document_id']}", f"/api/evidence/{rows['evidence_id']}",
              f"/api/questions/{rows['question_id']}", f"/api/findings/{rows['finding_id']}"]
    for path in routes:
        for org, status in [("ORG_DEFAULT", 200), ("ORG_OTHER", 404)]:
            response = client.get(path, headers={"X-Org-Id": org})
            assert response.status_code == status, path
            assert response.headers["cache-control"] == "private, no-store", path
            assert "X-Org-Id" in response.headers["vary"], path
    assert client.get("/api/projects").headers["cache-control"] == "private, no-store"
    feed = client.get("/api/public-events/feed.atom")
    assert feed.headers["cache-control"] == "no-cache" and "etag" in feed.headers


# ---------------------------------------------------------------------------
# Upload / documents / pages / files
# ---------------------------------------------------------------------------


def test_upload_registers_documents_and_serves_files(client, env):
    project = create_project(client)
    project_id = project["project_id"]

    pdf_bytes = make_pdf_bytes()
    response = upload_pdf(client, project_id, "CSR.pdf", parse=False, content=pdf_bytes)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] == 1
    document = payload["documents"][0]
    assert document["filename"] == "CSR.pdf"
    assert document["doc_type"] == "CSR"
    assert document["stored_path"].startswith(f"projects/{project_id}/documents/")
    assert document["file_url"].startswith("/api/files/")
    saved = env["data_dir"] / document["stored_path"]
    assert saved.is_file()

    documents = client.get(f"/api/projects/{project_id}/documents").json()
    assert documents["count"] == 1
    assert documents["documents"][0]["evidence_count"] == 0

    # Duplicate sha256 upload -> dedupe, no error.
    duplicate = upload_pdf(client, project_id, "CSR-copy.pdf", parse=False, content=pdf_bytes)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicates"] == ["CSR-copy.pdf"]
    assert client.get(f"/api/projects/{project_id}/documents").json()["count"] == 1
    # Duplicate upload must not leave an orphaned copy on disk.
    doc_dir = env["data_dir"] / "projects" / project_id / "documents"
    assert [p.name for p in doc_dir.iterdir()] == ["CSR.pdf"]

    # File serving (project-scoped, path traversal blocked).
    served = client.get(document["file_url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert served.content == pdf_bytes

    assert client.get("/api/files/projects/../hijenny/store.py").status_code == 404
    assert client.get("/api/files/%2e%2e/%2e%2e/etc/passwd").status_code in (400, 403, 404)
    assert client.get("/api/files/AGENTS_CONTRACT.md").status_code == 404
    # Only project document/export artefacts are ever served (not the DB).
    (env["data_dir"] / "secret.txt").write_text("do not serve me", encoding="utf-8")
    assert client.get("/api/files/secret.txt").status_code == 404
    assert client.get("/api/files/hijenny.db").status_code == 404
    assert client.get(f"/api/files/projects/{project_id}/documents/../../secret.txt").status_code == 404

    # Document detail + pages.
    doc_detail = client.get(f"/api/projects/{project_id}/documents/{document['document_id']}").json()
    assert doc_detail["document"]["filename"] == "CSR.pdf"
    assert doc_detail["document"]["pages"] == []

    with store.session_scope() as session:
        store.add_pages(
            session,
            document["document_id"],
            [
                {
                    "page_no": 1,
                    "text": "ORR was 42.0% in the ITT population.",
                    "width": 612,
                    "height": 792,
                    "blocks": [{"text": "ORR was 42.0% in the ITT population.", "bbox": [72, 120, 420, 140], "type": "paragraph"}],
                },
                {"page_no": 2, "text": "Safety summary page.", "width": 612, "height": 792, "blocks": []},
            ],
        )
        store.update_document(session, document["document_id"], status="parsed", page_count=2)

    page = client.get(f"/api/projects/{project_id}/documents/{document['document_id']}/pages/1")
    assert page.status_code == 200
    page_body = page.json()
    assert page_body["page_no"] == 1
    assert "42.0%" in page_body["text"]
    assert page_body["blocks"][0]["bbox"] == [72, 120, 420, 140]
    assert page_body["total_pages"] == 2
    assert client.get(f"/api/projects/{project_id}/documents/{document['document_id']}/pages/99").status_code == 404

    # Global document/page aliases (document ids are globally unique).
    alias_doc = client.get(f"/api/documents/{document['document_id']}")
    assert alias_doc.status_code == 200
    assert alias_doc.json()["document"]["document_id"] == document["document_id"]
    alias_page = client.get(f"/api/documents/{document['document_id']}/pages/2")
    assert alias_page.status_code == 200
    assert alias_page.json()["page_no"] == 2
    assert client.get("/api/documents/doc_missing/pages/1").status_code == 404

    # Cross-project document scoping -> 404.
    other = create_project(client, name="Other project")
    assert client.get(
        f"/api/projects/{other['project_id']}/documents/{document['document_id']}/pages/1"
    ).status_code == 404
    assert client.get(
        f"/api/projects/{project_id}/documents/doc_missing/pages/1"
    ).status_code == 404


def test_upload_parses_with_ingestion_module(client, monkeypatch):
    project = create_project(client)
    project_id = project["project_id"]

    fake = types.ModuleType("hijenny.ingestion")
    calls: list[str] = []

    def ingest_file(session, project_id, document_id, force=False):
        calls.append(document_id)
        store.add_pages(
            session,
            document_id,
            [
                {
                    "page_no": 1,
                    "text": "Parsed by fake ingestion: ORR 42.0%.",
                    "width": 612,
                    "height": 792,
                    "blocks": [{"text": "Parsed by fake ingestion: ORR 42.0%.", "bbox": [72, 100, 430, 120], "type": "paragraph"}],
                }
            ],
        )
        store.update_document(session, document_id, status="parsed", page_count=1)
        return {"document_id": document_id, "pages": 1}

    fake.ingest_file = ingest_file
    monkeypatch.setitem(sys.modules, "hijenny.ingestion", fake)

    response = upload_pdf(client, project_id, "protocol.pdf", parse=True, content=make_pdf_bytes("Protocol", "Dose escalation study."))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ingestion"]["available"] is True
    assert payload["ingestion"]["attempted"] is True
    assert payload["ingestion"]["results"][0]["ok"] is True
    assert calls

    document_id = payload["documents"][0]["document_id"]
    assert client.get(f"/api/projects/{project_id}/documents/{document_id}").json()["document"]["page_count"] == 1
    page = client.get(f"/api/projects/{project_id}/documents/{document_id}/pages/1")
    assert page.status_code == 200
    assert "Parsed by fake ingestion" in page.json()["text"]


def test_upload_without_ingestion_returns_503_but_registers(client, monkeypatch):
    project = create_project(client)
    project_id = project["project_id"]
    monkeypatch.setattr(api_main, "_ingest_file_handler", lambda: None)

    response = upload_pdf(client, project_id, "IB.pdf", parse=True, content=make_pdf_bytes("Investigator Brochure"))
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["count"] == 1
    assert detail["documents"][0]["filename"] == "IB.pdf"
    assert "not available" in detail["ingestion"]["message"].lower()

    listed = client.get(f"/api/projects/{project_id}/documents").json()
    assert listed["count"] == 1
    assert listed["documents"][0]["status"] == "uploaded"


# ---------------------------------------------------------------------------
# Runs / analyze
# ---------------------------------------------------------------------------


def test_analyze_and_run_progress(client, monkeypatch):
    project = create_project(client)
    project_id = project["project_id"]
    created: dict[str, str] = {}

    def fake_start(project_id_arg, force=False):
        with store.session_scope() as session:
            run = store.create_run(session, project_id_arg, ["INGEST", "EXTRACT"])
            store.update_run(session, run["run_id"], state="EXTRACTING", stats={"stage_ingest": {"state": "done"}})
        created["run_id"] = run["run_id"]
        return run["run_id"]

    monkeypatch.setattr(pipeline, "start_pipeline", fake_start)

    response = client.post(f"/api/projects/{project_id}/analyze", json={"force": False})
    assert response.status_code == 200, response.text
    payload = response.json()
    run_id = payload["run_id"]
    assert run_id == created["run_id"]
    assert payload["progress"]["stages_total"] >= 7
    assert payload["progress"]["stages_done"] == 1

    run = client.get(f"/api/runs/{run_id}")
    assert run.status_code == 200
    run_body = run.json()
    assert run_body["run"]["state"] == "EXTRACTING"
    assert run_body["progress"]["percent"] >= 0

    again = client.post(f"/api/projects/{project_id}/analyze", json={})
    assert again.status_code == 200
    assert again.json()["already_running"] is True

    runs = client.get(f"/api/projects/{project_id}/runs").json()
    assert runs["count"] >= 1
    assert client.get("/api/runs/run_missing").status_code == 404
    assert client.post("/api/projects/prj_missing/analyze", json={}).status_code == 404


# ---------------------------------------------------------------------------
# Evidence / findings / questions / coverage / review
# ---------------------------------------------------------------------------


def test_evidence_findings_questions_and_review_events(client):
    project = create_project(client)
    project_id = project["project_id"]
    seeded = seed_review_data(project_id)

    # Evidence
    evidence_list = client.get(f"/api/projects/{project_id}/evidence").json()
    assert evidence_list["count"] == 1
    item = evidence_list["evidence"][0]
    assert item["field_name"] == "orr"
    assert item["value_num"] == pytest.approx(42.0)
    assert item["quote"] == "ORR was 42.0% in the ITT population."
    assert evidence_list["facets"]["field_name"]["orr"] == 1

    filtered = client.get(f"/api/projects/{project_id}/evidence?field_name=orr&verification_status=pending").json()
    assert filtered["count"] == 1
    assert client.get(f"/api/projects/{project_id}/evidence?verification_status=verified").json()["count"] == 0
    assert client.get(f"/api/projects/{project_id}/evidence?search=42.0").json()["count"] == 1

    detail = client.get(f"/api/evidence/{seeded['evidence_id']}").json()
    assert detail["evidence"]["evidence_id"] == seeded["evidence_id"]
    assert detail["findings"][0]["finding_id"] == seeded["finding_id"]
    assert detail["questions"][0]["question_id"] == seeded["question_id"]
    assert client.get("/api/evidence/ev_missing").status_code == 404

    verified = client.patch(
        f"/api/evidence/{seeded['evidence_id']}",
        json={"verification_status": "verified", "note": "checked against page 1", "actor": "alice"},
    )
    assert verified.status_code == 200
    assert verified.json()["evidence"]["verification_status"] == "verified"

    # Findings
    findings = client.get(f"/api/projects/{project_id}/findings").json()
    assert findings["count"] == 1
    assert findings["findings"][0]["severity"] == "critical"
    assert client.get(f"/api/projects/{project_id}/findings?severity=high").json()["count"] == 0
    finding_detail = client.get(f"/api/findings/{seeded['finding_id']}").json()
    assert finding_detail["finding"]["title"].startswith("ORR differs")
    assert finding_detail["evidence"][0]["evidence_id"] == seeded["evidence_id"]

    patched = client.patch(
        f"/api/findings/{seeded['finding_id']}",
        json={"status": "confirmed", "severity": "high", "actor": "bob"},
    )
    assert patched.status_code == 200
    assert patched.json()["finding"]["status"] == "confirmed"
    assert patched.json()["finding"]["severity"] == "high"
    assert client.patch(f"/api/findings/{seeded['finding_id']}", json={"status": "nonsense"}).status_code == 422
    assert client.patch("/api/findings/fnd_missing", json={"status": "open"}).status_code == 404

    # Questions
    questions = client.get(f"/api/projects/{project_id}/questions").json()
    assert questions["count"] == 1
    assert questions["questions"][0]["priority"] == "high"
    q_patch = client.patch(
        f"/api/questions/{seeded['question_id']}",
        json={"priority": "critical", "owner": "Biostatistics", "review_status": "approved", "actor": "carol"},
    )
    assert q_patch.status_code == 200
    assert q_patch.json()["question"]["priority"] == "critical"
    assert q_patch.json()["question"]["owner"] == "Biostatistics"
    assert q_patch.json()["question"]["review_status"] == "approved"
    assert client.patch(
        f"/api/questions/{seeded['question_id']}", json={"owner": "Not A Real Owner"}
    ).status_code == 422
    assert client.patch("/api/questions/qst_missing", json={"priority": "high"}).status_code == 404
    assert client.get(f"/api/questions/{seeded['question_id']}").status_code == 200

    # Coverage
    coverage = client.get(f"/api/projects/{project_id}/coverage").json()
    assert coverage["count"] == 2
    assert coverage["facets"]["status"]["not_found"] == 1
    assert client.get(f"/api/projects/{project_id}/coverage?status=not_found").json()["count"] == 1

    # Stats
    stats = client.get(f"/api/projects/{project_id}/stats").json()
    assert stats["stats"]["evidence"] == 1
    assert stats["stats"]["findings"] == 1
    assert stats["stats"]["questions"] == 1

    # Review events (finding + evidence + question updates were logged)
    events = client.get(f"/api/projects/{project_id}/review-events").json()["events"]
    actors = {event["actor"] for event in events}
    assert {"alice", "bob", "carol"} <= actors
    assert any(event["entity_type"] == "question" for event in events)

    # Delete question logs a review event and removes it.
    deleted = client.delete(f"/api/questions/{seeded['question_id']}?actor=dave")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert client.get(f"/api/projects/{project_id}/questions").json()["count"] == 0
    assert client.delete(f"/api/questions/{seeded['question_id']}").status_code == 404
    events = client.get(f"/api/projects/{project_id}/review-events").json()["events"]
    assert any(event["action"] == "delete" and event["actor"] == "dave" for event in events)


# ---------------------------------------------------------------------------
# Exports through the API
# ---------------------------------------------------------------------------


def test_export_endpoints_produce_valid_files(client, env):
    import openpyxl
    from docx import Document

    project = create_project(client)
    project_id = project["project_id"]
    seed_review_data(project_id)

    xlsx_response = client.post(f"/api/projects/{project_id}/exports/questions")
    assert xlsx_response.status_code == 200, xlsx_response.text
    xlsx_payload = xlsx_response.json()
    assert xlsx_payload["download_url"].startswith("/api/files/projects/")
    workbook_bytes = client.get(xlsx_payload["download_url"])
    assert workbook_bytes.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(workbook_bytes.content))
    assert workbook.sheetnames == [
        "Executive Summary",
        "Asset Profile",
        "Asset Evaluation",
        "Discovery",
        "Evidence Matrix",
        "Findings",
        "Questions",
        "Coverage",
    ]
    assert workbook["Findings"].max_row >= 2

    report_response = client.post(f"/api/projects/{project_id}/exports/report")
    assert report_response.status_code == 200, report_response.text
    report_payload = report_response.json()
    stats = report_payload["stats"]
    for key in ("report_path", "report_md", "report_docx", "questions_xlsx"):
        assert stats[key]
    for path_key in ("report_md", "report_docx", "questions_xlsx"):
        assert (env["data_dir"] / "projects" / project_id / "exports") in [p.parent for p in [__import__("pathlib").Path(stats[path_key])]]
        assert __import__("pathlib").Path(stats[path_key]).is_file()

    md_download = client.get(report_payload["downloads"]["report_md"]["url"])
    assert md_download.status_code == 200
    assert "Asset Due Diligence Report" in md_download.text
    assert "requires expert human review" in md_download.text.lower()

    docx_download = client.get(report_payload["downloads"]["report_docx"]["url"])
    assert docx_download.status_code == 200
    docx = Document(io.BytesIO(docx_download.content))
    text = "\n".join(p.text for p in docx.paragraphs)
    assert "Key Findings" in text
    assert "ORR differs" in text

    outputs = client.get(f"/api/projects/{project_id}/outputs").json()["outputs"]
    assert outputs["report_md"]["exists"] is True
    assert outputs["report_docx"]["exists"] is True
    assert outputs["questions_xlsx"]["exists"] is True

    assert client.post("/api/projects/prj_missing/exports/questions").status_code == 404
    assert client.post("/api/projects/prj_missing/exports/report").status_code == 404


def test_bd_review_history_and_brief(client, env):
    project = create_project(client)["project_id"]
    other = create_project(client, "Other")["project_id"]
    seeded = seed_review_data(project)
    path = f"/api/projects/{project}/followup/{seeded['question_id']}"
    doc = seeded["document_id"]

    def record(kind, revision, **fields):
        return client.post(path, json=dict(actor="test reviewer", kind=kind, revision=revision, **fields))

    assert record("publish", 0, note="premature").status_code == 409
    assert record("request", 0, note="Provide table").is_success
    assert record("request", 0, note="Duplicate").status_code == 409
    foreign_doc = upload_pdf(client, other).json()["documents"][0]["document_id"]
    assert record("reply", 1, note="wrong", locator="p1", document_id=foreign_doc).status_code == 422
    source = dict(locator="Table 1", document_id=doc)
    assert record("reply", 1, note="table received", **source).is_success
    checkpoint = client.get(path + "/valuation-context").json()
    assert checkpoint["status"] == "answered" and checkpoint["revision"] == 2
    reply, = checkpoint["replies"]
    assert reply["document_id"] == doc and reply["locator"] == "Table 1" and reply["note"] == "table received"
    assert client.get(path.replace(project, other) + "/valuation-context").status_code == 404
    assert record("review", 2, decision="experiment", note="independent test required").is_success
    assert record("publish", 3, note="validation pending").is_success
    assert record("experiment_result", 4, note="result file reviewed", **source).is_success
    assert record("review", 5, decision="documents", note="no further experiments").is_success
    assert record("publish", 6, note="updated interpretation").is_success
    comparison = dict.fromkeys(("asset", "population", "line", "method", "followup",
                                "endpoint", "value", "limits"), "comparator datum")
    assert record("comparison", 7, **comparison, **source).is_success
    assert record("review", 8, decision="documents", note="comparator reviewed").is_success
    assert record("publish", 9, note="updated interpretation with comparator").is_success
    from docx import Document
    out = env["data_dir"] / "projects" / project / "exports"
    assert "updated interpretation" in (out / "HiJenny_Report.md").read_text()
    assert any("updated interpretation" in c.text for t in Document(out / "HiJenny_Report.docx").tables
               for row in t.rows for c in row.cells)
    assert client.get(f"/api/projects/{other}/followup").json()["history"] == {}


def test_seller_reply_export_is_project_scoped_and_limited_to_approved_requests(client):
    import openpyxl

    project = create_project(client)["project_id"]
    question = seed_review_data(project)["question_id"]
    route = f"/api/projects/{project}/exports/seller-reply.xlsx"
    assert client.get(route).status_code == 409
    client.patch(f"/api/questions/{question}", json={"status": "approved", "actor": "reviewer"}).raise_for_status()
    response = client.get(route)
    assert response.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    assert workbook.sheetnames == ["卖方答复"]
    header, row = list(workbook.active.values)
    assert header == ("问题编号", "待澄清问题", "所需材料", "卖方答复", "答复附件及定位", "答复日期")
    assert row[0] == question and row[3:] == (None, None, None)
    client.patch(f"/api/questions/{question}", json={"review_status": "rejected", "actor": "reviewer"}).raise_for_status()
    assert client.get(route).status_code == 409


@pytest.mark.skipif(pipeline.fcntl is None, reason="POSIX run ownership locks required")
def test_restart_recovers_abrupt_worker_exit_but_preserves_live_run(env):
    import os
    import subprocess

    store.init_db()
    with store.session_scope() as session:
        interrupted = store.create_project(session, name="interrupted")["project_id"]
        active = store.create_project(session, name="live")["project_id"]
    script = """
import os, sys
from hijenny import pipeline
def interrupt(stage):
    if stage == 'EXTRACT':
        os._exit(17)
    return lambda *a, **kw: {}
pipeline._load_handler = interrupt
pipeline.run_pipeline(sys.argv[1])
"""
    result = subprocess.run([sys.executable, "-c", script, interrupted], timeout=30,
                            env={**os.environ, "HIJENNY_DATA_DIR": str(env["data_dir"]),
                                 "HIJENNY_DB_PATH": str(env["db_path"])})
    assert result.returncode == 17
    with pipeline._owned_run(active) as live, TestClient(app) as client:
        run = client.get(f"/api/projects/{interrupted}/runs").json()["runs"][0]
        assert run["run"]["status"] == "failed" and run["run"]["finished_at"]
        assert run["progress"]["stages_done"] == 1
        assert client.get(f"/api/runs/{live['run_id']}").json()["run"]["status"] == "running"
        retry = client.post(f"/api/projects/{interrupted}/analyze").json()
        assert not retry["already_running"] and retry["run_id"] != run["run"]["run_id"]
        final = pipeline.wait_for_run(retry["run_id"], timeout=30)
        assert final["status"] == "succeeded", final["error"]


def test_simultaneous_analysis_requests_share_one_worker(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event

    project_id = create_project(client)["project_id"]
    callers = 6
    at_start = Barrier(callers)
    release = Event()
    original_start = pipeline.start_pipeline
    launched = []

    def simultaneous_start(project_id, force=False):
        at_start.wait(timeout=10)
        return original_start(project_id, force=force)

    def worker(run_id, project_id, force=False):
        launched.append(run_id)
        release.wait(timeout=20)
        with store.session_scope() as session:
            store.update_run(session, run_id, status="succeeded", finished=True)

    monkeypatch.setattr(pipeline, "start_pipeline", simultaneous_start)
    monkeypatch.setattr(pipeline, "_execute", worker)
    try:
        with ThreadPoolExecutor(max_workers=callers) as pool:
            futures = [pool.submit(client.post, f"/api/projects/{project_id}/analyze",
                                   json={"force": bool(i % 2)}) for i in range(callers)]
            replies = [future.result().json() for future in futures]
        assert sum(not reply["already_running"] for reply in replies) == 1
        assert {reply["run_id"] for reply in replies} == {launched[0]}
        assert len(launched) == 1
        assert client.get(f"/api/projects/{project_id}/runs").json()["count"] == 1
    finally:
        release.set()
        for run_id in launched:
            pipeline.wait_for_run(run_id, timeout=10)


def test_pdf_review_pages_reach_document_list_and_reports(client, monkeypatch):
    import fitz
    from docx import Document

    monkeypatch.setattr(config, "MAX_PAGES_PER_DOC", 3)
    monkeypatch.setattr(config, "PDF_WORKERS", 0)
    project_id = create_project(client)["project_id"]
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 40), "Synthetic source document for review.")
        pdf.new_page().draw_rect((40, 40, 60, 60), fill=(0, 0, 0))
        for _ in range(3):
            pdf.new_page()
        response = upload_pdf(client, project_id, "source.pdf", content=pdf.tobytes(), parse=True)
    assert response.is_success, response.text
    document = client.get(f"/api/projects/{project_id}/documents").json()["documents"][0]
    assert document["pdf_review_ranges"] == {"scan": [[2, 2]], "empty": [[3, 3]], "unparsed": [[4, 5]]}
    run = pipeline.run_pipeline(project_id)
    assert run["status"] == "succeeded", run["error"]
    outputs = client.get(f"/api/projects/{project_id}").json()["outputs"]
    md = client.get(outputs["report_md"]["url"]).text
    word = Document(io.BytesIO(client.get(outputs["report_docx"]["url"]).content))
    paragraphs = "\n".join(p.text for p in word.paragraphs)
    for text in ("possible scan or image-only · pp. 2", "no extractable content · pp. 3",
                 "beyond parsing limit · pp. 4–5"):
        assert text in md and text in paragraphs


def test_gateway_review_identity_covers_review_and_followup_writes(client, monkeypatch):
    project = create_project(client)
    pid = project["project_id"]
    rows = seed_review_data(pid)
    base = f"/api/projects/{pid}"
    question = "/api/questions/" + rows["question_id"]
    evidence = "/api/evidence/" + rows["evidence_id"]
    followup = base + "/followup/" + rows["question_id"]
    editor = {"X-Org-Id": project["org_id"], "X-Project-Role": "editor"}
    user = {**editor, "X-User-Id": "pilot-reviewer.42"}
    for flag in ("REQUIRE_REVIEW_ACTOR", "REQUIRE_PROJECT_ROLE", "REQUIRE_ORG_HEADER"):
        monkeypatch.setattr(config, flag, True)

    for headers, expected in ((editor, None), (user, user["X-User-Id"])):
        context = client.get(base + "/followup", headers=headers).json()
        assert context["gateway_identity_required"] and context["gateway_user"] == expected

    writes = [
        ("PATCH", base, {"name": "Identity audit"}),
        ("PATCH", evidence, {"verification_status": "verified"}),
        ("PATCH", "/api/findings/" + rows["finding_id"], {"status": "confirmed"}),
        ("PATCH", question, {"priority": "low"}),
        ("POST", followup, {"kind": "request", "revision": 0, "note": "Please provide table"}),
        ("DELETE", question + "?actor=local-name", None),
    ]
    for method, path, fields in writes:
        body = {"json": {**fields, "actor": "local-name"}} if fields else {}
        for invalid in (editor, {**editor, "X-User-Id": "a,b"}):
            assert client.request(method, path, headers=invalid, **body).status_code == 403
        client.request(method, path, headers=user, **body).raise_for_status()

    assert client.get(evidence, headers=editor).json()["evidence"]["verified_by"] == user["X-User-Id"]
    events = client.get(base + "/review-events", headers=editor).json()["events"]
    assert len(events) >= len(writes)
    assert {event["actor"] for event in events} == {user["X-User-Id"]}


def test_concurrent_evidence_review_preserves_first_result(client):
    from concurrent.futures import ThreadPoolExecutor

    pid = create_project(client)["project_id"]
    path = "/api/evidence/" + seed_review_data(pid)["evidence_id"]
    initial = client.get(path).json()["evidence"]["review_token"]

    def save(actor, token):
        return client.patch(path, json={"verification_status": "verified", "note": actor,
                                        "actor": actor, "expected_review_token": token})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda actor: save(actor, initial), ["one", "two"]))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r for r in results if r.is_success).json()["evidence"]
    conflict = next(r for r in results if r.status_code == 409).json()["detail"]["evidence"]
    assert winner == conflict == client.get(path).json()["evidence"]
    events = f"/api/projects/{pid}/review-events"
    assert client.get(events).json()["count"] == 1
    assert save("reconciled", winner["review_token"]).is_success
    assert client.get(events).json()["count"] == 2


def test_download_review_state_tracks_each_generated_file(client, env):
    pid = create_project(client)["project_id"]
    rows = seed_review_data(pid)
    base = f"/api/projects/{pid}"

    def states():
        return {k: v["evidence_review_state"] for k, v in client.get(base + "/outputs").json()["outputs"].items()}

    client.post(base + "/exports/report").raise_for_status()
    assert set(states().values()) == {"current"}
    client.patch("/api/evidence/" + rows["evidence_id"],
                 json={"verification_status": "failed", "note": "Page mismatch"}).raise_for_status()
    assert set(states().values()) == {"changed"}
    client.post(base + "/exports/questions").raise_for_status()
    assert states() == {"report_md": "changed", "report_docx": "changed", "questions_xlsx": "current"}
    md = env["data_dir"] / "projects" / pid / "exports" / "HiJenny_Report.md"
    md.write_text("# Earlier report", encoding="utf-8")
    assert states()["report_md"] == "unknown"
    client.post(base + "/exports/report").raise_for_status()
    assert set(states().values()) == {"current"}


def test_report_review_state_tracks_project_findings_and_seller_questions(client, env):
    pid = create_project(client)["project_id"]
    rows = seed_review_data(pid)
    base = f"/api/projects/{pid}"

    def outputs():
        return client.get(base + "/outputs").json()["outputs"]

    for path, change in ((base, {"indication": "Updated indication"}),
                         ("/api/findings/" + rows["finding_id"], {"severity": "high"}),
                         ("/api/questions/" + rows["question_id"], {"priority": "low"})):
        client.post(base + "/exports/report").raise_for_status()
        assert all(f["diligence_review_state"] == "current" for f in outputs().values())
        client.patch(path, json=change).raise_for_status()
        assert all((f["diligence_review_state"], f["evidence_review_state"]) == ("changed", "current")
                   for f in outputs().values())
    client.post(base + "/exports/questions").raise_for_status()
    assert outputs()["questions_xlsx"]["diligence_review_state"] == "current"
    assert outputs()["report_docx"]["diligence_review_state"] == "changed"
    md = env["data_dir"] / "projects" / pid / "exports" / "HiJenny_Report.md"
    marker, tail = md.read_text().split("\n", 1)
    md.write_text(marker.split(":")[0] + " -->\n" + tail)
    legacy = outputs()["report_md"]
    assert legacy["diligence_review_state"] == "unknown" and legacy["evidence_review_state"] == "current"


def test_brief_seller_queue_matches_followup_and_keeps_historical_questions(client):
    base = "/api/projects/" + create_project(client)["project_id"]
    rows = seed_review_data(base.rsplit("/", 1)[1])
    qid = rows["question_id"]
    followup = base + "/followup"

    def check(stage, next_step=None):
        assert client.get(followup).json()["stages"][qid] == stage
        exports = client.post(base + "/exports/report").json()["downloads"]
        md = client.get(exports["report_md"]["url"]).text
        queue = md.split("### Top seller questions")[1].split("### Evidence still missing")[0]
        assert qid in md.split("## 7. Seller Questions")[1]
        assert (qid in queue) == bool(next_step)
        if next_step:
            assert next_step in queue

    def record(kind, revision, **fields):
        client.post(followup + "/" + qid, json=dict(kind=kind, revision=revision, actor="pilot", **fields)).raise_for_status()

    check("request", "Review before sending")
    question = "/api/questions/" + qid
    client.patch(question, json={"status": "approved"}).raise_for_status()
    check("request", "Ready to send")
    record("request", 0, note="Source requested")
    check("reply", "Awaiting seller response")
    record("reply", 1, note="Received", locator="Table 1", document_id=rows["document_id"])
    check("review")
    record("review", 2, note="Clarify population", decision="clarify")
    check("clarify", "Request clarification")
    client.patch(question, json={"status": "closed"}).raise_for_status()
    check("closed")
    client.patch(question, json={"review_status": "rejected"}).raise_for_status()
    check("rejected")


def test_terminal_question_requires_explicit_reopen_with_reason(client):
    project = create_project(client)["project_id"]
    qid = seed_review_data(project)["question_id"]
    base = f"/api/projects/{project}/followup"
    route = base + "/" + qid
    question = "/api/questions/" + qid

    def record(kind, revision, note):
        return client.post(route, json=dict(kind=kind, actor="pilot", revision=revision, note=note))

    record("request", 0, "First request").raise_for_status()
    for i, state in enumerate(({"status": "closed"}, {"status": "dismissed"}, {"review_status": "rejected"})):
        rev = 1 + 2 * i
        client.patch(question, json=state).raise_for_status()
        assert client.get(base).json()["allowed"][qid] == ["reopen"]
        assert record("request", rev, "Old form").status_code == 409
        assert record("reopen", rev, " ").status_code == 422
        record("reopen", rev, "Additional source received").raise_for_status()
        assert record("reopen", rev, "Duplicate").status_code == 409
        ledger = client.get(base).json()
        assert ledger["stages"][qid] == "request" and "publish" not in ledger["allowed"][qid]
        assert ledger["history"][qid][-1]["after"]["note"] == "Additional source received"
        assert ledger["questions"][0]["review_status"] == "pending"
        record("request", rev + 1, "Next request").raise_for_status()
