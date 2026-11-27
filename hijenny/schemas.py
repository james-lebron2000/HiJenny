"""Frozen domain contract for HiJenny (Lead-owned).

Hierarchy:  Document -> Page -> Evidence -> Finding -> Question
Every Evidence row carries a *comparability vector* so that two numbers are only
compared when trial / arm / population / data cutoff / endpoint definition /
assessment method agree.  This is what keeps the consistency agent from
manufacturing contradictions out of incomparable numbers.

Do not change enum members or function signatures without telling the Lead:
other modules import from this file.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enumerations (controlled vocabulary)
# ---------------------------------------------------------------------------


class DocType(StrEnum):
    CSR = "CSR"
    PROTOCOL = "PROTOCOL"
    IB = "IB"
    SAP = "SAP"
    SAFETY = "SAFETY"
    DISPOSITION = "DISPOSITION"
    CMC = "CMC"
    PRECLINICAL = "PRECLINICAL"
    REGULATORY = "REGULATORY"
    PATENT = "PATENT"
    COMMERCIAL = "COMMERCIAL"
    OTHER = "OTHER"


class DocStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class EvidenceCategory(StrEnum):
    STUDY_DESIGN = "study_design"
    CLINICAL_EFFICACY = "clinical_efficacy"
    CLINICAL_SAFETY = "clinical_safety"
    PATIENT_BASELINE = "patient_baseline"
    STATISTICS = "statistics"
    DISPOSITION = "disposition"
    CMC = "cmc"
    REGULATORY = "regulatory"
    IP = "ip"
    COMMERCIAL = "commercial"
    GENERAL = "general"


class FieldName(StrEnum):
    # asset / study level
    ASSET_NAME = "asset_name"
    MECHANISM = "mechanism"
    TARGET = "target"
    MODALITY = "modality"
    NCT_ID = "nct_id"
    PHASE = "phase"
    INDICATION = "indication"
    DESIGN = "design"
    ARMS = "arms"
    COMPARATOR = "comparator"
    RANDOMIZATION = "randomization"
    BLINDING = "blinding"
    PRIMARY_ENDPOINT = "primary_endpoint"
    SECONDARY_ENDPOINTS = "secondary_endpoints"
    DATA_CUTOFF = "data_cutoff"
    SAMPLE_SIZE = "sample_size"
    N_ITT = "n_itt"
    N_SAFETY = "n_safety"
    N_PP = "n_pp"
    N_FAS = "n_fas"
    AGE_MEDIAN = "age_median"
    MALE_PCT = "male_pct"
    # efficacy
    ORR = "orr"
    DCR = "dcr"
    CR = "cr"
    PR = "pr"
    SD = "sd"
    PD = "pd"
    DOR = "dor"
    PFS = "pfs"
    OS = "os"
    HR = "hr"
    CI_LOWER = "ci_lower"
    CI_UPPER = "ci_upper"
    P_VALUE = "p_value"
    FOLLOW_UP = "follow_up"
    TTR = "ttr"
    # safety
    ANY_AE = "any_ae"
    GRADE3_AE = "grade3_ae"
    TREATMENT_RELATED_AE = "treatment_related_ae"
    SAE = "sae"
    TREATMENT_RELATED_DEATH = "treatment_related_death"
    DOSE_REDUCTION = "dose_reduction"
    DOSE_INTERRUPTION = "dose_interruption"
    DISCONTINUATION = "discontinuation"
    AESI = "aesi"
    # statistical / quality
    RESPONSE_CRITERIA = "response_criteria"
    SAP_VERSION = "sap_version"
    MISSING_DATA_METHOD = "missing_data_method"
    MULTIPLICITY = "multiplicity"
    SUBGROUP_ANALYSIS = "subgroup_analysis"
    PROTOCOL_DEVIATION = "protocol_deviation"
    AUDIT_SUMMARY = "audit_summary"


NUMERIC_FIELDS: set[str] = {
    FieldName.ORR, FieldName.DCR, FieldName.CR, FieldName.PR, FieldName.SD,
    FieldName.PD, FieldName.DOR, FieldName.PFS, FieldName.OS, FieldName.HR,
    FieldName.CI_LOWER, FieldName.CI_UPPER, FieldName.P_VALUE,
    FieldName.FOLLOW_UP, FieldName.TTR, FieldName.SAMPLE_SIZE,
    FieldName.N_ITT, FieldName.N_SAFETY, FieldName.N_PP, FieldName.AGE_MEDIAN,
    FieldName.MALE_PCT, FieldName.ANY_AE, FieldName.GRADE3_AE,
    FieldName.TREATMENT_RELATED_AE, FieldName.SAE, FieldName.DOSE_REDUCTION,
    FieldName.DOSE_INTERRUPTION, FieldName.DISCONTINUATION,
}
PERCENT_FIELDS: set[str] = {
    FieldName.ORR, FieldName.DCR, FieldName.CR, FieldName.PR, FieldName.SD,
    FieldName.PD, FieldName.MALE_PCT, FieldName.ANY_AE, FieldName.GRADE3_AE,
    FieldName.TREATMENT_RELATED_AE, FieldName.SAE, FieldName.DOSE_REDUCTION,
    FieldName.DOSE_INTERRUPTION, FieldName.DISCONTINUATION,
}
TIME_FIELDS: set[str] = {
    FieldName.DOR, FieldName.PFS, FieldName.OS, FieldName.FOLLOW_UP, FieldName.TTR,
}
COUNT_FIELDS: set[str] = {
    FieldName.SAMPLE_SIZE, FieldName.N_ITT, FieldName.N_SAFETY, FieldName.N_PP,
    FieldName.N_FAS,
}

# Fields the consistency agent is allowed to reconcile, grouped by rule.
EFFICACY_FIELDS = [FieldName.ORR, FieldName.DCR, FieldName.PFS, FieldName.OS, FieldName.HR]
SAFETY_FIELDS = [FieldName.GRADE3_AE, FieldName.SAE, FieldName.TREATMENT_RELATED_DEATH]
SIZE_FIELDS = [FieldName.SAMPLE_SIZE, FieldName.N_ITT, FieldName.N_SAFETY]


class VerificationStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    UNVERIFIABLE = "unverifiable"


UNSUPPORTED_REVIEW_STATUSES = frozenset({VerificationStatus.FAILED, VerificationStatus.UNVERIFIABLE})


class FindingType(StrEnum):
    DATA_INCONSISTENCY = "data_inconsistency"      # Type A direct contradiction
    VERSION_DIFFERENCE = "version_difference"      # Type B data cutoff / version
    DEFINITION_DIFFERENCE = "definition_difference"  # Type C definition / population
    COMPARABILITY_UNCLEAR = "comparability_unclear"  # missing dims: cannot compare
    EVIDENCE_GAP = "evidence_gap"
    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    TECHNICAL_CONCERN = "technical_concern"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(StrEnum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"
    NEEDS_REVIEW = "needs_review"


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CoverageStatus(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    NOT_APPLICABLE = "not_applicable"
    NOT_REVIEWED = "not_reviewed"


class QuestionStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    SENT = "sent"
    ANSWERED = "answered"
    CLOSED = "closed"
    DISMISSED = "dismissed"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"


class Owner(StrEnum):
    CLINICAL = "Clinical Development"
    SAFETY = "Safety / Pharmacovigilance"
    BIOSTATISTICS = "Biostatistics"
    REGULATORY = "Regulatory Affairs"
    CMC = "CMC / Manufacturing"
    IP = "IP / Legal"
    COMMERCIAL = "Commercial"
    LEGAL = "Legal"
    BD = "Business Development"


class RunState(StrEnum):
    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    PARSING = "PARSING"
    CLASSIFYING = "CLASSIFYING"
    EXTRACTING = "EXTRACTING"
    VALIDATING = "VALIDATING"
    ANALYZING = "ANALYZING"
    GENERATING_QUESTIONS = "GENERATING_QUESTIONS"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    REPORT_READY = "REPORT_READY"
    FAILED = "FAILED"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Helpers shared by every module
# ---------------------------------------------------------------------------


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_ws(text: str | None) -> str:
    """NFKC + collapse whitespace; used for quote verification."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("\u00a0", " ").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", t).strip()


