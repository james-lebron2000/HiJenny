"""Synthetic "clean control" package generator (eval-dev owned).

Purpose: **false-positive control**.  A package whose documents agree with each
other and which contains every field the diligence checklist asks for should
produce *no* findings ("clean" means internally consistent and complete).  Any
finding raised on this package is a demonstrated false positive.

The package is generated at run time into a caller-provided directory (tests:
``tmp_path``; benchmark: a temp dir) so no binaries are committed and the
Lead-owned ``data/`` tree is never touched.  ``ground_truth.json`` with fact
locations is written next to the files (anchors are located with PyMuPDF, the
same way the Lead's generator works), so extraction accuracy can also be scored
on the clean control.

Content is deliberately tiny and repetitive: one page per document, all numbers
identical (ORR 40.0%, SAE 12.0%, cutoff 2026-01-15, ITT population, PP n=90,
multiplicity controlled, protocol deviations 7, audit summary present).
"""
from __future__ import annotations

import json
from pathlib import Path

CLEAN_ORR = 40.0
CLEAN_SAE = 12.0
CLEAN_CUTOFF = "2026-01-15"

_DOCUMENTS: dict[str, tuple[str, list[str]]] = {
    "Clinical_Study_Report_v1.0.pdf": (
        "Clinical Study Report",
        [
            "Asset: ASD-101 (zafinostat). Indication: MTAP-deleted advanced solid tumors.",
            "Study ASD-101-201 (NCT05712345), Phase 2, single-arm, open-label.",
            "Overall response rate (ORR) was 40.0% in the ITT population "
            "(data cutoff 2026-01-15; RECIST 1.1, investigator assessment).",
            "Serious adverse events (SAE) occurred in 12.0% of patients in the safety population.",
            "The per-protocol (PP) population comprised 90 patients; PP ORR was 40.0%.",
            "Multiplicity was controlled with a hierarchical testing procedure "
            "and a two-sided alpha of 0.05.",
            "Prespecified subgroup analyses are summarised in Table 12.",
            "Protocol deviations: 7 major deviations were recorded.",
            "An audit summary is provided in Appendix A.",
        ],
    ),
    "Investigator_Brochure_v3.0.pdf": (
        "Investigator's Brochure",
        [
            "Overall response rate (ORR) was 40.0% (data cutoff 2026-01-15; RECIST 1.1).",
            "Serious adverse events (SAE) occurred in 12.0% of patients.",
            "The per-protocol (PP) population comprised 90 patients.",
        ],
    ),
    "Safety_Summary_v1.0.pdf": (
        "Safety Summary",
        [
            "Serious adverse events (SAE) occurred in 12.0% of patients in the safety population.",
            "Any adverse event (AE) occurred in 88.0% of patients.",
        ],
    ),
    "Statistical_Analysis_Plan_v1.0.pdf": (
        "Statistical Analysis Plan",
        [
            "Analysis population: ITT. Overall response rate (ORR) was 40.0% "
            "(data cutoff 2026-01-15).",
            "Multiplicity: hierarchical testing, two-sided alpha 0.05.",
            "Per-protocol population: 90 patients.",
        ],
    ),
    "Protocol_v1.0.pdf": (
        "Clinical Protocol",
        [
            "Study ASD-101-201 (NCT05712345), Phase 2 in MTAP-deleted advanced solid tumors.",
            "Protocol deviations: 7 major deviations were recorded.",
            "Monitoring and audit summary: Appendix A.",
        ],
    ),
}

_CLEAN_FACTS: list[dict] = [
    {
        "field_name": "orr",
        "document": "Clinical_Study_Report_v1.0.pdf",
        "document_type": "CSR",
        "anchor": "Overall response rate (ORR) was 40.0% in the ITT population",
        "value_num": CLEAN_ORR,
        "unit": "%",
        "population": "ITT",
        "data_cutoff": CLEAN_CUTOFF,
    },
    {
        "field_name": "sae",
        "document": "Clinical_Study_Report_v1.0.pdf",
        "document_type": "CSR",
        "anchor": "Serious adverse events (SAE) occurred in 12.0%",
        "value_num": CLEAN_SAE,
        "unit": "%",
        "population": "SAFETY",
    },
    {
        "field_name": "orr",
        "document": "Investigator_Brochure_v3.0.pdf",
        "document_type": "IB",
        "anchor": "Overall response rate (ORR) was 40.0%",
        "value_num": CLEAN_ORR,
        "unit": "%",
        "data_cutoff": CLEAN_CUTOFF,
    },
    {
        "field_name": "sae",
        "document": "Safety_Summary_v1.0.pdf",
        "document_type": "SAFETY",
        "anchor": "Serious adverse events (SAE) occurred in 12.0%",
        "value_num": CLEAN_SAE,
        "unit": "%",
        "population": "SAFETY",
    },
]


def _write_pdf(path: Path, title: str, paragraphs: list[str]) -> None:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=LETTER)
    pdf.setTitle(title)
    y = 730
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(72, y, title)
    y -= 30
    pdf.setFont("Helvetica", 11)
    for paragraph in paragraphs:
        words = paragraph.split()
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if len(candidate) > 95:
                pdf.drawString(72, y, line)
                y -= 16
                line = word
            else:
                line = candidate
        if line:
            pdf.drawString(72, y, line)
            y -= 16
        y -= 6
    pdf.showPage()
    pdf.save()


def _locate_page(pdf_path: Path, anchor: str) -> int:
    """1-based page number containing ``anchor`` (whitespace-normalised)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:  # pragma: no cover - PyMuPDF is a declared dependency
        return 1
    normalized_anchor = " ".join(anchor.split())
    with fitz.open(pdf_path) as document:
        for page_index in range(document.page_count):
            text = " ".join((document[page_index].get_text("text") or "").split())
            if normalized_anchor in text:
                return page_index + 1
    return 1


def build_clean_package(dest_dir: str | Path) -> dict:
    """Generate the clean-control package (PDFs + ground_truth.json).

    Returns ``{"package_dir", "files", "ground_truth_path"}``.
    """
    package_dir = Path(dest_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    for filename, (title, paragraphs) in _DOCUMENTS.items():
        _write_pdf(package_dir / filename, title, paragraphs)
        files.append(filename)

    facts: list[dict] = []
    for fact in _CLEAN_FACTS:
        page = _locate_page(package_dir / fact["document"], fact["anchor"])
        facts.append({**fact, "page": page, "value_text": None, "tolerance": 0.05})

    ground_truth = {
        "package": package_dir.name,
        "variant": "clean_control",
        "asset_name": "ASD-101 (zafinostat)",
        "trial": {
            "nct_id": "NCT05712345",
            "study_id": "ASD-101-201",
            "phase": "Phase 2",
            "data_cutoff": CLEAN_CUTOFF,
        },
        "facts": facts,
        "finding_topics": [],
        "question_topics": [],
        "coverage": [],
        "lifecycle": {},
        "injection": {},
        "expect_no_findings": True,
    }
    gt_path = package_dir / "ground_truth.json"
    gt_path.write_text(json.dumps(ground_truth, indent=2, sort_keys=True), encoding="utf-8")
    return {"package_dir": str(package_dir), "files": files, "ground_truth_path": str(gt_path)}


__all__ = ["CLEAN_CUTOFF", "CLEAN_ORR", "CLEAN_SAE", "build_clean_package"]
