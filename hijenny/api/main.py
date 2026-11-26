"""HiJenny — HTTP API + review web UI host (app-dev owned).

Run locally:

    uvicorn hijenny.api.main:app --reload --port 8123

The app is deliberately tolerant of pipeline modules that are not yet present:
ingestion/reporting imports are lazy and missing modules surface as clear
``503``/stage errors instead of breaking app startup.
"""
from __future__ import annotations

import hashlib
import inspect
import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Header, HTTPException, Path as PathParam, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from hijenny import config, exports, pipeline, reporting, store
from sqlalchemy import text
from hijenny import diligence
from hijenny.schemas import Owner, utcnow

SERVICE_NAME = "HiJenny API"
VERSION = "0.1.0"

MAX_UPLOAD_BYTES = max(1, int(config.MAX_UPLOAD_MB)) * 1024 * 1024
_WEB_DIR = Path(config.WEB_DIR)

_DOC_TYPE_ORDER = [
    ("csr", "CSR"),
    ("clinical study report", "CSR"),
    ("protocol", "PROTOCOL"),
    ("sap", "SAP"),
    ("statistical analysis plan", "SAP"),
    ("investigator", "IB"),
    ("investigator's brochure", "IB"),
    ("safety", "SAFETY"),
    ("disposition", "DISPOSITION"),
    ("cmc", "CMC"),
    ("manufactur", "CMC"),
    ("nonclinical", "PRECLINICAL"),
    ("preclinical", "PRECLINICAL"),
    ("regulatory", "REGULATORY"),
    ("patent", "PATENT"),
    ("commercial", "COMMERCIAL"),
    ("forecast", "COMMERCIAL"),
]

# ---------------------------------------------------------------------------
# Dependencies / helpers
# ---------------------------------------------------------------------------


def _data_dir() -> Path:
    return Path(config.DATA_DIR)


def _project_dir(project_id: str) -> Path:
    return exports.project_dir(project_id)


def _documents_dir(project_id: str) -> Path:
    return exports.documents_dir(project_id)


def _exports_dir(project_id: str) -> Path:
    return exports.exports_dir(project_id)


def _org(header_value: str | None) -> str | None:
    value = (header_value or "").strip()
    return value or None


def _project_or_404(session, project_id: str, org_id: str | None = None) -> dict:
    project = store.get_project(session, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if org_id and project.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return project


def _document_or_404(session, document_id: str, project_id: str | None = None) -> dict:
    document = store.get_document(session, document_id)
    if not document or (project_id and document.get("project_id") != project_id):
        raise HTTPException(status_code=404, detail=f"Document not found: {document_id}")
    return document


def _finding_or_404(session, finding_id: str) -> dict:
    finding = store.get_finding(session, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail=f"Finding not found: {finding_id}")
    return finding


def _question_or_404(session, question_id: str) -> dict:
    question = store.get_question(session, question_id)
    if not question:
        raise HTTPException(status_code=404, detail=f"Question not found: {question_id}")
    return question


def _evidence_or_404(session, evidence_id: str) -> dict:
    rows = store.get_evidence_by_ids(session, [evidence_id])
    if not rows:
        raise HTTPException(status_code=404, detail=f"Evidence not found: {evidence_id}")
    return rows[0]


def _rel_to_data(path: str | Path) -> str | None:
    """Return the POSIX path relative to DATA_DIR, or None when outside it."""
    try:
        return Path(path).resolve().relative_to(_data_dir().resolve()).as_posix()
    except (ValueError, OSError):
        return None


def file_url(path: str | Path) -> str:
    """Public URL for a file under DATA_DIR ('' when outside/serving unsafe)."""
    rel = _rel_to_data(path)
    if rel is None:
        return ""
    return "/api/files/" + quote(rel, safe="/")


def _stored_path_url(document: dict) -> str:
    stored = (document.get("stored_path") or "").strip()
    if not stored:
        return ""
    candidate = Path(stored)
    if not candidate.is_absolute():
        candidate = _data_dir() / candidate
    return file_url(candidate)


def _doc_json(document: dict) -> dict:
    out = dict(document)
    url = _stored_path_url(document)
    name = str(document.get("filename") or "")
    out["file_url"] = url
    out["pdf_url"] = url if name.lower().endswith(".pdf") else ""
    out["is_pdf"] = name.lower().endswith(".pdf")
    return out


def _safe_filename(raw: str | None) -> str:
    name = Path(raw or "upload.bin").name
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .")
    return name or "upload.bin"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _group_counts(rows: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _guess_doc_type(filename: str) -> str:
    """Classification fallback until ``hijenny.ingestion`` exists (heuristic)."""
    try:  # pragma: no cover - depends on parser-dev module availability
        from hijenny import ingestion  # type: ignore

        for attr in ("classify_filename", "classify_document", "classify"):
            func = getattr(ingestion, attr, None)
            if callable(func):
                try:
                    result = func(filename)
                except Exception:
                    continue
                if isinstance(result, (tuple, list)) and result:
                    result = result[0]
                if isinstance(result, str) and result:
                    return result.upper()
    except Exception:
        pass
    lowered = filename.lower()
    for needle, doc_type in _DOC_TYPE_ORDER:
        if needle in lowered:
            return doc_type
    return "OTHER"


def _document_detail(session, project_id: str, document_id: str) -> dict:
    document = _document_or_404(session, document_id, project_id)
    pages = store.get_pages(session, document_id)
    evidence = store.get_evidence(session, project_id, document_id=document_id)
    doc = _doc_json(document)
    doc["pages"] = [
        {"page_no": page["page_no"], "char_count": page.get("char_count", len(page.get("text") or ""))}
        for page in pages
    ]
    doc["evidence_count"] = len(evidence)
    return {"document": doc, "project_id": project_id}


def _page_payload(session, project_id: str, document_id: str, page: int) -> dict:
    document = _document_or_404(session, document_id, project_id)
    page_data = store.get_page(session, document_id, page)
    if page_data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page} of document {document_id} is not parsed/available",
        )
    pages = store.get_pages(session, document_id)
    evidence = store.get_evidence(session, project_id, document_id=document_id)
    page_evidence = [ev for ev in evidence if int(ev.get("page") or 0) == page]
    return {
        "project_id": project_id,
        "document": _doc_json(document),
        "page": page_data,
        "page_no": page_data["page_no"],
        "total_pages": len(pages) or document.get("page_count") or 0,
        "blocks": page_data.get("blocks", []),
        "text": page_data.get("text", ""),
        "evidence": page_evidence,
    }


