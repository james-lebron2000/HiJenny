"""SQLAlchemy models (frozen contract, Lead-owned).

SQLite by default; the schema is portable to PostgreSQL (JSON stored as TEXT
with helper serialisation).  Set HIJENNY_DB_PATH to relocate the database.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    org_id: Mapped[str] = mapped_column(String(64), default="ORG_DEFAULT")
    asset_name: Mapped[str] = mapped_column(String(200), default="")
    indication: Mapped[str] = mapped_column(String(200), default="")
    phase: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(40), default="CREATED")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class Asset(Base):
    __tablename__ = "assets"

    asset_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    modality: Mapped[str] = mapped_column(String(120), default="")
    target: Mapped[str] = mapped_column(String(200), default="")
    mechanism: Mapped[str] = mapped_column(String(400), default="")
    indication: Mapped[str] = mapped_column(String(300), default="")
    phase: Mapped[str] = mapped_column(String(40), default="")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default="")


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    filename: Mapped[str] = mapped_column(String(400))
    stored_path: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    doc_type: Mapped[str] = mapped_column(String(40), default="OTHER")
    doc_family: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[str] = mapped_column(String(40), default="1.0")
    doc_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str] = mapped_column(String(16), default="en")
    status: Mapped[str] = mapped_column(String(40), default="uploaded")
    parse_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str] = mapped_column(String(40), default="")
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    supersedes: Mapped[str | None] = mapped_column(String(48), nullable=True)
    uploaded_at: Mapped[str] = mapped_column(String(40), default="")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")

    __table_args__ = (Index("ix_documents_project_sha", "project_id", "sha256", unique=True),)


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(48), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[float] = mapped_column(Float, default=0.0)
    height: Mapped[float] = mapped_column(Float, default=0.0)
    blocks_json: Mapped[str] = mapped_column(Text, default="[]")

    __table_args__ = (Index("ix_pages_doc_page", "document_id", "page_no", unique=True),)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    chunk_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(48), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    start_char: Mapped[int] = mapped_column(Integer, default=0)
    bbox_json: Mapped[str] = mapped_column(Text, default="[]")


class ClinicalTrial(Base):
    __tablename__ = "clinical_trials"

    trial_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    asset_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    nct_id: Mapped[str] = mapped_column(String(40), default="")
    internal_id: Mapped[str] = mapped_column(String(80), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    phase: Mapped[str] = mapped_column(String(40), default="")
    indication: Mapped[str] = mapped_column(String(300), default="")
    design: Mapped[str] = mapped_column(String(300), default="")
    arms_json: Mapped[str] = mapped_column(Text, default="[]")
    comparator: Mapped[str] = mapped_column(String(300), default="")
    primary_endpoint: Mapped[str] = mapped_column(String(400), default="")
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_cutoff: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default="")


class Evidence(Base):
    __tablename__ = "evidence"

    evidence_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    asset_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    trial_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    document_id: Mapped[str] = mapped_column(String(48), index=True)
    document_type: Mapped[str] = mapped_column(String(40), default="")
    filename: Mapped[str] = mapped_column(String(400), default="")
    field_name: Mapped[str] = mapped_column(String(60), index=True)
    category: Mapped[str] = mapped_column(String(40), default="general")
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    population: Mapped[str | None] = mapped_column(String(60), nullable=True)
    arm: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trial_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    data_cutoff: Mapped[str | None] = mapped_column(String(40), nullable=True)
    endpoint_def: Mapped[str | None] = mapped_column(String(300), nullable=True)
    assessment_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    comparable_key: Mapped[str] = mapped_column(String(32), default="")
    page: Mapped[int] = mapped_column(Integer, default=1)
    table_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quote: Mapped[str] = mapped_column(Text)
    bbox_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    extractor: Mapped[str] = mapped_column(String(60), default="rules")
    verification_status: Mapped[str] = mapped_column(String(40), default="pending")
    verification_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(40), unique=True)
    created_at: Mapped[str] = mapped_column(String(40), default="")

    __table_args__ = (
        Index("ix_evidence_project_field", "project_id", "field_name"),
        Index("ix_evidence_project_compkey", "project_id", "comparable_key"),
    )


class Finding(Base):
    __tablename__ = "findings"

    finding_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    finding_type: Mapped[str] = mapped_column(String(40), default="")
    domain: Mapped[str] = mapped_column(String(40), default="clinical")
    title: Mapped[str] = mapped_column(String(400))
    explanation: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(30), default="open")
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    possible_causes_json: Mapped[str] = mapped_column(Text, default="[]")
    missing_information_json: Mapped[str] = mapped_column(Text, default="[]")
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    dedup_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_by_run: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default="")


class FindingEvidence(Base):
    __tablename__ = "finding_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    finding_id: Mapped[str] = mapped_column(String(48), index=True)
    evidence_id: Mapped[str] = mapped_column(String(48), index=True)


class Question(Base):
    __tablename__ = "questions"

    question_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    question: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="Clinical")
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    owner: Mapped[str] = mapped_column(String(80), default="Clinical Development")
    required_documents_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    finding_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    review_status: Mapped[str] = mapped_column(String(30), default="pending")
    dedup_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    finding_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="auto")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class QuestionEvidence(Base):
    __tablename__ = "question_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[str] = mapped_column(String(48), index=True)
    evidence_id: Mapped[str] = mapped_column(String(48), index=True)


class CoverageItem(Base):
    __tablename__ = "coverage_items"

    coverage_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    run_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    rule_id: Mapped[str] = mapped_column(String(80), default="")
    dimension: Mapped[str] = mapped_column(String(80), default="")
    requirement: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(30), default="not_reviewed")
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    created_at: Mapped[str] = mapped_column(String(40), default="")


class ReviewEvent(Base):
    __tablename__ = "review_events"

    event_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(48))
    actor: Mapped[str] = mapped_column(String(120), default="system")
    action: Mapped[str] = mapped_column(String(80))
    before_json: Mapped[str] = mapped_column(Text, default="{}")
    after_json: Mapped[str] = mapped_column(Text, default="{}")
    ts: Mapped[str] = mapped_column(String(40), default="")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    state: Mapped[str] = mapped_column(String(40), default="CREATED")
    status: Mapped[str] = mapped_column(String(30), default="running")
    stages_json: Mapped[str] = mapped_column(Text, default="[]")
    stats_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(String(40), default="")
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(40), default="reviewer")
    created_at: Mapped[str] = mapped_column(String(40), default="")


class ProjectMember(Base):
    __tablename__ = "project_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(48), index=True)
    user_id: Mapped[str] = mapped_column(String(48), index=True)
    role: Mapped[str] = mapped_column(String(40), default="reviewer")
