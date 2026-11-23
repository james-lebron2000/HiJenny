"""Seller-question generation agent (analysis-dev).

Converts (a) open findings with severity medium or above and (b) high-priority
coverage gaps into professional, evidence-linked seller questions for a pharma
business-development diligence.

Design rules:

* **Professional BD English, not accusatory.** Questions say what the current
  package reports and ask the seller to confirm / provide; they never assert
  that the seller failed to do something.  Missing evidence is phrased as "not
  identified in the current package".
* **Actionable.** Every question contains the specific ask, why it matters and
  the suggested verification step (in ``rationale``), the exact documents/data
  requested (``required_documents``), the source evidence ids and the
  responsible owner domain.
* **Deterministic dedupe/merge.** A question derived from a finding uses
  ``hash(finding dedup_key + normalised ask category)``; a question derived from
  a rule uses ``hash(rule_id + requirement + category)``.  Finding-derived and
  coverage-derived questions for the same checklist item therefore collapse into
  one question.  Candidates that share the same owner, category, finding type
  and document request are additionally merged into one request so the seller
  receives a consolidated ask rather than near-duplicates.
* **Resumable / human-review friendly.** Persistence goes through
  :func:`store.save_questions`, which preserves locked rows and reports
  ``skipped_locked``; re-running updates the same dedup keys instead of
  duplicating questions.

Entry point: :func:`run(session, project_id, force=False) -> stats`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from hijenny import rules as rules_module
from hijenny import store
from hijenny.schemas import (
    CoverageStatus,
    FindingType,
    Owner,
    Priority,
    QuestionDraft,
    Severity,
)

# ---------------------------------------------------------------------------
# Domain vocabulary
# ---------------------------------------------------------------------------

_SEVERITY_TO_PRIORITY: dict[str, str] = {
    str(Severity.CRITICAL): str(Priority.CRITICAL),
    str(Severity.HIGH): str(Priority.HIGH),
    str(Severity.MEDIUM): str(Priority.MEDIUM),
    str(Severity.LOW): str(Priority.LOW),
    str(Severity.INFO): str(Priority.LOW),
}
_PRIORITY_RANK: dict[str, int] = {
    str(Priority.CRITICAL): 0,
    str(Priority.HIGH): 1,
    str(Priority.MEDIUM): 2,
    str(Priority.LOW): 3,
}

DOMAIN_CATEGORIES: dict[str, str] = {
    "efficacy": "Efficacy",
    "safety": "Safety",
    "statistics": "Statistical analysis",
    "population": "Analysis populations",
    "baseline": "Baseline characteristics",
    "disposition": "Patient disposition",
    "quality": "Data quality & compliance",
    "data_maturity": "Data maturity",
    "data_quality": "Data quality",
    "endpoints": "Endpoints",
    "study_design": "Study design",
    "technical": "Document integrity",
    "clinical": "Clinical data",
}

DOMAIN_OWNERS: dict[str, str] = {
    "efficacy": str(Owner.CLINICAL),
    "safety": str(Owner.SAFETY),
    "statistics": str(Owner.BIOSTATISTICS),
    "population": str(Owner.BIOSTATISTICS),
    "baseline": str(Owner.CLINICAL),
    "disposition": str(Owner.CLINICAL),
    "quality": str(Owner.REGULATORY),
    "data_maturity": str(Owner.BIOSTATISTICS),
    "data_quality": str(Owner.BIOSTATISTICS),
    "endpoints": str(Owner.CLINICAL),
    "study_design": str(Owner.CLINICAL),
    "technical": str(Owner.LEGAL),
    "clinical": str(Owner.CLINICAL),
}

DOMAIN_DOCUMENTS: dict[str, list[str]] = {
    "efficacy": [
        "Updated Clinical Study Report (CSR) efficacy tables",
        "Statistical Analysis Plan (SAP)",
        "Analysis datasets / TLF outputs for the endpoint",
    ],
    "safety": [
        "Updated safety tables and SAE listing",
        "CSR safety section",
        "Analysis datasets for adverse events",
    ],
    "statistics": [
        "Statistical Analysis Plan (SAP)",
        "Analysis datasets / TLF outputs",
    ],
    "population": [
        "SAP population definitions and sizes",
        "CSR disposition / population tables",
    ],
    "baseline": ["CSR baseline characteristics table"],
    "disposition": ["Patient disposition listing", "CSR disposition table"],
    "quality": [
        "Protocol deviation log",
        "Independent monitoring / audit summary",
        "CSR data-quality section",
    ],
    "data_maturity": [
        "Updated CSR / SAP data cut-off documentation",
        "Updated analysis tables for the current data snapshot",
    ],
    "endpoints": ["Updated CSR endpoint tables", "SAP endpoint definitions"],
    "study_design": ["Clinical protocol / amended protocol", "CSR study design section"],
    "technical": [
        "Document provenance confirmation",
        "Native version of the flagged document",
    ],
    "data_quality": ["Text-searchable or native version of the affected tables"],
    "clinical": ["Updated CSR section supporting the reported value"],
}

# Human-readable expansion of document type codes used by the rule packs.
DOCUMENT_TYPE_LABELS: dict[str, str] = {
    "CSR": "Updated Clinical Study Report (CSR)",
    "SAP": "Statistical Analysis Plan (SAP)",
    "IB": "Investigator's Brochure (IB)",
    "PROTOCOL": "Clinical Study Protocol",
    "SAFETY": "Safety summary / SAE tables",
    "DISPOSITION": "Patient disposition listing",
    "CMC": "CMC Module 3 summary",
    "REGULATORY": "Regulatory correspondence",
    "PRECLINICAL": "Nonclinical study reports",
    "PATENT": "Patent family documentation",
    "COMMERCIAL": "Commercial forecast model",
}

_OWNER_BY_LOWER = {owner.value.lower(): owner.value for owner in Owner}
_OPEN_FINDING_SEVERITIES = {
    str(Severity.CRITICAL),
    str(Severity.HIGH),
    str(Severity.MEDIUM),
}
_QUESTIONABLE_FINDING_TYPES = {
    str(FindingType.DATA_INCONSISTENCY),
    str(FindingType.VERSION_DIFFERENCE),
    str(FindingType.DEFINITION_DIFFERENCE),
    str(FindingType.COMPARABILITY_UNCLEAR),
    str(FindingType.EVIDENCE_GAP),
    str(FindingType.INCOMPLETE_EVIDENCE),
    str(FindingType.TECHNICAL_CONCERN),
}

CONSISTENCY_ASKS: dict[str, str] = {
    str(FindingType.DATA_INCONSISTENCY): (
        "Please confirm which value is the final analysis value, reconcile the difference "
        "with the underlying source table or analysis dataset, and confirm the analysis "
        "population, data cut-off date and assessment method that apply."
    ),
    str(FindingType.VERSION_DIFFERENCE): (
        "Please confirm the current data cut-off, identify any documents that reflect an "
        "earlier snapshot, and provide the updated analysis supporting the most recent value."
    ),
    str(FindingType.DEFINITION_DIFFERENCE): (
        "Please clarify which analysis population, endpoint definition and assessment method "
        "apply to each analysis, and provide a like-for-like comparison of the primary and "
        "sensitivity results."
    ),
    str(FindingType.COMPARABILITY_UNCLEAR): (
        "Please confirm the analysis population, data cut-off, endpoint definition and "
        "assessment method applicable to each reported value so that the values can be "
        "compared on a consistent basis."
    ),
}

CONSISTENCY_WHY: dict[str, str] = {
    str(FindingType.DATA_INCONSISTENCY): (
        "unreconciled figures feed directly into valuation and regulatory risk, and a "
        "discrepancy in the data room must be explained before the numbers can be relied upon."
    ),
    str(FindingType.VERSION_DIFFERENCE): (
        "a diligence model should rely on the current data snapshot; mixing cut-offs can "
        "create artificial discrepancies and misstate the latest efficacy or safety position."
    ),
    str(FindingType.DEFINITION_DIFFERENCE): (
        "primary and sensitivity analyses can legitimately differ, but the comparison must be "
        "made per definition before an efficacy or safety conclusion is drawn."
    ),
    str(FindingType.COMPARABILITY_UNCLEAR): (
        "values cannot be compared until the analysis metadata is confirmed; a confirmation "
        "prevents a false discrepancy from being escalated."
    ),
}

CONSISTENCY_VERIFICATION: dict[str, str] = {
    str(FindingType.DATA_INCONSISTENCY): (
        "cross-check the final CSR table against the analysis dataset / TLF output and confirm "
        "the population, cut-off and assessor flags."
    ),
    str(FindingType.VERSION_DIFFERENCE): (
        "compare the analysis cut-off across CSR, SAP and IB and confirm which documents have "
        "been updated to the latest snapshot."
    ),
    str(FindingType.DEFINITION_DIFFERENCE): (
        "compare the primary and sensitivity analyses using the same endpoint definition and "
        "confirm the population and assessor for each."
    ),
    str(FindingType.COMPARABILITY_UNCLEAR): (
        "check the analysis output metadata (population, cut-off, method) against the cited "
        "table and the SAP."
    ),
}


@dataclass
class _Candidate:
    """Intermediate question before dedupe/merge."""

    dedup_key: str
    context: str
    ask: str
    rationale: str
    category: str
    priority: str
    owner: str
    required_documents: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    finding_ids: list[str] = field(default_factory=list)
    finding_type: str | None = None
    field: str | None = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _hash_key(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _canonical_owner(value: Any, fallback: str) -> str:
    if isinstance(value, Owner):
        return value.value
    if value:
        canonical = _OWNER_BY_LOWER.get(str(value).strip().lower())
        if canonical:
            return canonical
    return fallback


def _category_for(domain: str) -> str:
    return DOMAIN_CATEGORIES.get(domain, "Clinical data")


def _documents_for(domain: str) -> list[str]:
    return list(DOMAIN_DOCUMENTS.get(domain, DOMAIN_DOCUMENTS["clinical"]))


def _expand_document_codes(codes: Sequence[str]) -> list[str]:
    documents: list[str] = []
    for code in codes:
        label = DOCUMENT_TYPE_LABELS.get(str(code).upper())
        documents.append(label or str(code))
    return documents


def _dedupe_list(values: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _join_evidence_ids(evidence_ids: Sequence[str]) -> str:
    ids = _dedupe_list(evidence_ids)
    if not ids:
        return "document-level finding (no single evidence row)"
    return ", ".join(ids)


def _format_value_line(value: Mapping[str, Any]) -> str:
    label = value.get("value_label")
    if label is None:
        label = value.get("value")
    document = value.get("document") or value.get("document_id") or "unknown document"
    page = value.get("page")
    suffix = f" (p.{page})" if page else ""
    return f"{label} in {document}{suffix}"


def _format_dims(dims: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key, label in (
        ("population", "population"),
        ("data_cutoff", "data cut-off"),
        ("assessment_method", "assessment method"),
        ("endpoint_def", "endpoint definition"),
        ("arm", "arm"),
        ("trial_key", "trial"),
    ):
        value = dims.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            parts.append(f"{label} {', '.join(str(v) for v in value)}")
        else:
            parts.append(f"{label} {value}")
    return "; ".join(parts)


def _finding_evidence_ids(finding: Mapping[str, Any]) -> list[str]:
    return [str(value) for value in finding.get("evidence_ids") or []]


# ---------------------------------------------------------------------------
# Candidate builders
# ---------------------------------------------------------------------------


def _consistency_candidate(finding: Mapping[str, Any]) -> _Candidate:
    finding_type = str(finding.get("finding_type"))
    detail = finding.get("detail") or {}
    values = detail.get("values") or []
    dims = detail.get("dims") or {}
    value_text = "; ".join(_format_value_line(value) for value in values[:6]) or "values not captured"
    if len(values) > 6:
        value_text += f"; and {len(values) - 6} further record(s)"
    label = str(
        detail.get("field_label") or detail.get("field") or "the reported value"
    ).replace("_", " ")
    context = f"The current package reports {label} as {value_text}."
    dim_text = _format_dims(dims)
    if dim_text:
        context += f" Reported analysis metadata: {dim_text}."
    ask = CONSISTENCY_ASKS.get(finding_type, CONSISTENCY_ASKS[str(FindingType.COMPARABILITY_UNCLEAR)])
    domain = str(finding.get("domain") or "clinical")
    category = _category_for(domain)
    owner = _canonical_owner(None, DOMAIN_OWNERS.get(domain, str(Owner.CLINICAL)))
    priority = _SEVERITY_TO_PRIORITY.get(str(finding.get("severity")), str(Priority.MEDIUM))
    why = CONSISTENCY_WHY.get(finding_type, "the discrepancy must be reconciled before reliance.")
    verification = CONSISTENCY_VERIFICATION.get(
        finding_type, "verify the reported value against the source table and analysis output."
    )
    rationale = (
        f"Why it matters: {why} Suggested verification: {verification} "
        f"Evidence IDs: {_join_evidence_ids(_finding_evidence_ids(finding))}."
    )
    dedup = _hash_key(f"question|finding|{finding.get('dedup_key')}|{category}")
    return _Candidate(
        dedup_key=dedup,
        context=context,
        ask=ask,
        rationale=rationale,
        category=category,
        priority=priority,
        owner=owner,
        required_documents=_documents_for(domain),
        evidence_ids=_finding_evidence_ids(finding),
        finding_ids=[str(finding.get("finding_id"))],
        finding_type=finding_type,
        field=str(detail.get("field") or "") or None,
    )


def _gap_candidate_from_finding(
    finding: Mapping[str, Any], rules_by_id: Mapping[str, rules_module.Rule]
) -> _Candidate:
    detail = finding.get("detail") or {}
    rule_id = str(detail.get("rule_id") or finding.get("rule_id") or "")
    requirement = str(detail.get("requirement") or "the missing evidence item")
    dimension = str(detail.get("dimension") or finding.get("domain") or "clinical")
    status = str(detail.get("status") or str(CoverageStatus.NOT_FOUND))
    rule = rules_by_id.get(rule_id)
    documents = _expand_document_codes(rule.required_documents) if rule else _documents_for(dimension)
    documents = _dedupe_list([*documents, *_documents_for(dimension)]) if not documents else documents
    owner = _canonical_owner(
        detail.get("owner") or (rule.owner if rule else None),
        DOMAIN_OWNERS.get(dimension, str(Owner.CLINICAL)),
    )
    category = _category_for(dimension)
    priority = _SEVERITY_TO_PRIORITY.get(str(finding.get("severity")), str(Priority.MEDIUM))
    if status == str(CoverageStatus.PARTIAL):
        context = (
            f"The current package contains only partial evidence for '{requirement}' "
            f"({dimension}); the complete item was not identified in the current package."
        )
    else:
        context = (
            f"The current package does not identify evidence for '{requirement}' "
            f"({dimension}); this item was not identified in the current package."
        )
    ask = _gap_ask(rule, requirement, dimension, documents)
    rationale = (
        f"Why it matters: this checklist item is required to complete diligence on the asset; "
        f"a missing or partial item limits the ability to assess the asset independently of the "
        f"seller's summary. Suggested verification: confirm whether the item is addressed "
        f"elsewhere in the package and obtain the underlying source output for cross-checking. "
        f"Evidence IDs: {_join_evidence_ids(_finding_evidence_ids(finding))}."
    )
    dedup = _hash_key(f"qgap|{rule_id}|{requirement}|{category}")
    return _Candidate(
        dedup_key=dedup,
        context=context,
        ask=ask,
        rationale=rationale,
        category=category,
        priority=priority,
        owner=owner,
        required_documents=documents,
        evidence_ids=_finding_evidence_ids(finding),
        finding_ids=[str(finding.get("finding_id"))],
        finding_type=str(finding.get("finding_type")),
    )


def _gap_ask(
    rule: rules_module.Rule | None,
    requirement: str,
    dimension: str,
    documents: Sequence[str],
) -> str:
    if rule is not None and rule.question_template:
        rendered = rules_module.format_question_template(
            rule.question_template,
            {
                "requirement": requirement,
                "dimension": dimension,
                "documents": ", ".join(documents) if documents else "the relevant source documents",
                "asset": "the asset",
            },
        ).strip()
        if rendered:
            return rendered
    return (
        "Please provide the source documentation and underlying analysis output for this "
        "item, or confirm where it is addressed in the package."
    )


def _gap_candidate_from_coverage(
    coverage: Mapping[str, Any], rules_by_id: Mapping[str, rules_module.Rule]
) -> _Candidate:
    rule_id = str(coverage.get("rule_id") or "")
    requirement = str(coverage.get("requirement") or "the missing evidence item")
    dimension = str(coverage.get("dimension") or "clinical")
    status = str(coverage.get("status") or "")
    rule = rules_by_id.get(rule_id)
    documents = _expand_document_codes(rule.required_documents) if rule else _documents_for(dimension)
    owner = _canonical_owner(
        rule.owner if rule else None,
        DOMAIN_OWNERS.get(dimension, str(Owner.CLINICAL)),
    )
    category = _category_for(dimension)
    priority = str(coverage.get("priority") or Priority.MEDIUM)
    if status == str(CoverageStatus.PARTIAL):
        context = (
            f"The current package contains only partial evidence for '{requirement}' "
            f"({dimension}); the complete item was not identified in the current package."
        )
    else:
        context = (
            f"The current package does not identify evidence for '{requirement}' "
            f"({dimension}); this item was not identified in the current package."
        )
    ask = _gap_ask(rule, requirement, dimension, documents)
    evidence_ids = [str(value) for value in coverage.get("evidence_ids") or []]
    rationale = (
        f"Why it matters: this checklist item is required to complete diligence on the asset; "
        f"a missing or partial item limits the ability to assess the asset independently of the "
        f"seller's summary. Suggested verification: confirm whether the item is addressed "
        f"elsewhere in the package and obtain the underlying source output for cross-checking. "
        f"Evidence IDs: {_join_evidence_ids(evidence_ids)}."
    )
    dedup = _hash_key(f"qgap|{rule_id}|{requirement}|{category}")
    return _Candidate(
        dedup_key=dedup,
        context=context,
        ask=ask,
        rationale=rationale,
        category=category,
        priority=priority,
        owner=owner,
        required_documents=documents,
        evidence_ids=evidence_ids,
        finding_ids=[],
        finding_type=str(FindingType.EVIDENCE_GAP),
    )


def _technical_candidate(finding: Mapping[str, Any]) -> _Candidate:
    detail = finding.get("detail") or {}
    documents = ", ".join(str(d) for d in detail.get("documents") or []) or "the flagged document"
    finding_type = str(finding.get("finding_type"))
    if finding_type == str(FindingType.TECHNICAL_CONCERN):
        category = "Document integrity"
        domain = "technical"
        context = (
            f"Document '{documents}' contains text that appears to be addressed to automated "
            f"reviewers embedded within the document content."
        )
        ask = (
            "Please confirm the provenance and integrity of the document and the flagged page, "
            "and confirm whether the embedded text was intended to be part of the submitted "
            "document."
        )
        why = (
            "document integrity affects the reliance that can be placed on the data room; "
            "embedded instructions are treated strictly as data and must be clarified before "
            "the document is relied upon."
        )
        verification = (
            "compare the flagged page against the native source file and confirm the document "
            "version history with the seller."
        )
        owner = str(Owner.LEGAL)
        asked_documents = _documents_for("technical")
    else:
        category = "Data quality"
        domain = "data_quality"
        context = (
            f"Evidence extracted from '{documents}' could not be fully verified against the cited "
            f"page text, so the reported values cannot yet be confirmed from the source."
        )
        ask = (
            "Please provide a text-searchable or native version of the affected tables or "
            "sections so that the reported values can be verified against their source."
        )
        why = (
            "unverified values weaken every downstream finding and question; the source must be "
            "confirmable before the numbers are relied upon."
        )
        verification = (
            "re-run quote verification against the supplied searchable source and confirm the "
            "page references for the affected evidence items."
        )
        owner = str(Owner.BIOSTATISTICS)
        asked_documents = _documents_for("data_quality")
    priority = _SEVERITY_TO_PRIORITY.get(str(finding.get("severity")), str(Priority.HIGH))
    rationale = (
        f"Why it matters: {why} Suggested verification: {verification} "
        f"Evidence IDs: {_join_evidence_ids(_finding_evidence_ids(finding))}."
    )
    dedup = _hash_key(f"question|finding|{finding.get('dedup_key')}|{category}")
    return _Candidate(
        dedup_key=dedup,
        context=context,
        ask=ask,
        rationale=rationale,
        category=category,
        priority=priority,
        owner=owner,
        required_documents=asked_documents,
        evidence_ids=_finding_evidence_ids(finding),
        finding_ids=[str(finding.get("finding_id"))],
        finding_type=finding_type,
        field=str(detail.get("field") or "") or None,
    )


# ---------------------------------------------------------------------------
# Dedupe / merge
# ---------------------------------------------------------------------------


def _merge_candidates(first: _Candidate, other: _Candidate) -> None:
    """Merge *other* into *first* in place (keeps the primary question text)."""
    first.evidence_ids = _dedupe_list([*first.evidence_ids, *other.evidence_ids])
    first.finding_ids = _dedupe_list([*first.finding_ids, *other.finding_ids])
    first.required_documents = _dedupe_list([*first.required_documents, *other.required_documents])
    if _PRIORITY_RANK.get(other.priority, 9) < _PRIORITY_RANK.get(first.priority, 9):
        first.priority = other.priority
    if other.rationale and other.rationale not in first.rationale:
        first.rationale = f"{first.rationale}\n{other.rationale}"


def _merge_group(group: Sequence[_Candidate]) -> QuestionDraft:
    """Consolidate several candidates that request the same documents."""
    ordered = sorted(
        group,
        key=lambda candidate: (
            _PRIORITY_RANK.get(candidate.priority, 9),
            candidate.category,
            candidate.dedup_key,
        ),
    )
    owner = ordered[0].owner
    category = ordered[0].category
    finding_type = ordered[0].finding_type
    priority = min(
        (candidate.priority for candidate in ordered),
        key=lambda value: _PRIORITY_RANK.get(value, 9),
    )
    documents = _dedupe_list([doc for candidate in ordered for doc in candidate.required_documents])
    evidence_ids = _dedupe_list([eid for candidate in ordered for eid in candidate.evidence_ids])
    finding_ids = _dedupe_list([fid for candidate in ordered for fid in candidate.finding_ids])
    points = " ".join(
        f"({index}) {candidate.context} {candidate.ask}"
        for index, candidate in enumerate(ordered, start=1)
    )
    question = (
        f"Please provide the following clarifications and supporting documents ({category}): "
        f"{points}"
    )
    rationale_parts = ["This consolidated request covers several related items for the asset."]
    for candidate in ordered:
        if candidate.rationale and candidate.rationale not in rationale_parts:
            rationale_parts.append(candidate.rationale)
    rationale = "\n".join(rationale_parts)
    if documents:
        rationale += "\nRequested documents: " + "; ".join(documents) + "."
    signature = "|".join(sorted(documents))
    dedup = _hash_key(f"qmerge|{owner}|{category}|{finding_type}|{signature}")
    return QuestionDraft(
        question=question,
        rationale=rationale,
        category=category,
        priority=Priority(priority),
        owner=Owner(owner),
        required_documents=documents,
        evidence_ids=evidence_ids,
        finding_ids=finding_ids,
        dedup_key=dedup,
        finding_type=finding_type,
        source="auto",
    )


def _single_question(candidate: _Candidate) -> QuestionDraft:
    rationale = candidate.rationale
    if candidate.required_documents:
        rationale += "\nRequested documents: " + "; ".join(candidate.required_documents) + "."
    return QuestionDraft(
        question=f"{candidate.context} {candidate.ask}",
        rationale=rationale,
        category=candidate.category,
        priority=Priority(candidate.priority),
        owner=Owner(candidate.owner),
        required_documents=candidate.required_documents,
        evidence_ids=candidate.evidence_ids,
        finding_ids=candidate.finding_ids,
        dedup_key=candidate.dedup_key,
        finding_type=candidate.finding_type,
        source="auto",
    )


_CONSISTENCY_FINDING_TYPES = {
    str(FindingType.DATA_INCONSISTENCY),
    str(FindingType.VERSION_DIFFERENCE),
    str(FindingType.DEFINITION_DIFFERENCE),
    str(FindingType.COMPARABILITY_UNCLEAR),
}
_MAX_QUESTION_EVIDENCE = 12


def _merge_field_cluster(field: str, group: Sequence[_Candidate]) -> QuestionDraft:
    """One question per (field, finding class) cluster across documents.

    A field can produce a Type A reconciliation ask, a Type B cut-off ask and a
    Type C definition ask at once.  The seller should receive a single request
    with numbered points (priority = the most severe member), not three separate
    questions with different priorities.
    """
    ordered = sorted(
        group,
        key=lambda candidate: (_PRIORITY_RANK.get(candidate.priority, 9), candidate.dedup_key),
    )
    category = ordered[0].category
    owner = ordered[0].owner
    priority = min(
        (candidate.priority for candidate in ordered),
        key=lambda value: _PRIORITY_RANK.get(value, 9),
    )
    finding_type = ordered[0].finding_type
    field_label = str(ordered[0].context).split(" reports ", 1)[-1].split(" as ", 1)[0]
    documents = _dedupe_list([doc for candidate in ordered for doc in candidate.required_documents])
    evidence_ids = _dedupe_list(
        [eid for candidate in ordered for eid in candidate.evidence_ids]
    )[:_MAX_QUESTION_EVIDENCE]
    finding_ids = _dedupe_list([fid for candidate in ordered for fid in candidate.finding_ids])
    if len(ordered) == 1:
        question = f"{ordered[0].context} {ordered[0].ask}"
    else:
        points = " ".join(
            f"({index}) {candidate.context} {candidate.ask}"
            for index, candidate in enumerate(ordered, start=1)
        )
        question = (
            f"Please provide the following clarifications and supporting documents for "
            f"{field_label} ({category}): {points}"
        )
    rationale_parts = [
        "This consolidated request covers the related reconciliation / definition points "
        "for one reported metric."
    ]
    for candidate in ordered:
        if candidate.rationale and candidate.rationale not in rationale_parts:
            rationale_parts.append(candidate.rationale)
    rationale = "\n".join(rationale_parts)
    if documents:
        rationale += "\nRequested documents: " + "; ".join(documents) + "."
    return QuestionDraft(
        question=question,
        rationale=rationale,
        category=category,
        priority=Priority(priority),
        owner=Owner(owner),
        required_documents=documents,
        evidence_ids=evidence_ids,
        finding_ids=finding_ids,
        dedup_key=_hash_key(f"qmerge|field|{field}"),
        finding_type=finding_type,
        source="auto",
    )


def _build_question_drafts(
    candidates: Sequence[_Candidate],
) -> tuple[list[QuestionDraft], int]:
    """Dedupe by key, then merge candidates sharing owner/category/type/doc request."""
    deduped: dict[str, _Candidate] = {}
    merged = 0
    for candidate in candidates:
        existing = deduped.get(candidate.dedup_key)
        if existing is None:
            deduped[candidate.dedup_key] = candidate
        else:
            _merge_candidates(existing, candidate)
            merged += 1

    consistency_fields: dict[str, list[_Candidate]] = {}
    others: list[_Candidate] = []
    for candidate in deduped.values():
        if candidate.field and candidate.finding_type in _CONSISTENCY_FINDING_TYPES:
            consistency_fields.setdefault(candidate.field, []).append(candidate)
        else:
            others.append(candidate)

    drafts: list[QuestionDraft] = []
    for field in sorted(consistency_fields):
        group = consistency_fields[field]
        if len(group) > 1:
            merged += len(group) - 1
        drafts.append(_merge_field_cluster(field, group))

    groups: dict[tuple, list[_Candidate]] = {}
    for candidate in others:
        signature = tuple(sorted(_dedupe_list(candidate.required_documents)))
        group_key = (candidate.owner, candidate.category, candidate.finding_type, signature)
        groups.setdefault(group_key, []).append(candidate)
    for group in groups.values():
        if len(group) == 1:
            drafts.append(_single_question(group[0]))
        else:
            merged += len(group) - 1
            drafts.append(_merge_group(group))
    return drafts, merged


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(session, project_id: str, force: bool = False) -> dict:
    """Generate/update seller questions for *project_id*."""
    loaded_rules = rules_module.load_rules()
    rules_by_id = rules_module.rule_index(loaded_rules)

    findings = [
        finding
        for finding in store.list_findings(session, project_id)
        if str(finding.get("status")) == "open"
        and str(finding.get("severity")) in _OPEN_FINDING_SEVERITIES
        and str(finding.get("finding_type")) in _QUESTIONABLE_FINDING_TYPES
    ]
    coverage = [
        row
        for row in store.list_coverage(session, project_id)
        if str(row.get("status")) in {str(CoverageStatus.PARTIAL), str(CoverageStatus.NOT_FOUND)}
        and str(row.get("priority")) in {str(Priority.CRITICAL), str(Priority.HIGH)}
    ]

    candidates: list[_Candidate] = []
    for finding in findings:
        finding_type = str(finding.get("finding_type"))
        if finding_type == str(FindingType.EVIDENCE_GAP):
            candidates.append(_gap_candidate_from_finding(finding, rules_by_id))
        elif finding_type in {
            str(FindingType.TECHNICAL_CONCERN),
            str(FindingType.INCOMPLETE_EVIDENCE),
        }:
            candidates.append(_technical_candidate(finding))
        else:
            candidates.append(_consistency_candidate(finding))
    for row in coverage:
        candidates.append(_gap_candidate_from_coverage(row, rules_by_id))

    drafts, merged = _build_question_drafts(candidates)
    saved = store.save_questions(session, project_id, drafts)

    # Close auto-generated, unreviewed draft questions that no longer correspond
    # to any current finding/coverage item (e.g. a round-2 upload resolves the
    # underlying issue).  Locked, human-edited, approved or sent questions are
    # never touched.
    closed = 0
    current_keys = {draft.dedup_key for draft in drafts if draft.dedup_key}
    if findings or coverage:
        for question in store.list_questions(session, project_id):
            if str(question.get("status")) != "draft":
                continue
            if str(question.get("review_status")) != "pending" or question.get("locked"):
                continue
            if str(question.get("source")) != "auto":
                continue
            key = str(question.get("dedup_key") or "")
            if not key or key in current_keys:
                continue
            updated = store.update_question(
                session,
                question["question_id"],
                actor="system:questions",
                status="closed",
                evidence_ids=[],  # evidence rows were rebuilt by the updated package
            )
            if updated is not None:
                closed += 1

    return {
        "questions_created": saved["created"],
        "questions_updated": saved["updated"],
        "questions_closed": closed,
        "skipped_locked": saved["skipped_locked"],
        "merged": merged,
        "findings_considered": len(findings),
        "coverage_gaps_considered": len(coverage),
        "candidates": len(candidates),
    }
