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
