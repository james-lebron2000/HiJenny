"""Excel / Word / Markdown exports for HiJenny (app-dev owned).

Everything here is derived from persisted store rows — no pipeline/agent imports.
The professional workbook contains six sheets:

    Executive Summary | Asset Profile | Evidence Matrix | Findings | Questions | Coverage

``build_questions_workbook`` is the convenience entry point used by the API and
the REPORT pipeline stage; ``build_workbook`` is the data-in core so callers
(e.g. ``hijenny.reporting``) can render every artefact from one consistent
snapshot.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from hijenny import config, store

# ---------------------------------------------------------------------------
# Ordering / labelling
# ---------------------------------------------------------------------------

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
PRIORITY_ORDER = ["critical", "high", "medium", "low"]
COVERAGE_ORDER = ["not_found", "partial", "not_reviewed", "sufficient", "not_applicable"]

# Within one severity tier, order risks by category importance (Decision Brief).
FINDING_TYPE_PRIORITY = [
    "data_inconsistency",
    "technical_concern",
    "evidence_gap",
    "incomplete_evidence",
    "definition_difference",
    "comparability_unclear",
    "version_difference",
]

# Findings that count towards the current risk profile.  Resolved/dismissed rows
# are audit history and must never inflate headline counts or "open risks".
ACTIVE_FINDING_STATUSES = {"open", "confirmed", "needs_review"}
INACTIVE_FINDING_STATUSES = {"resolved", "dismissed"}

CATEGORY_ORDER = [
    "study_design",
    "clinical_efficacy",
    "clinical_safety",
    "patient_baseline",
    "statistics",
    "disposition",
    "cmc",
    "regulatory",
    "ip",
    "commercial",
    "general",
]

# Excel palette (hex without '#').
NAVY = "1F3864"
NAVY_LIGHT = "D9E2F3"
GREY = "F2F2F2"
BORDER = "BFBFBF"

SEVERITY_FILL = {
    "critical": ("C00000", "FFFFFF"),
    "high": ("E36C0A", "FFFFFF"),
    "medium": ("FFD966", "000000"),
    "low": ("C6E0B4", "000000"),
    "info": ("D9D9D9", "000000"),
}
PRIORITY_FILL = {
    "critical": ("C00000", "FFFFFF"),
    "high": ("F4B183", "000000"),
    "medium": ("FFD966", "000000"),
    "low": ("C6E0B4", "000000"),
}
STATUS_FILL = {
    "open": ("FFF2CC", "000000"),
    "needs_review": ("FCE4D6", "000000"),
    "confirmed": ("F8CBAD", "000000"),
    "dismissed": ("D9D9D9", "000000"),
    "resolved": ("C6E0B4", "000000"),
    "failed": ("C00000", "FFFFFF"),
    "verified": ("C6E0B4", "000000"),
    "pending": ("FFF2CC", "000000"),
    "unverifiable": ("D9D9D9", "000000"),
}

DEFAULT_DASH = "—"


# ---------------------------------------------------------------------------
# Paths / small helpers
# ---------------------------------------------------------------------------


def _safe_segment(value: str, fallback: str = "project") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._-")
    return cleaned or fallback


def project_dir(project_id: str) -> Path:
    """``<DATA_DIR>/projects/<project_id>`` (reads config dynamically for tests)."""
    return Path(config.DATA_DIR) / "projects" / _safe_segment(project_id)


def documents_dir(project_id: str) -> Path:
    return project_dir(project_id) / "documents"


def exports_dir(project_id: str) -> Path:
    return project_dir(project_id) / config.EXPORT_SUBDIR


def ensure_project_dirs(project_id: str) -> Path:
    out = exports_dir(project_id)
    out.mkdir(parents=True, exist_ok=True)
    return out


def export_filename(project_id: str, kind: str) -> Path:
    """Return the canonical export path without touching the filesystem."""
    names = {
        "questions": "Seller_Questions.xlsx",
        "workbook": "Seller_Questions.xlsx",
        "docx": "HiJenny_Report.docx",
        "md": "HiJenny_Report.md",
    }
    if kind not in names:
        raise ValueError(f"unknown export kind: {kind}")
    return exports_dir(project_id) / names[kind]


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def value_display(ev: dict) -> str:
    """Human-readable evidence value (42% / 12.5 months / 312 patients)."""
    num = ev.get("value_num")
    unit = ev.get("unit")
    if num is not None:
        try:
            text = f"{float(num):g}"
        except (TypeError, ValueError):  # pragma: no cover - defensive
            text = str(num)
        if unit == "%":
            return f"{text}%"
        if unit:
            return f"{text} {unit}"
        return text
    return str(ev.get("value_text") or "")


def _txt(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _num(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _joined(value: Any, sep: str = "; ") -> str:
    if not value:
        return ""
    if isinstance(value, (list, tuple, set)):
        return sep.join(_txt(v) for v in value if v not in (None, ""))
    return _txt(value)


def align_row(row: Iterable[Any] | None, count: int, missing: str = DEFAULT_DASH) -> list[str]:
    """Return exactly ``count`` non-empty string cells, padding/truncating as needed.

    Keeps table headers and data rows aligned (Asset Profile regression guard) and
    renders missing values as an em dash.
    """
    values = [_txt(value) for value in list(row or [])[:count]]
    while len(values) < count:
        values.append(missing)
    return [value if value not in ("", None) else missing for value in values]


def truncate(text: Any, limit: int = 400) -> str:
    text = _txt(text).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


_ILLEGAL_CELL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MAX_CELL_CHARS = 32000


def sanitize_cell(value: Any) -> Any:
    """Make a value safe for Excel/Word (strip control chars, cap length)."""
    if isinstance(value, str):
        cleaned = _ILLEGAL_CELL_CHARS.sub("", value)
        if len(cleaned) > _MAX_CELL_CHARS:
            cleaned = cleaned[:_MAX_CELL_CHARS] + "…"
        return cleaned
    return value


def sanitize_data(value: Any) -> Any:
    """Recursively sanitise every string in a data snapshot (copy)."""
    if isinstance(value, dict):
        return {key: sanitize_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_data(item) for item in value]
    return sanitize_cell(value)


def _rank(value: str | None, order: Sequence[str]) -> int:
    try:
        return list(order).index(str(value))
    except (ValueError, TypeError):
        return len(order)


def is_active_finding(finding: dict) -> bool:
    """True unless the finding is resolved or dismissed (history rows)."""
    return _txt(finding.get("status") or "open").lower() not in INACTIVE_FINDING_STATUSES


def split_findings(rows: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Split findings into (active, history) lists."""
    active: list[dict] = []
    history: list[dict] = []
    for finding in rows:
        (active if is_active_finding(finding) else history).append(finding)
    return active, history


def finding_counts(data: dict) -> dict[str, int]:
    """Counts used in every headline number: active findings vs history."""
    active = data.get("findings") or []
    history = data.get("findings_history") or []
    resolved = sum(1 for f in history if _txt(f.get("status")) == "resolved")
    dismissed = sum(1 for f in history if _txt(f.get("status")) == "dismissed")
    return {
        "active": len(active),
        "history": len(history),
        "resolved": resolved,
        "dismissed": dismissed,
        "critical": sum(1 for f in active if _txt(f.get("severity")) == "critical"),
        "high": sum(1 for f in active if _txt(f.get("severity")) == "high"),
    }


def _plural(count: int, singular: str, plural_form: str | None = None) -> str:
    return singular if count == 1 else (plural_form or singular + "s")


def findings_count_text(data: dict) -> str:
    """e.g. ``11 active findings (5 resolved)`` — never includes history in the headline."""
    counts = finding_counts(data)
    text = f"{counts['active']} active {_plural(counts['active'], 'finding')}"
    history_bits = []
    if counts["resolved"]:
        history_bits.append(f"{counts['resolved']} resolved")
    if counts["dismissed"]:
        history_bits.append(f"{counts['dismissed']} dismissed")
    if history_bits:
        text += " (" + ", ".join(history_bits) + ")"
    return text


