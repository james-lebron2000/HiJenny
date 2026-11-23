"""End-to-end integration test for the HiJenny pipeline (Lead-owned).

Runs the real workflow on the synthetic seller package:
  ingest -> extract -> validate -> consistency -> gap -> questions -> report

Asserts the product's core guarantees:
  * every evidence quote is verbatim in the cited page
  * no evidence fails source verification
  * the planted cross-document issues are surfaced as findings
  * questions are actionable (evidence + owner + requested documents)
  * report + Excel deliverables are produced
  * round-2 upload resolves the superseded-document findings
"""
from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

from hijenny import config, pipeline, store
from hijenny.schemas import normalize_ws

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_V1 = ROOT / "data" / "sample_package"
PACKAGE_V2 = ROOT / "data" / "sample_package_v2"

pytestmark = pytest.mark.skipif(
    not (PACKAGE_V1 / "ground_truth.json").exists(),
    reason="sample package not generated (python3 tools/make_sample_package.py --variant v1)",
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "e2e.db")
    store.configure(tmp_path / "e2e.db")
    store.init_db()
    with store.session_scope() as session:
        project = store.create_project(
            session,
            name="E2E integration",
            asset_name="ASD-101",
            indication="MTAP-deleted advanced solid tumors",
            phase="Phase 2",
        )
        project_id = project["project_id"]
        store.update_project(session, project_id, meta={"package_dir": str(PACKAGE_V1)})
    return project_id


def _evidence(session, project_id):
    return store.get_evidence(session, project_id)


def test_full_pipeline_on_seller_package(env):
    project_id = env
    run = pipeline.run_pipeline(project_id)
    assert run["status"] == "succeeded", f"pipeline failed: {run['error']}"
    assert run["state"] == "REPORT_READY"

    with store.session_scope() as session:
        stats = store.project_stats(session, project_id)
        evidence = _evidence(session, project_id)
        findings = store.list_findings(session, project_id)
        questions = store.list_questions(session, project_id)
        coverage = store.list_coverage(session, project_id)

        # --- package parsing -------------------------------------------------
        assert stats["documents"] == 13
        assert stats["pages"] >= 270
        assert stats["evidence"] >= 100
        assert stats["evidence_by_status"].get("failed", 0) == 0
        assert stats["evidence_by_status"].get("verified", 0) == stats["evidence"]

        # --- evidence provenance: quote is verbatim on the cited page --------
        for ev in evidence:
            page = store.get_page(session, ev["document_id"], ev["page"])
            assert page is not None, ev["evidence_id"]
            assert normalize_ws(ev["quote"]) in normalize_ws(page["text"]), ev["evidence_id"]

        # --- planted issues are surfaced -------------------------------------
        by_type = {f["finding_type"] for f in findings}
        for expected_type in {
            "data_inconsistency",
            "version_difference",
            "definition_difference",
            "evidence_gap",
            "technical_concern",
        }:
            assert expected_type in by_type, f"missing finding type {expected_type}: {by_type}"

        get_values = lambda finding: {  # noqa: E731
            ev["value_num"] for ev in store.get_evidence_by_ids(session, finding["evidence_ids"])
        }
        orr_a = [
            f for f in findings
            if f["finding_type"] == "data_inconsistency" and 42.0 in get_values(f) and 48.4 in get_values(f)
        ]
        assert orr_a, "Type A ORR discrepancy (42.0 vs 48.4) not detected"
        assert any("ORR" in f["title"] or "objective response rate" in f["title"] for f in orr_a)

        sae_a = [
            f for f in findings
            if f["finding_type"] == "data_inconsistency" and {19.8, 15.9} <= get_values(f)
        ]
        assert sae_a, "Type A SAE discrepancy (19.8 vs 15.9) not detected"

        injection = [f for f in findings if f["finding_type"] == "technical_concern"]
        assert injection, "prompt-injection attempt was not flagged"
        assert "Investigator_Brochure" in injection[0]["explanation"]

        # --- analysis does not manufacture contradictions from cut-off data --
        for finding in findings:
            if finding["finding_type"] != "data_inconsistency":
                continue
            records = store.get_evidence_by_ids(session, finding["evidence_ids"])
            cutoffs = {r["data_cutoff"] for r in records if r["data_cutoff"]}
            assert len(cutoffs) <= 1, (
                f"Type A finding {finding['title']} compares different data cut-offs: {cutoffs}"
            )

        # --- question quality ------------------------------------------------
        assert len(questions) >= 5
        for question in questions:
            assert question["owner"], question["question_id"]
            assert question["rationale"]
            if question["priority"] in {"critical", "high"}:
                # Finding-driven questions must cite evidence; gap-driven questions are
                # actionable through the requested documents instead.
                assert question["evidence_ids"] or question["required_documents"], question["question_id"]

        # --- coverage states -------------------------------------------------
        statuses = {c["status"] for c in coverage}
        assert "sufficient" in statuses
        assert "not_found" in statuses
        assert len(coverage) >= 20

    # --- deliverables --------------------------------------------------------
    exports = config.DATA_DIR / "projects" / project_id / "exports"
    report_md = exports / "HiJenny_Report.md"
    report_docx = exports / "HiJenny_Report.docx"
    questions_xlsx = exports / "Seller_Questions.xlsx"
    assert report_md.is_file() and report_md.stat().st_size > 5000
    assert report_docx.is_file() and report_docx.stat().st_size > 5000
    assert questions_xlsx.is_file() and questions_xlsx.stat().st_size > 5000

    report = report_md.read_text(encoding="utf-8")
    assert "human review" in report.lower()
    assert "48.4" in report and "42" in report
    assert "Seller Questions" in report or "questions" in report.lower()

    workbook = load_workbook(questions_xlsx)
    assert {"Executive Summary", "Evidence Matrix", "Findings", "Questions"}.issubset(set(workbook.sheetnames))
    assert workbook["Questions"].max_row >= 5


