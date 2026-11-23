"""Report generation stage (app-dev owned).

Pipeline contract: ``generate_project_outputs(session, project_id, force=False)``
returns a stats dict containing at least ``report_path`` and ``questions_xlsx``.

Produces, under ``<DATA_DIR>/projects/<project_id>/exports/``:

    HiJenny_Report.md      professional Markdown due-diligence report
    HiJenny_Report.docx    matching Word report (title page, tables, citations)
    Seller_Questions.xlsx  six-sheet styled workbook

Everything is derived from persisted store rows only — no agent imports, no LLM
calls — and regeneration is idempotent (same paths overwritten atomically).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hijenny import exports, store
from hijenny.schemas import utcnow


def _atomic_write(path: Path, write_fn) -> Path:
    """Write via a sibling temp file then replace, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=path.suffix, dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        write_fn(tmp_path)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:  # pragma: no cover - best effort cleanup
                pass
    return path


def generate_project_outputs(session, project_id: str, force: bool = False) -> dict:
    """Generate Markdown + DOCX report and the Excel workbook for one project.

    Args:
        session: SQLAlchemy session provided by the pipeline state machine.
        project_id: project to report on.
        force: accepted for stage-handler parity; regeneration already overwrites.

    Returns:
        stats dict with ``report_path``, ``report_md``, ``report_docx``,
        ``questions_xlsx`` plus row counts.
    """
    data = exports.collect_export_data(session, project_id)
    out_dir = exports.ensure_project_dirs(project_id)

    md_path = out_dir / "HiJenny_Report.md"
    docx_path = out_dir / "HiJenny_Report.docx"
    xlsx_path = out_dir / "Seller_Questions.xlsx"

    markdown = exports.render_markdown_report(data)
    _atomic_write(md_path, lambda p: p.write_text(markdown, encoding="utf-8"))
    _atomic_write(docx_path, lambda p: exports.build_report_docx(data, p))
    _atomic_write(xlsx_path, lambda p: exports.build_workbook(data, p))

    history = data.get("findings_history") or []
    counts = exports.finding_counts(data)
    stats = {
        "report_path": str(md_path),
        "report_md": str(md_path),
        "report_docx": str(docx_path),
        "questions_xlsx": str(xlsx_path),
        "documents": len(data["documents"]),
        "pages": int((data.get("stats") or {}).get("pages") or 0),
        "evidence": len(data["evidence"]),
        # Active findings only; resolved/dismissed history is reported separately.
        "findings": counts["active"],
        "findings_active": counts["active"],
        "findings_history": counts["history"],
        "findings_resolved": counts["resolved"],
        "findings_dismissed": counts["dismissed"],
        "questions": len(data["questions"]),
        "coverage": len(data["coverage"]),
        "assets": len(data["assets"]),
        "trials": len(data["trials"]),
        "generated_at": utcnow(),
    }
    return stats