_POPULATION_ALIASES = {
    "itt": "ITT",
    "intention-to-treat": "ITT",
    "intention to treat": "ITT",
    "intent-to-treat": "ITT",
    "full analysis set": "FAS",
    "fas": "FAS",
    "modified itt": "mITT",
    "modified intention-to-treat": "mITT",
    "mitt": "mITT",
    "safety population": "SAFETY",
    "safety set": "SAFETY",
    "safety analysis set": "SAFETY",
    "per-protocol": "PP",
    "per protocol": "PP",
    "pp": "PP",
    "response-evaluable": "RESPONSE_EVALUABLE",
    "response evaluable": "RESPONSE_EVALUABLE",
    "evaluable population": "RESPONSE_EVALUABLE",
    "all-comers": "ALL",
    "all comers": "ALL",
}


def normalize_population(raw: str | None) -> str | None:
    if not raw:
        return None
    key = normalize_ws(raw).lower().strip(" .;:()")
    if key in _POPULATION_ALIASES:
        return _POPULATION_ALIASES[key]
    for alias, canonical in _POPULATION_ALIASES.items():
        if alias in key:
            return canonical
    if re.fullmatch(r"n\s*=\s*\d+", key):
        return None
    return normalize_ws(raw)


_METHOD_ALIASES = [
    (r"recist\s*(?:v(?:ersion)?\s*)?1\.1", "RECIST 1.1"),
    (r"recist\s*(?:v(?:ersion)?\s*)?1\.0", "RECIST 1.0"),
    (r"irecist", "iRECIST"),
    (r"lugano", "Lugano"),
    (r"\bwho\b", "WHO"),
    (r"choi", "Choi"),
    (r"rcist", "RANO"),
    (r"rano", "RANO"),
]