def _outputs_info(project_id: str) -> dict:
    md = exports.export_filename(project_id, "md")
    docx = exports.export_filename(project_id, "docx")
    xlsx = exports.export_filename(project_id, "questions")
    return {
        "report_md": {"exists": md.is_file(), "url": file_url(md), "name": md.name},
        "report_docx": {"exists": docx.is_file(), "url": file_url(docx), "name": docx.name},
        "questions_xlsx": {"exists": xlsx.is_file(), "url": file_url(xlsx), "name": xlsx.name},
    }


def _progress(run: dict) -> dict:
    """Progress summary for a run row.

    ``pipeline.progress`` indexes every stage key directly, so partial stats
    (a run between stages) would raise; normalise missing stage keys first and
    fall back to an equivalent local calculation.
    """
    stats = dict(run.get("stats") or {})
    for stage in pipeline.STAGES:
        stats.setdefault(f"stage_{stage.lower()}", {})
    normalized = dict(run)
    normalized["stats"] = stats
    try:
        return pipeline.progress(normalized)
    except Exception:  # noqa: BLE001 - keep the UI alive on unexpected stats shapes
        done = sum(
            1
            for stage in pipeline.STAGES
            if isinstance(stats.get(f"stage_{stage.lower()}"), dict)
            and stats[f"stage_{stage.lower()}"].get("state") == "done"
        )
        return {
            "run_id": run.get("run_id"),
            "state": run.get("state"),
            "status": run.get("status"),
            "stages_done": done,
            "stages_total": len(pipeline.STAGES),
            "percent": round(100 * done / len(pipeline.STAGES)),
            "error": run.get("error"),
        }


def _run_json(run: dict) -> dict:
    return {"run": run, "progress": _progress(run)}


# ---------------------------------------------------------------------------
# Lazy pipeline glue (modules may not exist yet)
# ---------------------------------------------------------------------------


def _ingest_file_handler():
    """Return ``hijenny.ingestion.ingest_file`` when available, else None."""
    try:
        from hijenny.ingestion import ingest_file  # type: ignore

        return ingest_file
    except Exception:
        return None


def _call_ingest_file(handler, session, project_id: str, document: dict):
    """Call parser-dev's ``ingest_file`` with whatever signature it exposes."""
    document_id = document["document_id"]
    try:
        params = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):  # pragma: no cover - builtins only
        params = []

    kwargs: dict[str, Any] = {}
    for param in params:
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        name = param.name
        if name == "session":
            kwargs[name] = session
        elif name in {"project_id", "project"}:
            kwargs[name] = project_id
        elif name == "document_id":
            kwargs[name] = document_id
        elif name == "document":
            kwargs[name] = document
        elif name in {"force", "reparse"}:
            kwargs[name] = False
        elif name in {"file_path", "path"}:
            kwargs[name] = document.get("stored_path")
        elif param.default is inspect.Parameter.empty:
            # Unknown required parameter: fall through to positional attempts.
            kwargs = {}
            break
    if kwargs:
        try:
            return handler(**kwargs)
        except TypeError as exc:
            message = str(exc)
            if "argument" not in message and "positional" not in message:
                raise
    # Signature fallbacks (defensive; parser-dev owns the real signature).
    attempts = (
        lambda: handler(session, project_id, document_id),
        lambda: handler(session, document_id),
        lambda: handler(session, document),
        lambda: handler(document, force=False),
    )
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise RuntimeError("could not call hijenny.ingestion.ingest_file")  # pragma: no cover


