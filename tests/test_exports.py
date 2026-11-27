"""Export / report generation tests for HiJenny (app-dev owned).

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

from hijenny import config, exports, reporting, store
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

EXPECTED_SHEETS = [
    "Executive Summary",
    "Asset Profile",
    "Asset Evaluation",
    "Discovery",
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
    for question in seeded["questions"]:
        qid = question["question_id"]
        assert markdown.count(qid) >= 2 and table_text.count(qid) >= 2
        assert qid in {row[0] for row in workbook["Questions"].values}

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


# ---------------------------------------------------------------------------
# Report defect regressions (Asset Profile alignment, brief ranking)
# ---------------------------------------------------------------------------


def md_table_blocks(text: str) -> list[list[str]]:
    """Return contiguous markdown table blocks (each a list of raw '|' lines)."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def md_cells(line: str) -> list[str]:
    stripped = line.strip()
    assert stripped.startswith("|") and stripped.endswith("|"), line
    inner = stripped[1:-1]
    cells = re.split(r"(?<!\\)\|", inner)
    return [cell.strip().replace("\\|", "|") for cell in cells]


def test_asset_profile_headers_and_rows_are_aligned(env):
    """Regression: the Asset Profile table used to have 11 headers and 13 cells."""
    seeded = seed_project()
    project_id = seeded["project_id"]
    with store.session_scope() as session:
        stats = reporting.generate_project_outputs(session, project_id)

    markdown = Path(stats["report_md"]).read_text(encoding="utf-8")
    section = markdown.split("## 3. Asset Profile")[1].split("### Document version history")[0]
    blocks = md_table_blocks(section)
    assert blocks, "Asset Profile markdown table is missing"

    header = md_cells(blocks[0][0])
    assert header == exports.ASSET_PROFILE_HEADERS
    assert len(header) == 14
    for raw in blocks[0][1:]:
        assert len(md_cells(raw)) == len(header), f"misaligned Asset Profile row: {raw}"
    data_row = md_cells(blocks[0][2])
    column = {name: idx for idx, name in enumerate(header)}
    assert data_row[column["Sample Size"]] == "312"
    assert data_row[column["Data Cutoff"]] == "2026-01-15"
    assert data_row[column["Primary Endpoint"]] == "ORR per RECIST 1.1"
    assert data_row[column["NCT ID"]] == "NCT01234567"
    assert data_row[column["Asset"]] == "ABC-101"

    # XLSX sheet: same header/row widths and aligned values.
    workbook = load_workbook(stats["questions_xlsx"])
    sheet = workbook["Asset Profile"]
    sheet_header = [cell.value for cell in sheet[1]]
    assert sheet_header == exports.ASSET_PROFILE_HEADERS
    assert sheet.max_column == len(sheet_header)
    for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row):
        assert len(row) == len(sheet_header)
    assert sheet.cell(row=2, column=column["Sample Size"] + 1).value == "312"
    assert sheet.cell(row=2, column=column["Data Cutoff"] + 1).value == "2026-01-15"

    # DOCX Asset Profile table: every row has the same number of cells as the header.
    docx = Document(stats["report_docx"])
    asset_tables = [
        table for table in docx.tables
        if table.rows and table.rows[0].cells and table.rows[0].cells[0].text.strip() == "Asset"
    ]
    assert asset_tables, "Asset Profile DOCX table is missing"
    table = asset_tables[0]
    columns = len(table.rows[0].cells)
    assert columns >= 9
    for row in table.rows:
        assert len(row.cells) == columns

    # Empty project: fallback row must also align and use em dashes for missing values.
    with store.session_scope() as session:
        empty_project = store.create_project(session, name="No asset data project")
        empty_pid = empty_project["project_id"]
        empty_stats = reporting.generate_project_outputs(session, empty_pid)
    empty_md = Path(empty_stats["report_md"]).read_text(encoding="utf-8")
    empty_section = empty_md.split("## 3. Asset Profile")[1].split("### Document version history")[0]
    empty_blocks = md_table_blocks(empty_section)
    empty_header = md_cells(empty_blocks[0][0])
    empty_row = md_cells(empty_blocks[0][2])
    assert len(empty_row) == len(empty_header)
    assert "—" in empty_row
    empty_workbook = load_workbook(empty_stats["questions_xlsx"])
    empty_sheet = empty_workbook["Asset Profile"]
    assert empty_sheet.max_column == len(exports.ASSET_PROFILE_HEADERS)
    assert len([cell.value for cell in empty_sheet[2] if cell.value == "—"]) >= 1


