"""Persistence helpers (frozen contract, Lead-owned).

Modules must use these helpers instead of touching SQLAlchemy models directly.
Every function takes an explicit ``Session`` first so callers control
transactions; the API layer uses ``session_scope()`` per request.

All returned values are plain dicts (JSON-serialisable) so that modules and the
HTTP layer stay decoupled from the ORM.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.event import listen
from sqlalchemy.orm import Session, sessionmaker

from hijenny import config
from hijenny.db import (
    AgentRun,
    Asset,
    Base,
    ClinicalTrial,
    CoverageItem,
    Document,
    DocumentChunk,
    DocumentPage,
    Evidence,
    Finding,
    FindingEvidence,
    Project,
    ProjectMember,
    Question,
    QuestionEvidence,
    ReviewEvent,
    User,
)
from hijenny.schemas import (
    CoverageDraft,
    EvidenceDraft,
    FindingDraft,
    QuestionDraft,
    comparable_key,
    evidence_fingerprint,
    new_id,
    normalize_ws,
    utcnow,
)

_ENGINE = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


# ---------------------------------------------------------------------------
# Engine / session plumbing
# ---------------------------------------------------------------------------


def configure(db_path: str | Path | None = None) -> None:
    """Point the store at another database (tests).  Resets the engine."""
    global _ENGINE, _SESSION_FACTORY
    if db_path is not None:
        config.DB_PATH = Path(db_path)
    config.ensure_dirs()
    _ENGINE = None
    _SESSION_FACTORY = None


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        config.ensure_dirs()
        _ENGINE = create_engine(
            f"sqlite:///{config.DB_PATH}",
            connect_args={"check_same_thread": False},
            future=True,
        )

        def _pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=8000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        listen(_ENGINE, "connect", _pragmas)
        Base.metadata.create_all(_ENGINE)
    return _ENGINE


def _factory() -> sessionmaker[Session]:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SESSION_FACTORY


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(db_path: str | Path | None = None) -> None:
    if db_path is not None:
        configure(db_path)
    get_engine()


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _load(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _project_dict(r: Project) -> dict:
    return {
        "project_id": r.project_id,
        "name": r.name,
        "org_id": r.org_id,
        "asset_name": r.asset_name,
        "indication": r.indication,
        "phase": r.phase,
        "status": r.status,
        "meta": _load(r.meta_json, {}),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _asset_dict(r: Asset) -> dict:
    return {
        "asset_id": r.asset_id,
        "project_id": r.project_id,
        "name": r.name,
        "modality": r.modality,
        "target": r.target,
        "mechanism": r.mechanism,
        "indication": r.indication,
        "phase": r.phase,
        "meta": _load(r.meta_json, {}),
        "created_at": r.created_at,
    }


def _doc_dict(r: Document) -> dict:
    return {
        "document_id": r.document_id,
        "project_id": r.project_id,
        "filename": r.filename,
        "stored_path": r.stored_path,
        "sha256": r.sha256,
        "doc_type": r.doc_type,
        "doc_family": r.doc_family,
        "version": r.version,
        "doc_date": r.doc_date,
        "page_count": r.page_count,
        "language": r.language,
        "status": r.status,
        "parse_quality": r.parse_quality,
        "parse_error": r.parse_error,
        "parser_version": r.parser_version,
        "is_latest": bool(r.is_latest),
        "supersedes": r.supersedes,
        "uploaded_at": r.uploaded_at,
        "meta": _load(r.meta_json, {}),
    }


def _evidence_dict(r: Evidence) -> dict:
    bbox = _load(r.bbox_json, [])
    return {
        "evidence_id": r.evidence_id,
        "project_id": r.project_id,
        "asset_id": r.asset_id,
        "trial_id": r.trial_id,
        "document_id": r.document_id,
        "document_type": r.document_type,
        "filename": r.filename,
        "field_name": r.field_name,
        "category": r.category,
        "value_num": r.value_num,
        "value_text": r.value_text,
        "unit": r.unit,
        "population": r.population,
        "arm": r.arm,
        "trial_key": r.trial_key,
        "data_cutoff": r.data_cutoff,
        "endpoint_def": r.endpoint_def,
        "assessment_method": r.assessment_method,
        "comparable_key": r.comparable_key,
        "page": r.page,
        "table": r.table_name,
        "quote": r.quote,
        "bbox": bbox,
        "confidence": r.confidence,
        "extractor": r.extractor,
        "verification_status": r.verification_status,
        "verification_note": r.verification_note,
        "verified_by": r.verified_by,
        "created_at": r.created_at,
        "source": {
            "document_id": r.document_id,
            "document_type": r.document_type,
            "filename": r.filename,
            "page": r.page,
            "table": r.table_name,
            "quote": r.quote,
            "bbox": bbox,
        },
    }


def _finding_dict(r: Finding) -> dict:
    return {
        "finding_id": r.finding_id,
        "project_id": r.project_id,
        "finding_type": r.finding_type,
        "domain": r.domain,
        "title": r.title,
        "explanation": r.explanation,
        "severity": r.severity,
        "status": r.status,
        "evidence_ids": _load(r.evidence_ids_json, []),
        "possible_causes": _load(r.possible_causes_json, []),
        "missing_information": _load(r.missing_information_json, []),
        "recommended_action": r.recommended_action,
        "rule_id": r.rule_id,
        "detail": _load(r.detail_json, {}),
        "dedup_key": r.dedup_key,
        "created_by_run": r.created_by_run,
        "created_at": r.created_at,
    }


def _question_dict(r: Question) -> dict:
    return {
        "question_id": r.question_id,
        "project_id": r.project_id,
        "question": r.question,
        "rationale": r.rationale,
        "category": r.category,
        "priority": r.priority,
        "owner": r.owner,
        "required_documents": _load(r.required_documents_json, []),
        "evidence_ids": _load(r.evidence_ids_json, []),
        "finding_ids": _load(r.finding_ids_json, []),
        "status": r.status,
        "review_status": r.review_status,
        "dedup_key": r.dedup_key,
        "finding_type": r.finding_type,
        "source": r.source,
        "locked": bool(r.locked),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _coverage_dict(r: CoverageItem) -> dict:
    return {
        "coverage_id": r.coverage_id,
        "project_id": r.project_id,
        "run_id": r.run_id,
        "rule_id": r.rule_id,
        "dimension": r.dimension,
        "requirement": r.requirement,
        "status": r.status,
        "evidence_ids": _load(r.evidence_ids_json, []),
        "note": r.note,
        "priority": r.priority,
        "created_at": r.created_at,
    }


def _run_dict(r: AgentRun) -> dict:
    return {
        "run_id": r.run_id,
        "project_id": r.project_id,
        "state": r.state,
        "status": r.status,
        "stages": _load(r.stages_json, []),
        "stats": _load(r.stats_json, {}),
        "error": r.error,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }


# ---------------------------------------------------------------------------
# Projects / assets
# ---------------------------------------------------------------------------


def create_project(
    session: Session,
    name: str,
    org_id: str = "ORG_DEFAULT",
    asset_name: str = "",
    indication: str = "",
    phase: str = "",
    meta: dict | None = None,
) -> dict:
    now = utcnow()
    row = Project(
        project_id=new_id("prj"),
        name=name,
        org_id=org_id,
        asset_name=asset_name,
        indication=indication,
        phase=phase,
        status="CREATED",
        meta_json=_dump(meta or {}),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return _project_dict(row)


def get_project(session: Session, project_id: str) -> dict | None:
    row = session.get(Project, project_id)
    return _project_dict(row) if row else None


def list_projects(session: Session, org_id: str | None = None) -> list[dict]:
    stmt = select(Project).order_by(Project.created_at.desc())
    if org_id:
        stmt = stmt.where(Project.org_id == org_id)
    return [_project_dict(r) for r in session.scalars(stmt)]


def update_project(session: Session, project_id: str, **fields: Any) -> dict | None:
    row = session.get(Project, project_id)
    if not row:
        return None
    allowed = {"name", "asset_name", "indication", "phase", "status"}
    for key, value in fields.items():
        if key in allowed and value is not None:
            setattr(row, key, value)
    if "meta" in fields and fields["meta"] is not None:
        row.meta_json = _dump(fields["meta"])
    row.updated_at = utcnow()
    session.flush()
    return _project_dict(row)


def create_asset(session: Session, project_id: str, name: str, **fields: Any) -> dict:
    row = Asset(
        asset_id=new_id("ast"),
        project_id=project_id,
        name=name,
        modality=fields.get("modality", ""),
        target=fields.get("target", ""),
        mechanism=fields.get("mechanism", ""),
        indication=fields.get("indication", ""),
        phase=fields.get("phase", ""),
        meta_json=_dump(fields.get("meta", {})),
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return _asset_dict(row)


def get_asset(session: Session, project_id: str | None = None, name: str | None = None) -> dict | None:
    stmt = select(Asset)
    if project_id:
        stmt = stmt.where(Asset.project_id == project_id)
    if name:
        stmt = stmt.where(Asset.name == name)
    row = session.scalars(stmt.limit(1)).first()
    return _asset_dict(row) if row else None


def upsert_asset(session: Session, project_id: str, name: str, **fields: Any) -> dict:
    row = session.scalars(
        select(Asset).where(Asset.project_id == project_id, Asset.name == name).limit(1)
    ).first()
    if row is None:
        return create_asset(session, project_id, name, **fields)
    for key in ("modality", "target", "mechanism", "indication", "phase"):
        if fields.get(key):
            setattr(row, key, fields[key])
    if fields.get("meta"):
        merged = _load(row.meta_json, {})
        merged.update(fields["meta"])
        row.meta_json = _dump(merged)
    session.flush()
    return _asset_dict(row)


def list_assets(session: Session, project_id: str) -> list[dict]:
    rows = session.scalars(select(Asset).where(Asset.project_id == project_id)).all()
    return [_asset_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Clinical trials
# ---------------------------------------------------------------------------


def _trial_dict(r: ClinicalTrial) -> dict:
    return {
        "trial_id": r.trial_id,
        "project_id": r.project_id,
        "asset_id": r.asset_id,
        "nct_id": r.nct_id,
        "internal_id": r.internal_id,
        "title": r.title,
        "phase": r.phase,
        "indication": r.indication,
        "design": r.design,
        "arms": _load(r.arms_json, []),
        "comparator": r.comparator,
        "primary_endpoint": r.primary_endpoint,
        "sample_size": r.sample_size,
        "data_cutoff": r.data_cutoff,
        "source_document_id": r.source_document_id,
        "meta": _load(r.meta_json, {}),
        "created_at": r.created_at,
    }


def upsert_trial(session: Session, project_id: str, nct_id: str = "", internal_id: str = "", **fields: Any) -> dict:
    stmt = select(ClinicalTrial).where(ClinicalTrial.project_id == project_id)
    if nct_id:
        stmt = stmt.where(ClinicalTrial.nct_id == nct_id)
    elif internal_id:
        stmt = stmt.where(ClinicalTrial.internal_id == internal_id)
    else:
        stmt = stmt.where(ClinicalTrial.trial_id == "")
    row = session.scalars(stmt.limit(1)).first()
    if row is None:
        row = ClinicalTrial(
            trial_id=new_id("trl"),
            project_id=project_id,
            nct_id=nct_id or "",
            internal_id=internal_id or "",
            created_at=utcnow(),
        )
        session.add(row)
    for key in ("title", "phase", "indication", "design", "comparator", "primary_endpoint", "asset_id", "source_document_id"):
        if fields.get(key):
            setattr(row, key, fields[key])
    if fields.get("sample_size") is not None:
        row.sample_size = int(fields["sample_size"])
    if fields.get("data_cutoff"):
        row.data_cutoff = fields["data_cutoff"]
    if fields.get("arms"):
        row.arms_json = _dump(fields["arms"])
    if fields.get("meta"):
        merged = _load(row.meta_json, {})
        merged.update(fields["meta"])
        row.meta_json = _dump(merged)
    session.flush()
    return _trial_dict(row)


def list_trials(session: Session, project_id: str) -> list[dict]:
    rows = session.scalars(select(ClinicalTrial).where(ClinicalTrial.project_id == project_id)).all()
    return [_trial_dict(r) for r in rows]


def get_trial(session: Session, project_id: str, trial_key: str | None) -> dict | None:
    if not trial_key:
        return None
    for trial in list_trials(session, project_id):
        if trial_key in {trial["nct_id"], trial["internal_id"], trial["trial_id"]}:
            return trial
    return None


# ---------------------------------------------------------------------------
# Documents / pages / chunks
# ---------------------------------------------------------------------------


def create_document(
    session: Session,
    project_id: str,
    filename: str,
    sha256: str,
    stored_path: str = "",
    doc_type: str = "OTHER",
    version: str = "1.0",
    doc_date: str | None = None,
    page_count: int = 0,
    language: str = "en",
    status: str = "uploaded",
    doc_family: str | None = None,
    meta: dict | None = None,
) -> tuple[dict, bool]:
    """Create a document.  Returns (row, created).  Deduplicates by sha256."""
    existing = session.scalars(
        select(Document).where(Document.project_id == project_id, Document.sha256 == sha256).limit(1)
    ).first()
    if existing is not None:
        return _doc_dict(existing), False

    family = doc_family or doc_type
    supersedes = None
    previous = session.scalars(
        select(Document)
        .where(Document.project_id == project_id, Document.doc_family == family, Document.is_latest.is_(True))
        .order_by(Document.uploaded_at.desc())
        .limit(1)
    ).first()
    if previous is not None:
        supersedes = previous.document_id
        previous.is_latest = False
        previous.status = "superseded"

    row = Document(
        document_id=new_id("doc"),
        project_id=project_id,
        filename=filename,
        stored_path=stored_path,
        sha256=sha256,
        doc_type=doc_type,
        doc_family=family,
        version=version,
        doc_date=doc_date,
        page_count=page_count,
        language=language,
        status=status,
        parser_version=config.PARSER_VERSION,
        is_latest=True,
        supersedes=supersedes,
        uploaded_at=utcnow(),
        meta_json=_dump(meta or {}),
    )
    session.add(row)
    session.flush()
    return _doc_dict(row), True


def get_document(session: Session, document_id: str) -> dict | None:
    row = session.get(Document, document_id)
    return _doc_dict(row) if row else None


def get_document_by_sha(session: Session, project_id: str, sha256: str) -> dict | None:
    row = session.scalars(
        select(Document).where(Document.project_id == project_id, Document.sha256 == sha256).limit(1)
    ).first()
    return _doc_dict(row) if row else None


def list_documents(session: Session, project_id: str, latest_only: bool = False) -> list[dict]:
    stmt = select(Document).where(Document.project_id == project_id).order_by(Document.uploaded_at)
    rows = session.scalars(stmt).all()
    if latest_only:
        rows = [r for r in rows if r.is_latest]
    return [_doc_dict(r) for r in rows]


def update_document(session: Session, document_id: str, **fields: Any) -> dict | None:
    row = session.get(Document, document_id)
    if not row:
        return None
    allowed = {
        "filename", "stored_path", "sha256", "doc_type", "doc_family", "version",
        "doc_date", "page_count", "language", "status", "parse_quality",
        "parse_error", "parser_version", "is_latest", "supersedes",
    }
    for key, value in fields.items():
        if key in allowed:
            setattr(row, key, value)
    if "meta" in fields and fields["meta"] is not None:
        row.meta_json = _dump(fields["meta"])
    session.flush()
    return _doc_dict(row)


def delete_document(session: Session, document_id: str) -> bool:
    row = session.get(Document, document_id)
    if not row:
        return False
    session.execute(delete(DocumentPage).where(DocumentPage.document_id == document_id))
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    session.execute(delete(Evidence).where(Evidence.document_id == document_id))
    session.delete(row)
    session.flush()
    return True


def delete_pages_for_document(session: Session, document_id: str) -> int:
    result = session.execute(delete(DocumentPage).where(DocumentPage.document_id == document_id))
    session.flush()
    return int(result.rowcount or 0)


def add_pages(session: Session, document_id: str, pages: Sequence[dict]) -> int:
    """pages: [{page_no, text, width, height, blocks:[{text,bbox,type}]}]"""
    existing = set(
        session.scalars(select(DocumentPage.page_no).where(DocumentPage.document_id == document_id)).all()
    )
    count = 0
    for page in pages:
        if page["page_no"] in existing:
            continue
        session.add(
            DocumentPage(
                document_id=document_id,
                page_no=page["page_no"],
                text=page.get("text", ""),
                char_count=len(page.get("text", "")),
                width=page.get("width", 0.0),
                height=page.get("height", 0.0),
                blocks_json=_dump(page.get("blocks", [])),
            )
        )
        count += 1
    session.flush()
    return count


def get_pages(session: Session, document_id: str) -> list[dict]:
    rows = session.scalars(
        select(DocumentPage).where(DocumentPage.document_id == document_id).order_by(DocumentPage.page_no)
    ).all()
    return [
        {
            "page_no": r.page_no,
            "text": r.text,
            "char_count": r.char_count,
            "width": r.width,
            "height": r.height,
            "blocks": _load(r.blocks_json, []),
        }
        for r in rows
    ]


def get_page(session: Session, document_id: str, page_no: int) -> dict | None:
    row = session.scalars(
        select(DocumentPage).where(
            DocumentPage.document_id == document_id, DocumentPage.page_no == page_no
        )
    ).first()
    if not row:
        return None
    return {
        "page_no": row.page_no,
        "text": row.text,
        "width": row.width,
        "height": row.height,
        "blocks": _load(row.blocks_json, []),
    }


def add_chunks(session: Session, document_id: str, chunks: Sequence[dict]) -> int:
    count = 0
    for ch in chunks:
        session.add(
            DocumentChunk(
                chunk_id=new_id("chk"),
                document_id=document_id,
                page_no=ch["page_no"],
                chunk_index=ch.get("chunk_index", 0),
                text=ch["text"],
                start_char=ch.get("start_char", 0),
                bbox_json=_dump(ch.get("bbox", [])),
            )
        )
        count += 1
    session.flush()
    return count


def delete_chunks(session: Session, document_id: str) -> None:
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    session.flush()


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def save_evidence_batch(
    session: Session,
    project_id: str,
    document: dict,
    drafts: Iterable[EvidenceDraft],
    asset_id: str | None = None,
    trial_id: str | None = None,
) -> tuple[list[dict], int]:
    """Persist extractor output for one document.  Idempotent by fingerprint."""
    created: list[dict] = []
    skipped = 0
    for draft in drafts:
        fp = evidence_fingerprint(
            document["document_id"],
            str(draft.field_name),
            draft.value_num,
            draft.value_text,
            draft.page,
            draft.quote,
        )
        if session.scalars(select(Evidence.evidence_id).where(Evidence.fingerprint == fp).limit(1)).first():
            skipped += 1
            continue
        row = Evidence(
            evidence_id=new_id("ev"),
            project_id=project_id,
            asset_id=asset_id,
            trial_id=trial_id,
            document_id=document["document_id"],
            document_type=document.get("doc_type", ""),
            filename=document.get("filename", ""),
            field_name=str(draft.field_name),
            category=str(draft.category),
            value_num=draft.value_num,
            value_text=draft.value_text,
            unit=draft.unit,
            population=draft.population,
            arm=draft.arm,
            trial_key=draft.trial_key,
            data_cutoff=draft.data_cutoff,
            endpoint_def=draft.endpoint_def,
            assessment_method=draft.assessment_method,
            comparable_key=comparable_key(draft),
            page=draft.page,
            table_name=draft.table,
            quote=draft.quote,
            bbox_json=_dump(draft_bbox(document["document_id"], draft)),
            confidence=draft.confidence,
            extractor=draft.extractor,
            verification_status="pending",
            fingerprint=fp,
            created_at=utcnow(),
        )
        session.add(row)
        session.flush()
        created.append(_evidence_dict(row))
    return created, skipped


def draft_bbox(document_id: str, draft: EvidenceDraft) -> list[float]:
    """Optional hook: extractors may pass bbox through notes as JSON."""
    try:
        if draft.notes and draft.notes.startswith("{"):
            data = json.loads(draft.notes)
            bbox = data.get("bbox")
            if isinstance(bbox, list):
                return [float(v) for v in bbox]
    except (ValueError, TypeError):
        pass
    return []


def get_evidence(
    session: Session,
    project_id: str,
    field_name: str | None = None,
    field_names: Sequence[str] | None = None,
    category: str | None = None,
    document_id: str | None = None,
    asset_id: str | None = None,
    trial_id: str | None = None,
    trial_key: str | None = None,
    verification_status: str | None = None,
    limit: int = 5000,
) -> list[dict]:
    stmt = select(Evidence).where(Evidence.project_id == project_id)
    if field_name:
        stmt = stmt.where(Evidence.field_name == field_name)
    if field_names:
        stmt = stmt.where(Evidence.field_name.in_(list(field_names)))
    if category:
        stmt = stmt.where(Evidence.category == category)
    if document_id:
        stmt = stmt.where(Evidence.document_id == document_id)
    if asset_id:
        stmt = stmt.where(Evidence.asset_id == asset_id)
    if trial_id:
        stmt = stmt.where(Evidence.trial_id == trial_id)
    if trial_key:
        stmt = stmt.where(Evidence.trial_key == trial_key)
    if verification_status:
        stmt = stmt.where(Evidence.verification_status == verification_status)
    rows = session.scalars(stmt.order_by(Evidence.document_type, Evidence.page).limit(limit)).all()
    return [_evidence_dict(r) for r in rows]


def get_evidence_by_ids(session: Session, evidence_ids: Sequence[str]) -> list[dict]:
    ids = [i for i in evidence_ids if i]
    if not ids:
        return []
    rows = session.scalars(select(Evidence).where(Evidence.evidence_id.in_(ids))).all()
    by_id = {r.evidence_id: r for r in rows}
    return [_evidence_dict(by_id[i]) for i in ids if i in by_id]


def update_evidence_verification(
    session: Session,
    evidence_id: str,
    status: str,
    note: str | None = None,
    by: str = "program",
) -> dict | None:
    row = session.get(Evidence, evidence_id)
    if not row:
        return None
    row.verification_status = status
    row.verification_note = note
    row.verified_by = by
    session.flush()
    return _evidence_dict(row)


def delete_evidence_for_document(session: Session, document_id: str) -> int:
    result = session.execute(delete(Evidence).where(Evidence.document_id == document_id))
    session.flush()
    return int(result.rowcount or 0)


def search_evidence(session: Session, project_id: str, query: str, limit: int = 50) -> list[dict]:
    q = f"%{query.strip()}%"
    rows = session.scalars(
        select(Evidence)
        .where(Evidence.project_id == project_id)
        .where((Evidence.quote.like(q)) | (Evidence.value_text.like(q)) | (Evidence.filename.like(q)))
        .limit(limit)
    ).all()
    return [_evidence_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def save_findings(
    session: Session,
    project_id: str,
    drafts: Iterable[FindingDraft],
    run_id: str | None = None,
) -> dict:
    created = updated = 0
    stored: list[dict] = []
    for draft in drafts:
        key = draft.dedup_key or new_id("fnd")
        row = session.scalars(
            select(Finding).where(Finding.project_id == project_id, Finding.dedup_key == key).limit(1)
        ).first()
        if row is None:
            row = Finding(
                finding_id=new_id("fnd"),
                project_id=project_id,
                dedup_key=key,
                created_at=utcnow(),
                created_by_run=run_id,
                status="open",
            )
            session.add(row)
            created += 1
        else:
            updated += 1
        row.finding_type = str(draft.finding_type)
        row.domain = draft.domain
        row.title = draft.title
        row.explanation = draft.explanation
        row.severity = str(draft.severity)
        evidence_ids = _filter_existing_evidence_ids(session, draft.evidence_ids)
        row.evidence_ids_json = _dump(evidence_ids)
        row.possible_causes_json = _dump(draft.possible_causes)
        row.missing_information_json = _dump(draft.missing_information)
        row.recommended_action = draft.recommended_action
        row.rule_id = draft.rule_id
        row.detail_json = _dump(draft.detail)
        row.created_by_run = run_id or row.created_by_run
        session.flush()
        _sync_links(session, FindingEvidence, "finding_id", row.finding_id, evidence_ids)
        stored.append(_finding_dict(row))
    return {"created": created, "updated": updated, "findings": stored}


def _filter_existing_evidence_ids(session: Session, evidence_ids: Sequence[str]) -> list[str]:
    """Drop references to evidence rows that no longer exist (e.g. after a force re-ingest).

    Keeps the audit trail clean: a question/finding never points at a deleted
    evidence row, while the review-event history preserves what was changed.
    """
    ids = [str(i) for i in evidence_ids if i]
    if not ids:
        return []
    existing = set(session.scalars(select(Evidence.evidence_id).where(Evidence.evidence_id.in_(ids))).all())
    seen: set[str] = set()
    out: list[str] = []
    for eid in ids:
        if eid in existing and eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


def _sync_links(session: Session, model, fk_name: str, owner_id: str, evidence_ids: Sequence[str]) -> None:
    session.execute(delete(model).where(getattr(model, fk_name) == owner_id))
    seen = set()
    for ev_id in evidence_ids:
        if not ev_id or ev_id in seen:
            continue
        seen.add(ev_id)
        session.add(model(**{fk_name: owner_id, "evidence_id": ev_id}))
    session.flush()


def list_findings(
    session: Session,
    project_id: str,
    status: str | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
) -> list[dict]:
    stmt = select(Finding).where(Finding.project_id == project_id)
    if status:
        stmt = stmt.where(Finding.status == status)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if finding_type:
        stmt = stmt.where(Finding.finding_type == finding_type)
    rows = session.scalars(stmt.order_by(Finding.created_at, Finding.severity)).all()
    return [_finding_dict(r) for r in rows]


def get_finding(session: Session, finding_id: str) -> dict | None:
    row = session.get(Finding, finding_id)
    return _finding_dict(row) if row else None


def update_finding(
    session: Session,
    finding_id: str,
    actor: str = "human",
    **fields: Any,
) -> dict | None:
    row = session.get(Finding, finding_id)
    if not row:
        return None
    before = _finding_dict(row)
    allowed = {"status", "severity", "title", "explanation", "recommended_action"}
    for key, value in fields.items():
        if key in allowed and value is not None:
            setattr(row, key, value)
    session.flush()
    after = _finding_dict(row)
    if after != before:
        log_review_event(session, row.project_id, "finding", finding_id, actor, "update", before, after)
    return after


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def save_questions(
    session: Session,
    project_id: str,
    drafts: Iterable[QuestionDraft],
    run_id: str | None = None,
) -> dict:
    created = updated = skipped_locked = 0
    stored: list[dict] = []
    for draft in drafts:
        key = draft.dedup_key or new_id("qst")
        row = session.scalars(
            select(Question).where(Question.project_id == project_id, Question.dedup_key == key).limit(1)
        ).first()
        if row is not None and row.locked:
            skipped_locked += 1
            stored.append(_question_dict(row))
            continue
        if row is None:
            row = Question(
                question_id=new_id("qst"),
                project_id=project_id,
                dedup_key=key,
                created_at=utcnow(),
                updated_at=utcnow(),
                status="draft",
                review_status="pending",
                source=draft.source,
            )
            session.add(row)
            created += 1
        else:
            updated += 1
        row.question = draft.question
        row.rationale = draft.rationale
        row.category = draft.category
        row.priority = str(draft.priority)
        row.owner = str(draft.owner)
        row.required_documents_json = _dump(draft.required_documents)
        evidence_ids = _filter_existing_evidence_ids(session, draft.evidence_ids)
        row.evidence_ids_json = _dump(evidence_ids)
        row.finding_ids_json = _dump(draft.finding_ids)
        row.finding_type = draft.finding_type
        row.source = draft.source
        row.updated_at = utcnow()
        session.flush()
        _sync_links(session, QuestionEvidence, "question_id", row.question_id, evidence_ids)
        stored.append(_question_dict(row))
    return {
        "created": created,
        "updated": updated,
        "skipped_locked": skipped_locked,
        "questions": stored,
    }


def list_questions(
    session: Session,
    project_id: str,
    status: str | None = None,
    priority: str | None = None,
) -> list[dict]:
    stmt = select(Question).where(Question.project_id == project_id)
    if status:
        stmt = stmt.where(Question.status == status)
    if priority:
        stmt = stmt.where(Question.priority == priority)
    rows = session.scalars(stmt.order_by(Question.created_at)).all()
    return [_question_dict(r) for r in rows]


def get_question(session: Session, question_id: str) -> dict | None:
    row = session.get(Question, question_id)
    return _question_dict(row) if row else None


def update_question(
    session: Session,
    question_id: str,
    actor: str = "human",
    **fields: Any,
) -> dict | None:
    row = session.get(Question, question_id)
    if not row:
        return None
    before = _question_dict(row)
    allowed = {
        "question", "rationale", "category", "priority", "owner", "status",
        "review_status", "locked",
    }
    for key, value in fields.items():
        if key in allowed and value is not None:
            setattr(row, key, value)
    if fields.get("required_documents") is not None:
        row.required_documents_json = _dump(fields["required_documents"])
    if fields.get("evidence_ids") is not None:
        row.evidence_ids_json = _dump(_filter_existing_evidence_ids(session, fields["evidence_ids"]))
    if fields.get("finding_ids") is not None:
        row.finding_ids_json = _dump([str(i) for i in fields["finding_ids"] if i])
    row.updated_at = utcnow()
    session.flush()
    after = _question_dict(row)
    if after != before:
        log_review_event(session, row.project_id, "question", question_id, actor, "update", before, after)
    return after


def delete_question(session: Session, question_id: str, actor: str = "human") -> bool:
    row = session.get(Question, question_id)
    if not row:
        return False
    session.execute(delete(QuestionEvidence).where(QuestionEvidence.question_id == question_id))
    log_review_event(
        session, row.project_id, "question", question_id, actor, "delete", _question_dict(row), {}
    )
    session.delete(row)
    session.flush()
    return True


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def replace_coverage(
    session: Session,
    project_id: str,
    drafts: Iterable[CoverageDraft],
    run_id: str | None = None,
) -> list[dict]:
    session.execute(delete(CoverageItem).where(CoverageItem.project_id == project_id))
    items: list[dict] = []
    for draft in drafts:
        row = CoverageItem(
            coverage_id=new_id("cov"),
            project_id=project_id,
            run_id=run_id,
            rule_id=draft.rule_id,
            dimension=draft.dimension,
            requirement=draft.requirement,
            status=str(draft.status),
            evidence_ids_json=_dump(draft.evidence_ids),
            note=draft.note,
            priority=str(draft.priority),
            created_at=utcnow(),
        )
        session.add(row)
        session.flush()
        items.append(_coverage_dict(row))
    return items


def list_coverage(session: Session, project_id: str, status: str | None = None) -> list[dict]:
    stmt = select(CoverageItem).where(CoverageItem.project_id == project_id)
    if status:
        stmt = stmt.where(CoverageItem.status == status)
    rows = session.scalars(stmt.order_by(CoverageItem.dimension, CoverageItem.rule_id)).all()
    return [_coverage_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Runs / review events / stats
# ---------------------------------------------------------------------------


def create_run(session: Session, project_id: str, stages: Sequence[str]) -> dict:
    row = AgentRun(
        run_id=new_id("run"),
        project_id=project_id,
        state="CREATED",
        status="running",
        stages_json=_dump(list(stages)),
        stats_json="{}",
        started_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return _run_dict(row)


def update_run(session: Session, run_id: str, **fields: Any) -> dict | None:
    row = session.get(AgentRun, run_id)
    if not row:
        return None
    if fields.get("state") is not None:
        row.state = fields["state"]
    if fields.get("status") is not None:
        row.status = fields["status"]
    if fields.get("error") is not None:
        row.error = fields["error"]
    if fields.get("stats") is not None:
        merged = _load(row.stats_json, {})
        merged.update(fields["stats"])
        row.stats_json = _dump(merged)
    if fields.get("finished"):
        row.finished_at = utcnow()
    session.flush()
    return _run_dict(row)


def get_run(session: Session, run_id: str) -> dict | None:
    row = session.get(AgentRun, run_id)
    return _run_dict(row) if row else None


def list_runs(session: Session, project_id: str, limit: int = 20) -> list[dict]:
    rows = session.scalars(
        select(AgentRun).where(AgentRun.project_id == project_id).order_by(AgentRun.started_at.desc()).limit(limit)
    ).all()
    return [_run_dict(r) for r in rows]


def log_review_event(
    session: Session,
    project_id: str,
    entity_type: str,
    entity_id: str,
    actor: str,
    action: str,
    before: dict | None = None,
    after: dict | None = None,
) -> dict:
    row = ReviewEvent(
        event_id=new_id("rev"),
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        action=action,
        before_json=_dump(before or {}),
        after_json=_dump(after or {}),
        ts=utcnow(),
    )
    session.add(row)
    session.flush()
    return {
        "event_id": row.event_id,
        "project_id": project_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor": actor,
        "action": action,
        "before": before or {},
        "after": after or {},
        "ts": row.ts,
    }


def list_review_events(session: Session, project_id: str, limit: int = 200) -> list[dict]:
    rows = session.scalars(
        select(ReviewEvent)
        .where(ReviewEvent.project_id == project_id)
        .order_by(ReviewEvent.ts.desc())
        .limit(limit)
    ).all()
    return [
        {
            "event_id": r.event_id,
            "project_id": r.project_id,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "actor": r.actor,
            "action": r.action,
            "before": _load(r.before_json, {}),
            "after": _load(r.after_json, {}),
            "ts": r.ts,
        }
        for r in rows
    ]


def project_stats(session: Session, project_id: str) -> dict:
    def count(model, *conds) -> int:
        stmt = select(func.count()).select_from(model)
        if conds:
            stmt = stmt.where(*conds)
        return int(session.scalar(stmt) or 0)

    def group_by(model, column) -> dict:
        stmt = select(column, func.count()).select_from(model).where(model.project_id == project_id).group_by(column)
        return {str(k): int(v) for k, v in session.execute(stmt).all()}

    return {
        "documents": count(Document, Document.project_id == project_id, Document.is_latest.is_(True)),
        "documents_all": count(Document, Document.project_id == project_id),
        "pages": int(
            session.scalar(
                select(func.count())
                .select_from(DocumentPage)
                .join(Document, Document.document_id == DocumentPage.document_id)
                .where(Document.project_id == project_id)
            )
            or 0
        ),
        "evidence": count(Evidence, Evidence.project_id == project_id),
        "evidence_by_status": group_by(Evidence, Evidence.verification_status),
        "findings": count(Finding, Finding.project_id == project_id),
        "findings_by_severity": group_by(Finding, Finding.severity),
        "findings_by_type": group_by(Finding, Finding.finding_type),
        "questions": count(Question, Question.project_id == project_id),
        "questions_by_priority": group_by(Question, Question.priority),
        "coverage": count(CoverageItem, CoverageItem.project_id == project_id),
        "coverage_by_status": group_by(CoverageItem, CoverageItem.status),
    }


def get_pages_text(session: Session, document_id: str) -> dict[int, str]:
    rows = session.execute(
        select(DocumentPage.page_no, DocumentPage.text).where(DocumentPage.document_id == document_id)
    ).all()
    return {int(no): text for no, text in rows}


def submit_pilot_inquiry(session, request_key, fields):
    from sqlalchemy.dialects.sqlite import insert
    from hijenny.db import PilotInquiry

    receipt = new_id("pilot")
    session.execute(insert(PilotInquiry).values(receipt=receipt, request_key=request_key,
                    created_at=utcnow(), **fields).on_conflict_do_nothing(index_elements=["request_key"]))
    saved = session.scalar(select(PilotInquiry).where(PilotInquiry.request_key == request_key))
    if any(getattr(saved, k) != v for k, v in fields.items()):
        raise ValueError("受理请求已被使用，请为修改后的需求重新提交")
    return {"receipt": saved.receipt, "created_at": saved.created_at}


def list_pilot_inquiries(session, receipt=None):
    from hijenny.db import PilotInquiry, PilotProgress

    query = select(PilotInquiry).order_by(PilotInquiry.created_at.desc())
    if receipt:
        query = query.where(PilotInquiry.receipt == receipt)
    columns = ("receipt", "created_at", "email", "stage", "pages", "meeting", "deployment")
    rows = [{key: getattr(row, key) for key in columns} for row in session.scalars(query)]
    progress = select(PilotProgress).order_by(PilotProgress.id)
    if receipt:
        progress = progress.where(PilotProgress.receipt == receipt)
    by_receipt = {row["receipt"]: row for row in rows}
    for row in rows:
        row["progress"] = []
    for event in session.scalars(progress):
        if event.receipt in by_receipt:
            by_receipt[event.receipt]["progress"].append({
                "stage": event.stage, "reference": event.reference, "recorded_at": event.recorded_at})
    return rows


def record_pilot_progress(session, receipt, stage, reference):
    from hijenny.db import PilotInquiry, PilotProgress

    if not session.get(PilotInquiry, receipt):
        raise ValueError("受理编号不存在")
    if not 1 <= len(reference.strip()) <= 300:
        raise ValueError("请提供 1–300 字的跟进或凭据编号")
    session.add(PilotProgress(receipt=receipt, stage=stage, reference=reference.strip(), recorded_at=utcnow()))


def record_pilot_engagement(session, session_id, sample_version, event):
    from sqlalchemy.dialects.sqlite import insert
    from hijenny.db import PilotEngagement

    session.execute(insert(PilotEngagement).values(
        session_id=session_id, sample_version=sample_version, event=event, first_seen=utcnow()
    ).on_conflict_do_nothing(index_elements=["session_id", "sample_version", "event"]))


def pilot_metrics(session, since=None):
    from hijenny.db import PilotEngagement, PilotInquiry, PilotProgress

    # Anchor each cohort at entry so a later stage stays attached to its original cohort.
    counts = select(PilotEngagement.sample_version, PilotEngagement.event, func.count())
    receipts = select(func.count()).select_from(PilotInquiry)
    stages = select(PilotProgress.stage, func.count(func.distinct(PilotProgress.receipt))).join(
        PilotInquiry, PilotInquiry.receipt == PilotProgress.receipt)
    if since:
        from sqlalchemy.orm import aliased

        entry = aliased(PilotEngagement)
        counts = counts.where(select(entry.session_id).where(
            entry.session_id == PilotEngagement.session_id,
            entry.sample_version == PilotEngagement.sample_version,
            entry.event == "pilot_open", entry.first_seen >= since.isoformat()).exists())
        receipts = receipts.where(PilotInquiry.created_at >= since.isoformat())
        stages = stages.where(PilotInquiry.created_at >= since.isoformat())
    by_sample = {}
    for version, event, count in session.execute(counts.group_by(PilotEngagement.sample_version, PilotEngagement.event)):
        by_sample.setdefault(version, {})[event] = count
    return {
        "匿名会话数（按样例版本，每项事件去重）": by_sample,
        "受理编号数": session.scalar(receipts),
        "到达各销售阶段的受理编号数": dict(session.execute(stages.group_by(PilotProgress.stage)).all()),
        "入组起始日（UTC，按首次打开或受理日）": since.isoformat() if since else "全部",
    }