def test_round_two_upload_resolves_superseded_findings(env):
    if not (PACKAGE_V2 / "ground_truth.json").exists():
        pytest.skip("round-2 package not generated")
    project_id = env
    assert pipeline.run_pipeline(project_id)["status"] == "succeeded"

    with store.session_scope() as session:
        v1_findings = store.list_findings(session, project_id)
        assert any(f["finding_type"] == "data_inconsistency" for f in v1_findings)

    # Second round: seller uploads updated CSR / IB / SAP / safety / commercial deck.
    with store.session_scope() as session:
        store.update_project(session, project_id, meta={"package_dir": str(PACKAGE_V2)})
    run = pipeline.run_pipeline(project_id, force=False)
    assert run["status"] == "succeeded", run["error"]

    with store.session_scope() as session:
        documents = store.list_documents(session, project_id, latest_only=False)
        latest = {d["filename"] for d in documents if d["is_latest"]}
        assert "Clinical_Study_Report_v2.0.pdf" in latest
        assert "Clinical_Study_Report_v1.0.pdf" not in latest
        superseded = [d for d in documents if d["status"] == "superseded"]
        assert {d["filename"] for d in superseded} >= {
            "Clinical_Study_Report_v1.0.pdf",
            "Investigator_Brochure_v3.0.pdf",
            "Safety_Summary_v1.0.pdf",
        }

        findings = store.list_findings(session, project_id)
        active_ids = {d["document_id"] for d in documents if d["is_latest"]}
        active_evidence = [e for e in _evidence(session, project_id) if e["document_id"] in active_ids]

        # Open Type A findings must not compare records from superseded documents.
        for finding in findings:
            if finding["status"] in {"resolved", "dismissed"}:
                continue
            if finding["finding_type"] != "data_inconsistency":
                continue
            records = store.get_evidence_by_ids(session, finding["evidence_ids"])
            assert all(r["document_id"] in active_ids for r in records), (
                f"finding {finding['title']} still references superseded documents"
            )

        # The v1 Type A contradictions are resolved in the active package.
        assert not [
            f for f in findings
            if f["finding_type"] == "data_inconsistency" and f["status"] not in {"resolved", "dismissed"}
        ], "Type A contradictions still open after the seller's updated package"

        # ORR values in the active package are consistent (44.4) except the FAS/BICR sensitivity analysis.
        active_orr = [e for e in active_evidence if e["field_name"] == "orr"]
        itt_orr = {e["value_num"] for e in active_orr if e["population"] == "ITT" and e["value_num"] is not None}
        assert itt_orr == {44.4}, itt_orr
        fas_orr = {e["value_num"] for e in active_orr if e["population"] == "FAS"}
        assert 42.0 in fas_orr  # definition difference persists across rounds

        # PP population gap resolved by SAP v2 (n=118).
        pp = [c for c in store.list_coverage(session, project_id) if "per-protocol" in c["requirement"].lower()]
        assert pp and any(c["status"] == "sufficient" for c in pp), pp

        # A question list must still exist after the update.
        questions = store.list_questions(session, project_id)
        assert len(questions) >= 3