def test_decision_brief_highlights_prefer_narrative_source(env):
    """Same field/value in CSR and a data listing -> cite the narrative CSR."""
    with store.session_scope() as session:
        project = store.create_project(session, name="Narrative source project")
        project_id = project["project_id"]
        csr = store.create_document(
            session,
            project_id,
            filename="Clinical_Study_Report.pdf",
            sha256="c" * 64,
            stored_path=f"projects/{project_id}/documents/Clinical_Study_Report.pdf",
            doc_type="CSR",
            page_count=40,
            status="parsed",
        )[0]
        listing = store.create_document(
            session,
            project_id,
            filename="Clinical_Data_Listing.xlsx",
            sha256="d" * 64,
            stored_path=f"projects/{project_id}/documents/Clinical_Data_Listing.xlsx",
            doc_type="OTHER",
            page_count=2,
            status="parsed",
        )[0]
        store.save_evidence_batch(
            session,
            project_id,
            csr,
            [
                EvidenceDraft(
                    field_name=FieldName.PFS,
                    category=EvidenceCategory.CLINICAL_EFFICACY,
                    value_num=7.1,
                    unit="months",
                    population="ITT",
                    page=4,
                    quote="Median PFS was 7.1 months in the ITT population.",
                    confidence=0.5,
                )
            ],
        )
        store.save_evidence_batch(
            session,
            project_id,
            listing,
            [
                EvidenceDraft(
                    field_name=FieldName.PFS,
                    category=EvidenceCategory.CLINICAL_EFFICACY,
                    value_num=7.1,
                    unit="months",
                    population="ITT",
                    page=1,
                    quote="Median PFS (months) 7.1 ITT",
                    confidence=0.99,
                )
            ],
        )
        store.save_evidence_batch(
            session,
            project_id,
            csr,
            [
                EvidenceDraft(
                    field_name=FieldName.ORR,
                    category=EvidenceCategory.CLINICAL_EFFICACY,
                    value_num=42.0,
                    unit="%",
                    population="ITT",
                    page=5,
                    quote="ORR was 42.0% in the ITT population.",
                )
            ],
        )

    with store.session_scope() as session:
        stats = reporting.generate_project_outputs(session, project_id)
    markdown = Path(stats["report_md"]).read_text(encoding="utf-8")
    brief = markdown.split("## 1. Decision Brief")[1].split("## 2. Executive Summary")[0]
    highlight_lines = [
        line for line in brief.splitlines() if line.startswith("- ") and "supported by" in line
    ]
    assert highlight_lines
    pfs_line = next(line for line in highlight_lines if "PFS" in line.upper())
    assert "Clinical_Study_Report.pdf" in pfs_line
    assert "Clinical_Data_Listing.xlsx" not in pfs_line
    assert "page 4" in pfs_line
    assert "Median PFS was 7.1 months" in pfs_line
    evidence_id = pfs_line.split("Evidence ID: ")[1]
    with store.session_scope() as session:
        source, = store.get_evidence_by_ids(session, [evidence_id])
    assert source["document_id"] == csr["document_id"] and source["page"] == 4
    word = Document(stats["report_docx"])
    assert any("PFS 7.1 months" in p.text and evidence_id in p.text for p in word.paragraphs)
    assert any(evidence_id in c.text for t in word.tables for row in t.rows for c in row.cells)