def _ingest_one(handler, session, project_id: str, document: dict) -> dict:
    result: dict[str, Any] = {
        "document_id": document["document_id"],
        "filename": document.get("filename"),
        "ok": False,
        "result": None,
        "error": None,
    }
    try:
        parsed = _call_ingest_file(handler, session, project_id, document)
        result["ok"] = True
        if isinstance(parsed, dict):
            result["result"] = parsed
        elif parsed is not None:
            result["result"] = {"detail": str(parsed)}
    except Exception as exc:  # noqa: BLE001 - surfaced to the client
        result["error"] = f"{type(exc).__name__}: {exc}"
        current = store.get_document(session, document["document_id"])
        if current and current.get("status") in {"uploaded", "parsing"}:
            store.update_document(
                session,
                document["document_id"],
                status="failed",
                parse_error=result["error"][:2000],
            )
    return result


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

FindingStatusLit = Literal["open", "confirmed", "dismissed", "resolved", "needs_review"]
SeverityLit = Literal["critical", "high", "medium", "low", "info"]
PriorityLit = Literal["critical", "high", "medium", "low"]
QuestionStatusLit = Literal["draft", "reviewed", "approved", "sent", "answered", "closed", "dismissed"]
ReviewStatusLit = Literal["pending", "approved", "edited", "rejected"]
VerificationLit = Literal["pending", "verified", "failed", "unverifiable"]
CoverageStatusLit = Literal["sufficient", "partial", "not_found", "not_applicable", "not_reviewed"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    asset_name: str = Field(default="", max_length=200)
    indication: str = Field(default="", max_length=200)
    phase: str = Field(default="", max_length=40)
    org_id: str = Field(default="ORG_DEFAULT", max_length=64)
    meta: dict[str, Any] = Field(default_factory=dict)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    asset_name: str | None = None
    indication: str | None = None
    phase: str | None = None
    status: str | None = None
    meta: dict[str, Any] | None = None
    actor: str = "reviewer"


class AnalyzeRequest(BaseModel):
    force: bool = False


class FindingPatch(BaseModel):
    status: FindingStatusLit | None = None
    severity: SeverityLit | None = None
    title: str | None = None
    explanation: str | None = None
    recommended_action: str | None = None
    actor: str = "reviewer"


class QuestionPatch(BaseModel):
    question: str | None = Field(default=None, min_length=10)
    rationale: str | None = None
    category: str | None = None
    priority: PriorityLit | None = None
    owner: str | None = None
    status: QuestionStatusLit | None = None
    review_status: ReviewStatusLit | None = None
    required_documents: list[str] | None = None
    locked: bool | None = None
    actor: str = "reviewer"

    @field_validator("owner")
    @classmethod
    def _owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = {owner.value for owner in Owner}
        if value not in allowed:
            raise ValueError(f"owner must be one of {sorted(allowed)}")
        return value


class EvidencePatch(BaseModel):
    verification_status: VerificationLit
    note: str | None = None
    actor: str = "reviewer"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover - exercised by TestClient
    config.ensure_dirs()
    try:
        store.init_db()
    except Exception:  # noqa: BLE001 - app must still boot to report health
        pass
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="HiJenny",
        version=VERSION,
        description="Evidence-first pharma BD due-diligence API and review UI.",
        lifespan=_lifespan,
    )
    from .public_events import router as public_events_router
    app.include_router(public_events_router)
    from .research import router as research_router
    from .discovery import router as discovery_router
    app.include_router(discovery_router)
    from .pilot import router as pilot_router
    app.include_router(pilot_router)
    from .clinical import router as clinical_router
    app.include_router(research_router)
    from .brief import router as brief_router
    app.include_router(brief_router)
    app.include_router(clinical_router)
    from .medical import router as medical_router
    app.include_router(medical_router)
    from .study import router as study_router
    app.include_router(study_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if _WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")

    # -- UI -----------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index():
        index_file = Path(config.WEB_DIR) / "index.html"
        if index_file.is_file():
            return FileResponse(index_file, media_type="text/html")
        return HTMLResponse(
            "<h1>HiJenny</h1><p>Review UI not installed (web/index.html missing).</p>",
            status_code=200,
        )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    # -- Health -------------------------------------------------------------
    @app.get("/api/health")
    def health():
        database = "ok"
        try:
            store.get_engine()
        except Exception as exc:  # noqa: BLE001
            database = f"error: {type(exc).__name__}: {exc}"
        return {
            "status": "ok" if database == "ok" else "degraded",
            "service": SERVICE_NAME,
            "version": VERSION,
            "database": database,
            "db_path": str(config.DB_PATH),
            "data_dir": str(_data_dir()),
            "web_available": (Path(config.WEB_DIR) / "index.html").is_file(),
            "ingestion_available": _ingest_file_handler() is not None,
            "time": utcnow(),
        }

    @app.get("/api/llm-status")
    def llm_status():
        available = config.llm_available()
        return {
            "available": available,
            "mode": config.EXTRACT_MODE,
            "model": config.LLM_MODEL if available else None,
            "base_url": config.LLM_BASE_URL if available else None,
            "offline_default": True,
            "note": (
                "LLM mode is enabled; evidence is still human-verifiable."
                if available
                else "Offline deterministic extraction (no API key configured)."
            ),
        }

    # -- Projects -----------------------------------------------------------
    @app.post("/api/projects", status_code=201)
    def create_project(payload: ProjectCreate, x_org_id: str | None = Header(default=None, alias="X-Org-Id")):
        with store.session_scope() as session:
            project = store.create_project(
                session,
                name=payload.name.strip(),
                org_id=_org(x_org_id) or payload.org_id,
                asset_name=payload.asset_name.strip(),
                indication=payload.indication.strip(),
                phase=payload.phase.strip(),
                meta=payload.meta or {},
            )
        return {"project": project}

    @app.get("/api/projects")
    def list_projects(
        org_id: str | None = Query(default=None),
        x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    ):
        scope = _org(x_org_id) or _org(org_id)
        with store.session_scope() as session:
            projects = store.list_projects(session, org_id=scope)
            enriched = []
            for project in projects:
                stats = store.project_stats(session, project["project_id"])
                enriched.append(
                    {
                        **project,
                        "stats": stats,
                        "outputs": _outputs_info(project["project_id"]),
                    }
                )
        return {"projects": enriched, "count": len(enriched)}

    @app.get("/api/projects/{project_id}")
    def get_project(
        project_id: str,
        x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    ):
        with store.session_scope() as session:
            project = _project_or_404(session, project_id, _org(x_org_id))
            stats = store.project_stats(session, project_id)
            runs = store.list_runs(session, project_id, limit=5)
            documents = store.list_documents(session, project_id)
            assets = store.list_assets(session, project_id)
            trials = store.list_trials(session, project_id)
        return {
            "project": project,
            "stats": stats,
            "latest_run": _run_json(runs[0]) if runs else None,
            "runs": [_run_json(r) for r in runs],
            "documents": [_doc_json(d) for d in documents],
            "assets": assets,
            "trials": trials,
            "outputs": _outputs_info(project_id),
        }

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, payload: ProjectPatch):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            fields = payload.model_dump(exclude_none=True)
            actor = fields.pop("actor", "reviewer")
            before = store.get_project(session, project_id) or {}
            project = store.update_project(session, project_id, **fields)
            after = store.get_project(session, project_id) or {}
            if after != before:
                store.log_review_event(session, project_id, "project", project_id, actor, "update", before, after)
        return {"project": project}

    # -- Documents ----------------------------------------------------------
    @app.get("/api/projects/{project_id}/documents")
    def list_documents(
        project_id: str,
        latest_only: bool = Query(default=False),
        x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    ):
        with store.session_scope() as session:
            _project_or_404(session, project_id, _org(x_org_id))
            documents = store.list_documents(session, project_id, latest_only=latest_only)
            evidence = store.get_evidence(session, project_id, limit=10000)
        evidence_counts: dict[str, int] = {}
        for ev in evidence:
            evidence_counts[ev["document_id"]] = evidence_counts.get(ev["document_id"], 0) + 1
        docs = []
        for document in documents:
            doc = _doc_json(document)
            doc["evidence_count"] = evidence_counts.get(document["document_id"], 0)
            docs.append(doc)
        return {"documents": docs, "count": len(docs)}

    @app.post("/api/projects/{project_id}/documents")
    async def upload_documents(
        project_id: str,
        files: list[UploadFile] = File(...),
        parse: bool = Query(default=True),
        doc_type: str | None = Query(default=None),
        x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    ):
        if not files:
            raise HTTPException(status_code=400, detail="No files supplied")

        handler = _ingest_file_handler()
        registered: list[dict] = []
        ingestion_results: list[dict] = []
        duplicates: list[str] = []
        warnings: list[str] = []

        with store.session_scope() as session:
            project = _project_or_404(session, project_id, _org(x_org_id))
            docs_dir = _documents_dir(project_id)
            docs_dir.mkdir(parents=True, exist_ok=True)

            for upload in files:
                raw_name = _safe_filename(upload.filename)
                content = await upload.read()
                if not content:
                    warnings.append(f"{raw_name}: empty file skipped")
                    continue
                if len(content) > MAX_UPLOAD_BYTES:
                    warnings.append(f"{raw_name}: exceeds {config.MAX_UPLOAD_MB} MB limit, skipped")
                    continue
                sha = _sha256(content)
                target = docs_dir / raw_name
                if target.exists():
                    stem, suffix = target.stem, target.suffix
                    counter = 1
                    while target.exists():
                        target = docs_dir / f"{stem}-{counter}{suffix}"
                        counter += 1
                target.write_bytes(content)
                rel_path = target.relative_to(_data_dir()).as_posix()
                document, created = store.create_document(
                    session,
                    project_id=project_id,
                    filename=raw_name,
                    sha256=sha,
                    stored_path=rel_path,
                    doc_type=(doc_type or _guess_doc_type(raw_name)).upper(),
                    status="uploaded",
                    meta={"size_bytes": len(content), "content_type": upload.content_type},
                )
                if not created:
                    duplicates.append(raw_name)
                    warnings.append(f"{raw_name}: identical file already registered (sha256 match)")
                    # Do not leave an orphaned duplicate copy on disk (keep the
                    # canonical stored file untouched).
                    try:
                        stored = (document.get("stored_path") or "").strip()
                        stored_abs = Path(stored) if stored else None
                        if stored_abs is not None and not stored_abs.is_absolute():
                            stored_abs = _data_dir() / stored_abs
                        if stored_abs is None or stored_abs.resolve() != target.resolve():
                            target.unlink(missing_ok=True)
                    except OSError:  # pragma: no cover - best-effort cleanup
                        pass
                    registered.append(_doc_json(document))
                    continue
                registered.append(_doc_json(document))

                if not parse:
                    continue
                if handler is None:
                    continue
                ingestion_results.append(_ingest_one(handler, session, project_id, document))

            if project.get("status") == "CREATED":
                store.update_project(session, project_id, status="UPLOADING")

            # Refresh persisted rows after ingestion updates.
            refreshed = {
                d_id: store.get_document(session, d_id)
                for d_id in {d["document_id"] for d in registered}
            }

        documents_out = [_doc_json(refreshed[d["document_id"]] or d) for d in registered]

        ingestion_payload = {
            "available": handler is not None,
            "attempted": bool(parse) and handler is not None,
            "results": ingestion_results,
            "errors": [r for r in ingestion_results if r.get("error")],
            "message": None,
        }
        payload = {
            "project_id": project_id,
            "count": len(documents_out),
            "created": len(registered) - len(duplicates),
            "duplicates": duplicates,
            "documents": documents_out,
            "ingestion": ingestion_payload,
            "warnings": warnings,
        }

        if parse and handler is None:
            msg = (
                "Documents were registered and saved, but the parsing module "
                "(hijenny.ingestion) is not available yet, so pages were not extracted. "
                "Re-upload or run analysis once the parser is installed."
            )
            ingestion_payload["message"] = msg
            payload["detail"] = msg
            raise HTTPException(status_code=503, detail=payload)
        if not parse:
            ingestion_payload["message"] = "parse=false: documents registered only; run analysis to parse."
        return payload

    @app.get("/api/projects/{project_id}/documents/{document_id}")
    def get_document(project_id: str, document_id: str):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            return _document_detail(session, project_id, document_id)

    @app.get("/api/projects/{project_id}/documents/{document_id}/pages/{page}")
    def get_page(project_id: str, document_id: str, page: int = PathParam(..., ge=1)):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            return _page_payload(session, project_id, document_id, page)

    # Convenience aliases (document id is globally unique).
    @app.get("/api/documents/{document_id}")
    def get_document_alias(document_id: str):
        with store.session_scope() as session:
            document = _document_or_404(session, document_id)
            return _document_detail(session, document["project_id"], document_id)

    @app.get("/api/documents/{document_id}/pages/{page}")
    def get_page_alias(document_id: str, page: int = PathParam(..., ge=1)):
        with store.session_scope() as session:
            document = _document_or_404(session, document_id)
            return _page_payload(session, document["project_id"], document_id, page)

    @app.post("/api/projects/{project_id}/documents/{document_id}/parse")
    def parse_document(project_id: str, document_id: str):
        handler = _ingest_file_handler()
        if handler is None:
            raise HTTPException(
                status_code=503,
                detail="Parsing module (hijenny.ingestion) is not available yet; document left registered only.",
            )
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            document = _document_or_404(session, document_id, project_id)
            result = _ingest_one(handler, session, project_id, document)
            if result.get("error"):
                store.update_document(session, document_id, status="failed", parse_error=result["error"][:2000])
            refreshed = store.get_document(session, document_id) or document
        return {"document": _doc_json(refreshed), "ingestion": result}

    # -- Analysis runs ------------------------------------------------------
    @app.post("/api/projects/{project_id}/analyze")
    def analyze_project(project_id: str, payload: AnalyzeRequest | None = None):
        force = bool(payload.force) if payload else False
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            runs = store.list_runs(session, project_id, limit=1)
            if runs and runs[0].get("status") == "running":
                return {
                    "run_id": runs[0]["run_id"],
                    "project_id": project_id,
                    "already_running": True,
                    "progress": _progress(runs[0]),
                }
        run_id = pipeline.start_pipeline(project_id, force=force)
        with store.session_scope() as session:
            run = store.get_run(session, run_id)
        return {
            "run_id": run_id,
            "project_id": project_id,
            "already_running": False,
            "progress": _progress(run) if run else {"run_id": run_id, "percent": 0, "status": "running"},
        }

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        with store.session_scope() as session:
            run = store.get_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        return _run_json(run)

    @app.get("/api/projects/{project_id}/runs")
    def list_runs(project_id: str, limit: int = Query(default=20, ge=1, le=100)):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            runs = store.list_runs(session, project_id, limit=limit)
        return {"runs": [_run_json(r) for r in runs], "count": len(runs)}

    # -- Evidence -----------------------------------------------------------
    @app.get("/api/projects/{project_id}/evidence")
    def list_evidence(
        project_id: str,
        field_name: str | None = Query(default=None),
        category: str | None = Query(default=None),
        document_id: str | None = Query(default=None),
        verification_status: VerificationLit | None = Query(default=None),
        trial_key: str | None = Query(default=None),
        asset_id: str | None = Query(default=None),
        search: str | None = Query(default=None),
        limit: int = Query(default=2000, ge=1, le=10000),
    ):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            rows = store.get_evidence(
                session,
                project_id,
                field_name=field_name,
                category=category,
                document_id=document_id,
                asset_id=asset_id,
                trial_key=trial_key,
                verification_status=verification_status,
                limit=limit,
            )
        if search:
            needle = search.strip().lower()
            rows = [
                ev
                for ev in rows
                if needle in str(ev.get("quote") or "").lower()
                or needle in str(ev.get("value_text") or "").lower()
                or needle in str(ev.get("filename") or "").lower()
                or needle in str(ev.get("field_name") or "").lower()
            ]
        return {
            "evidence": rows,
            "count": len(rows),
            "facets": {
                "field_name": _group_counts(rows, "field_name"),
                "category": _group_counts(rows, "category"),
                "document_id": _group_counts(rows, "document_id"),
                "verification_status": _group_counts(rows, "verification_status"),
            },
        }

    @app.get("/api/evidence/{evidence_id}")
    def get_evidence(evidence_id: str):
        with store.session_scope() as session:
            evidence = _evidence_or_404(session, evidence_id)
            document = store.get_document(session, evidence["document_id"])
            findings = [
                f
                for f in store.list_findings(session, evidence["project_id"])
                if evidence_id in (f.get("evidence_ids") or [])
            ]
            questions = [
                q
                for q in store.list_questions(session, evidence["project_id"])
                if evidence_id in (q.get("evidence_ids") or [])
            ]
        return {
            "evidence": evidence,
            "document": _doc_json(document) if document else None,
            "findings": findings,
            "questions": questions,
        }

    @app.patch("/api/evidence/{evidence_id}")
    def patch_evidence(evidence_id: str, payload: EvidencePatch):
        with store.session_scope() as session:
            evidence = _evidence_or_404(session, evidence_id)
            before = dict(evidence)
            updated = store.update_evidence_verification(
                session,
                evidence_id,
                status=payload.verification_status,
                note=payload.note,
                by=payload.actor,
            )
            if updated:
                store.log_review_event(
                    session,
                    evidence["project_id"],
                    "evidence",
                    evidence_id,
                    payload.actor,
                    "verify",
                    before,
                    updated,
                )
        return {"evidence": updated}

    # -- Findings -----------------------------------------------------------
    @app.get("/api/projects/{project_id}/findings")
    def list_findings(
        project_id: str,
        severity: SeverityLit | None = Query(default=None),
        status: FindingStatusLit | None = Query(default=None),
        finding_type: str | None = Query(default=None),
        domain: str | None = Query(default=None),
    ):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            rows = store.list_findings(
                session, project_id, status=status, severity=severity, finding_type=finding_type
            )
        if domain:
            rows = [row for row in rows if row.get("domain") == domain]
        return {
            "findings": rows,
            "count": len(rows),
            "facets": {
                "severity": _group_counts(rows, "severity"),
                "status": _group_counts(rows, "status"),
                "finding_type": _group_counts(rows, "finding_type"),
                "domain": _group_counts(rows, "domain"),
            },
        }

    @app.get("/api/findings/{finding_id}")
    def get_finding(finding_id: str):
        with store.session_scope() as session:
            finding = _finding_or_404(session, finding_id)
            evidence = store.get_evidence_by_ids(session, finding.get("evidence_ids") or [])
            questions = [
                q
                for q in store.list_questions(session, finding["project_id"])
                if finding_id in (q.get("finding_ids") or [])
            ]
        return {"finding": finding, "evidence": evidence, "questions": questions}

    @app.patch("/api/findings/{finding_id}")
    def patch_finding(finding_id: str, payload: FindingPatch):
        fields = payload.model_dump(exclude_none=True)
        actor = fields.pop("actor", "reviewer")
        with store.session_scope() as session:
            _finding_or_404(session, finding_id)
            updated = store.update_finding(session, finding_id, actor=actor, **fields)
        return {"finding": updated}

    # -- Questions ----------------------------------------------------------
    @app.get("/api/projects/{project_id}/questions")
    def list_questions(
        project_id: str,
        status: QuestionStatusLit | None = Query(default=None),
        priority: PriorityLit | None = Query(default=None),
        owner: str | None = Query(default=None),
        review_status: ReviewStatusLit | None = Query(default=None),
    ):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            rows = store.list_questions(session, project_id, status=status, priority=priority)
        if owner:
            rows = [row for row in rows if row.get("owner") == owner]
        if review_status:
            rows = [row for row in rows if row.get("review_status") == review_status]
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        rows.sort(key=lambda r: (order.get(str(r.get("priority")), 9), str(r.get("created_at") or "")))
        return {
            "questions": rows,
            "count": len(rows),
            "facets": {
                "priority": _group_counts(rows, "priority"),
                "status": _group_counts(rows, "status"),
                "owner": _group_counts(rows, "owner"),
                "category": _group_counts(rows, "category"),
            },
        }

    @app.get("/api/questions/{question_id}")
    def get_question(question_id: str):
        with store.session_scope() as session:
            question = _question_or_404(session, question_id)
            evidence = store.get_evidence_by_ids(session, question.get("evidence_ids") or [])
        return {"question": question, "evidence": evidence}

    @app.patch("/api/questions/{question_id}")
    def patch_question(question_id: str, payload: QuestionPatch):
        fields = payload.model_dump(exclude_none=True)
        actor = fields.pop("actor", "reviewer")
        with store.session_scope() as session:
            _question_or_404(session, question_id)
            updated = store.update_question(session, question_id, actor=actor, **fields)
        return {"question": updated}

    @app.delete("/api/questions/{question_id}")
    def delete_question(question_id: str, actor: str = Query(default="reviewer")):
        with store.session_scope() as session:
            _question_or_404(session, question_id)
            deleted = store.delete_question(session, question_id, actor=actor)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Question not found: {question_id}")
        return {"deleted": True, "question_id": question_id, "actor": actor}

    # -- Coverage / stats / review -----------------------------------------
    @app.get("/api/projects/{project_id}/coverage")
    def get_coverage(project_id: str, status: CoverageStatusLit | None = Query(default=None)):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            rows = store.list_coverage(session, project_id, status=status)
        return {
            "coverage": rows,
            "count": len(rows),
            "facets": {"status": _group_counts(rows, "status"), "priority": _group_counts(rows, "priority")},
        }

    @app.get("/api/projects/{project_id}/stats")
    def get_stats(project_id: str):
        with store.session_scope() as session:
            project = _project_or_404(session, project_id)
            stats = store.project_stats(session, project_id)
            documents = store.list_documents(session, project_id)
            runs = store.list_runs(session, project_id, limit=5)
        return {
            "project": project,
            "stats": stats,
            "documents_by_type": _group_counts(documents, "doc_type"),
            "documents_by_status": _group_counts(documents, "status"),
            "latest_run": _run_json(runs[0]) if runs else None,
            "runs": [_run_json(r) for r in runs],
            "outputs": _outputs_info(project_id),
        }

    @app.get("/api/projects/{project_id}/overview")
    def project_overview(project_id: str):
        """Aggregated asset-discovery + evaluation data for the interactive dashboard."""
        from hijenny.overview import build_overview

        with store.session_scope() as session:
            _project_or_404(session, project_id)
            return build_overview(session, project_id)

    @app.get("/api/projects/{project_id}/review-events")
    def list_review_events(project_id: str, limit: int = Query(default=200, ge=1, le=1000)):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            events = store.list_review_events(session, project_id, limit=limit)
        return {"events": events, "count": len(events)}

    @app.get("/api/projects/{project_id}/followup")
    def followup(project_id: str):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            history = diligence.histories(session, project_id)
            questions = store.list_questions(session, project_id)
            return {"questions": questions, "history": history,
                    "allowed": {q["question_id"]: diligence.allowed(history.get(q["question_id"], []), q)
                                for q in questions},
                    "documents": [_doc_json(d) for d in store.list_documents(session, project_id)],
                    "evidence": store.get_evidence(session, project_id, limit=10000)}

    @app.get("/api/projects/{project_id}/followup/{question_id}/valuation-context")
    def valuation_context(project_id: str, question_id: str):
        with store.session_scope() as session:
            session.execute(text("BEGIN"))
            q = _question_or_404(session, question_id)
            if q["project_id"] != project_id:
                raise HTTPException(404, "Question not found in project")
            events = store.list_review_events(session, project_id, -1, followup_question=question_id)
            events.sort(key=lambda event: event["after"]["revision"])
            replies = [dict(event_id=e["event_id"], ts=e["ts"], actor=e["actor"], **e["after"])
                       for e in events if e["action"] == "reply"]
            return {"question_id": question_id, "question": q["question"], "status": q["status"],
                    "revision": events[-1]["after"]["revision"] if events else 0, "replies": replies}

    @app.post("/api/projects/{project_id}/followup/{question_id}")
    def record_followup(project_id: str, question_id: str, action: diligence.Action):
        with store.session_scope() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            _project_or_404(session, project_id)
            event = diligence.append(session, project_id, question_id, action)
            history = diligence.histories(session, project_id)[question_id]
            if any(e["action"] == "publish" for e in history):
                reporting.generate_project_outputs(session, project_id)
            return event

    # -- Exports ------------------------------------------------------------
    @app.get("/api/projects/{project_id}/exports/seller-reply.xlsx")
    def export_seller_reply(project_id: str):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            approved = [q for q in store.list_questions(session, project_id, status="approved")
                        if q.get("review_status") != "rejected"]
        if not approved:
            raise HTTPException(409, "请先在卖方问题列表将拟发送问题的状态设为 approved。")
        return Response(exports.seller_reply_workbook(approved),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="HiJenny-Seller-Reply.xlsx"',
                                 "Cache-Control": "no-store"})

    @app.post("/api/projects/{project_id}/exports/questions")
    def export_questions(project_id: str):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            path = exports.build_questions_workbook(project_id, session=session)
        return {
            "kind": "questions_xlsx",
            "path": str(path),
            "name": path.name,
            "download_url": file_url(path),
            "sheets": [
                "Executive Summary",
                "Asset Profile",
                "Evidence Matrix",
                "Findings",
                "Questions",
                "Coverage",
            ],
        }

    @app.post("/api/projects/{project_id}/exports/report")
    def export_report(project_id: str):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
            stats = reporting.generate_project_outputs(session, project_id)
        return {
            "stats": stats,
            "downloads": {
                "report_md": {
                    "path": stats["report_md"],
                    "name": Path(stats["report_md"]).name,
                    "url": file_url(stats["report_md"]),
                },
                "report_docx": {
                    "path": stats["report_docx"],
                    "name": Path(stats["report_docx"]).name,
                    "url": file_url(stats["report_docx"]),
                },
                "questions_xlsx": {
                    "path": stats["questions_xlsx"],
                    "name": Path(stats["questions_xlsx"]).name,
                    "url": file_url(stats["questions_xlsx"]),
                },
            },
        }

    @app.get("/api/projects/{project_id}/outputs")
    def get_outputs(project_id: str):
        with store.session_scope() as session:
            _project_or_404(session, project_id)
        return {"project_id": project_id, "outputs": _outputs_info(project_id)}

    # -- Safe file serving --------------------------------------------------
    @app.get("/api/files/{file_path:path}")
    def serve_file(file_path: str):
        if not file_path or file_path.endswith("/"):
            raise HTTPException(status_code=404, detail="File not found")
        data_root = _data_dir().resolve()
        candidate = (data_root / file_path).resolve()
        if candidate != data_root and data_root not in candidate.parents:
            raise HTTPException(status_code=403, detail="Path traversal blocked")
        # Only project document/export artefacts are downloadable — never the
        # SQLite database or arbitrary data-dir files.
        try:
            parts = candidate.relative_to(data_root).parts
        except ValueError:  # pragma: no cover - already covered above
            raise HTTPException(status_code=403, detail="Path traversal blocked") from None
        allowed = (
            len(parts) >= 4
            and parts[0] == "projects"
            and parts[2] in {"documents", "exports"}
        )
        if not allowed:
            raise HTTPException(status_code=404, detail="File not found")
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        media_type = _media_type(candidate)
        headers: dict[str, str] = {"X-Content-Type-Options": "nosniff"}
        if not media_type.startswith(("application/pdf", "text/", "image/")):
            headers["Content-Disposition"] = f'attachment; filename="{candidate.name}"'
        return FileResponse(candidate, media_type=media_type, headers=headers)

    return app


_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def _media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _MEDIA_TYPES:
        return _MEDIA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


app = create_app()