def normalize_method(raw: str | None) -> str | None:
    if not raw:
        return None
    key = normalize_ws(raw).lower()
    for pattern, canonical in _METHOD_ALIASES:
        if re.search(pattern, key):
            return canonical
    return None


_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d-%b-%Y", "%b %d, %Y",
    "%B %d, %Y", "%d %B %Y", "%m/%d/%Y", "%Y%m%d",
)


def normalize_date(raw: str | None) -> str | None:
    """Return ISO date (YYYY-MM-DD) or None."""
    if not raw:
        return None
    t = normalize_ws(raw).strip(" .,;:")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})", t)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)[:3]} {m.group(3)}", "%d %b %Y").date().isoformat()
        except ValueError:
            return None
    return None


def normalize_trial(raw: str | None) -> str | None:
    if not raw:
        return None
    t = normalize_ws(raw).upper()
    m = re.search(r"NCT\s*0*(\d{4,8})", t)
    if m:
        return f"NCT{m.group(1).zfill(8)}"
    if re.fullmatch(r"[A-Z0-9][A-Z0-9\-_/ ]{3,40}", t):
        return t
    return None


def normalize_endpoint_def(raw: str | None, method: str | None = None) -> str | None:
    if raw:
        base = normalize_ws(raw)
        if method and method.lower() not in base.lower():
            return f"{base} [{method}]"
        return base
    return f"defined-by-method [{method}]" if method else None