def test_decision_brief_open_risks_ordered_by_category(env):
    """Within the same severity, risk order follows category importance."""
    with store.session_scope() as session:
        project = store.create_project(session, name="Risk ordering project")
        project_id = project["project_id"]
        store.save_findings(
            session,
            project_id,
            [
                FindingDraft(
                    finding_type=FindingType.VERSION_DIFFERENCE,
                    domain="clinical",
                    title="Version difference item",
                    explanation="Version/cutoff difference.",
                    severity=Severity.HIGH,
                    dedup_key="risk.version",
                ),
                FindingDraft(
                    finding_type=FindingType.EVIDENCE_GAP,
                    domain="clinical",
                    title="Evidence gap item",
                    explanation="Missing package item.",
                    severity=Severity.HIGH,
                    dedup_key="risk.gap",
                ),
                FindingDraft(
                    finding_type=FindingType.TECHNICAL_CONCERN,
                    domain="clinical",
                    title="Technical concern item",
                    explanation="Technical concern.",
                    severity=Severity.HIGH,
                    dedup_key="risk.tech",
                ),
                FindingDraft(
                    finding_type=FindingType.DATA_INCONSISTENCY,
                    domain="clinical",
                    title="Data inconsistency item",
                    explanation="Direct contradiction.",
                    severity=Severity.HIGH,
                    dedup_key="risk.data",
                ),
                FindingDraft(
                    finding_type=FindingType.COMPARABILITY_UNCLEAR,
                    domain="clinical",
                    title="Comparability item",
                    explanation="Dimensions missing.",
                    severity=Severity.HIGH,
                    dedup_key="risk.comp",
                ),
            ],
        )
    with store.session_scope() as session:
        stats = reporting.generate_project_outputs(session, project_id)

    markdown = Path(stats["report_md"]).read_text(encoding="utf-8")
    section = markdown.split("### Top open risks")[1].split("### Top seller questions")[0]
    blocks = md_table_blocks(section)
    assert blocks
    rows = blocks[0][2:]
    titles = [md_cells(raw)[2] for raw in rows]
    assert titles == [
        "Data inconsistency item",
        "Technical concern item",
        "Evidence gap item",
        "Comparability item",
        "Version difference item",
    ]
    assert [md_cells(raw)[1] for raw in rows] == ["high"] * 5


