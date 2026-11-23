"""Export / report generation tests for AssetDD AI (app-dev owned).

Every test isolates itself with ``store.configure(tmp_path/'t.db')`` and a
monkeypatched ``config.DATA_DIR``.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from openpyxl import load_workbook
from docx import Document

from assetdd import config, exports, reporting, store
from assetdd.schemas import (
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

EXPECTED_SHEETS = [
    "Executive Summary",
    "Asset Profile",
    "Evidence Matrix",
    "Findings",
    "Questions",
    "Coverage",
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    store.configure(db_path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    return {"data_dir": data_dir, "db_path": db_path, "root": tmp_path}


def seed_project(*, with_findings: bool = True) -> dict:
    """Create a project with two document versions, evidence, findings, questions, coverage."""
    with store.session_scope() as session:
        project = store.create_project(
            session,
            name="Onco Asset Due Diligence",
            org_id="ORG_TEST",
            asset_name="ABC-101",
            indication="NSCLC",
            phase="Phase 2",
        )
        project_id = project["project_id"]

        # Round 1 CSR then round 2 CSR (same doc family -> supersedes v1).
        csr_v1, _ = store.create_document(
            session,
            project_id,
            filename="CSR_v1.pdf",
            sha256="1" * 64,
            stored_path=f"projects/{project_id}/documents/CSR_v1.pdf",
            doc_type="CSR",
            version="1.0",
            page_count=180,
            status="parsed",
        )
        csr_v2, _ = store.create_document(
            session,
            project_id,
            filename="CSR_v2.pdf",
            sha256="2" * 64,
            stored_path=f"projects/{project_id}/documents/CSR_v2.pdf",
            doc_type="CSR",
            version="2.0",
            page_count=203,
            status="parsed",
        )
        protocol, _ = store.create_document(
            session,
            project_id,
            filename="Protocol.pdf",
            sha256="3" * 64,
            stored_path=f"projects/{project_id}/documents/Protocol.pdf",
            doc_type="PROTOCOL",
            version="1.0",
            page_count=60,
            status="parsed",
        )
        store.add_pages(
            session,
            csr_v2["document_id"],
            [
                {
                    "page_no": 42,
                    "text": "ORR was 42.0% in the ITT population. Data cutoff 15 January 2026.",
                    "width": 612,
                    "height": 792,
                    "blocks": [
                        {"text": "ORR was 42.0% in the ITT population.", "bbox": [72, 120, 420, 140], "type": "paragraph"}
                    ],
                }
            ],
        )
        asset = store.upsert_asset(
            session,
            project_id,
            "ABC-101",
            modality="Monoclonal antibody",
            target="TARGET-X",
            mechanism="Receptor antagonist",
            indication="NSCLC",
            phase="Phase 2",
        )
        store.upsert_trial(
            session,
            project_id,
            nct_id="NCT01234567",
            asset_id=asset["asset_id"],
            title="ABC-101-201",
            phase="Phase 2",
            indication="NSCLC",
            design="Randomized, open-label",
            comparator="Standard of care",
            primary_endpoint="ORR per RECIST 1.1",
            sample_size=312,
            data_cutoff="2026-01-15",
            source_document_id=csr_v2["document_id"],
        )
        evidence, _ = store.save_evidence_batch(
            session,
            project_id,
            csr_v2,
            [
                EvidenceDraft(
                    field_name=FieldName.ORR,
                    category=EvidenceCategory.CLINICAL_EFFICACY,
                    value_num=42.0,
                    unit="%",
                    population="ITT",
                    arm="ABC-101 200mg",
                    trial_key="NCT01234567",
                    data_cutoff="2026-01-15",
                    assessment_method="RECIST 1.1",
                    page=42,
                    quote="ORR was 42.0% in the ITT population.",
                    confidence=0.9,
                ),
                EvidenceDraft(
                    field_name=FieldName.SAE,
                    category=EvidenceCategory.CLINICAL_SAFETY,
                    value_num=19.8,
                    unit="%",
                    population="SAFETY",
                    arm="ABC-101 200mg",
                    trial_key="NCT01234567",
                    page=88,
                    quote="Serious adverse events occurred in 19.8% of patients.",
                    confidence=0.8,
                ),
                EvidenceDraft(
                    field_name=FieldName.NCT_ID,
                    category=EvidenceCategory.STUDY_DESIGN,
                    value_text="NCT01234567",
                    page=1,
                    quote="NCT01234567",
                    confidence=0.99,
                ),
            ],
        )
        for ev in evidence:
            store.update_evidence_verification(session, ev["evidence_id"], "verified", by="tester")

        findings = []
        if with_findings:
            findings = store.save_findings(
                session,
                project_id,
                [
                    FindingDraft(
                        finding_type=FindingType.DATA_INCONSISTENCY,
                        domain="clinical",
                        title="ORR discrepancy between CSR and commercial deck",
                        explanation="CSR reports 42.0% while the commercial deck reports 48.2% for the same ITT population and cutoff.",
                        severity=Severity.CRITICAL,
                        evidence_ids=[evidence[0]["evidence_id"]],
                        possible_causes=["different cut", "transcription error"],
                        missing_information=["reconciliation memo"],
                        recommended_action="Ask the seller to reconcile both values and provide the underlying analysis output.",
                        rule_id="consistency.orr.cross_document",
                        dedup_key="finding.orr",
                    ),
                    FindingDraft(
                        finding_type=FindingType.EVIDENCE_GAP,
                        domain="clinical",
                        title="Per-protocol population analysis missing",
                        explanation="No PP population analysis is included in the package.",
                        severity=Severity.HIGH,
                        missing_information=["PP population analysis"],
                        rule_id="gap.pp",
                        dedup_key="finding.pp",
                    ),
                ],
            )
        questions = store.save_questions(
            session,
            project_id,
            [
                QuestionDraft(
                    question="Please reconcile the ORR of 42.0% (CSR) with the 48.2% shown in the commercial deck.",
                    rationale="Both values are reported for the same ITT population and data cutoff.",
                    category="Clinical",
                    priority=Priority.CRITICAL,
                    owner=Owner.CLINICAL,
                    required_documents=["CSR", "analysis output"],
                    evidence_ids=[evidence[0]["evidence_id"]],
                    finding_ids=[findings["findings"][0]["finding_id"]] if findings else [],
                    dedup_key="question.orr",
                ),
                QuestionDraft(
                    question="Please provide the per-protocol population analysis for the primary endpoint.",
                    rationale="The package does not include the PP analysis required to assess sensitivity.",
                    category="Biostatistics",
                    priority=Priority.HIGH,
                    owner=Owner.BIOSTATISTICS,
                    required_documents=["SAP", "PP analysis output"],
                    dedup_key="question.pp",
                ),
            ],
        )
        store.replace_coverage(
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
                    requirement="Multiplicity control for key endpoints",
                    status=CoverageStatus.PARTIAL,
                    priority=Priority.MEDIUM,
                    note="SAP describes alpha allocation only at a high level.",
                ),
                CoverageDraft(
                    rule_id="coverage.demographics",
                    dimension="Baseline",
                    requirement="Demographics and baseline characteristics",
                    status=CoverageStatus.SUFFICIENT,
                    evidence_ids=[evidence[0]["evidence_id"]],
                ),
            ],
        )

    return {
        "project_id": project_id,
        "document_id": csr_v2["document_id"],
        "documents": [csr_v1, csr_v2, protocol],
        "evidence": evidence,
        "findings": findings.get("findings", []) if findings else [],
        "questions": questions["questions"],
    }


def collect(project_id: str) -> dict:
    with store.session_scope() as session:
        return exports.collect_export_data(session, project_id)


# ---------------------------------------------------------------------------
# Workbook
# ---------------------------------------------------------------------------


def test_workbook_structure_and_styling(env):
    seeded = seed_project()
    project_id = seeded["project_id"]
    out_path = env["root"] / "workbook.xlsx"
    result = exports.build_questions_workbook(project_id, out_path=out_path)
    assert result == out_path
    assert out_path.is_file() and out_path.stat().st_size > 5000

    workbook = load_workbook(out_path)
    assert workbook.sheetnames == EXPECTED_SHEETS

    summary = workbook["Executive Summary"]
    assert "Onco Asset Due Diligence" in "".join(
        str(cell.value or "") for row in summary.iter_rows() for cell in row
    )
    assert summary.freeze_panes
    assert summary.max_row > 20

    evidence_ws = workbook["Evidence Matrix"]
    assert evidence_ws.freeze_panes == "A2"
    assert evidence_ws.auto_filter.ref
    headers = [cell.value for cell in evidence_ws[1]]
    assert headers[:4] == ["Field", "Category", "Value", "Unit"]
    assert "Quote" in headers and "Page" in headers and "Verification" in headers
    assert evidence_ws.max_row == 1 + 3  # ORR, SAE + NCT identifier metadata
    values = [evidence_ws.cell(row=row, column=3).value for row in (2, 3)]
    assert "42%" in values or "42" in values

    findings_ws = workbook["Findings"]
    assert findings_ws.freeze_panes == "A2"
    assert findings_ws.max_row == 1 + 2
    severity_cell = findings_ws.cell(row=2, column=4)
    assert severity_cell.value in {"critical", "high"}
    assert severity_cell.fill is not None
    assert severity_cell.fill.fgColor.rgb not in (None, "00000000")
    explanation_header_idx = [c.value for c in findings_ws[1]].index("Explanation") + 1
    assert findings_ws.cell(row=2, column=explanation_header_idx).alignment.wrap_text is True

    questions_ws = workbook["Questions"]
    assert questions_ws.max_row == 1 + 2
    q_headers = [c.value for c in questions_ws[1]]
    for expected in ("Priority", "Owner", "Question", "Rationale", "Required Documents", "Status", "Review Status"):
        assert expected in q_headers

    coverage_ws = workbook["Coverage"]
    assert coverage_ws.max_row == 1 + 3
    statuses = {coverage_ws.cell(row=row, column=4).value for row in range(2, 5)}
    assert {"not_found", "partial", "sufficient"} == statuses

    profile_ws = workbook["Asset Profile"]
    assert profile_ws.max_row >= 2
    profile_values = " ".join(str(c.value or "") for c in profile_ws[2])
    assert "ABC-101" in profile_values
    assert "NCT01234567" in profile_values


def test_workbook_from_question_rows_only(env):
    rows = [
        {
            "question_id": "qst_1",
            "priority": "high",
            "category": "Clinical",
            "owner": "Clinical Development",
            "question": "Please reconcile the ORR values.",
            "rationale": "Values differ.",
            "required_documents": ["CSR"],
            "evidence_ids": ["ev_1"],
            "finding_ids": ["fnd_1"],
            "status": "draft",
            "review_status": "pending",
            "source": "manual",
            "created_at": "2026-01-01T00:00:00",
        }
    ]
    out_path = env["root"] / "rows.xlsx"
    exports.build_questions_workbook(
        rows=rows,
        project={"project_id": "prj_manual", "name": "Manual project"},
        out_path=out_path,
    )
    workbook = load_workbook(out_path)
    assert workbook.sheetnames == EXPECTED_SHEETS
    assert workbook["Questions"].max_row == 2
    assert workbook["Questions"].cell(row=2, column=5).value == "Please reconcile the ORR values."

    # First positional argument may also be the row list.
    out_path_2 = env["root"] / "rows2.xlsx"
    exports.build_questions_workbook(rows, project={"project_id": "prj_manual", "name": "Manual"}, out_path=out_path_2)
    assert load_workbook(out_path_2)["Questions"].max_row == 2


# ---------------------------------------------------------------------------
# Reporting stage
# ---------------------------------------------------------------------------


def test_generate_project_outputs_markdown_docx_xlsx(env):
    seeded = seed_project()
    project_id = seeded["project_id"]

    with store.session_scope() as session:
        stats = reporting.generate_project_outputs(session, project_id)

    export_dir = env["data_dir"] / "projects" / project_id / "exports"
    for key in ("report_path", "report_md", "report_docx", "questions_xlsx"):
        assert stats[key], key
        path = Path(stats[key])
        assert path.is_file(), f"{key} missing: {path}"
        assert path.parent == export_dir, (key, path.parent, export_dir)
    assert stats["evidence"] == 3
    assert stats["findings"] == 2
    assert stats["questions"] == 2
    assert stats["generated_at"]

    # ---- Markdown ------------------------------------------------------
    markdown = Path(stats["report_md"]).read_text(encoding="utf-8")
    assert "Onco Asset Due Diligence" in markdown
    assert "## 1. Decision Brief" in markdown
    assert "## 2. Executive Summary" in markdown
    assert "## 8. Human Review & Limitations" in markdown
    assert markdown.index("## 1. Decision Brief") < markdown.index("## 2. Executive Summary")
    assert "No automated Go/No-Go recommendation is made" in markdown
    assert "Asset & transaction context" in markdown
    assert "What the evidence supports" in markdown
    assert "Top open risks" in markdown
    assert "Evidence still missing" in markdown
    # Evidence highlights: clinical importance order, citated, metadata skipped.
    assert "ORR 42%" in markdown
    assert "page 42" in markdown
    assert markdown.index("**ORR 42%**") < markdown.index("**SAE 19.8%**")
    assert "- **NCT ID" not in markdown and "- **NCT_ID" not in markdown
    # Missing evidence is grouped, not a flat duplicated list.
    assert "Package-level gaps (coverage)" in markdown
    assert "Finding-specific clarification needs" in markdown
    assert "reconciliation memo" in markdown
    # Findings are quoted verbatim with document/page citations.
    assert "ORR discrepancy between CSR and commercial deck" in markdown
    assert "ORR was 42.0% in the ITT population." in markdown
    assert "Evidence citations" in markdown
    # Version history shows round-2 supersede.
    assert "### Document version history" in markdown
    assert "CSR_v1.pdf" in markdown and "CSR_v2.pdf" in markdown
    assert re.search(r"CSR_v1\.pdf \| CSR \| 1\.0 \| superseded", markdown)
    assert re.search(r"CSR_v2\.pdf \| CSR \| 2\.0 \| latest", markdown)
    # Evidence gap + seller questions.
    assert "Per-protocol population analysis" in markdown
    assert "Please reconcile the ORR" in markdown
    assert "Biostatistics" in markdown
    # Disclaimers.
    assert "AI-generated analysis" in markdown
    assert "not a substitute for" in markdown

    # ---- DOCX ----------------------------------------------------------
    docx_path = Path(stats["report_docx"])
    assert docx_path.stat().st_size > 10000
    docx = Document(str(docx_path))
    text = "\n".join(paragraph.text for paragraph in docx.paragraphs)
    assert "ASSET DUE DILIGENCE REPORT" in text
    assert "1. Decision Brief" in text
    assert "No automated Go/No-Go recommendation is made" in text
    assert "2. Executive Summary" in text
    assert "5. Key Findings" in text
    assert "8. Human Review & Limitations" in text
    assert "ORR discrepancy between CSR and commercial deck" in text
    assert "ORR was 42.0% in the ITT population." in text
    assert "Document version history" in text
    assert len(docx.tables) >= 6
    table_text = "\n".join(
        " | ".join(cell.text for cell in row.cells) for table in docx.tables for row in table.rows
    )
    assert "ABC-101" in table_text
    assert "NCT01234567" in table_text
    assert "CSR_v1.pdf" in table_text and "superseded" in table_text
    assert "Why it matters" in table_text
    assert "Clinical Development" in table_text

    # ---- Workbook ------------------------------------------------------
    workbook = load_workbook(stats["questions_xlsx"])
    assert workbook.sheetnames == EXPECTED_SHEETS
    assert workbook["Questions"].max_row == 3

    # ---- Idempotent regeneration --------------------------------------
    # Same artefacts in the same place, no duplicated findings/questions; only
    # the generation timestamp changes between runs.
    timestamp_re = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    first_md = timestamp_re.sub("<TS>", markdown)
    first_docx_paragraphs = len(docx.paragraphs)
    with store.session_scope() as session:
        stats_again = reporting.generate_project_outputs(session, project_id, force=True)
    assert stats_again["report_md"] == stats["report_md"]
    second_md = timestamp_re.sub("<TS>", Path(stats_again["report_md"]).read_text(encoding="utf-8"))
    assert second_md == first_md
    assert second_md.count("## 1. Decision Brief") == 1
    assert second_md.count("#### ORR discrepancy between CSR and commercial deck") == 1
    docx_again = Document(str(docx_path))
    assert len(docx_again.paragraphs) == first_docx_paragraphs
    # No leftover temp files from atomic writes.
    leftovers = [p.name for p in export_dir.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_generate_outputs_for_empty_project(env):
    with store.session_scope() as session:
        project = store.create_project(session, name="Empty Project")
        project_id = project["project_id"]

    with store.session_scope() as session:
        stats = reporting.generate_project_outputs(session, project_id)

    markdown = Path(stats["report_md"]).read_text(encoding="utf-8")
    assert "Empty Project" in markdown
    assert "## 1. Decision Brief" in markdown
    assert "No open consistency or completeness risks have been generated yet." in markdown
    assert "No extracted evidence is available yet" in markdown
    assert "No outstanding coverage gaps or finding-specific clarification needs" in markdown
    assert Path(stats["report_docx"]).is_file()
    workbook = load_workbook(stats["questions_xlsx"])
    assert workbook.sheetnames == EXPECTED_SHEETS
    assert workbook["Findings"].max_row == 1
    assert workbook["Questions"].max_row == 1


def test_generate_outputs_unknown_project_raises(env):
    with store.session_scope() as session:
        with pytest.raises(ValueError):
            reporting.generate_project_outputs(session, "prj_missing")


def test_markdown_citations_only_from_store_data(env):
    seeded = seed_project()
    data = collect(seeded["project_id"])
    markdown = exports.render_markdown_report(data)
    evidence_ids = {ev["evidence_id"] for ev in data["evidence"]}
    finding_evidence = {
        evidence_id for finding in data["findings"] for evidence_id in finding.get("evidence_ids") or []
    }
    assert finding_evidence
    assert finding_evidence <= evidence_ids
    # The report cites the exact quote and the correct page for each finding.
    for finding in data["findings"]:
        for evidence_id in finding.get("evidence_ids") or []:
            ev = next(item for item in data["evidence"] if item["evidence_id"] == evidence_id)
            assert ev["quote"] in markdown
    assert "42" in markdown
    assert "19.8%" in markdown
