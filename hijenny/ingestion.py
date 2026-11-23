"""INGEST stage: file -> document + pages + chunks + metadata (parser-dev owned).

Public API
----------
``ingest_file(session, project_id, path, doc_type=None, version=None, force=False)``
    Parse one explicit file and persist it (idempotent by SHA-256).
``ingest_directory(session, project_id, directory, force=False)``
    Ingest every supported file in a package directory (sorted, non-recursive).
``ingest_project(session, project_id, force=False)``
    Pipeline stage handler; resolves the project package directory, then ingests
    plus any documents already registered by the API but not parsed yet.

Returns stats dicts with at least ``documents_parsed``, ``documents_failed``,
``documents_skipped``, ``pages_parsed``, ``chunks_created`` (and extra counts).
Per-file failures are isolated: the document row is persisted with
``status="failed"`` and ``parse_error`` and the rest of the package continues.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from hijenny import config, store
from hijenny.parsers import detect_format, parse_file, sha256_file
from hijenny.parsers import common as parser_common
from hijenny.parsers.injection import scan_pages
from hijenny.schemas import DocType, normalize_date, utcnow

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx", ".pptx", ".xlsx")
_INGEST_DOC_STATUSES = {"parsed", "superseded"}

# ---------------------------------------------------------------------------
# Classification heuristics (filename + title/leading text; no corpus specifics)
# ---------------------------------------------------------------------------

_CLASSIFY_RULES: list[tuple[str, list[tuple[str, float]]]] = [
    (
        "CSR",
        [
            (r"\bcsr\b", 5.0),
            (r"\bclinical study report\b", 6.0),
            (r"\bstudy report\b", 3.0),
            (r"\bclinical study results?\b", 3.0),
        ],
    ),
    (
        "PROTOCOL",
        [
            (r"\bclinical trial protocol\b", 6.0),
            (r"\bstudy protocol\b", 5.0),
            (r"\bprotocol\b", 3.5),
        ],
    ),
    (
        "IB",
        [
            (r"\binvestigators? brochure\b", 6.0),
            (r"\bib\b", 5.0),
        ],
    ),
    (
        "SAP",
        [
            (r"\bstatistical analysis plan\b", 6.0),
            (r"\bsap\b", 5.0),
            (r"\bstatistical analysis\b", 3.0),
        ],
    ),
    (
        "SAFETY",
        [
            (r"\bsafety summary\b", 6.0),
            (r"\bdsur\b", 6.0),
            (r"\bpsur\b", 6.0),
            (r"\bsafety\b", 3.0),
            (r"\bsae\b", 3.0),
            (r"\bserious adverse events?\b", 3.0),
            (r"\bpharmacovigilance\b", 4.0),
        ],
    ),
    (
        "DISPOSITION",
        [
            (r"\bpatient disposition\b", 6.0),
            (r"\bsubject disposition\b", 5.0),
            (r"\bdisposition\b", 3.5),
        ],
    ),
    (
        "CMC",
        [
            (r"\bcmc\b", 6.0),
            (r"\bchemistry manufacturing\b", 6.0),
            (r"\bmodule 3\b", 5.0),
            (r"\bdrug substance\b", 4.0),
            (r"\bdrug product\b", 4.0),
            (r"\bmanufacturing\b", 3.0),
            (r"\bformulation\b", 2.5),
        ],
    ),
    (
        "PRECLINICAL",
        [
            (r"\bnonclinical\b", 6.0),
            (r"\bnon clinical\b", 6.0),
            (r"\bpreclinical\b", 6.0),
            (r"\bpre clinical\b", 6.0),
            (r"\btoxicology\b", 5.0),
            (r"\bmodule 4\b", 5.0),
            (r"\bpharmacology\b", 3.0),
        ],
    ),
    (
        "REGULATORY",
        [
            (r"\bregulatory meeting\b", 6.0),
            (r"\bmeeting minutes\b", 5.0),
            (r"\bmodule 1\b", 5.0),
            (r"\bregulatory\b", 4.0),
            (r"\bcorrespondence\b", 4.0),
            (r"\bapproval letter\b", 4.0),
            (r"\bind\b", 4.0),
            (r"\bbla\b", 4.0),
            (r"\bnda\b", 4.0),
        ],
    ),
    (
        "PATENT",
        [
            (r"\bpatent family\b", 6.0),
            (r"\bpatent\b", 5.0),
            (r"\bintellectual property\b", 4.0),
        ],
    ),
    (
        "COMMERCIAL",
        [
            (r"\bcommercial forecast\b", 6.0),
            (r"\bcommercial\b", 5.0),
            (r"\bforecast\b", 5.0),
            (r"\bvaluation\b", 4.0),
            (r"\bmarket\b", 3.0),
            (r"\bpricing\b", 3.0),
            (r"\brevenue\b", 3.0),
        ],
    ),
    (
        "OTHER",
        [
            (r"\bdeviation log\b", 7.0),
            (r"\bdata listing\b", 7.0),
            (r"\bground truth\b", 7.0),
        ],
    ),
]

_COMPILED_CLASSIFY_RULES: list[tuple[str, list[tuple[re.Pattern[str], float]]]] = [
    (label, [(re.compile(pattern), weight) for pattern, weight in patterns])
    for label, patterns in _CLASSIFY_RULES
]

_CLASSIFY_MIN_SCORE = 3.0

_DOC_TYPE_ALIASES: dict[str, str] = {
    "CLINICAL STUDY REPORT": "CSR",
    "STUDY REPORT": "CSR",
    "CLINICAL TRIAL PROTOCOL": "PROTOCOL",
    "INVESTIGATOR BROCHURE": "IB",
    "INVESTIGATORS BROCHURE": "IB",
    "STATISTICAL ANALYSIS PLAN": "SAP",
    "SAFETY SUMMARY": "SAFETY",
    "DSUR": "SAFETY",
    "PSUR": "SAFETY",
    "PATIENT DISPOSITION": "DISPOSITION",
    "CHEMISTRY MANUFACTURING CONTROLS": "CMC",
    "NONCLINICAL": "PRECLINICAL",
    "NON CLINICAL": "PRECLINICAL",
    "IP": "PATENT",
}

_VERSION_RE = re.compile(
    r"(?i)(?:\b(?:version|ver|edition|ed|revision|rev)\.?\s*[:\-]?\s*|(?<![a-z0-9])v[\s._\-]?)(\d+(?:[._]\d+)*)"
)
_FAMILY_VERSION_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:v|ver|version|ed|edition|rev|revision)\.?[\s._\-]*\d+(?:[._]\d+)*"
)
_FAMILY_NOISE_RE = re.compile(r"(?i)\b(?:final|draft|updated|clean|signed|latest|new|copy)\b")
_DATE_HINT_RE = re.compile(
    r"(?i)\b(?:dated?|date\s+of\s+issue|issue\s+date|version\s+date|report\s+date)\s*[:\-]?\s*"
    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
)


def _normalize_for_match(text: str) -> str:
    """Lowercase alphanumeric words only; separator-insensitive matching."""
    t = (text or "").lower().replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def normalize_doc_type(value: str | None) -> str | None:
    """Map an explicit/user-supplied doc type to a :class:`DocType` member."""
    if value is None:
        return None
    key = re.sub(r"[^A-Za-z0-9]+", " ", str(value).strip()).strip().upper()
    if not key:
        return None
    if key in DocType.__members__:
        return key
    return _DOC_TYPE_ALIASES.get(key, "OTHER")


def classify_document(
    filename: str,
    title: str | None = None,
    first_page_text: str | None = None,
) -> tuple[str, dict]:
    """Heuristically classify a document into a :class:`DocType` value.

    Evidence is weighted: filename (strongest) > title/metadata > leading page
    text.  A minimum score is required so that a single incidental word in body
    text (e.g. "protocol" inside a deviation log) cannot override the filename.
    Returns ``(doc_type_str, detail_dict)``.
    """
    sources = (
        ("filename", _normalize_for_match(Path(str(filename or "")).stem), 3.0),
        ("title", _normalize_for_match(title or ""), 2.5),
        ("text", _normalize_for_match((first_page_text or "")[:6000]), 1.0),
    )
    scores: dict[str, float] = {}
    signals: list[str] = []
    for label, patterns in _COMPILED_CLASSIFY_RULES:
        score = 0.0
        for regex, weight in patterns:
            for source_name, source_text, source_weight in sources:
                if source_text and regex.search(source_text):
                    score += weight * source_weight
                    if len(signals) < 12:
                        signals.append(f"{label}:{source_name}:{regex.pattern}")
        if score > 0:
            scores[label] = score
    best_label = "OTHER"
    best_score = 0.0
    for label, _patterns in _COMPILED_CLASSIFY_RULES:
        score = scores.get(label, 0.0)
        if score > best_score:
            best_score, best_label = score, label
    if best_score < _CLASSIFY_MIN_SCORE:
        best_label, best_score = "OTHER", 0.0
    detail = {
        "doc_type": best_label,
        "score": round(best_score, 2),
        "signals": signals[:8],
    }
    return best_label, detail


def normalize_version(raw: str | None, default: str = "1.0") -> str:
    """Normalise ``v2`` / ``Version 3.1`` / ``2.0`` to dotted ``major.minor``."""
    if raw is None:
        return default
    text = str(raw).strip()
    if not text:
        return default
    match = re.search(r"(\d+(?:[._]\d+)*)", text)
    if not match:
        return text[:40]
    parts = [part for part in re.split(r"[._]", match.group(1)) if part != ""]
    numbers = [str(int(part)) for part in parts]
    if len(numbers) == 1:
        numbers.append("0")
    return ".".join(numbers)


def detect_version(filename: str = "", title: str | None = None, text: str | None = None) -> str:
    """Detect a document version from filename, then title, then leading text."""
    for source in (filename or "", title or "", (text or "")[:2000]):
        if not source:
            continue
        match = _VERSION_RE.search(source)
        if match:
            return normalize_version(match.group(1))
    return "1.0"


def detect_doc_family(filename: str, doc_type: str) -> str:
    """Stable family key: doc_type + filename stem with the version token removed.

    Only same doc_type AND same normalised stem can share a family, which stops
    e.g. an unrelated "Deviation Log" (mentions 'protocol' in its content) from
    superseding "Clinical_Trial_Protocol".
    """
    stem = Path(str(filename or "")).stem.lower()
    stem = _FAMILY_VERSION_RE.sub(" ", stem)
    stem = _FAMILY_NOISE_RE.sub(" ", stem)
    stem = re.sub(r"[^a-z0-9]+", " ", stem).strip()
    stem = re.sub(r"\s+", " ", stem)
    if not stem:
        return str(doc_type or "OTHER")
    return f"{doc_type or 'OTHER'}::{stem}"


def _version_sort_key(version: str | None) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(version or ""))
    if not parts:
        return (0,)
    return tuple(int(part) for part in parts)


# ---------------------------------------------------------------------------
# Chunking (retrieval only; extraction reads pages directly)
# ---------------------------------------------------------------------------


def chunk_page_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[tuple[int, str]]:
    """Split page text into overlapping ``(start_char, text)`` windows."""
    if not text or not text.strip():
        return []
    length = len(text)
    pieces: list[tuple[int, str]] = []
    start = 0
    while start < length:
        end = min(length, start + chunk_size)
        if end < length:
            search_from = start + int(chunk_size * 0.6)
            newline = text.rfind("\n", search_from, end)
            space = text.rfind(" ", search_from, end)
            cut = max(newline, space)
            if cut > start:
                end = cut
        piece = text[start:end].strip()
        if piece:
            pieces.append((start, piece))
        if end >= length:
            break
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        start = next_start
    return pieces


def _piece_bboxes(page, pieces: list[tuple[int, str]]) -> list[list[float]]:
    """Union bbox of the text blocks covered by each chunk (best effort)."""
    spans: list[tuple[int, int, list[float]]] = []
    cursor = 0
    for block in page.blocks:
        text = (block.text or "").strip()
        if not text or not block.bbox:
            continue
        index = page.text.find(text, cursor)
        if index < 0:
            index = page.text.find(text)
            if index < 0:
                continue
        spans.append((index, index + len(text), list(block.bbox)))
        cursor = index + len(text)
    boxes: list[list[float]] = []
    for start, piece in pieces:
        end = start + len(piece)
        covering = [bbox for (b_start, b_end, bbox) in spans if b_end > start and b_start < end]
        boxes.append(parser_common.bbox_union(covering))
    return boxes


def _page_to_dict(page) -> dict:
    return {
        "page_no": int(page.page_no),
        "text": page.text or "",
        "width": float(page.width or 0.0),
        "height": float(page.height or 0.0),
        "blocks": [
            {
                "text": block.text,
                "bbox": list(block.bbox or []),
                "type": block.block_type,
                "block_type": block.block_type,
            }
            for block in page.blocks
        ],
    }


def _build_chunks(pages) -> list[dict]:
    chunks: list[dict] = []
    for page in pages:
        pieces = chunk_page_text(page.text)
        boxes = _piece_bboxes(page, pieces)
        for (start, text), bbox in zip(pieces, boxes):
            chunks.append(
                {
                    "page_no": int(page.page_no),
                    "chunk_index": len(chunks),
                    "text": text,
                    "start_char": start,
                    "bbox": bbox,
                }
            )
    return chunks


# ---------------------------------------------------------------------------
# Package directory resolution / ingestion
# ---------------------------------------------------------------------------


def resolve_package_dir(session: Session, project_id: str) -> Path | None:
    """Locate the package directory for a project (Lead-confirmed order)."""
    candidates: list[Path] = []
    env = os.getenv("HIJENNY_PACKAGE_DIR")
    if env:
        candidates.append(Path(env))
    project = store.get_project(session, project_id) or {}
    meta = project.get("meta") or {}
    for key in ("package_dir", "package_path", "upload_dir", "documents_dir", "source_dir"):
        value = meta.get(key)
        if value:
            candidates.append(Path(str(value)))
    candidates.extend(
        [
            config.DATA_DIR / "projects" / project_id / "documents",
            config.DATA_DIR / "uploads" / project_id,
            config.DATA_DIR / "packages" / project_id,
        ]
    )
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else (config.REPO_ROOT / candidate)
        if path.is_dir():
            return path
    return None


def _resolve_path(raw: str | Path) -> Path:
    """Resolve an absolute path, or a path relative to the repo root / data dir.

    The API upload endpoint stores ``stored_path`` relative to ``config.DATA_DIR``
    (e.g. ``projects/<id>/documents/x.pdf``), while tests pass absolute paths.
    """
    path = Path(raw)
    if path.is_absolute():
        return path
    for base in (config.REPO_ROOT, config.DATA_DIR):
        candidate = base / path
        if candidate.exists():
            return candidate
    return config.REPO_ROOT / path


def _empty_stats() -> dict:
    return {
        "documents_seen": 0,
        "documents_parsed": 0,
        "documents_failed": 0,
        "documents_skipped": 0,
        "pages_parsed": 0,
        "chunks_created": 0,
        "evidence_deleted_on_force": 0,
        "documents": [],
    }


def _merge_file_stats(stats: dict, result: dict) -> None:
    stats["documents_seen"] += 1
    status = result.get("status")
    if status == "parsed":
        stats["documents_parsed"] += 1
    elif status == "failed":
        stats["documents_failed"] += 1
    else:
        stats["documents_skipped"] += 1
    stats["pages_parsed"] += int(result.get("pages_parsed", 0) or 0)
    stats["chunks_created"] += int(result.get("chunks_created", 0) or 0)
    stats["evidence_deleted_on_force"] += int(result.get("evidence_deleted", 0) or 0)
    if len(stats.get("documents", [])) < 500:
        stats.setdefault("documents", []).append(
            {
                "document_id": result.get("document_id"),
                "filename": result.get("filename"),
                "status": status,
                "doc_type": result.get("doc_type"),
                "version": result.get("version"),
                "doc_family": result.get("doc_family"),
                "page_count": result.get("page_count", 0),
                "pages_parsed": result.get("pages_parsed", 0),
                "chunks_created": result.get("chunks_created", 0),
                "parse_quality": result.get("parse_quality", 0.0),
                "injection_flag_count": result.get("injection_flag_count", 0),
                "parse_error": result.get("parse_error"),
            }
        )


def _merge_stats(stats: dict, other: dict) -> None:
    for key in (
        "documents_seen",
        "documents_parsed",
        "documents_failed",
        "documents_skipped",
        "pages_parsed",
        "chunks_created",
        "evidence_deleted_on_force",
    ):
        stats[key] = int(stats.get(key, 0)) + int(other.get(key, 0) or 0)
    docs = other.get("documents") or []
    stats.setdefault("documents", []).extend(docs)
    if "files_found" in other:
        stats["files_found"] = int(stats.get("files_found", 0)) + int(other.get("files_found", 0) or 0)


def _detect_doc_date(first_page_text: str | None) -> str | None:
    match = _DATE_HINT_RE.search(first_page_text or "")
    if not match:
        return None
    return normalize_date(match.group(1))


def _document_meta(
    parsed,
    *,
    fmt: str | None,
    path: Path,
    digest: str,
    doc_type: str,
    classification: dict,
    version: str,
    doc_family: str,
    injections: list[dict],
) -> dict:
    meta = dict(parsed.meta or {})
    meta.update(
        {
            "source": "ingest",
            "format": fmt or meta.get("format") or path.suffix.lstrip(".").lower(),
            "parser_version": config.PARSER_VERSION,
            "sha256": digest,
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "doc_type": doc_type,
            "classification": classification,
            "version": version,
            "doc_family": doc_family,
            "injection_flags": injections,
            "injection_flag_count": len(injections),
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
            "ingested_at": utcnow(),
        }
    )
    return meta


def _persist_failure(
    session: Session,
    project_id: str,
    path: Path,
    digest: str,
    existing: dict | None,
    doc_type: str | None,
    version: str | None,
    error: str,
    fmt: str | None,
) -> dict:
    """Persist/refresh a failed document row (audit trail, no pages)."""
    if existing is not None:
        updated = store.update_document(
            session, existing["document_id"], status="failed", parse_error=error
        )
        if updated is not None:
            return updated
    cls_type = normalize_doc_type(doc_type) or classify_document(path.name)[0]
    ver = normalize_version(version) if version else detect_version(filename=path.name)
    family = detect_doc_family(path.name, cls_type)
    meta = {
        "source": "ingest",
        "format": fmt,
        "parser_version": config.PARSER_VERSION,
        "doc_type": cls_type,
        "version": ver,
        "doc_family": family,
        "injection_flags": [],
        "parse_error": error,
    }
    doc, _created = store.create_document(
        session,
        project_id,
        filename=path.name,
        sha256=digest,
        stored_path=str(path.resolve()),
        doc_type=cls_type,
        version=ver,
        doc_family=family,
        page_count=0,
        language="en",
        status="failed",
        meta=meta,
    )
    # create_document does not take parse_error; persist it in a follow-up update.
    updated = store.update_document(session, doc["document_id"], status="failed", parse_error=error)
    return updated or doc


def _reconcile_family(session: Session, project_id: str, family: str) -> None:
    """Ensure the highest-version document in a family is ``is_latest``.

    ``store.create_document`` supersedes by upload order; this corrects the case
    where an older version is ingested after a newer one.
    """
    docs = [doc for doc in store.list_documents(session, project_id) if doc.get("doc_family") == family]
    if len(docs) < 2:
        return
    ordered = sorted(
        docs,
        key=lambda doc: (
            _version_sort_key(doc.get("version")),
            str(doc.get("uploaded_at") or ""),
            str(doc.get("document_id") or ""),
        ),
    )
    eligible = [doc for doc in ordered if doc.get("status") != "failed"]
    if not eligible:
        return
    latest_id = eligible[-1]["document_id"]
    previous_id: str | None = None
    for doc in ordered:
        document_id = doc["document_id"]
        status = doc.get("status")
        if document_id == latest_id:
            store.update_document(
                session,
                document_id,
                is_latest=True,
                status="parsed" if status in {"parsed", "uploaded", "superseded"} else status,
                supersedes=previous_id,
            )
        elif status == "failed":
            store.update_document(session, document_id, is_latest=False, supersedes=previous_id)
        else:
            store.update_document(
                session, document_id, is_latest=False, status="superseded", supersedes=previous_id
            )
        previous_id = document_id


def ingest_file(
    session: Session,
    project_id: str,
    path: str | Path,
    doc_type: str | None = None,
    version: str | None = None,
    force: bool = False,
) -> dict:
    """Parse and persist one file.  Never raises for parse-level problems."""
    file_path = _resolve_path(path)
    result: dict[str, Any] = {
        "document_id": None,
        "filename": file_path.name,
        "status": "failed",
        "doc_type": None,
        "version": None,
        "doc_family": None,
        "page_count": 0,
        "pages_parsed": 0,
        "chunks_created": 0,
        "parse_quality": 0.0,
        "injection_flag_count": 0,
        "injection_flags": [],
        "evidence_deleted": 0,
        "parse_error": None,
    }
    if not file_path.is_file():
        result["parse_error"] = f"file not found: {file_path}"
        return result

    try:
        digest = sha256_file(file_path)
    except OSError as exc:
        result["parse_error"] = f"unreadable file: {exc}"
        return result

    fmt = detect_format(file_path)
    existing = store.get_document_by_sha(session, project_id, digest)
    if existing is not None and not force and existing.get("status") in _INGEST_DOC_STATUSES:
        result.update(
            {
                "document_id": existing["document_id"],
                "status": "skipped",
                "doc_type": existing.get("doc_type"),
                "version": existing.get("version"),
                "doc_family": existing.get("doc_family"),
                "page_count": existing.get("page_count", 0),
                "parse_quality": existing.get("parse_quality") or 0.0,
                "injection_flag_count": len((existing.get("meta") or {}).get("injection_flags") or []),
            }
        )
        return result

    if fmt is None:
        error = f"unsupported document format: {file_path.suffix or file_path.name}"
        doc = _persist_failure(
            session, project_id, file_path, digest, existing, doc_type, version, error, fmt=None
        )
        result.update(
            {
                "document_id": doc["document_id"],
                "doc_type": doc.get("doc_type"),
                "version": doc.get("version"),
                "doc_family": doc.get("doc_family"),
                "parse_error": error,
            }
        )
        return result

    parsed = parse_file(file_path)
    if parsed.parse_error:
        doc = _persist_failure(
            session,
            project_id,
            file_path,
            digest,
            existing,
            doc_type,
            version,
            parsed.parse_error,
            fmt=fmt,
        )
        result.update(
            {
                "document_id": doc["document_id"],
                "doc_type": doc.get("doc_type"),
                "version": doc.get("version"),
                "doc_family": doc.get("doc_family"),
                "parse_error": parsed.parse_error,
            }
        )
        return result

    first_text = parser_common.first_pages_text(parsed.pages)
    title = parsed.title or ""
    cls_type, cls_detail = classify_document(file_path.name, title, first_text)
    override = normalize_doc_type(doc_type)
    if override:
        cls_type = override
        cls_detail = {"doc_type": override, "score": None, "signals": ["doc_type-override"]}
    doc_version = normalize_version(version) if version else detect_version(file_path.name, title, first_text)
    doc_family = detect_doc_family(file_path.name, cls_type)
    language = parsed.language or parser_common.detect_language(first_text)
    injected = scan_pages(parsed.pages)
    doc_date = _detect_doc_date(first_text)
    quality = float(parsed.parse_quality or 0.0)

    meta = _document_meta(
        parsed,
        fmt=fmt,
        path=file_path,
        digest=digest,
        doc_type=cls_type,
        classification=cls_detail,
        version=doc_version,
        doc_family=doc_family,
        injections=injected,
    )
    page_dicts = [_page_to_dict(page) for page in parsed.pages]
    chunks = _build_chunks(parsed.pages)

    evidence_deleted = 0
    if existing is not None:
        document_id = existing["document_id"]
        evidence_deleted = int(store.delete_evidence_for_document(session, document_id) or 0)
        store.delete_chunks(session, document_id)
        store.delete_pages_for_document(session, document_id)
        meta["reingested"] = True
        doc = store.update_document(
            session,
            document_id,
            filename=file_path.name,
            stored_path=str(file_path.resolve()),
            doc_type=cls_type,
            version=doc_version,
            doc_family=doc_family,
            doc_date=doc_date,
            page_count=len(page_dicts),
            language=language,
            status="parsed",
            parse_quality=quality,
            parse_error=None,
            meta=meta,
        )
    else:
        doc, created = store.create_document(
            session,
            project_id,
            filename=file_path.name,
            sha256=digest,
            stored_path=str(file_path.resolve()),
            doc_type=cls_type,
            version=doc_version,
            doc_date=doc_date,
            page_count=len(page_dicts),
            language=language,
            status="parsed",
            doc_family=doc_family,
            meta=meta,
        )
        if not created:
            result.update(
                {
                    "document_id": doc["document_id"],
                    "status": "skipped",
                    "doc_type": doc.get("doc_type"),
                    "version": doc.get("version"),
                    "doc_family": doc.get("doc_family"),
                    "page_count": doc.get("page_count", 0),
                }
            )
            return result
    if doc is None:  # defensive: update_document returned None
        result["parse_error"] = "failed to persist document row"
        return result

    pages_added = store.add_pages(session, doc["document_id"], page_dicts)
    chunks_created = store.add_chunks(session, doc["document_id"], chunks)
    meta["pages"] = len(page_dicts)
    meta["chunks_created"] = chunks_created
    store.update_document(
        session,
        doc["document_id"],
        status="parsed",
        parse_quality=quality,
        parse_error=None,
        page_count=len(page_dicts),
        meta=meta,
    )
    _reconcile_family(session, project_id, doc_family)
    final_doc = store.get_document(session, doc["document_id"]) or doc

    result.update(
        {
            "document_id": final_doc["document_id"],
            "status": "parsed",
            "doc_type": final_doc.get("doc_type"),
            "version": final_doc.get("version"),
            "doc_family": final_doc.get("doc_family"),
            "page_count": final_doc.get("page_count", len(page_dicts)),
            "pages_parsed": int(pages_added),
            "chunks_created": int(chunks_created),
            "parse_quality": quality,
            "injection_flag_count": len(injected),
            "injection_flags": injected,
            "evidence_deleted": evidence_deleted,
            "is_latest": bool(final_doc.get("is_latest", True)),
        }
    )
    return result


def ingest_directory(
    session: Session,
    project_id: str,
    directory: str | Path,
    force: bool = False,
) -> dict:
    """Ingest all supported files directly inside *directory* (sorted)."""
    stats = _empty_stats()
    dir_path = Path(directory)
    stats["source_dir"] = str(dir_path)
    if not dir_path.is_dir():
        stats["error"] = f"package directory not found: {dir_path}"
        return stats
    files = sorted(
        (
            entry
            for entry in dir_path.iterdir()
            if entry.is_file()
            and entry.suffix.lower() in SUPPORTED_EXTENSIONS
            and not entry.name.startswith((".", "~$"))
        ),
        key=lambda entry: entry.name.lower(),
    )
    stats["files_found"] = len(files)
    for file_path in files:
        _merge_file_stats(stats, ingest_file(session, project_id, file_path, force=force))
    stats.pop("source_dir", None)
    stats["source_dir"] = str(dir_path)
    return stats


def ingest_project(session: Session, project_id: str, force: bool = False) -> dict:
    """Pipeline INGEST stage handler (signature per AGENTS_CONTRACT)."""
    stats = _empty_stats()
    directory = resolve_package_dir(session, project_id)
    stats["source_dir"] = str(directory) if directory is not None else None
    if directory is not None:
        _merge_stats(stats, ingest_directory(session, project_id, directory, force=force))

    # API-upload flow / eval setups may have registered documents already;
    # parse any that are still in "uploaded"/"parsing" state.
    for doc in store.list_documents(session, project_id):
        if doc.get("status") not in {"uploaded", "parsing"}:
            continue
        stored_path = doc.get("stored_path") or ""
        if not stored_path:
            continue
        candidate = _resolve_path(stored_path)
        if not candidate.is_file():
            continue
        _merge_file_stats(stats, ingest_file(session, project_id, candidate, force=force))

    project_stats = store.project_stats(session, project_id)
    stats["documents_total"] = int(project_stats.get("documents_all", 0) or 0)
    stats["documents_latest"] = int(project_stats.get("documents", 0) or 0)
    stats["pages_total"] = int(project_stats.get("pages", 0) or 0)
    return stats
