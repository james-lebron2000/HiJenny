"""Asset 360 overview (discovery + evaluation) tests — Lead-owned."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hijenny import config, exports, store
from hijenny.overview import build_overview
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


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    store.configure(tmp_path / "overview.db")
    store.init_db()
    with store.session_scope() as session:
        proj = store.create_project(
            session, name="Overview test", asset_name="ASD-101",
            indication="MTAP-deleted advanced solid tumors", phase="Phase 2",
        )
        pid = proj["project_id"]
        store.upsert_asset(
            session, pid, "ASD-101",
            modality="oral small molecule", target="PRMT5",
            mechanism="selective PRMT5 inhibitor",
            indication="MTAP-deleted advanced solid tumors", phase="Phase 2",
        )
        store.upsert_trial(
            session, pid, nct_id="NCT05712345", internal_id="ASD-101-201",
            phase="Phase 2", indication="MTAP-deleted advanced solid tumors",
            design="open-label, single-arm", primary_endpoint="ORR per RECIST 1.1",
            sample_size=126, data_cutoff="2026-01-15",
        )
        doc, _ = store.create_document(
            session, project_id=pid, filename="Clinical_Study_Report_v1.0.pdf",
            sha256="a" * 64, doc_type="CSR", page_count=2,
        )
        store.add_pages(
            session,
            doc["document_id"],
            [
                {"page_no": 1, "text": "ORR was 42.0% in the ITT population. SAE rate was 19.8%.", "width": 595, "height": 842},
                {"page_no": 2, "text": "Data cutoff: 2026-01-15. Median PFS was 7.1 months.", "width": 595, "height": 842},
            ],
        )
        drafts = [
            EvidenceDraft(
                field_name=FieldName.ORR, category=EvidenceCategory.CLINICAL_EFFICACY,
                value_num=42.0, unit="%", population="ITT", data_cutoff="2026-01-15",
                endpoint_def="ORR per RECIST 1.1", assessment_method="RECIST 1.1",
                page=1, quote="ORR was 42.0% in the ITT population.", confidence=0.9,
            ),
            EvidenceDraft(
                field_name=FieldName.SAE, category=EvidenceCategory.CLINICAL_SAFETY,
                value_num=19.8, unit="%", population="SAFETY", data_cutoff="2026-01-15",
                endpoint_def="SAE", page=1, quote="SAE rate was 19.8%.", confidence=0.9,
            ),
            EvidenceDraft(
                field_name=FieldName.DATA_CUTOFF, category=EvidenceCategory.STUDY_DESIGN,
                value_text="2026-01-15", unit="date", page=2,
                quote="Data cutoff: 2026-01-15.", confidence=0.9,
            ),
            EvidenceDraft(
                field_name=FieldName.PFS, category=EvidenceCategory.CLINICAL_EFFICACY,
                value_num=7.1, unit="months", population="ITT", data_cutoff="2026-01-15",
                endpoint_def="PFS per RECIST 1.1", assessment_method="RECIST 1.1",
                page=2, quote="Median PFS was 7.1 months.", confidence=0.9,
            ),
        ]
        created, _ = store.save_evidence_batch(session, pid, doc, drafts)
        assert len(created) == 4
        store.save_findings(
            session,
            pid,
            [
                FindingDraft(
                    finding_type=FindingType.DATA_INCONSISTENCY, domain="efficacy",
                    title="Data inconsistency: ORR differs",
                    explanation="ORR 42.0% vs 48.4% under the same dimensions.",
                    severity=Severity.CRITICAL, evidence_ids=[created[0]["evidence_id"]],
                    dedup_key="test:a:orr",
                    detail={"class": "A"},
                ),
                FindingDraft(
                    finding_type=FindingType.EVIDENCE_GAP, domain="quality",
                    title="Evidence gap: audit summary",
                    explanation="Not identified in the current package.",
                    severity=Severity.MEDIUM, dedup_key="test:gap:audit",
                ),
            ],
        )
        store.replace_coverage(
            session,
            pid,
            [
                CoverageDraft(rule_id="r1", dimension="efficacy", requirement="ORR",
                              status=CoverageStatus.SUFFICIENT),
                CoverageDraft(rule_id="r2", dimension="statistics", requirement="SAP",
                              status=CoverageStatus.PARTIAL),
            ],
        )
        store.save_questions(
            session,
            pid,
            [
                QuestionDraft(
                    question="Please reconcile the ORR values and confirm the authoritative analysis.",
                    rationale="Two sources report different ORR under the same dimensions.",
                    category="Efficacy", priority=Priority.CRITICAL, owner=Owner.CLINICAL,
                    required_documents=["Updated CSR"], evidence_ids=[created[0]["evidence_id"]],
                    dedup_key="test:q:orr",
                )
            ],
            run_id=None,
        )
    return pid


def test_overview_discovery_and_evaluation(project):
    with store.session_scope() as session:
        data = build_overview(session, project)
    disc = data["discovery"]
    evaluation = data["evaluation"]

    assert disc["assets"][0]["name"] == "ASD-101"
    assert disc["trials"][0]["nct_id"] == "NCT05712345"
    fields = {fact["field"]: fact for fact in disc["key_facts"]}
    assert fields["orr"]["value_display"] == "42.0%"
    assert fields["orr"]["document"] == "Clinical_Study_Report_v1.0.pdf"
    assert fields["orr"]["evidence_id"]
    assert len(disc["attributes"]) >= 8
    assert disc["documents"]["present_count"] >= 1

    dims = {dim["id"]: dim for dim in evaluation["dimensions"]}
    assert set(dims) == {"profile", "efficacy", "safety", "quality", "consistency",
                         "coverage", "maturity", "package"}
    assert 0 <= evaluation["overall"]["score"] <= 100
    # A critical contradiction caps readiness at 65.
    assert evaluation["overall"]["score"] <= 65
    assert evaluation["overall"]["critical_contradictions"] == 1
    assert evaluation["risk"]["by_severity"]["critical"] == 1
    assert evaluation["consistency"]["A"] == 1
    assert evaluation["coverage"]["counts"]["sufficient"] == 1
    assert data["findings"] and data["questions"]

    # --- report appendix + workbook sheets ---------------------------------
    with store.session_scope() as session:
        snapshot = exports.collect_export_data(session, project)
    md = exports.render_markdown_report(snapshot)
    assert "Appendix A. Asset Discovery & Evaluation" in md
    assert "Evaluation scorecard" in md
    assert "42.0%" in md

    wb_path = exports.build_workbook(snapshot, exports.export_filename(project, "questions"))
    from openpyxl import load_workbook

    wb = load_workbook(wb_path)
    assert "Asset Evaluation" in wb.sheetnames
    assert "Discovery" in wb.sheetnames


def test_overview_api(project):
    from hijenny.api.main import create_app

    client = TestClient(create_app())
    resp = client.get(f"/api/projects/{project}/overview")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["evaluation"]["overall"]["score"] <= 100
    assert payload["project"]["project_id"] == project
    assert client.get("/api/projects/does-not-exist/overview").status_code == 404