def comparable_key(ev: "EvidenceDraft | EvidenceRecord") -> str:
    payload = json.dumps(
        {
            "trial": ev.trial_key,
            "arm": (ev.arm or "").strip().lower() or None,
            "population": ev.population,
            "data_cutoff": ev.data_cutoff,
            "endpoint_def": ev.endpoint_def,
            "assessment_method": ev.assessment_method,
        },
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def evidence_fingerprint(document_id: str, field_name: str, value_num, value_text,
                         page: int, quote: str) -> str:
    payload = "|".join(
        [
            document_id,
            field_name,
            "" if value_num is None else f"{value_num:.6g}",
            "" if value_text is None else normalize_ws(value_text).lower(),
            str(page),
            normalize_ws(quote)[:300].lower(),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Draft / transport models
# ---------------------------------------------------------------------------


class SourceRef(BaseModel):
    document_id: str
    document_type: str | None = None
    filename: str | None = None
    page: int
    table: str | None = None
    quote: str
    bbox: list[float] | None = None


class EvidenceDraft(BaseModel):
    """What an extractor returns.  The store assigns IDs and hashes."""

    model_config = {"extra": "forbid"}

    field_name: FieldName
    category: EvidenceCategory
    value_num: float | None = None
    value_text: str | None = None
    unit: str | None = None
    population: str | None = None
    arm: str | None = None
    trial_key: str | None = None
    data_cutoff: str | None = None
    endpoint_def: str | None = None
    assessment_method: str | None = None
    page: int = Field(ge=1)
    table: str | None = None
    quote: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    extractor: str = "rules"
    notes: str | None = None

    @field_validator("population")
    @classmethod
    def _pop(cls, v: str | None) -> str | None:
        return normalize_population(v)

    @field_validator("assessment_method")
    @classmethod
    def _method(cls, v: str | None) -> str | None:
        return normalize_method(v)

    @field_validator("data_cutoff")
    @classmethod
    def _cutoff(cls, v: str | None) -> str | None:
        return normalize_date(v)

    @field_validator("trial_key")
    @classmethod
    def _trial(cls, v: str | None) -> str | None:
        return normalize_trial(v)

    @model_validator(mode="after")
    def _has_value(self) -> "EvidenceDraft":
        if self.value_num is None and not self.value_text:
            raise ValueError(f"{self.field_name}: evidence needs value_num or value_text")
        if self.field_name in NUMERIC_FIELDS and self.value_num is None:
            raise ValueError(f"{self.field_name}: numeric field needs value_num")
        if self.value_num is not None and self.unit is None:
            if self.field_name in PERCENT_FIELDS:
                self.unit = "%"
            elif self.field_name in TIME_FIELDS:
                self.unit = "months"
            elif self.field_name in COUNT_FIELDS:
                self.unit = "patients"
        return self


class EvidenceRecord(EvidenceDraft):
    """Persisted evidence with provenance and verification metadata."""

    evidence_id: str
    project_id: str
    document_id: str
    document_type: str | None = None
    filename: str | None = None
    asset_id: str | None = None
    trial_id: str | None = None
    fingerprint: str
    comparable_key: str = ""
    verification_status: VerificationStatus = VerificationStatus.PENDING
    verification_note: str | None = None
    verified_by: str | None = None
    created_at: str = ""
    source: SourceRef | None = None


class FindingDraft(BaseModel):
    model_config = {"extra": "forbid"}

    finding_type: FindingType
    domain: str
    title: str
    explanation: str
    severity: Severity = Severity.MEDIUM
    evidence_ids: list[str] = Field(default_factory=list)
    possible_causes: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommended_action: str | None = None
    rule_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str | None = None


class QuestionDraft(BaseModel):
    model_config = {"extra": "forbid"}

    question: str = Field(min_length=10)
    rationale: str
    category: str
    priority: Priority = Priority.MEDIUM
    owner: Owner = Owner.CLINICAL
    required_documents: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    dedup_key: str | None = None
    finding_type: str | None = None
    source: str = "auto"


class CoverageDraft(BaseModel):
    model_config = {"extra": "forbid"}

    rule_id: str
    dimension: str
    requirement: str
    status: CoverageStatus
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None
    priority: Priority = Priority.MEDIUM   # only meaningful when not sufficient


class RunSummary(BaseModel):
    run_id: str
    project_id: str
    state: RunState
    status: RunStatus
    stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: str = ""
    finished_at: str | None = None


# ---------------------------------------------------------------------------
# API-facing row projections (serialised by FastAPI)
# ---------------------------------------------------------------------------


class DocumentOut(BaseModel):
    document_id: str
    project_id: str
    filename: str
    doc_type: str
    version: str
    doc_date: str | None = None
    page_count: int
    sha256: str
    status: str
    language: str | None = None
    parse_quality: float | None = None
    parse_error: str | None = None
    doc_family: str | None = None
    is_latest: bool = True
    uploaded_at: str


class EvidenceOut(BaseModel):
    evidence_id: str
    project_id: str
    asset_id: str | None = None
    trial_id: str | None = None
    document_id: str
    document_type: str | None = None
    filename: str | None = None
    field_name: str
    category: str
    value_num: float | None = None
    value_text: str | None = None
    unit: str | None = None
    population: str | None = None
    arm: str | None = None
    trial_key: str | None = None
    data_cutoff: str | None = None
    endpoint_def: str | None = None
    assessment_method: str | None = None
    page: int
    table: str | None = None
    quote: str
    confidence: float
    extractor: str
    verification_status: str
    verification_note: str | None = None
    comparable_key: str
    source: SourceRef | None = None


class FindingOut(BaseModel):
    finding_id: str
    project_id: str
    finding_type: str
    domain: str
    title: str
    explanation: str
    severity: str
    status: str
    evidence_ids: list[str]
    possible_causes: list[str]
    missing_information: list[str]
    recommended_action: str | None = None
    rule_id: str | None = None
    detail: dict[str, Any]
    created_at: str


class QuestionOut(BaseModel):
    question_id: str
    project_id: str
    question: str
    rationale: str
    category: str
    priority: str
    owner: str
    required_documents: list[str]
    evidence_ids: list[str]
    finding_ids: list[str]
    status: str
    review_status: str
    dedup_key: str | None = None
    source: str
    created_at: str


class CoverageOut(BaseModel):
    coverage_id: str
    project_id: str
    rule_id: str
    dimension: str
    requirement: str
    status: str
    evidence_ids: list[str]
    note: str | None = None
    priority: str
