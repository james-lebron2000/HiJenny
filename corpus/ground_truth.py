"""Load and validate package ground truth (eval-dev owned).

The Lead-owned generator writes ``ground_truth.json`` next to the package files.
This loader accepts the Lead-pinned shape and is tolerant about section naming
(``facts`` / ``expected_facts`` ...) but strict about the fields that make
scoring sound:

* every fact needs ``field_name``, a matchable document key (``document`` or
  ``document_type``), a 1-based ``page`` and a value (``value_num`` or
  ``value_text``);
* ``page`` may be omitted only when the caller passes ``strict=False`` (then the
  fact is dropped with a warning rather than scored on a wildcard page).

When no ground-truth file exists the loader falls back to
``corpus.adjudication`` (topic-level expectations only; fact-level extraction
stays scored as "not_scored" because inventing page locations would be
dishonest).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from corpus.adjudication import fallback_ground_truth

GT_FILENAME = "ground_truth.json"

_LIST_KEYS = {
    "facts": ("facts", "expected_facts", "extraction", "extracted_facts"),
    "finding_topics": (
        "finding_topics",
        "findings",
        "expected_finding_topics",
        "expected_findings",
    ),
    "question_topics": (
        "question_topics",
        "questions",
        "expected_question_topics",
        "expected_questions",
    ),
    "coverage": ("coverage", "expected_coverage", "coverage_expectations"),
}

_PACKAGE_SUFFIXES = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".txt"}


def package_ground_truth_path(package_dir: str | Path) -> Path:
    return Path(package_dir) / GT_FILENAME


def package_files(package_dir: str | Path) -> list[Path]:
    """Supported documents in a package directory (non-recursive, sorted)."""
    directory = Path(package_dir)
    if not directory.exists():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.name != GT_FILENAME
        and p.suffix.lower() in _PACKAGE_SUFFIXES
    )


def _first_list(data: dict, keys: tuple[str, ...]) -> list:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _normalise_fact(fact: dict) -> dict:
    out = dict(fact)
    out.setdefault("value_text", None)
    out.setdefault("value_num", None)
    if out.get("document") is None and fact.get("filename") is not None:
        out["document"] = fact["filename"]
    return out


def _fact_problem(fact: dict) -> str | None:
    if not isinstance(fact, dict):
        return "not_an_object"
    if not fact.get("field_name") and not fact.get("field"):
        return "missing_field_name"
    if not (fact.get("document") or fact.get("document_type") or fact.get("filename")):
        return "missing_document"
    if fact.get("page") is None and fact.get("page_no") is not None:
        fact["page"] = fact["page_no"]
    if fact.get("page") is None:
        return "missing_page"
    if fact.get("value_num") is None and fact.get("value_text") is None:
        return "missing_value"
    return None


def load_ground_truth(
    package_dir: str | Path,
    *,
    strict: bool = True,
) -> dict:
    """Load adjudicated ground truth for a package directory.

    Returns a dict with stable keys: ``package``, ``variant``, ``asset_name``,
    ``trial``, ``facts``, ``finding_topics``, ``question_topics``, ``coverage``,
    ``lifecycle``, ``injection``, ``source`` (``"file"`` or
    ``"adjudication_fallback"``), ``path``, ``warnings`` and ``raw``.
    """
    directory = Path(package_dir)
    gt_path = package_ground_truth_path(directory)
    warnings: list[str] = []
    if not gt_path.exists():
        fallback = fallback_ground_truth(directory.name)
        fallback.update(
            {
                "source": "adjudication_fallback",
                "path": None,
                "warnings": [
                    f"{gt_path} not found; using corpus.adjudication fallback. "
                    "Fact-level extraction metrics will be reported as not_scored."
                ],
                "raw": {},
                "eval_notes": {},
                "ignore_documents": [],
                "ignore_fields": [],
                "collapse_duplicates": False,
                "known_limitations": [],
            }
        )
        return fallback

    try:
        raw = json.loads(gt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read ground truth {gt_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"ground truth {gt_path} must be a JSON object")

    facts: list[dict] = []
    for index, fact in enumerate(_first_list(raw, _LIST_KEYS["facts"])):
        if not isinstance(fact, dict):
            warnings.append(f"fact[{index}] is not an object; dropped")
            continue
        fact = _normalise_fact(fact)
        problem = _fact_problem(fact)
        if problem:
            message = f"fact[{index}] {problem}; dropped"
            if strict:
                raise ValueError(f"{gt_path}: {message}")
            warnings.append(message)
            continue
        facts.append(fact)

    notes = raw.get("eval_notes") or {}
    ignore_documents = (
        notes.get("ignore_documents_for_fact_scoring")
        or raw.get("ignore_documents_for_fact_scoring")
        or []
    )
    ignore_fields = (
        notes.get("ignore_fields_for_fact_scoring")
        or raw.get("ignore_fields_for_fact_scoring")
        or []
    )
    # The Lead eval note says identical (field, document, value) predictions are
    # counted once; honour it whenever the note is present.
    collapse_duplicates = bool(notes.get("duplicates") or raw.get("collapse_duplicates"))

    data = {
        "package": raw.get("package") or directory.name,
        "variant": raw.get("variant")
        or ("v2" if "v2" in directory.name.lower() else "v1"),
        "asset_name": raw.get("asset_name"),
        "trial": raw.get("trial") or {},
        "facts": facts,
        "finding_topics": [
            dict(t) for t in _first_list(raw, _LIST_KEYS["finding_topics"]) if isinstance(t, dict)
        ],
        "question_topics": [
            dict(t) for t in _first_list(raw, _LIST_KEYS["question_topics"]) if isinstance(t, dict)
        ],
        "coverage": [
            dict(c) for c in _first_list(raw, _LIST_KEYS["coverage"]) if isinstance(c, dict)
        ],
        "lifecycle": dict(raw.get("lifecycle") or {}),
        "injection": dict(raw.get("injection") or {}),
        "expect_no_findings": bool(raw.get("expect_no_findings", False)),
        "eval_notes": dict(notes) if isinstance(notes, dict) else {},
        "ignore_documents": [str(d) for d in ignore_documents if d],
        "ignore_fields": [str(f) for f in ignore_fields if f],
        "collapse_duplicates": collapse_duplicates,
        "known_limitations": [str(x) for x in (raw.get("known_limitations") or []) if x],
        "source": "file",
        "path": str(gt_path),
        "warnings": warnings,
        "raw": raw,
    }
    return data



# ---------------------------------------------------------------------------
# Ground-truth self-check (preflight: do page/anchor/value actually line up?)
# ---------------------------------------------------------------------------

_DOC_PAGE_CACHE: dict[str, dict[int, str]] = {}


def _normalise(text: str | None) -> str:
    import re

    return re.sub(r"\s+", " ", text or "").strip()


def document_page_texts(path: str | Path) -> dict[int, str]:
    """Extract 1-based page/slide/sheet text for a package document (read-only)."""
    target = Path(path)
    key = str(target)
    if key in _DOC_PAGE_CACHE:
        return _DOC_PAGE_CACHE[key]
    suffix = target.suffix.lower()
    pages: dict[int, str] = {}
    if suffix == ".pdf":
        import fitz  # PyMuPDF

        with fitz.open(target) as document:
            for index in range(document.page_count):
                pages[index + 1] = document[index].get_text("text") or ""
    elif suffix == ".pptx":
        from pptx import Presentation

        presentation = Presentation(str(target))
        for index, slide in enumerate(presentation.slides, start=1):
            parts: list[str] = []
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                    parts.append(shape.text_frame.text)
                if getattr(shape, "has_table", False) and shape.has_table:
                    for row in shape.table.rows:
                        for cell in row.cells:
                            parts.append(cell.text)
            pages[index] = "\n".join(parts)
    elif suffix == ".xlsx":
        import openpyxl

        workbook = openpyxl.load_workbook(str(target), data_only=True, read_only=True)
        for index, sheet in enumerate(workbook.worksheets, start=1):
            parts = []
            for row in sheet.iter_rows(values_only=True):
                parts.append(" | ".join("" if value is None else str(value) for value in row))
            pages[index] = "\n".join(parts)
    elif suffix == ".docx":
        from docx import Document as DocxDocument

        document = DocxDocument(str(target))
        pages[1] = "\n".join(paragraph.text for paragraph in document.paragraphs)
    _DOC_PAGE_CACHE[key] = pages
    return pages


def verify_ground_truth_facts(
    package_dir: str | Path,
    facts: list[dict],
    *,
    default_tolerance: float = 0.05,
) -> dict:
    """Check that each fact's page/anchor/value really exist in the package.

    This is a *corpus* preflight, not pipeline scoring: it catches stale or
    mis-generated ground truth before those errors are misattributed to the
    pipeline.  Returns ``{"checked", "issues", "ok"}``.
    """
    directory = Path(package_dir)
    issues: list[dict] = []
    for index, fact in enumerate(facts):
        document = fact.get("document") or fact.get("filename")
        page = fact.get("page")
        if not document or page is None:
            issues.append({"index": index, "field_name": fact.get("field_name"), "issue": "missing_document_or_page"})
            continue
        path = directory / str(document)
        if not path.exists():
            issues.append({"index": index, "field_name": fact.get("field_name"), "document": document, "issue": "document_missing"})
            continue
        texts = document_page_texts(path)
        page_text = _normalise(texts.get(int(page), ""))
        if not page_text:
            issues.append({"index": index, "field_name": fact.get("field_name"), "document": document, "page": page, "issue": "page_missing"})
            continue
        anchor = _normalise(fact.get("anchor"))
        if anchor and anchor not in page_text:
            issues.append({"index": index, "field_name": fact.get("field_name"), "document": document, "page": page, "issue": "anchor_not_on_page", "anchor": anchor[:200]})
            continue
        value_num = fact.get("value_num")
        if value_num is not None:
            import re

            tolerance = fact.get("tolerance")
            tolerance = float(tolerance) if tolerance is not None else default_tolerance
            numbers = [
                float(match.replace(",", ""))
                for match in re.findall(r"\d[\d,]*(?:\.\d+)?", page_text)
            ]
            if not any(abs(number - float(value_num)) <= tolerance for number in numbers):
                issues.append(
                    {
                        "index": index,
                        "field_name": fact.get("field_name"),
                        "document": document,
                        "page": page,
                        "issue": "value_not_on_page",
                        "value_num": value_num,
                        "tolerance": tolerance,
                    }
                )
                continue
        value_text = fact.get("value_text")
        # When an anchor is present it already proves the fact is grounded; the
        # value_text is a normalised value, not necessarily a verbatim string.
        if value_text and not value_num and not anchor:
            if _normalise(str(value_text)).lower() not in page_text.lower():
                issues.append(
                    {
                        "index": index,
                        "field_name": fact.get("field_name"),
                        "document": document,
                        "page": page,
                        "issue": "value_text_not_on_page",
                        "value_text": str(value_text)[:200],
                    }
                )
    return {"checked": len(facts), "issues": issues, "ok": not issues}

__all__ = [
    "GT_FILENAME",
    "document_page_texts",
    "load_ground_truth",
    "package_files",
    "package_ground_truth_path",
    "verify_ground_truth_facts",
]