def test_report_counts_only_active_findings(env):
    """Headline counts, open-risk lists and the Findings sheet must exclude resolved history."""
    with store.session_scope() as session:
        project = store.create_project(session, name="Active counts project")
        project_id = project["project_id"]
        result = store.save_findings(
            session,
            project_id,
            [
                FindingDraft(
                    finding_type=FindingType.DATA_INCONSISTENCY,
                    domain="clinical",
                    title="Open inconsistency",
                    explanation="Still an active issue for the deal team.",
                    severity=Severity.HIGH,
                    dedup_key="counts.open",
                ),
                FindingDraft(
                    finding_type=FindingType.DATA_INCONSISTENCY,
                    domain="clinical",
                    title="Resolved historical issue",
                    explanation="Reconciled after the seller response.",
                    severity=Severity.CRITICAL,
                    dedup_key="counts.resolved",
                ),
            ],
        )
        resolved_id = next(
            finding["finding_id"]
            for finding in result["findings"]
            if finding["title"] == "Resolved historical issue"
        )
        store.update_finding(session, resolved_id, status="resolved", actor="tester")

    with store.session_scope() as session:
        stats = reporting.generate_project_outputs(session, project_id)

    # Stage stats: active only, history reported separately.
    assert stats["findings"] == 1
    assert stats["findings_active"] == 1
    assert stats["findings_history"] == 1
    assert stats["findings_resolved"] == 1
    assert stats["findings_dismissed"] == 0

    markdown = Path(stats["report_md"]).read_text(encoding="utf-8")
    assert "1 active finding (1 resolved)" in markdown

    # Risk counts table: only the active high-severity finding.
    risk_counts = markdown.split("### Risk counts")[1].split("## 3.")[0]
    severity_table = md_table_blocks(risk_counts)[0]
    assert md_cells(severity_table[2]) == ["High", "1"]
    assert md_cells(severity_table[3])[:2] == ["**Total**", "**1**"]
    assert "Resolved/dismissed history: 1 finding(s) are excluded" in markdown

    # Key Findings: active finding only; resolved history in its own subsection.
    key_findings = markdown.split("## 5. Key Findings")[1].split("## 6.")[0]
    assert "### Resolved findings (history)" in key_findings
    active_part, history_part = key_findings.split("### Resolved findings (history)")
    assert "Open inconsistency" in active_part
    assert "Resolved historical issue" not in active_part
    assert "Resolved historical issue" in history_part
    assert "status `resolved`" in history_part

    # Decision Brief open risks: only the active finding.
    brief = markdown.split("## 1. Decision Brief")[1].split("## 2. Executive Summary")[0]
    risk_section = brief.split("### Top open risks")[1].split("### Top seller questions")[0]
    risk_rows = md_table_blocks(risk_section)[0][2:]
    assert len(risk_rows) == 1
    assert "Open inconsistency" in risk_rows[0]
    assert "Resolved historical issue" not in risk_rows[0]

    # XLSX: Findings sheet has active rows only (+ Status column filter).
    workbook = load_workbook(stats["questions_xlsx"])
    findings_sheet = workbook["Findings"]
    assert findings_sheet.max_row == 2  # header + single active finding
    status_column = [cell.value for cell in findings_sheet[1]].index("Status") + 1
    assert findings_sheet.cell(row=2, column=status_column).value == "open"
    assert findings_sheet.auto_filter.ref is not None
    summary_rows = [[cell.value for cell in row] for row in workbook["Executive Summary"].iter_rows()]
    summary_labels = {
        str(row[0]): (row[1] if len(row) > 1 else None) for row in summary_rows if row and row[0]
    }
    summary_text = " ".join(str(value or "") for row in summary_rows for value in row)
    assert "Active findings" in summary_labels
    assert summary_labels["Active findings"] == 1
    assert "Resolved findings (history)" in summary_labels
    assert summary_labels["Resolved findings (history)"] == 1
    assert "Resolved historical issue" in summary_text
    severity_rows = [row for row in summary_rows if row and row[0] == "HIGH"]
    assert any(row[1] == 1 for row in severity_rows)  # active risk profile only
    assert not [row for row in summary_rows if row and row[0] == "CRITICAL"]

    # DOCX: active headline + history subsection.
    docx = Document(stats["report_docx"])
    docx_text = "\n".join(paragraph.text for paragraph in docx.paragraphs)
    assert "1 active finding (1 resolved)" in docx_text
    assert "Resolved/dismissed history: 1 finding(s) are excluded" in docx_text
    assert "Resolved findings (history)" in docx_text
    assert "Resolved historical issue" in docx_text
    assert "Open inconsistency" in docx_text


def test_source_review_exceptions_follow_evidence_into_exports(env):
    seeded = seed_project()
    pid = seeded["project_id"]
    with store.session_scope() as session:
        for ev in store.get_evidence(session, pid):
            status = "failed" if ev["field_name"] == "orr" else "unverifiable"
            store.update_evidence_verification(session, ev["evidence_id"], status,
                                               note="Check original " + ev["field_name"], by="reviewer-27")
        data = exports.collect_export_data(session, pid)
        assert not exports.evidence_support_highlights(data)
        assert not data["overview"]["discovery"]["key_facts"]
        outputs = reporting.generate_project_outputs(session, pid)
    md = Path(outputs["report_md"]).read_text()
    supported = md.split("### What the evidence supports")[1].split("### Source review exceptions")[0]
    assert "All extracted items have unresolved source checks." in supported and "42%" not in supported
    assert "| ORR |" in md and "| failed | reviewer-27 | Check original orr |" in md
    report = Document(outputs["report_docx"])
    review_table = next(t for t in report.tables if t.cell(0, 0).text == "Field" and t.cell(0, 1).text == "Source")
    assert len(review_table.rows) == 4
    assert any([c.text for c in row.cells][2:5] == ["failed", "reviewer-27", "Check original orr"]
               and row.cells[5].text in md and row.cells[5].text.startswith("ev_") for row in review_table.rows)
    sheet = load_workbook(outputs["questions_xlsx"], read_only=True)["Evidence Matrix"]
    headers, *rows = list(sheet.values)
    matrix = [dict(zip(headers, row)) for row in rows]
    assert all(r["Reviewed by"] == "reviewer-27" and r["Evidence ID"] for r in matrix)
    assert {r["Verification"] for r in matrix} == {"failed", "unverifiable"}