def sort_findings(rows: Iterable[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (
            _rank(r.get("severity"), SEVERITY_ORDER),
            _rank(r.get("finding_type"), FINDING_TYPE_PRIORITY),
            _txt(r.get("created_at")),
            _txt(r.get("finding_id")),
        ),
    )


def sort_questions(rows: Iterable[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (
            _rank(r.get("priority"), PRIORITY_ORDER),
            _txt(r.get("owner")),
            _txt(r.get("created_at")),
            _txt(r.get("question_id")),
        ),
    )


def sort_evidence(rows: Iterable[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (
            _rank(r.get("category"), CATEGORY_ORDER),
            _txt(r.get("document_type")),
            _txt(r.get("filename")),
            int(r.get("page") or 0),
            _txt(r.get("field_name")),
        ),
    )


def sort_coverage(rows: Iterable[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (
            _rank(r.get("status"), COVERAGE_ORDER),
            _rank(r.get("priority"), PRIORITY_ORDER),
            _txt(r.get("dimension")),
            _txt(r.get("rule_id")),
        ),
    )


# ---------------------------------------------------------------------------
# Data snapshot
# ---------------------------------------------------------------------------


def collect_export_data(session, project_id: str) -> dict:
    """Collect every row an export/report needs into one plain-dict snapshot."""
    project = store.get_project(session, project_id)
    if not project:
        raise ValueError(f"project not found: {project_id}")

    documents = store.list_documents(session, project_id)
    assets = store.list_assets(session, project_id)
    trials = store.list_trials(session, project_id)
    evidence = store.get_evidence(session, project_id, limit=10000)
    findings_all = store.list_findings(session, project_id)
    findings, findings_history = split_findings(findings_all)
    questions = store.list_questions(session, project_id)
    coverage = store.list_coverage(session, project_id)
    stats = store.project_stats(session, project_id)
    runs = store.list_runs(session, project_id, limit=5)

    latest_documents = [d for d in documents if d.get("is_latest", True)]
    doc_names = {d["document_id"]: d.get("filename") or "" for d in documents}
    asset_names = {a["asset_id"]: a.get("name") or "" for a in assets}
    trial_names = {t["trial_id"]: (t.get("nct_id") or t.get("internal_id") or "") for t in trials}

    # Enrich evidence with display-friendly names.
    for ev in evidence:
        ev["document_name"] = ev.get("filename") or doc_names.get(ev.get("document_id"), "")
        ev["asset_name"] = asset_names.get(ev.get("asset_id") or "", "")
        if not ev.get("trial_key") and ev.get("trial_id"):
            ev["trial_key"] = trial_names.get(ev["trial_id"], "")

    from hijenny.overview import build_overview
    from hijenny.diligence import brief_updates

    return {
        "bd_updates": brief_updates(session, project_id, questions),
        "project": project,
        "stats": stats,
        "overview": build_overview(session, project_id),
        "documents": documents,
        "assets": assets,
        "trials": trials,
        "evidence": sort_evidence(evidence),
        "findings": sort_findings(findings),
        "findings_history": sort_findings(findings_history),
        "questions": sort_questions(questions),
        "coverage": sort_coverage(coverage),
        "runs": runs,
        "docs_by_type": _count_by(documents, "doc_type"),
        "latest_documents": latest_documents,
        "pages_latest": sum(int(d.get("page_count") or 0) for d in latest_documents),
        "generated_at": _now(),
        "severity_counts": _count_by(findings, "severity", SEVERITY_ORDER),
        "priority_counts": _count_by(questions, "priority", PRIORITY_ORDER),
        "coverage_counts": _count_by(coverage, "status", COVERAGE_ORDER),
        "finding_type_counts": _count_by(findings, "finding_type"),
        "finding_counts": {
            "active": len(findings),
            "history": len(findings_history),
            "resolved": sum(1 for f in findings_history if _txt(f.get("status")) == "resolved"),
            "dismissed": sum(1 for f in findings_history if _txt(f.get("status")) == "dismissed"),
        },
    }


def _count_by(rows: Iterable[dict], field: str, order: Sequence[str] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = _txt(row.get(field)) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    if order:
        ordered = {k: counts[k] for k in order if k in counts}
        ordered.update({k: v for k, v in counts.items() if k not in ordered})
        return ordered
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def evidence_index(data: dict) -> dict[str, dict]:
    return {ev["evidence_id"]: ev for ev in data.get("evidence", [])}


def documents_by_id(data: dict) -> dict[str, dict]:
    return {d["document_id"]: d for d in data.get("documents", [])}


# ---------------------------------------------------------------------------
# Asset profile rows
# ---------------------------------------------------------------------------


def asset_profile_rows(data: dict) -> list[list[str]]:
    project = data.get("project") or {}
    assets = data.get("assets") or []
    trials = data.get("trials") or []
    docs = documents_by_id(data)
    ev = data.get("evidence") or []

    rows: list[list[str]] = []
    if assets or trials:
        for asset in assets:
            asset_trials = [t for t in trials if t.get("asset_id") == asset.get("asset_id")]
            if not asset_trials:
                rows.append(_asset_profile_row(asset, None, docs))
            for trial in asset_trials:
                rows.append(_asset_profile_row(asset, trial, docs))
        # Trials not linked to an asset.
        unlinked = [t for t in trials if not t.get("asset_id")]
        for trial in unlinked:
            rows.append(_asset_profile_row({}, trial, docs))
        return rows

    # Fallback: derive a profile from sheet-level evidence + project metadata.
    by_field: dict[str, dict] = {}
    for item in ev:
        field = item.get("field_name") or ""
        if field and field not in by_field and item.get("category") in {
            "study_design",
            "clinical_efficacy",
            "clinical_safety",
            "patient_baseline",
            "general",
        }:
            by_field[field] = item

    def pick(field: str) -> str:
        item = by_field.get(field)
        return value_display(item) if item else ""

    design = pick("design")
    if not design and by_field.get("phase"):
        design = f"Phase {pick('phase')} study"
    source_doc = ""
    first = next(iter(by_field.values()), None)
    if first:
        source_doc = first.get("document_name") or docs.get(first.get("document_id"), {}).get("filename", "")
    rows.append(
        [
            _txt(project.get("asset_name")) or "(not extracted)",
            pick("modality"),
            pick("target"),
            pick("mechanism"),
            _txt(project.get("indication")) or pick("indication"),
            _txt(project.get("phase")) or pick("phase"),
            "",
            pick("nct_id"),
            design,
            pick("comparator"),
            pick("primary_endpoint"),
            pick("sample_size") or pick("n_itt"),
            pick("data_cutoff"),
            source_doc,
        ]
    )
    return rows


def _asset_profile_row(asset: dict, trial: dict | None, docs: dict[str, dict]) -> list[str]:
    trial = trial or {}
    source_doc = ""
    if trial.get("source_document_id"):
        source_doc = docs.get(trial["source_document_id"], {}).get("filename", "")
    return [
        _txt(asset.get("name")),
        _txt(asset.get("modality")),
        _txt(asset.get("target")),
        _txt(asset.get("mechanism")),
        _txt(asset.get("indication")),
        _txt(asset.get("phase")),
        _txt(trial.get("title")) or _txt(trial.get("internal_id")),
        _txt(trial.get("nct_id")),
        _txt(trial.get("design")),
        _txt(trial.get("comparator")),
        _txt(trial.get("primary_endpoint")),
        _num(trial.get("sample_size")),
        _txt(trial.get("data_cutoff")),
        source_doc,
    ]


# ---------------------------------------------------------------------------
# Workbook builders
# ---------------------------------------------------------------------------

ASSET_PROFILE_HEADERS = [
    "Asset", "Modality", "Target", "Mechanism", "Indication", "Phase", "Trial",
    "NCT ID", "Design", "Comparator", "Primary Endpoint", "Sample Size",
    "Data Cutoff", "Source Document",
]

EVIDENCE_HEADERS = [
    "Field", "Category", "Value", "Unit", "Population", "Arm", "Trial",
    "Data Cutoff", "Endpoint Definition", "Assessment Method", "Document", "Page",
    "Quote", "Verification", "Confidence",
]

FINDING_HEADERS = [
    "Finding ID", "Type", "Domain", "Severity", "Status", "Title", "Explanation",
    "Possible Causes", "Missing Information", "Recommended Action", "Rule",
    "Evidence", "Created",
]

QUESTION_HEADERS = [
    "Question ID", "Priority", "Category", "Owner", "Question", "Rationale",
    "Required Documents", "Evidence Refs", "Findings", "Status", "Review Status",
    "Source", "Created",
]

COVERAGE_HEADERS = [
    "Rule", "Dimension", "Requirement", "Status", "Priority", "Evidence Refs", "Note",
]


def _openpyxl():
    from openpyxl import Workbook  # local import keeps API startup light
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    return Workbook, Alignment, Border, Font, PatternFill, Side, get_column_letter


def _apply_fill(cell, color: str, font_color: str, bold: bool = False) -> None:
    from openpyxl.styles import Font, PatternFill

    cell.fill = PatternFill("solid", fgColor=color)
    cell.font = Font(bold=bold, color=font_color, size=cell.font.size or 10)


def _table_sheet(
    wb,
    title: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    widths: dict[int, int],
    *,
    tab_color: str = NAVY,
    wrap_cols: Sequence[int] = (),
    fill_rules: dict[int, str] | None = None,
):
    """Create a styled table sheet with frozen header + autofilter."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(title=title)
    ws.sheet_properties.tabColor = tab_color

    thin = Side(style="thin", color=BORDER)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    header_font = Font(bold=True, color="FFFFFF", size=10)
    header_fill = PatternFill("solid", fgColor=NAVY)
    for idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(idx)].width = widths.get(idx, 18)
    ws.row_dimensions[1].height = 30

    max_col = max(1, len(headers))
    last_row = 1
    for r_idx, row in enumerate(rows, start=2):
        last_row = r_idx
        for c_idx in range(1, max_col + 1):
            value = row[c_idx - 1] if c_idx - 1 < len(row) else ""
            if isinstance(value, (list, tuple)):
                value = "; ".join(_txt(v) for v in value if v not in (None, ""))
            if isinstance(value, (dict,)):
                value = _joined(value)
            cell = ws.cell(row=r_idx, column=c_idx, value=sanitize_cell(value))
            cell.border = border
            cell.font = Font(size=9)
            cell.alignment = Alignment(
                horizontal="left",
                vertical="top",
                wrap_text=(c_idx in wrap_cols),
            )
            wrap = c_idx in wrap_cols
            if wrap:
                cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        # Colour-code severity / priority / status columns by value.
        if fill_rules:
            for c_idx, kind in fill_rules.items():
                raw = row[c_idx - 1] if c_idx - 1 < len(row) else ""
                key = _txt(raw).strip().lower()
                palette = {
                    "severity": SEVERITY_FILL,
                    "priority": PRIORITY_FILL,
                    "status": STATUS_FILL,
                    "coverage": {"not_found": SEVERITY_FILL["critical"], "partial": SEVERITY_FILL["medium"], "not_reviewed": SEVERITY_FILL["low"]},
                    "verification": STATUS_FILL,
                }.get(kind, {})
                if key in palette:
                    fg, font_color = palette[key]
                    _apply_fill(ws.cell(row=r_idx, column=c_idx), fg, font_color, bold=(key in {"critical", "high"}))

    ws.freeze_panes = "A2"
    if last_row >= 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{last_row}"
    ws.sheet_view.showGridLines = False
    return ws


def _sheet_executive_summary(wb, data: dict) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ws = wb.active
    ws.title = "Executive Summary"
    ws.sheet_properties.tabColor = "C00000"
    ws.sheet_view.showGridLines = False
    for idx, width in {1: 30, 2: 52, 3: 22, 4: 12, 5: 12, 6: 16}.items():
        ws.column_dimensions[get_column_letter(idx)].width = width

    project = data.get("project") or {}
    stats = data.get("stats") or {}
    severity = data.get("severity_counts") or {}
    priority = data.get("priority_counts") or {}
    coverage = data.get("coverage_counts") or {}
    ftypes = data.get("finding_type_counts") or {}
    docs = data.get("documents") or []
    evidence = data.get("evidence") or []
    findings = data.get("findings") or []
    findings_history = data.get("findings_history") or []
    counts = finding_counts(data)
    questions = data.get("questions") or []

    navy_fill = PatternFill("solid", fgColor=NAVY)
    navy_light_fill = PatternFill("solid", fgColor=NAVY_LIGHT)
    grey_fill = PatternFill("solid", fgColor=GREY)
    thin = Side(style="thin", color=BORDER)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def title_row(row: int, text: str, span: str = "A:F", size: int = 11) -> int:
        ws.merge_cells(f"A{row}:F{row}")
        cell = ws.cell(row=row, column=1, value=text)
        cell.font = Font(bold=True, size=size, color="FFFFFF")
        cell.fill = navy_fill
        cell.alignment = Alignment(vertical="center", horizontal="left")
        ws.row_dimensions[row].height = 22 if size > 10 else 20
        return row + 1

    def kv(row: int, label: str, value: Any) -> int:
        a = ws.cell(row=row, column=1, value=label)
        a.font = Font(bold=True, size=10, color="333333")
        a.fill = grey_fill
        a.border = border
        b = ws.cell(row=row, column=2, value=sanitize_cell(value if value not in (None, "") else DEFAULT_DASH))
        b.font = Font(size=10)
        b.border = border
        b.alignment = Alignment(vertical="top", wrap_text=True)
        return row + 1

    ws.merge_cells("A1:F1")
    cell = ws.cell(row=1, column=1, value="HiJenny — Due Diligence Package Export")
    cell.font = Font(bold=True, size=18, color="FFFFFF")
    cell.fill = navy_fill
    cell.alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[1].height = 34

    ws.merge_cells("A2:F2")
    sub = ws.cell(row=2, column=1, value=_txt(project.get("name")) or "(unnamed project)")
    sub.font = Font(bold=True, size=13, color=NAVY)
    sub.fill = navy_light_fill
    sub.alignment = Alignment(vertical="center")
    ws.row_dimensions[2].height = 26

    row = 4
    row = title_row(row, "PROJECT")
    row = kv(row, "Project ID", project.get("project_id"))
    row = kv(row, "Project", project.get("name"))
    row = kv(row, "Organization", project.get("org_id"))
    row = kv(row, "Asset", project.get("asset_name"))
    row = kv(row, "Indication", project.get("indication"))
    row = kv(row, "Phase", project.get("phase"))
    row = kv(row, "Status", project.get("status"))
    row = kv(row, "Generated", data.get("generated_at"))

    row += 1
    row = title_row(row, "PACKAGE STATISTICS")
    for label, value in [
        ("Documents (latest)", stats.get("documents", len(docs))),
        ("Documents (all versions)", stats.get("documents_all", len(docs))),
        ("Pages parsed", stats.get("pages", 0)),
        ("Evidence items", stats.get("evidence", len(evidence))),
        ("Active findings", counts["active"]),
        ("Resolved findings (history)", counts["history"]),
        ("Questions", stats.get("questions", len(questions))),
        ("Coverage checks", stats.get("coverage", 0)),
        ("Assets", len(data.get("assets") or [])),
        ("Clinical trials", len(data.get("trials") or [])),
    ]:
        row = kv(row, label, value)

    row += 1
    row = title_row(row, "RISK PROFILE — FINDINGS BY SEVERITY")
    for sev, count in severity.items():
        a = ws.cell(row=row, column=1, value=str(sev).upper())
        a.border = border
        a.font = Font(bold=True, size=10)
        fg, font_color = SEVERITY_FILL.get(str(sev).lower(), ("F2F2F2", "000000"))
        _apply_fill(a, fg, font_color, bold=True)
        b = ws.cell(row=row, column=2, value=int(count))
        b.border = border
        b.font = Font(size=10)
        row += 1
    if not severity:
        row = kv(row, "Findings", "No findings generated yet")

    row += 1
    row = title_row(row, "QUESTIONS BY PRIORITY")
    for prio, count in priority.items():
        a = ws.cell(row=row, column=1, value=str(prio).upper())
        a.border = border
        a.font = Font(bold=True, size=10)
        fg, font_color = PRIORITY_FILL.get(str(prio).lower(), ("F2F2F2", "000000"))
        _apply_fill(a, fg, font_color, bold=True)
        b = ws.cell(row=row, column=2, value=int(count))
        b.border = border
        b.font = Font(size=10)
        row += 1
    if not priority:
        row = kv(row, "Questions", "No seller questions generated yet")

    row += 1
    row = title_row(row, "COVERAGE STATUS")
    for status, count in coverage.items():
        row = kv(row, str(status).replace("_", " ").title(), int(count))
    if not coverage:
        row = kv(row, "Coverage", "No coverage assessment yet")

    row += 1
    row = title_row(row, "FINDINGS BY TYPE")
    for ftype, count in ftypes.items():
        row = kv(row, str(ftype).replace("_", " ").title(), int(count))
    if not ftypes:
        row = kv(row, "Findings by type", DEFAULT_DASH)

    row += 1
    row = title_row(row, "TOP FINDINGS (BY SEVERITY)")
    if findings:
        for finding in findings[:10]:
            a = ws.cell(row=row, column=1, value=str(finding.get("severity", "")).upper())
            a.border = border
            fg, font_color = SEVERITY_FILL.get(str(finding.get("severity", "")).lower(), ("F2F2F2", "000000"))
            _apply_fill(a, fg, font_color, bold=True)
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
            b = ws.cell(row=row, column=2, value=sanitize_cell(_txt(finding.get("title"))))
            b.border = border
            b.font = Font(size=10)
            b.alignment = Alignment(vertical="top", wrap_text=True)
            row += 1
    else:
        row = kv(row, "Findings", "No active findings")

    row += 1
    row = title_row(row, "RESOLVED FINDINGS (HISTORY)")
    row = kv(row, "History rows (not in risk counts)", counts["history"])
    if findings_history:
        for finding in findings_history[:10]:
            status = _txt(finding.get("status"))
            label = f"{_txt(finding.get('severity')).upper()} · {status}"
            row = kv(row, label, _txt(finding.get("title")))
    else:
        row = kv(row, "History", "No resolved or dismissed findings")

    row += 1
    row = title_row(row, "DOCUMENT INVENTORY")
    row = kv(row, "Document count", len(docs))
    for doc in docs[:25]:
        row = kv(
            row,
            _txt(doc.get("doc_type")) or "OTHER",
            f"{_txt(doc.get('filename'))}  ({int(doc.get('page_count') or 0)} pages, {_txt(doc.get('status'))})",
        )

    ws.freeze_panes = "A4"


def _sheet_asset_profile(wb, data: dict) -> None:
    widths = {1: 24, 2: 16, 3: 18, 4: 32, 5: 22, 6: 10, 7: 30, 8: 14, 9: 28,
              10: 18, 11: 30, 12: 12, 13: 14, 14: 28}
    rows = [align_row(row, len(ASSET_PROFILE_HEADERS)) for row in asset_profile_rows(data)]
    _table_sheet(
        wb,
        "Asset Profile",
        ASSET_PROFILE_HEADERS,
        rows,
        widths,
        tab_color="2E75B6",
        wrap_cols=(4, 7, 9, 11, 14),
        fill_rules={6: "status"},
    )


def _sheet_evidence_matrix(wb, data: dict) -> None:
    widths = {1: 16, 2: 16, 3: 11, 4: 9, 5: 12, 6: 16, 7: 14, 8: 12, 9: 26,
              10: 14, 11: 26, 12: 7, 13: 60, 14: 13, 15: 10}
    rows = []
    for ev in data.get("evidence") or []:
        rows.append(
            [
                _txt(ev.get("field_name")),
                _txt(ev.get("category")),
                value_display(ev),
                _txt(ev.get("unit")),
                _txt(ev.get("population")),
                _txt(ev.get("arm")),
                _txt(ev.get("trial_key")),
                _txt(ev.get("data_cutoff")),
                _txt(ev.get("endpoint_def")),
                _txt(ev.get("assessment_method")),
                _txt(ev.get("document_name") or ev.get("filename")),
                int(ev.get("page") or 0),
                _txt(ev.get("quote")),
                _txt(ev.get("verification_status")),
                _num(ev.get("confidence")),
            ]
        )
    _table_sheet(
        wb,
        "Evidence Matrix",
        EVIDENCE_HEADERS,
        rows,
        widths,
        tab_color="548235",
        wrap_cols=(9, 13),
        fill_rules={14: "verification"},
    )


def _sheet_findings(wb, data: dict) -> None:
    widths = {1: 22, 2: 22, 3: 12, 4: 11, 5: 14, 6: 42, 7: 50, 8: 34, 9: 30,
              10: 38, 11: 16, 12: 9, 13: 20}
    rows = []
    for finding in data.get("findings") or []:
        rows.append(
            [
                _txt(finding.get("finding_id")),
                _txt(finding.get("finding_type")),
                _txt(finding.get("domain")),
                _txt(finding.get("severity")),
                _txt(finding.get("status")),
                _txt(finding.get("title")),
                _txt(finding.get("explanation")),
                _joined(finding.get("possible_causes")),
                _joined(finding.get("missing_information")),
                _txt(finding.get("recommended_action")),
                _txt(finding.get("rule_id")),
                len(finding.get("evidence_ids") or []),
                _txt(finding.get("created_at")),
            ]
        )
    _table_sheet(
        wb,
        "Findings",
        FINDING_HEADERS,
        rows,
        widths,
        tab_color="C00000",
        wrap_cols=(6, 7, 8, 9, 10),
        fill_rules={4: "severity", 5: "status"},
    )


def _sheet_questions(wb, data: dict) -> None:
    widths = {1: 22, 2: 10, 3: 16, 4: 20, 5: 58, 6: 46, 7: 24, 8: 20, 9: 20,
              10: 12, 11: 14, 12: 10, 13: 20}
    rows = []
    for q in data.get("questions") or []:
        rows.append(
            [
                _txt(q.get("question_id")),
                _txt(q.get("priority")),
                _txt(q.get("category")),
                _txt(q.get("owner")),
                _txt(q.get("question")),
                _txt(q.get("rationale")),
                _joined(q.get("required_documents")),
                _joined(q.get("evidence_ids"), sep="\n"),
                _joined(q.get("finding_ids")),
                _txt(q.get("status")),
                _txt(q.get("review_status")),
                _txt(q.get("source")),
                _txt(q.get("created_at")),
            ]
        )
    _table_sheet(
        wb,
        "Questions",
        QUESTION_HEADERS,
        rows,
        widths,
        tab_color="7030A0",
        wrap_cols=(5, 6, 7, 8, 9),
        fill_rules={2: "priority", 10: "status"},
    )


def _sheet_coverage(wb, data: dict) -> None:
    widths = {1: 22, 2: 26, 3: 52, 4: 14, 5: 10, 6: 24, 7: 44}
    rows = []
    for item in data.get("coverage") or []:
        rows.append(
            [
                _txt(item.get("rule_id")),
                _txt(item.get("dimension")),
                _txt(item.get("requirement")),
                _txt(item.get("status")),
                _txt(item.get("priority")),
                _joined(item.get("evidence_ids"), sep="\n"),
                _txt(item.get("note")),
            ]
        )
    _table_sheet(
        wb,
        "Coverage",
        COVERAGE_HEADERS,
        rows,
        widths,
        tab_color="BF8F00",
        wrap_cols=(3, 6, 7),
        fill_rules={4: "coverage", 5: "priority"},
    )


def _sheet_asset_evaluation(wb, data: dict) -> None:
    overview = data.get("overview") or {}
    evaluation = overview.get("evaluation") or {}
    overall = evaluation.get("overall") or {}
    risk = evaluation.get("risk") or {}
    consistency = evaluation.get("consistency") or {}
    rows: list[list[Any]] = []
    for dim in evaluation.get("dimensions") or []:
        rows.append([
            _txt(dim.get("label_zh")),
            _txt(dim.get("label_en")),
            dim.get("score"),
            dim.get("weight"),
            _txt(dim.get("basis")),
        ])
    rows.append([
        "尽调就绪度 / Diligence readiness",
        _txt(overall.get("title_en")),
        overall.get("score"),
        1.0,
        f"等级 {overall.get('label_zh')}；"
        + (f"因 {overall.get('critical_contradictions')} 项 critical 矛盾封顶 {overall.get('readiness_cap')}；" if overall.get("readiness_cap") else "")
        + "规则化汇总，不构成投资建议",
    ])
    rows.append(["风险暴露 / Risk exposure", _txt(risk.get("label_zh")), risk.get("weighted_score"), "", f"active findings {risk.get('active_findings', 0)}；按严重度加权"])
    rows.append([
        "一致性 / Consistency A/B/C/U",
        "comparability classes",
        f"{consistency.get('A', 0)}/{consistency.get('B', 0)}/{consistency.get('C', 0)}/{consistency.get('U', 0)}",
        "",
        "A 直接矛盾 / B 版本差异 / C 定义差异 / U 可比性不明",
    ])
    _table_sheet(
        wb,
        "Asset Evaluation",
        ["Dimension (ZH)", "Dimension (EN)", "Score (0-100)", "Weight", "Basis"],
        rows,
        {1: 30, 2: 30, 3: 14, 4: 10, 5: 80},
        tab_color="C00000",
        wrap_cols=(5,),
    )


def _sheet_asset_discovery(wb, data: dict) -> None:
    overview = data.get("overview") or {}
    disc = overview.get("discovery") or {}
    rows: list[list[Any]] = []
    for attr in disc.get("attributes") or []:
        source = "asset registry"
        if attr.get("document"):
            source = f"{attr.get('document')} p.{attr.get('page') or '—'}"
        rows.append(["Asset", f"{_txt(attr.get('label_zh'))} / {_txt(attr.get('label_en'))}", _txt(attr.get("value")), source])
    for fact in disc.get("key_facts") or []:
        meta = " · ".join(str(v) for v in (fact.get("population"), fact.get("cutoff"), fact.get("assessment_method")) if v)
        rows.append([
            "Key clinical fact",
            _txt(fact.get("label_zh")),
            f"{_txt(fact.get('value_display'))} ({meta or '—'})",
            f"{fact.get('document')} p.{fact.get('page') or '—'} (evidence {fact.get('evidence_id')})",
        ])
    docs = disc.get("documents") or {}
    for row in docs.get("by_type") or []:
        rows.append(["Document inventory", _txt(row.get("doc_type")), f"{row.get('documents')} docs · {row.get('pages')} pages", "active documents"])
    _table_sheet(
        wb,
        "Discovery",
        ["Section", "Item", "Value", "Source"],
        rows,
        {1: 20, 2: 34, 3: 48, 4: 46},
        tab_color="2E75B6",
        wrap_cols=(3, 4),
    )


def build_workbook(data: dict, out_path: str | Path | None = None) -> Path:
    """Render the six-sheet styled workbook from a data snapshot. Returns path."""
    data = sanitize_data(data)
    Workbook, *_ = _openpyxl()
    project_id = (data.get("project") or {}).get("project_id") or "project"
    target = Path(out_path) if out_path else export_filename(project_id, "questions")
    target.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    _sheet_executive_summary(wb, data)
    _sheet_asset_profile(wb, data)
    _sheet_asset_evaluation(wb, data)
    _sheet_asset_discovery(wb, data)
    _sheet_evidence_matrix(wb, data)
    _sheet_findings(wb, data)
    _sheet_questions(wb, data)
    _sheet_coverage(wb, data)

    wb.properties.title = "HiJenny — Due Diligence Workbook"
    wb.properties.creator = "HiJenny"
    wb.properties.subject = _txt((data.get("project") or {}).get("name"))
    wb.save(target)
    return target


def build_questions_workbook(
    project_id_or_rows: str | Iterable[dict] | None = None,
    *,
    session=None,
    out_path: str | Path | None = None,
    rows: Iterable[dict] | None = None,
    data: dict | None = None,
    project: dict | None = None,
) -> Path:
    """Build the questions workbook.

    Accepted call styles::

        build_questions_workbook("prj_x")                    # full project workbook
        build_questions_workbook(rows=[...], project={...})   # questions only
        build_questions_workbook("prj_x", session=s, out_path=p)
    """
    first = project_id_or_rows
    if isinstance(first, (list, tuple)) and rows is None:
        rows = first
        first = None

    project_id = first if isinstance(first, str) and first else None
    if project_id is None and project:
        project_id = project.get("project_id")

    def _minimal_data() -> dict:
        return {
            "project": project or {"project_id": "project", "name": "Project"},
            "stats": {},
            "documents": [],
            "assets": [],
            "trials": [],
            "evidence": [],
            "findings": [],
            "findings_history": [],
            "questions": list(rows or []),
            "coverage": [],
            "generated_at": _now(),
        }

    if data is None:
        rows_only = rows is not None and session is None
        if project_id and not rows_only:
            if session is not None:
                data = collect_export_data(session, project_id)
            else:
                with store.session_scope() as own_session:
                    data = collect_export_data(own_session, project_id)
        else:
            data = _minimal_data()
    if rows is not None:
        data = dict(data)
        data["questions"] = sort_questions(list(rows))
        data["priority_counts"] = _count_by(data["questions"], "priority", PRIORITY_ORDER)

    if out_path is None and project_id:
        out_path = export_filename(project_id, "questions")
    return build_workbook(data, out_path)


# ---------------------------------------------------------------------------
# Shared report helpers
# ---------------------------------------------------------------------------


def key_findings(data: dict, limit: int = 3) -> list[dict]:
    findings = data.get("findings") or []
    return [f for f in findings if str(f.get("severity")) in {"critical", "high"}][:limit] or findings[:limit]


def top_evidence_rows(data: dict, limit: int = 40) -> list[list[str]]:
    """Efficacy + safety evidence for the clinical matrix table."""
    efficacy = {"orr", "dcr", "pfs", "os", "hr", "dor", "cr", "pr"}
    safety = {"sae", "grade3_ae", "any_ae", "treatment_related_death", "discontinuation", "aesi"}
    rows: list[list[str]] = []
    for ev in data.get("evidence") or []:
        field = str(ev.get("field_name") or "")
        if field not in efficacy | safety:
            continue
        rows.append(
            [
                _txt(field).upper(),
                value_display(ev),
                _txt(ev.get("population")) or DEFAULT_DASH,
                _txt(ev.get("arm")) or DEFAULT_DASH,
                _txt(ev.get("data_cutoff")) or DEFAULT_DASH,
                _txt(ev.get("assessment_method")) or DEFAULT_DASH,
                _txt(ev.get("document_name") or ev.get("filename")),
                str(int(ev.get("page") or 0)),
                truncate(ev.get("quote"), 160),
            ]
        )
        if len(rows) >= limit:
            break
    return rows


def coverage_gaps(data: dict) -> list[dict]:
    return [
        item
        for item in data.get("coverage") or []
        if str(item.get("status")) not in {"sufficient", "not_applicable"}
    ]


def review_summary(data: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for q in data.get("questions") or []:
        key = f"{_txt(q.get('status')) or 'draft'} / {_txt(q.get('review_status')) or 'pending'}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _md_escape(text: Any) -> str:
    return _txt(text).replace("|", "\\|").replace("\n", " ").strip()


def _md_cell(text: Any, limit: int = 220) -> str:
    return _md_escape(truncate(text, limit)) or DEFAULT_DASH


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


WHY_IT_MATTERS = {
    "data_inconsistency": "Inconsistent reported values can misstate the asset profile and undermine valuation and deal assumptions.",
    "version_difference": "Different versions/data cutoffs mean the most favourable number may not be the most current.",
    "definition_difference": "Population or endpoint-definition differences make cross-document comparisons unreliable.",
    "comparability_unclear": "Missing comparability dimensions prevent a reliable read-across between reported numbers.",
    "evidence_gap": "The package does not support a required diligence dimension, so the associated risk stays unquantified.",
    "incomplete_evidence": "Incomplete evidence limits the ability to verify the reported claim.",
    "technical_concern": "The technical concern may affect the reliability of the submitted data.",
}

DECISION_DISCLAIMER = (
    "No automated Go/No-Go recommendation is made; the brief summarises evidence "
    "and open items for human review."
)


# Clinical importance ordering for the one-page Decision Brief, per product spec:
# ORR > DCR > PFS > OS > DoR > sample size > grade>=3 AE > SAE > any-AE.
HIGHLIGHT_FIELDS = [
    "orr", "dcr", "pfs", "os", "dor",
    "n_itt", "sample_size", "n_safety",
    "grade3_ae", "sae", "any_ae",
    # secondary clinical read-outs (only when nothing above is available)
    "hr", "cr", "pr", "treatment_related_death", "discontinuation",
    "age_median", "male_pct",
]
# Identifier / design / statistics metadata: never used as evidence highlights.
HIGHLIGHT_SKIP = {
    "nct_id", "trial_key", "phase", "indication", "asset_name", "design",
    "arms", "randomization", "blinding", "modality", "target", "mechanism",
    "data_cutoff", "response_criteria", "endpoint_def", "sap_version",
    "multiplicity", "missing_data_method", "subgroup_analysis",
    "protocol_deviation", "audit_summary", "follow_up",
}


# Narrative sources beat data listings/tables for Decision Brief citations.
SOURCE_TYPE_PRIORITY = {
    "CSR": 0, "SAP": 1, "IB": 2, "SAFETY": 3, "PROTOCOL": 4, "DISPOSITION": 5,
    "REGULATORY": 6, "PRECLINICAL": 7, "CMC": 8, "PATENT": 9, "COMMERCIAL": 10,
    "OTHER": 11,
}


def source_document_rank(ev: dict) -> int:
    """Rank candidate citation sources: CSR > SAP > IB > SAFETY > PROTOCOL > XLSX/PPTX."""
    filename = _txt(ev.get("filename")).lower()
    if filename.endswith((".xlsx", ".xls", ".csv")):
        return 20
    if filename.endswith((".pptx", ".ppt")):
        return 21
    doc_type = _txt(ev.get("document_type") or "").upper()
    return SOURCE_TYPE_PRIORITY.get(doc_type, 15)


def evidence_support_highlights(data: dict, limit: int = 4) -> list[dict]:
    """2-3 distinct, best-supported evidence items (verified first, then confidence).

    Headline efficacy/safety fields are preferred; pure metadata fields (NCT id,
    cutoff, response criteria, ...) are skipped when anything better exists.
    """
    evidence = list(data.get("evidence") or [])
    if not evidence:
        return []
    latest_documents = data.get("latest_documents")
    if latest_documents is None:
        latest_documents = [d for d in data.get("documents") or [] if d.get("is_latest", True)]
    latest_ids = {d.get("document_id") for d in latest_documents}

    def field_rank(field: str) -> int:
        return HIGHLIGHT_FIELDS.index(field) if field in HIGHLIGHT_FIELDS else len(HIGHLIGHT_FIELDS)

    def sort_key(ev: dict):
        return (
            1 if _txt(ev.get("field_name")) in HIGHLIGHT_SKIP else 0,
            field_rank(_txt(ev.get("field_name"))),
            # Current package first: never quote a superseded document in the brief
            # when the same field is available from a latest version.
            0 if ev.get("document_id") in latest_ids else 1,
            # Prefer the narrative source over a data listing/table for citations.
            source_document_rank(ev),
            0 if str(ev.get("verification_status")) == "verified" else 1,
            -(float(ev.get("confidence") or 0)),
            _txt(ev.get("filename")),
        )

    ranked = sorted(evidence, key=sort_key)
    picks: list[dict] = []
    seen: set[str] = set()
    for ev in ranked:
        field = _txt(ev.get("field_name"))
        if field in seen or field in HIGHLIGHT_SKIP:
            continue
        seen.add(field)
        picks.append(ev)
        if len(picks) >= limit:
            break
    if not picks:  # everything was metadata: fall back to distinct fields anyway
        for ev in ranked:
            field = _txt(ev.get("field_name"))
            if field in seen:
                continue
            seen.add(field)
            picks.append(ev)
            if len(picks) >= limit:
                break
    return picks


def open_risks(data: dict, limit: int = 5) -> list[dict]:
    """Top open findings ordered by severity then finding category importance."""
    findings = [
        finding
        for finding in data.get("findings") or []
        if str(finding.get("status")) not in {"dismissed", "resolved"}
    ]
    findings.sort(
        key=lambda finding: (
            _rank(finding.get("severity"), SEVERITY_ORDER),
            _rank(finding.get("finding_type"), FINDING_TYPE_PRIORITY),
            _txt(finding.get("created_at")),
            _txt(finding.get("finding_id")),
        )
    )
    return findings[:limit]


def doc_version_rows(data: dict) -> list[list[str]]:
    """Document version/history table rows (filename, type, version, state, pages)."""
    rows = []
    for document in data.get("documents") or []:
        latest = bool(document.get("is_latest", True))
        rows.append(
            [
                _txt(document.get("filename")),
                _txt(document.get("doc_type")),
                _txt(document.get("version")),
                "latest" if latest else "superseded",
                str(int(document.get("page_count") or 0)),
            ]
        )
    return rows


def decision_brief_data(data: dict) -> dict:
    """Assemble the Decision Brief content from store rows (factual, no recommendation)."""
    project = data.get("project") or {}
    stats = data.get("stats") or {}
    project_id = _txt(project.get("project_id"))
    context = [
        ["Project", _txt(project.get("name")) or DEFAULT_DASH],
        ["Asset", _txt(project.get("asset_name")) or DEFAULT_DASH],
        ["Indication", _txt(project.get("indication")) or DEFAULT_DASH],
        ["Phase", _txt(project.get("phase")) or DEFAULT_DASH],
        ["Organization", _txt(project.get("org_id")) or DEFAULT_DASH],
        [
            "Package",
            f"{stats.get('documents', len(data.get('latest_documents') or data.get('documents') or []))} latest documents · "
            f"{data.get('pages_latest') or stats.get('pages', 0)} pages · "
            f"{stats.get('evidence', len(data.get('evidence') or []))} evidence items",
        ],
        ["Run date", _txt(data.get("generated_at"))],
    ]
    highlights = []
    for ev in evidence_support_highlights(data):
        field = _txt(ev.get("field_name")).replace("_", " ").upper()
        dims = ", ".join(
            value for value in (_txt(ev.get("population")), _txt(ev.get("arm"))) if value
        )
        dimensions = f" ({dims})" if dims else ""
        highlights.append(
            f"**{field} {value_display(ev)}**{dimensions} — supported by "
            f"*{_txt(ev.get('document_name') or ev.get('filename'))}*, page {int(ev.get('page') or 0)} "
            f"“{truncate(ev.get('quote'), 160)}”."
        )
    if not highlights:
        highlights.append(
            "No extracted evidence is available yet; evidence extraction has not produced "
            "verifiable values for this package."
        )

    risks = [
        {
            "severity": _txt(finding.get("severity")),
            "title": _txt(finding.get("title")),
            "why": WHY_IT_MATTERS.get(
                _txt(finding.get("finding_type")),
                "Unresolved diligence item that requires seller follow-up before reliance.",
            ),
        }
        for finding in open_risks(data)
    ]

    questions = [
        {
            "priority": _txt(question.get("priority")),
            "owner": _txt(question.get("owner")),
            "question": _txt(question.get("question")),
        }
        for question in (data.get("questions") or [])[:5]
    ]

    # Package-level gaps (from coverage analysis) vs finding-specific
    # clarification needs; identical strings are de-duplicated.
    missing_coverage: list[str] = []
    seen_coverage: set[str] = set()
    for item in coverage_gaps(data)[:10]:
        dimension = _txt(item.get("dimension")) or "General"
        requirement = _txt(item.get("requirement"))
        key = f"{dimension}|{requirement}".lower()
        if key in seen_coverage:
            continue
        seen_coverage.add(key)
        missing_coverage.append(
            f"**{dimension}** — {requirement} "
            f"(coverage: {_txt(item.get('status'))}, priority: {_txt(item.get('priority'))})"
        )

    missing_findings: list[dict] = []
    seen_missing: set[str] = set()
    for finding in data.get("findings") or []:
        items: list[str] = []
        for raw in finding.get("missing_information") or []:
            text = _txt(raw).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen_missing:
                continue
            seen_missing.add(key)
            items.append(text)
            if len(items) >= 3:
                break
        if items:
            missing_findings.append(
                {
                    "title": _txt(finding.get("title")),
                    "severity": _txt(finding.get("severity")),
                    "items": items,
                }
            )
        if len(missing_findings) >= 6:
            break

    return {
        "project_id": project_id,
        "context": context,
        "highlights": highlights,
        "risks": risks,
        "questions": questions,
        "missing_coverage": missing_coverage,
        "missing_findings": missing_findings,
        "disclaimer": DECISION_DISCLAIMER,
    }


def _md_overview_appendix(overview: dict) -> list[str]:
    """Human-readable appendix for the interactive Asset 360 dashboard."""
    if not overview:
        return []
    disc = overview.get("discovery") or {}
    evaluation = overview.get("evaluation") or {}
    overall = evaluation.get("overall") or {}
    lines: list[str] = []
    add = lines.append
    add("")
    add("## Appendix A. Asset Discovery & Evaluation")
    add("")
    add(
        "> 药物资产发现与评估汇总。评估分数由证据完整度、可核验性、一致性、覆盖度与数据成熟度规则化计算；"
        "**不构成投资建议，也不自动给出 Go/No-Go 结论**。"
    )
    add("")
    add("### A.1 Discovered asset")
    add("")
    add("| Field | Value | Source |")
    add("|---|---|---|")
    for attr in disc.get("attributes") or []:
        source = "—"
        if attr.get("document"):
            source = f"{_md_cell(attr['document'])} p.{attr.get('page') or '—'}"
        elif attr.get("source") == "asset_registry":
            source = "asset registry"
        add(
            f"| {_md_cell(attr.get('label_zh'))} / {_md_cell(attr.get('label_en'))} "
            f"| {_md_cell(attr.get('value'))} | {source} |"
        )
    add("")
    add("### A.2 Key clinical facts")
    add("")
    add("| Fact | Value | Population / cutoff / method | Source |")
    add("|---|---|---|---|")
    for fact in disc.get("key_facts") or []:
        meta = " · ".join(
            str(v) for v in (fact.get("population"), fact.get("cutoff"), fact.get("assessment_method")) if v
        ) or "—"
        add(
            f"| {_md_cell(fact.get('label_zh'))} | {_md_cell(fact.get('value_display'))} "
            f"| {_md_cell(meta)} | {_md_cell(fact.get('document'))} p.{fact.get('page') or '—'} |"
        )
    add("")
    add("### A.3 Evaluation scorecard")
    add("")
    add("| Dimension | Score / 100 | Weight | Basis |")
    add("|---|---:|---:|---|")
    for dim in evaluation.get("dimensions") or []:
        add(
            f"| {_md_cell(dim.get('label_zh'))} / {_md_cell(dim.get('label_en'))} "
            f"| {dim.get('score')} | {dim.get('weight')} | {_md_cell(dim.get('basis'), 180)} |"
        )
    add(
        f"| **{_md_cell(overall.get('title_zh'))}** | **{overall.get('score')}** | **1.00** "
        f"| **{_md_cell(overall.get('label_zh'))}** |"
    )
    add("")
    cap = overall.get("readiness_cap")
    if cap:
        add(
            f"> 由于存在 {overall.get('critical_contradictions')} 项 critical 跨文件矛盾，"
            f"尽调就绪度封顶为 {cap}。"
        )
        add("")
    add(
        f"风险暴露：**{_md_cell((evaluation.get('risk') or {}).get('label_zh'))}**"
        f"（A/B/C/U 分类：{(evaluation.get('consistency') or {}).get('A', 0)}/"
        f"{(evaluation.get('consistency') or {}).get('B', 0)}/"
        f"{(evaluation.get('consistency') or {}).get('C', 0)}/"
        f"{(evaluation.get('consistency') or {}).get('U', 0)}）。"
    )
    add("")
    add("### A.4 Document inventory")
    add("")
    add("| Document type | Documents | Pages |")
    add("|---|---:|---:|")
    for row in (disc.get("documents") or {}).get("by_type") or []:
        add(f"| {_md_cell(row.get('doc_type'))} | {row.get('documents')} | {row.get('pages')} |")
    add("")
    add(
        "Nonclinical / regulatory / patent / commercial documents are listed as unstructured "
        "previews on the dashboard and are not part of the extracted evidence matrix."
    )
    add("")
    return lines


def render_markdown_report(data: dict) -> str:
    """Professional Markdown due-diligence report derived from store rows."""
    data = sanitize_data(data)
    project = data.get("project") or {}
    stats = data.get("stats") or {}
    findings = data.get("findings") or []
    questions = data.get("questions") or []
    coverage = data.get("coverage") or []
    gaps = coverage_gaps(data)
    severity = data.get("severity_counts") or {}
    priority = data.get("priority_counts") or {}
    evidence_count = len(data.get("evidence") or [])
    docs = data.get("documents") or []
    brief = decision_brief_data(data)

    lines: list[str] = []
    add = lines.append

    add(f"# {_txt(project.get('name')) or 'Asset'} — Asset Due Diligence Report")
    add("")
    add(f"**Asset:** {_txt(project.get('asset_name')) or DEFAULT_DASH}  ")
    add(f"**Indication:** {_txt(project.get('indication')) or DEFAULT_DASH}  ")
    add(f"**Phase:** {_txt(project.get('phase')) or DEFAULT_DASH}  ")
    add(f"**Project:** `{_txt(project.get('project_id'))}` · **Organization:** {_txt(project.get('org_id'))}  ")
    add(
        f"**Prepared:** {data.get('generated_at')} · **Package:** "
        f"{stats.get('documents', len(docs))} documents · {data.get('pages_latest') or stats.get('pages', 0)} pages · "
        f"{stats.get('evidence', evidence_count)} evidence items · "
        f"{findings_count_text(data)} · {stats.get('questions', len(questions))} questions"
    )
    add("")
    add("**Generated by HiJenny** — evidence-first pharmaceutical due diligence platform  ")
    add("")
    add(
        "> **AI-generated analysis — requires expert human review.** This report is a "
        "due-diligence working aid produced by an automated evidence pipeline; it is not "
        "legal, medical, regulatory or investment advice and is not a substitute for "
        "expert due diligence. Every finding and question must be verified by a qualified "
        "reviewer against the source documents before any decision or communication."
    )
    add("")

    # -- 1. Decision Brief --------------------------------------------------
    add("## 1. Decision Brief")
    add("")
    add(
        "One-page summary for BD heads and investment committee: what the package shows, "
        "what is still open, and what the seller must answer. It summarises evidence only."
    )
    add("")
    for update in data.get("bd_updates", []):
        add("### BD 复核更新")
        for key, value in update:
            add(f"- {key} · {_md_cell(value, 4000)}")
        add("")
    add("### Asset & transaction context")
    add("")
    add("| Field | Value |")
    add("|---|---|")
    for label, value in brief["context"]:
        add(f"| {_md_escape(label)} | {_md_cell(value, 180)} |")
    add("")
    add("### What the evidence supports")
    add("")
    for bullet in brief["highlights"]:
        add(f"- {bullet}")
    add("")
    add("### Top open risks")
    add("")
    if brief["risks"]:
        add("| # | Severity | Risk | Why it matters |")
        add("|---|---|---|---|")
        for idx, risk in enumerate(brief["risks"], start=1):
            add(
                f"| {idx} | {_md_escape(risk['severity'])} | {_md_cell(risk['title'], 160)} | "
                f"{_md_cell(risk['why'], 200)} |"
            )
    else:
        add("_No open consistency or completeness risks have been generated yet._")
    add("")
    add("### Top seller questions")
    add("")
    if brief["questions"]:
        add("| Priority | Owner | Question |")
        add("|---|---|---|")
        for question in brief["questions"]:
            add(
                f"| {_md_escape(question['priority'])} | {_md_cell(question['owner'], 60)} | "
                f"{_md_cell(question['question'], 300)} |"
            )
    else:
        add("_No seller questions have been generated yet._")
    add("")
    add("### Evidence still missing")
    add("")
    if brief["missing_coverage"] or brief["missing_findings"]:
        if brief["missing_coverage"]:
            add("**Package-level gaps (coverage)**")
            add("")
            for item in brief["missing_coverage"]:
                add(f"- {item}")
            add("")
        if brief["missing_findings"]:
            add("**Finding-specific clarification needs**")
            add("")
            for group in brief["missing_findings"]:
                add(
                    f"- **{_md_escape(group['title'])}** "
                    f"({_md_escape(group['severity'])} severity) — "
                    + "; ".join(_md_escape(item) for item in group["items"])
                )
            add("")
    else:
        add("_No outstanding coverage gaps or finding-specific clarification needs were flagged for the supplied package._")
        add("")
    add("### Decision status")
    add("")
    add(f"**{brief['disclaimer']}**")
    add("")

    # -- 2. Executive Summary ----------------------------------------------
    add("## 2. Executive Summary")
    add("")
    add("**Key points**")
    add("")
    doc_types = data.get("docs_by_type") or {}
    type_text = ", ".join(f"{k}: {v}" for k, v in doc_types.items()) or "no classified documents"
    add(
        f"1. **Package assembled.** {stats.get('documents', len(docs))} latest document(s), "
        f"{data.get('pages_latest') or stats.get('pages', 0)} parsed page(s) and {stats.get('evidence', evidence_count)} "
        f"extracted evidence item(s); document mix — {type_text}."
    )
    top = key_findings(data, limit=3)
    counts = finding_counts(data)
    active_summary = findings_count_text(data)
    if top:
        add(
            "2. **Principal risks.** "
            + active_summary
            + (
                f", including {severity.get('critical', 0)} critical and {severity.get('high', 0)} high-severity item(s). "
                if findings
                else ". "
            )
            + "Highest-priority items: "
            + "; ".join(f"{_txt(f.get('title'))} ({_txt(f.get('severity'))})" for f in top)
            + "."
        )
    else:
        add(
            f"2. **Principal risks.** {active_summary}. No active consistency or completeness "
            "findings were generated for this package."
        )
    if gaps:
        add(
            f"3. **Evidence gaps.** {len(gaps)} of {len(coverage)} coverage requirement(s) are not "
            "sufficiently supported by the package; see Section 6. "
            f"{len(questions)} seller question(s) were drafted ({priority.get('critical', 0)} critical, "
            f"{priority.get('high', 0)} high priority) to close them."
        )
    else:
        add(
            f"3. **Evidence gaps.** Coverage checks recorded {len(coverage)} requirement(s) with no "
            f"outstanding gaps flagged. {len(questions)} seller question(s) were drafted."
        )
    add("")
    add("### Risk counts")
    add("")
    add("| Severity | Findings |")
    add("|---|---:|")
    for sev in SEVERITY_ORDER:
        if sev in severity:
            add(f"| {sev.capitalize()} | {severity[sev]} |")
    add(f"| **Total** | **{counts['active']}** |")
    add("")
    if counts["history"]:
        add(
            f"_Resolved/dismissed history: {counts['history']} finding(s) are excluded from these "
            "counts and listed in Section 5._"
        )
        add("")
    add("| Priority | Seller questions |")
    add("|---|---:|")
    for prio in PRIORITY_ORDER:
        if prio in priority:
            add(f"| {prio.capitalize()} | {priority[prio]} |")
    add(f"| **Total** | **{len(questions)}** |")
    add("")

    # -- 3. Asset Profile ---------------------------------------------------
    add("## 3. Asset Profile")
    add("")
    add("| " + " | ".join(ASSET_PROFILE_HEADERS) + " |")
    add("|" + "|".join(["---"] * len(ASSET_PROFILE_HEADERS)) + "|")
    profile_rows = [align_row(row, len(ASSET_PROFILE_HEADERS)) for row in asset_profile_rows(data)]
    if profile_rows:
        for row in profile_rows:
            add("| " + " | ".join(_md_cell(v, 100) for v in row) + " |")
    else:
        add("| " + " | ".join([DEFAULT_DASH] * len(ASSET_PROFILE_HEADERS)) + " |")
    add("")
    if data.get("trials"):
        add("**Clinical trials**")
        add("")
        add("| Trial | NCT | Phase | Design | Primary endpoint |")
        add("|---|---|---|---|---|")
        for trial in data["trials"]:
            add(
                "| "
                + " | ".join(
                    [
                        _md_cell(trial.get("title") or trial.get("internal_id"), 80),
                        _md_cell(trial.get("nct_id"), 30),
                        _md_cell(trial.get("phase"), 20),
                        _md_cell(trial.get("design"), 120),
                        _md_cell(trial.get("primary_endpoint"), 120),
                    ]
                )
                + " |"
            )
        add("")
    add("### Document version history")
    add("")
    add("Version and supersede status of every document in the package, so round-2 uploads are traceable.")
    add("")
    history = doc_version_rows(data)
    if history:
        add("| Filename | Type | Version | State | Pages |")
        add("|---|---|---|---|---:|")
        for row in history:
            add("| " + " | ".join(_md_cell(value, 120) for value in row) + " |")
    else:
        add("_No documents registered yet._")
    add("")

    # -- 4. Clinical evidence matrix ---------------------------------------
    add("## 4. Clinical Evidence Matrix")
    add("")
    add(
        "Top efficacy and safety evidence items extracted from the package. "
        "Values are only comparable when trial, arm, population, data cutoff and assessment "
        "method agree — missing dimensions are shown as “—”. "
        f"Full provenance: {evidence_count} extracted evidence item(s) in the accompanying workbook."
    )
    add("")
    matrix = top_evidence_rows(data)
    if matrix:
        add("| Field | Value | Population | Arm | Data cutoff | Method | Document | Page | Quote |")
        add("|---|---|---|---|---|---|---|---:|---|")
        for row in matrix:
            add("| " + " | ".join(_md_cell(v) for v in row) + " |")
    else:
        add("_No efficacy or safety evidence has been extracted yet._")
    add("")

    # -- 5. Findings --------------------------------------------------------
    add("## 5. Key Findings")
    add("")
    if not findings:
        add("_No findings have been generated for this package yet._")
        add("")
    else:
        by_id = evidence_index(data)
        groups: dict[str, list[dict]] = {}
        for finding in findings:
            groups.setdefault(str(finding.get("severity") or "info"), []).append(finding)
        for sev in SEVERITY_ORDER:
            group = groups.get(sev)
            if not group:
                continue
            add(f"### {sev.capitalize()} severity ({len(group)})")
            add("")
            for finding in group:
                add(f"#### {_txt(finding.get('title'))}")
                add("")
                add(f"**Type:** {_txt(finding.get('finding_type'))} · **Domain:** {_txt(finding.get('domain'))} · "
                    f"**Status:** {_txt(finding.get('status'))} · **Rule:** {_txt(finding.get('rule_id')) or DEFAULT_DASH}")
                add("")
                add(_txt(finding.get("explanation")) or DEFAULT_DASH)
                add("")
                if finding.get("possible_causes"):
                    add(f"**Possible causes:** {_joined(finding.get('possible_causes'))}")
                    add("")
                if finding.get("missing_information"):
                    add(f"**Missing information:** {_joined(finding.get('missing_information'))}")
                    add("")
                cited = [by_id[eid] for eid in finding.get("evidence_ids") or [] if eid in by_id]
                if cited:
                    add("**Evidence citations:**")
                    add("")
                    for ev in cited:
                        add(
                            f"- *{_txt(ev.get('document_name') or ev.get('filename'))}*, page "
                            f"{int(ev.get('page') or 0)} — “{_txt(ev.get('quote'))}” "
                            f"({_txt(ev.get('verification_status'))})"
                        )
                    add("")
                recommended = finding.get("recommended_action") or (
                    "Request the underlying source data and written confirmation from the seller "
                    "to reconcile this item."
                )
                add(f"**Recommended verification:** {_txt(recommended)}")
                add("")

    history_findings = data.get("findings_history") or []
    if history_findings:
        add(f"### Resolved findings (history) — {len(history_findings)}")
        add("")
        add("Not included in the active risk counts above; retained for auditability.")
        add("")
        for finding in history_findings:
            add(
                f"- **{_txt(finding.get('title'))}** — {_txt(finding.get('severity'))} severity, "
                f"{_txt(finding.get('finding_type'))}, status `{_txt(finding.get('status'))}`"
            )
        add("")

    # -- 6. Evidence gaps ---------------------------------------------------
    add("## 6. Evidence Gaps")
    add("")
    if gaps:
        add(
            f"{len(gaps)} of {len(coverage)} coverage requirement(s) are not sufficiently "
            "supported by the documents supplied. These are diligence blockers until resolved."
        )
        add("")
        add("| Dimension | Requirement | Status | Priority | Note |")
        add("|---|---|---|---|---|")
        for item in gaps:
            add(
                "| "
                + " | ".join(
                    [
                        _md_cell(item.get("dimension"), 60),
                        _md_cell(item.get("requirement"), 160),
                        _md_cell(item.get("status"), 30),
                        _md_cell(item.get("priority"), 30),
                        _md_cell(item.get("note"), 160),
                    ]
                )
                + " |"
            )
    else:
        add("_No evidence gaps were flagged by the coverage analysis._")
    add("")

    # -- 7. Seller questions ------------------------------------------------
    add("## 7. Seller Questions")
    add("")
    if questions:
        add("| Priority | Owner | Category | Question | Rationale | Required documents | Status |")
        add("|---|---|---|---|---|---|---|")
        for q in questions:
            add(
                "| "
                + " | ".join(
                    [
                        _md_cell(q.get("priority"), 20),
                        _md_cell(q.get("owner"), 60),
                        _md_cell(q.get("category"), 40),
                        _md_cell(q.get("question"), 320),
                        _md_cell(q.get("rationale"), 320),
                        _md_cell(_joined(q.get("required_documents")), 160),
                        _md_cell(q.get("status"), 30),
                    ]
                )
                + " |"
            )
    else:
        add("_No seller questions have been generated yet._")
    add("")

    # -- 8. Review + limitations --------------------------------------------
    add("## 8. Human Review & Limitations")
    add("")
    add("**Review status**")
    add("")
    review = review_summary(data)
    if review:
        add("| Question status / review status | Count |")
        add("|---|---:|")
        for key, count in review.items():
            add(f"| {_md_escape(key)} | {count} |")
    else:
        add("_No questions are awaiting review._")
    add("")
    add("**Limitations**")
    add("")
    add("- The analysis is generated by automated rule-based extraction and consistency checks. "
        "It may miss evidence, mis-read tables, or over/under-state severity.")
    add("- Only the documents supplied in this package were analysed; missing documents cannot be "
        "assessed, and absence of a finding is not evidence of absence of risk.")
    add("- Numeric comparability is limited by the dimensions available in the source (trial, arm, "
        "population, data cutoff, endpoint definition, assessment method).")
    add("- Analysis is based on the data cutoff dates stated in the documents; later data may change "
        "the conclusions.")
    add("- Findings and questions require review, correction and sign-off by qualified clinical, "
        "safety, biostatistics, regulatory and legal reviewers before external use.")
    add("")
    add(
        "**Disclaimer.** This document was produced by HiJenny from the supplied due-diligence "
        "package. It is an AI-generated working aid, not a substitute for expert due diligence, and "
        "must not be relied upon as legal, medical, regulatory, tax or investment advice. All quoted "
        "text is believed to be verbatim from the source documents, but the source documents remain "
        "the authoritative record."
    )
    add("")
    lines.extend(_md_overview_appendix(data.get("overview") or {}))
    return "\n".join(lines)


def write_markdown_report(data: dict, out_path: str | Path | None = None) -> Path:
    project_id = (data.get("project") or {}).get("project_id") or "project"
    target = Path(out_path) if out_path else export_filename(project_id, "md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown_report(data), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Word report
# ---------------------------------------------------------------------------


def _docx_imports():
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    return {
        "Document": Document,
        "WD_SECTION": WD_SECTION,
        "WD_TABLE_ALIGNMENT": WD_TABLE_ALIGNMENT,
        "WD_ALIGN_PARAGRAPH": WD_ALIGN_PARAGRAPH,
        "WD_BREAK": WD_BREAK,
        "OxmlElement": OxmlElement,
        "qn": qn,
        "Inches": Inches,
        "Pt": Pt,
        "RGBColor": RGBColor,
    }


def _shade(cell, hex_color: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _set_cell_text(cell, text: str, *, bold: bool = False, size: float = 8.5, color: str | None = None) -> None:
    from docx.shared import RGBColor

    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(_txt(sanitize_cell(text)))
    run.bold = bold
    run.font.size = _docx_imports()["Pt"](size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def _docx_table(doc, headers: Sequence[str], rows: Sequence[Sequence[Any]], *, widths=None, font_size=8.5):
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Inches

    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    header_cells = table.rows[0].cells
    for idx, header in enumerate(headers):
        _set_cell_text(header_cells[idx], header, bold=True, size=font_size, color="FFFFFF")
        _shade(header_cells[idx], NAVY)
    for row in rows:
        cells = table.add_row().cells
        for idx in range(len(headers)):
            value = row[idx] if idx < len(row) else ""
            if isinstance(value, (list, tuple)):
                value = "; ".join(_txt(v) for v in value if v not in (None, ""))
            _set_cell_text(cells[idx], value, size=font_size)
    if widths:
        for row in table.rows:
            for idx, width in enumerate(widths):
                if idx < len(row.cells):
                    row.cells[idx].width = Inches(width)
    return table


def _docx_bullets(doc, items: Sequence[str]) -> None:
    for item in items:
        if item:
            doc.add_paragraph(_txt(item), style="List Bullet")


def _docx_kv_table(doc, pairs: Sequence[tuple[str, Any]], widths=(1.7, 4.6)) -> None:
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Inches

    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for label, value in pairs:
        cells = table.add_row().cells
        _set_cell_text(cells[0], label, bold=True, size=9)
        _shade(cells[0], NAVY_LIGHT)
        _set_cell_text(cells[1], value, size=9)
        cells[0].width = Inches(widths[0])
        cells[1].width = Inches(widths[1])


def _docx_footer(doc) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    section = doc.sections[0]
    footer = section.footer
    paragraph = footer.paragraphs[0]
    paragraph.text = ""
    run = paragraph.add_run("HiJenny · Confidential — AI-generated, requires human review · Page ")
    run.font.size = _docx_imports()["Pt"](8)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    paragraph._p.append(fld)


def build_report_docx(data: dict, out_path: str | Path | None = None) -> Path:
    """Render the professional Word report from a data snapshot. Returns path."""
    data = sanitize_data(data)
    imp = _docx_imports()
    Document, Pt, RGBColor, WD_ALIGN_PARAGRAPH, WD_BREAK, WD_SECTION = (
        imp["Document"],
        imp["Pt"],
        imp["RGBColor"],
        imp["WD_ALIGN_PARAGRAPH"],
        imp["WD_BREAK"],
        imp["WD_SECTION"],
    )

    project = data.get("project") or {}
    stats = data.get("stats") or {}
    findings = data.get("findings") or []
    findings_history = data.get("findings_history") or []
    counts = finding_counts(data)
    questions = data.get("questions") or []
    coverage = data.get("coverage") or []
    gaps = coverage_gaps(data)
    severity = data.get("severity_counts") or {}
    priority = data.get("priority_counts") or {}

    project_id = project.get("project_id") or "project"
    target = Path(out_path) if out_path else export_filename(project_id, "docx")
    target.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    core = doc.core_properties
    core.title = "HiJenny — Asset Due Diligence Report"
    core.author = "HiJenny"
    core.comments = "Evidence-first pharmaceutical due diligence report. AI-generated; requires expert human review."
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)
    for section in doc.sections:
        section.top_margin = imp["Inches"](0.7)
        section.bottom_margin = imp["Inches"](0.7)
        section.left_margin = imp["Inches"](0.75)
        section.right_margin = imp["Inches"](0.75)

    # -- Title page ---------------------------------------------------------
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = title.add_run("ASSET DUE DILIGENCE REPORT")
    run.bold = True
    run.font.size = Pt(26)
    run.font.color.rgb = RGBColor.from_string(NAVY)
    brand = doc.add_paragraph()
    brand_run = brand.add_run("HIJENNY · EVIDENCE-FIRST DUE DILIGENCE")
    brand_run.bold = True
    brand_run.font.size = Pt(9)
    brand_run.font.color.rgb = RGBColor.from_string("6F86A8")
    subtitle = doc.add_paragraph()
    run = subtitle.add_run(_txt(project.get("name")) or "Project")
    run.bold = True
    run.font.size = Pt(16)
    run.font.color.rgb = RGBColor.from_string("2E75B6")
    doc.add_paragraph(
        "Evidence-first analysis of the supplied due-diligence package — documents, pages, "
        "verbatim quotes, consistency findings and seller questions."
    )
    doc.add_paragraph("")
    _docx_kv_table(
        doc,
        [
            ("Project ID", _txt(project.get("project_id"))),
            ("Organization", _txt(project.get("org_id"))),
            ("Asset", _txt(project.get("asset_name")) or DEFAULT_DASH),
            ("Indication", _txt(project.get("indication")) or DEFAULT_DASH),
            ("Phase", _txt(project.get("phase")) or DEFAULT_DASH),
            ("Prepared", _txt(data.get("generated_at"))),
            (
                "Package",
                f"{stats.get('documents', len(data.get('documents') or []))} documents · "
                f"{data.get('pages_latest') or stats.get('pages', 0)} pages · "
                f"{stats.get('evidence', len(data.get('evidence') or []))} evidence items",
            ),
        ],
    )
    doc.add_paragraph("")
    note = doc.add_paragraph()
    run = note.add_run("AI-GENERATED — REQUIRES EXPERT HUMAN REVIEW")
    run.bold = True
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor.from_string("C00000")
    doc.add_paragraph(
        "This report is a due-diligence working aid produced by an automated evidence pipeline. "
        "It is not a substitute for expert due diligence and must not be relied upon as legal, "
        "medical, regulatory or investment advice. All findings and questions must be verified "
        "against the source documents by qualified reviewers before external use."
    )
    doc.add_page_break()

    # -- 1. Decision Brief --------------------------------------------------
    brief = decision_brief_data(data)
    doc.add_heading("1. Decision Brief", level=1)
    doc.add_paragraph(
        "One-page summary for BD heads and investment committee: what the package shows, what is "
        "still open, and what the seller must answer. It summarises evidence only."
    )
    for update in data.get("bd_updates", []):
        doc.add_heading("BD 复核更新", level=2)
        _docx_kv_table(doc, update)
    doc.add_heading("Asset & transaction context", level=2)
    _docx_table(doc, ["Field", "Value"], brief["context"], widths=(1.7, 4.6), font_size=9)
    doc.add_heading("What the evidence supports", level=2)
    if brief["highlights"]:
        for bullet in brief["highlights"]:
            paragraph = doc.add_paragraph(style="List Bullet")
            run = paragraph.add_run(_txt(bullet).replace("**", "").replace("*", ""))
            run.font.size = Pt(9)
    else:  # pragma: no cover - defensive
        doc.add_paragraph("No extracted evidence is available yet.")
    doc.add_heading("Top open risks", level=2)
    if brief["risks"]:
        _docx_table(
            doc,
            ["#", "Severity", "Risk", "Why it matters"],
            [[idx, risk["severity"], risk["title"], risk["why"]] for idx, risk in enumerate(brief["risks"], start=1)],
            widths=(0.35, 0.8, 2.3, 2.85),
            font_size=8,
        )
    else:
        doc.add_paragraph("No open consistency or completeness risks have been generated yet.")
    doc.add_heading("Top seller questions", level=2)
    if brief["questions"]:
        _docx_table(
            doc,
            ["Priority", "Owner", "Question"],
            [[q["priority"], q["owner"], q["question"]] for q in brief["questions"]],
            widths=(0.8, 1.4, 4.1),
            font_size=8,
        )
    else:
        doc.add_paragraph("No seller questions have been generated yet.")
    doc.add_heading("Evidence still missing", level=2)
    if brief["missing_coverage"] or brief["missing_findings"]:
        if brief["missing_coverage"]:
            lead_paragraph = doc.add_paragraph()
            run = lead_paragraph.add_run("Package-level gaps (coverage)")
            run.bold = True
            run.font.size = Pt(9)
            for item in brief["missing_coverage"]:
                paragraph = doc.add_paragraph(style="List Bullet")
                run = paragraph.add_run(_txt(item).replace("**", ""))
                run.font.size = Pt(8.5)
        if brief["missing_findings"]:
            lead_paragraph = doc.add_paragraph()
            run = lead_paragraph.add_run("Finding-specific clarification needs")
            run.bold = True
            run.font.size = Pt(9)
            for group in brief["missing_findings"]:
                paragraph = doc.add_paragraph(style="List Bullet")
                run = paragraph.add_run(
                    f"{group['title']} ({group['severity']} severity): " + "; ".join(group["items"])
                )
                run.font.size = Pt(8.5)
    else:
        doc.add_paragraph(
            "No outstanding coverage gaps or finding-specific clarification needs were flagged "
            "for the supplied package."
        )
    doc.add_heading("Decision status", level=2)
    decision_paragraph = doc.add_paragraph()
    run = decision_paragraph.add_run(brief["disclaimer"])
    run.bold = True
    run.font.size = Pt(9.5)
    run.font.color.rgb = RGBColor.from_string("C00000")
    doc.add_page_break()

    # -- 2. Executive summary ----------------------------------------------
    doc.add_heading("2. Executive Summary", level=1)
    top = key_findings(data, limit=3)
    doc_types = data.get("docs_by_type") or {}
    type_text = ", ".join(f"{k}: {v}" for k, v in doc_types.items()) or "no classified documents"
    doc.add_paragraph(
        f"Package assembled: {stats.get('documents', len(data.get('documents') or []))} latest "
        f"document(s), {data.get('pages_latest') or stats.get('pages', 0)} parsed page(s) and "
        f"{stats.get('evidence', len(data.get('evidence') or []))} extracted evidence item(s) "
        f"({type_text})."
    )
    active_summary = findings_count_text(data)
    if top:
        doc.add_paragraph(
            f"Principal risks: {active_summary}, including "
            f"{severity.get('critical', 0)} critical and {severity.get('high', 0)} high-severity "
            "item(s)."
        )
        _docx_bullets(doc, [f"{_txt(f.get('title'))} — {_txt(f.get('severity'))}" for f in top])
    else:
        doc.add_paragraph(
            f"Principal risks: {active_summary}. No active consistency or completeness "
            "findings were generated yet."
        )
    if gaps:
        doc.add_paragraph(
            f"Evidence gaps: {len(gaps)} of {len(coverage)} coverage requirement(s) are not "
            f"sufficiently supported. {len(questions)} seller question(s) were drafted."
        )
    else:
        doc.add_paragraph(
            f"Evidence gaps: no outstanding gaps flagged across {len(coverage)} coverage "
            f"requirement(s). {len(questions)} seller question(s) were drafted."
        )

    doc.add_heading("Risk counts", level=2)
    risk_rows = [[sev.capitalize(), severity.get(sev, 0)] for sev in SEVERITY_ORDER if sev in severity]
    risk_rows.append(["Total", len(findings)])
    _docx_table(doc, ["Severity", "Findings"], risk_rows, widths=(2.2, 1.2))
    doc.add_paragraph("")
    prio_rows = [[p.capitalize(), priority.get(p, 0)] for p in PRIORITY_ORDER if p in priority]
    prio_rows.append(["Total", len(questions)])
    _docx_table(doc, ["Priority", "Seller questions"], prio_rows, widths=(2.2, 1.2))
    if counts["history"]:
        doc.add_paragraph(
            f"Resolved/dismissed history: {counts['history']} finding(s) are excluded from the "
            "counts above and listed in Section 5."
        )

    # -- 2. Asset profile ---------------------------------------------------
    doc.add_heading("3. Asset Profile", level=1)
    profile_rows = asset_profile_rows(data)
    if profile_rows:
        _docx_table(
            doc,
            ["Asset", "Modality", "Target", "Mechanism", "Indication", "Phase", "Trial", "NCT ID", "Design"],
            [align_row(row, 9) for row in profile_rows],
            widths=(1.0, 0.8, 0.9, 1.1, 0.9, 0.7, 0.8, 0.8, 1.2),
            font_size=8,
        )
    else:
        doc.add_paragraph("No asset profile could be extracted from the package.")
    if data.get("trials"):
        doc.add_heading("Clinical trials", level=2)
        _docx_table(
            doc,
            ["Trial", "NCT ID", "Phase", "Design", "Primary endpoint"],
            [
                [
                    _txt(t.get("title") or t.get("internal_id")),
                    _txt(t.get("nct_id")),
                    _txt(t.get("phase")),
                    _txt(t.get("design")),
                    _txt(t.get("primary_endpoint")),
                ]
                for t in data["trials"]
            ],
            widths=(1.6, 0.8, 0.6, 1.5, 1.6),
            font_size=8,
        )

    doc.add_heading("Document version history", level=2)
    doc.add_paragraph(
        "Version and supersede status of every document in the package, so round-2 uploads are traceable."
    )
    history_rows = doc_version_rows(data)
    if history_rows:
        _docx_table(
            doc,
            ["Filename", "Type", "Version", "State", "Pages"],
            history_rows,
            widths=(2.9, 1.0, 0.9, 1.0, 0.6),
            font_size=8,
        )
    else:
        doc.add_paragraph("No documents registered yet.")

    # -- 3. Clinical evidence matrix ---------------------------------------
    doc.add_heading("4. Clinical Evidence Matrix", level=1)
    doc.add_paragraph(
        "Top efficacy and safety evidence items. Values are comparable only when trial, arm, "
        "population, data cutoff and assessment method agree; “—” means the source did not "
        "state the dimension."
    )
    matrix = top_evidence_rows(data)
    if matrix:
        _docx_table(
            doc,
            ["Field", "Value", "Population", "Arm", "Cutoff", "Method", "Document", "Page", "Quote"],
            matrix,
            widths=(0.7, 0.6, 0.7, 0.7, 0.7, 0.7, 1.0, 0.4, 1.8),
            font_size=7.5,
        )
    else:
        doc.add_paragraph("No efficacy or safety evidence has been extracted yet.")

    # -- 4. Key findings ----------------------------------------------------
    doc.add_heading("5. Key Findings", level=1)
    if not findings:
        doc.add_paragraph("No findings have been generated for this package yet.")
    else:
        by_id = evidence_index(data)
        groups: dict[str, list[dict]] = {}
        for finding in findings:
            groups.setdefault(str(finding.get("severity") or "info"), []).append(finding)
        for sev in SEVERITY_ORDER:
            group = groups.get(sev)
            if not group:
                continue
            doc.add_heading(f"{sev.capitalize()} severity ({len(group)})", level=2)
            for finding in group:
                head = doc.add_paragraph()
                run = head.add_run(_txt(finding.get("title")))
                run.bold = True
                run.font.size = Pt(11)
                run.font.color.rgb = RGBColor.from_string(
                    {"critical": "C00000", "high": "E36C0A"}.get(sev, "333333")
                )
                meta = doc.add_paragraph()
                run = meta.add_run(
                    f"Type: {_txt(finding.get('finding_type'))} · Domain: {_txt(finding.get('domain'))} · "
                    f"Status: {_txt(finding.get('status'))} · Rule: {_txt(finding.get('rule_id')) or DEFAULT_DASH}"
                )
                run.italic = True
                run.font.size = Pt(8.5)
                run.font.color.rgb = RGBColor.from_string("666666")
                doc.add_paragraph(_txt(finding.get("explanation")) or DEFAULT_DASH)
                if finding.get("possible_causes"):
                    doc.add_paragraph(f"Possible causes: {_joined(finding.get('possible_causes'))}")
                if finding.get("missing_information"):
                    doc.add_paragraph(f"Missing information: {_joined(finding.get('missing_information'))}")
                cited = [by_id[eid] for eid in finding.get("evidence_ids") or [] if eid in by_id]
                if cited:
                    cite_title = doc.add_paragraph()
                    run = cite_title.add_run("Evidence citations")
                    run.bold = True
                    run.font.size = Pt(9)
                    for ev in cited:
                        doc.add_paragraph(
                            f"{_txt(ev.get('document_name') or ev.get('filename'))}, page "
                            f"{int(ev.get('page') or 0)}: “{_txt(ev.get('quote'))}” "
                            f"({_txt(ev.get('verification_status'))})",
                            style="List Bullet",
                        )
                recommended = finding.get("recommended_action") or (
                    "Request the underlying source data and written confirmation from the seller "
                    "to reconcile this item."
                )
                reco = doc.add_paragraph()
                run = reco.add_run(f"Recommended verification: {_txt(recommended)}")
                run.italic = True
                run.font.size = Pt(9)
                doc.add_paragraph("")

    if findings_history:
        doc.add_heading(f"Resolved findings (history) ({len(findings_history)})", level=2)
        history_lead = doc.add_paragraph(
            "Not included in the active risk counts above; retained for auditability."
        )
        history_lead.italic = True
        for finding in findings_history:
            paragraph = doc.add_paragraph(style="List Bullet")
            run = paragraph.add_run(
                f"{_txt(finding.get('title'))} — {_txt(finding.get('severity'))} severity, "
                f"{_txt(finding.get('finding_type'))}, status {_txt(finding.get('status'))}"
            )
            run.font.size = Pt(9)

    # -- 5. Evidence gaps ---------------------------------------------------
    doc.add_heading("6. Evidence Gaps", level=1)
    if gaps:
        doc.add_paragraph(
            f"{len(gaps)} of {len(coverage)} coverage requirement(s) are not sufficiently supported "
            "by the supplied documents. These are diligence blockers until resolved."
        )
        _docx_table(
            doc,
            ["Dimension", "Requirement", "Status", "Priority", "Note"],
            [
                [
                    _txt(i.get("dimension")),
                    _txt(i.get("requirement")),
                    _txt(i.get("status")),
                    _txt(i.get("priority")),
                    _txt(i.get("note")),
                ]
                for i in gaps
            ],
            widths=(1.0, 2.0, 0.8, 0.7, 1.7),
            font_size=8,
        )
    else:
        doc.add_paragraph("No evidence gaps were flagged by the coverage analysis.")

    # -- 6. Seller questions ------------------------------------------------
    doc.add_heading("7. Seller Questions", level=1)
    if questions:
        _docx_table(
            doc,
            ["Priority", "Owner", "Question", "Rationale", "Required documents"],
            [
                [
                    _txt(q.get("priority")),
                    _txt(q.get("owner")),
                    _txt(q.get("question")),
                    _txt(q.get("rationale")),
                    _joined(q.get("required_documents")),
                ]
                for q in questions
            ],
            widths=(0.6, 1.0, 2.0, 1.8, 1.0),
            font_size=8,
        )
    else:
        doc.add_paragraph("No seller questions have been generated yet.")

    # -- Appendix A. Asset discovery & evaluation --------------------------
    overview = data.get("overview") or {}
    if overview:
        evaluation = overview.get("evaluation") or {}
        overall = evaluation.get("overall") or {}
        disc = overview.get("discovery") or {}
        doc.add_heading("Appendix A. Asset Discovery & Evaluation", level=1)
        doc.add_paragraph(
            "Rule-based summary of the discovered asset and the diligence-readiness dimensions. "
            "The score is not an investment rating and no Go/No-Go recommendation is produced."
        )
        doc.add_heading("A.1 Discovered asset", level=2)
        attr_rows = [
            [attr.get("label_zh"), _txt(attr.get("value")),
             (f"{attr.get('document')} p.{attr.get('page')}" if attr.get("document") else "asset registry")]
            for attr in (disc.get("attributes") or [])[:6]
        ]
        if attr_rows:
            _docx_table(doc, ["Field", "Value", "Source"], attr_rows, widths=(1.5, 3.0, 2.2), font_size=8)
        doc.add_heading("A.2 Evaluation scorecard", level=2)
        dim_rows = [
            [dim.get("label_zh"), dim.get("score"), dim.get("weight"), _txt(dim.get("basis"))]
            for dim in (evaluation.get("dimensions") or [])
        ]
        dim_rows.append([overall.get("title_zh"), overall.get("score"), 1.0, _txt(overall.get("label_zh"))])
        _docx_table(doc, ["Dimension", "Score / 100", "Weight", "Basis"], dim_rows,
                    widths=(1.5, 0.8, 0.7, 3.2), font_size=7.5)
        doc.add_paragraph(
            f"Risk exposure: {_txt((evaluation.get('risk') or {}).get('label_zh'))}. "
            "Findings and scores require expert review; no automated Go/No-Go conclusion is made."
        )
        doc.add_paragraph("")

    # -- 7. Review + limitations -------------------------------------------
    doc.add_heading("8. Human Review & Limitations", level=1)
    doc.add_heading("Review status", level=2)
    review = review_summary(data)
    if review:
        _docx_table(
            doc,
            ["Question status / review status", "Count"],
            [[k, v] for k, v in review.items()],
            widths=(3.6, 1.0),
            font_size=8.5,
        )
    else:
        doc.add_paragraph("No questions are awaiting review.")
    doc.add_heading("Limitations", level=2)
    _docx_bullets(
        doc,
        [
            "The analysis is generated by automated rule-based extraction and consistency checks; it may "
            "miss evidence, mis-read tables, or over/under-state severity.",
            "Only the documents supplied in this package were analysed; absent documents cannot be assessed, "
            "and absence of a finding is not evidence of absence of risk.",
            "Numeric comparability is limited by the dimensions available in the source (trial, arm, population, "
            "data cutoff, endpoint definition, assessment method).",
            "Analysis reflects the stated data cutoff dates; later data may change the conclusions.",
            "Findings and questions require review, correction and sign-off by qualified clinical, safety, "
            "biostatistics, regulatory and legal reviewers before external use.",
        ],
    )
    doc.add_paragraph("")
    disclaimer = doc.add_paragraph()
    run = disclaimer.add_run("Disclaimer. ")
    run.bold = True
    run.font.size = Pt(8.5)
    run = disclaimer.add_run(
        "This document was produced by HiJenny from the supplied due-diligence package. It is an "
        "AI-generated working aid, not a substitute for expert due diligence, and must not be relied "
        "upon as legal, medical, regulatory, tax or investment advice. All quoted text is believed to "
        "be verbatim from the source documents, but the source documents remain the authoritative record."
    )
    run.font.size = Pt(8.5)

    try:
        _docx_footer(doc)
    except Exception:  # pragma: no cover - footer is cosmetic
        pass

    doc.core_properties.title = f"{_txt(project.get('name'))} — Asset Due Diligence Report"
    doc.core_properties.author = "HiJenny"
    doc.core_properties.comments = "AI-generated due-diligence report; requires expert human review."
    doc.save(target)
    return target


def seller_reply_workbook(questions, *, sample=False):
    """Export only request text, material requests and blank reply fields."""
    from io import BytesIO
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.title = "合成样例 · 卖方问题单" if sample else "卖方问题单"
    rows = [[q["question_id"], q["question"], _joined(q.get("required_documents")),
             "", "", ""] for q in questions]
    _table_sheet(wb, "合成样例" if sample else "卖方答复",
                 ["问题编号", "待澄清问题", "所需材料", "卖方答复", "答复附件及定位", "答复日期"],
                 rows, {1: 23, 2: 70, 3: 50, 4: 60, 5: 42, 6: 18},
                 wrap_cols=(2, 3, 4, 5))
    stream = BytesIO()
    wb.save(stream)
    return stream.getvalue()
