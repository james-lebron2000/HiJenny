"""Evidence-gap / coverage agent (analysis-dev).

Coverage is computed from **structured evidence presence** (``store.get_evidence``
by field), never from vector search: a checklist requirement is only
``sufficient`` when the expected evidence fields are actually present with a page
citation.  Statuses:

``sufficient``     every required field present and minimum document/record counts met.
``partial``        some evidence present, but fields or document coverage are incomplete.

A requirement is ``sufficient`` only when **every** field listed in its
``fields`` list has at least one matching evidence record (plus the
``min_documents`` / ``min_records`` / ``acceptable_texts`` predicates declared by
the rule).  This is what turns a ``n_pp``-missing rule into ``partial`` rather
than ``sufficient`` when ITT and FAS sizes are present.
``not_found``      relevant documents are in the package but the evidence was not
                   identified.  Wording is deliberately "not identified in the
                   current package" — absence of evidence is **not** evidence that
                   the sponsor did not perform the activity.
``not_applicable`` the rule does not apply to the project context (phase/area/
                   modality mismatch); reported transparently rather than dropped.
``not_reviewed``   no structured evidence has been extracted yet, so coverage
                   cannot be assessed.

The agent also produces two data-quality findings that are not clinical gaps:

* ``TECHNICAL_CONCERN`` — document metadata carries ``injection_flags`` from
  ingestion (prompt-injection attempts).  The text is treated strictly as
  untrusted data and never as an instruction; the finding exists so a human can
  assess document integrity/provenance.
* ``INCOMPLETE_EVIDENCE`` — a document family has evidence that failed quote
  verification (``failed`` / ``unverifiable``), summarised as a per-family count.

Coverage rows are persisted with :func:`store.replace_coverage`; findings are
upserted by stable dedup key.  Superseded documents are excluded from coverage
and from comparisons (active set = ``store.list_documents(..., latest_only=True)``).
Auto-generated findings that are no longer reproduced (e.g. after a round-2
upload closes a gap) are marked ``resolved`` so the lifecycle is visible.

Entry point: :func:`run(session, project_id, force=False) -> stats`.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Mapping, Sequence

from assetdd import rules as rules_module
from assetdd import store
from assetdd.rules import Requirement, Rule
from assetdd.schemas import (
    CoverageDraft,
    CoverageStatus,
    FindingDraft,
    FindingType,
    Priority,
    Severity,
    normalize_ws,
)

_PRIORITY_TO_SEVERITY: dict[str, str] = {
    str(Priority.CRITICAL): str(Severity.CRITICAL),
    str(Priority.HIGH): str(Severity.HIGH),
    str(Priority.MEDIUM): str(Severity.MEDIUM),
    str(Priority.LOW): str(Severity.LOW),
}
_GAP_FINDING_PRIORITIES: frozenset[str] = frozenset(
    {str(Priority.CRITICAL), str(Priority.HIGH), str(Priority.MEDIUM)}
)
_NOT_FOUND_PHRASE = "not identified in the current package"


# ---------------------------------------------------------------------------
# Project context
# ---------------------------------------------------------------------------


def build_context(session, project_id: str) -> dict[str, Any]:
    """Derive the rule-selection context from project/asset metadata."""
    project = store.get_project(session, project_id) or {}
    assets = store.list_assets(session, project_id)
    asset = assets[0] if assets else {}
    meta = project.get("meta") or {}
    indication = project.get("indication") or asset.get("indication") or ""
    phase = project.get("phase") or asset.get("phase") or ""
    modality = asset.get("modality") or ""
    area = meta.get("therapeutic_area") or rules_module.infer_therapeutic_area(indication)
    return rules_module.normalize_context(
        {
            "therapeutic_area": area,
            "phase": phase,
            "modality": modality,
            "indication": indication,
        }
    )


def _context_summary(context: Mapping[str, Any]) -> str:
    parts = []
    if context.get("therapeutic_area"):
        parts.append(f"therapeutic area {context['therapeutic_area']}")
    if context.get("phase"):
        parts.append(f"phase {context['phase']}")
    if context.get("modality"):
        parts.append(f"modality {context['modality']}")
    return ", ".join(parts) if parts else "unknown project context"


def _not_applicable_reason(rule: Rule, context: Mapping[str, Any]) -> str:
    ctx = rules_module.normalize_context(context)
    problems: list[str] = []
    if rule.therapeutic_areas and ctx.get("therapeutic_area"):
        matches = any(
            value in ctx["therapeutic_area"] or ctx["therapeutic_area"] in value
            for value in rule.therapeutic_areas
        )
        if not matches:
            problems.append(
                f"therapeutic area {', '.join(rule.therapeutic_areas)} "
                f"does not match project {ctx['therapeutic_area']}"
            )
    if rule.phases and ctx.get("phase") is not None and ctx["phase"] not in rule.phases:
        problems.append(
            f"phase {ctx['phase']} not in rule phases {list(rule.phases)}"
        )
    if rule.modalities and ctx.get("modality"):
        matches = any(
            value in ctx["modality"] or ctx["modality"] in value
            for value in rule.modalities
        )
        if not matches:
            problems.append(
                f"modality {', '.join(rule.modalities)} does not match project {ctx['modality']}"
            )
    return "; ".join(problems) if problems else f"rule does not apply to {_context_summary(context)}"


# ---------------------------------------------------------------------------
# Requirement evaluation
# ---------------------------------------------------------------------------


def _record_text(record: Mapping[str, Any]) -> str:
    parts = [
        str(record.get("value_text") or ""),
        str(record.get("quote") or ""),
        str(record.get("filename") or ""),
    ]
    return normalize_ws(" ".join(parts)).lower()


def _filter_acceptable(
    requirement: Requirement, records: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    if not requirement.acceptable_texts:
        return list(records)
    wanted = tuple(normalize_ws(text).lower() for text in requirement.acceptable_texts)
    return [record for record in records if any(token in _record_text(record) for token in wanted)]


def _evaluate_requirement(
    rule: Rule,
    requirement: Requirement,
    records: Sequence[Mapping[str, Any]],
    raw_records: Sequence[Mapping[str, Any]],
    *,
    project_has_evidence: bool,
) -> tuple[CoverageStatus, str, list[str]]:
    """Return (status, note, missing_fields) for one checklist requirement."""
    if not project_has_evidence:
        return (
            CoverageStatus.NOT_REVIEWED,
            "Not reviewed yet: no structured evidence has been extracted from the "
            "current package, so coverage cannot be assessed.",
            list(requirement.fields),
        )
    if not records:
        if raw_records:
            return (
                CoverageStatus.PARTIAL,
                "Partially covered: evidence for this item was identified, but it does "
                "not contain the expected content ("
                + ", ".join(requirement.acceptable_texts)
                + "); the specific results were " + _NOT_FOUND_PHRASE + ".",
                list(requirement.fields),
            )
        return (
            CoverageStatus.NOT_FOUND,
            f"Not found: {requirement.label} was {_NOT_FOUND_PHRASE} "
            f"(field(s): {', '.join(requirement.fields)}). This is a coverage "
            f"observation about the submitted package; it does not establish that the "
            f"activity was not performed.",
            list(requirement.fields),
        )

    document_ids = {str(record.get("document_id") or "") for record in records}
    fields_present = {str(record.get("field_name")) for record in records}
    missing_fields = [field for field in requirement.fields if field not in fields_present]
    checks = rules_module.evaluate_checks(rule, requirement, records)
    checks_ok = all(checks.values())
    if checks_ok and not missing_fields:
        return (
            CoverageStatus.SUFFICIENT,
            f"Sufficient: {len(records)} record(s) across {len(document_ids)} document(s) identified.",
            [],
        )
    problems: list[str] = []
    if missing_fields:
        problems.append(
            f"field(s) {_NOT_FOUND_PHRASE}: {', '.join(missing_fields)}"
        )
    if not checks.get("min_records", True):
        problems.append(
            f"{len(records)} record(s) found, minimum {requirement.min_records} required"
        )
    if not checks.get("min_documents", True):
        problems.append(
            f"{len(document_ids)} source document(s) found, minimum "
            f"{requirement.min_documents} required"
        )
    if not checks.get("acceptable_texts", True):
        problems.append("identified evidence does not satisfy the expected content check")
    note = "Partially covered: " + "; ".join(problems) + "."
    return CoverageStatus.PARTIAL, note, missing_fields


# ---------------------------------------------------------------------------
# Finding builders
# ---------------------------------------------------------------------------


def _gap_dedup_key(rule: Rule, requirement: Requirement) -> str:
    digest = hashlib.sha256(f"{rule.rule_id}|{requirement.id}".encode("utf-8")).hexdigest()[:32]
    return f"gap:{digest}"


def _gap_finding(
    rule: Rule,
    requirement: Requirement,
    status: CoverageStatus,
    records: Sequence[Mapping[str, Any]],
    missing_fields: Sequence[str],
    coverage_note: str,
) -> FindingDraft:
    documents = sorted(
        {
            str(record.get("filename") or record.get("document_id") or "")
            for record in records
        }
    )
    all_evidence_ids = [
        str(record.get("evidence_id")) for record in records if record.get("evidence_id")
    ]
    # Cap the linked evidence for readability; the full list is kept in detail.
    evidence_ids = all_evidence_ids[:6]
    fields = list(requirement.fields)
    required_documents = list(rule.required_documents) or ["Relevant source documentation"]
    if status == CoverageStatus.NOT_FOUND:
        title = f"Evidence gap: {requirement.label}"
        explanation = (
            f"The due-diligence checklist item '{requirement.label}' "
            f"({rule.dimension}) was {_NOT_FOUND_PHRASE}: no structured evidence "
            f"for field(s) {', '.join(fields)} was found in the active documents. "
            f"This is a statement about the submitted package only; it does not "
            f"establish that the underlying activity was not performed, and it may "
            f"reflect a document that was not included or a value not captured by the "
            f"current extractor."
        )
        missing_information = [
            f"Evidence for '{requirement.label}' (field(s): {', '.join(fields)}) "
            f"was {_NOT_FOUND_PHRASE}."
        ]
    else:
        title = f"Partial evidence: {requirement.label}"
        explanation = (
            f"Only partial evidence for the checklist item '{requirement.label}' "
            f"({rule.dimension}) was identified in the current package. "
            f"{coverage_note} The missing items are requested from the seller rather "
            f"than assumed to be absent from the underlying program."
        )
        missing_information = [
            f"Complete evidence for '{requirement.label}'; missing: "
            f"{', '.join(missing_fields) if missing_fields else ', '.join(fields)} "
            f"({_NOT_FOUND_PHRASE})."
        ]
    return FindingDraft(
        finding_type=FindingType.EVIDENCE_GAP,
        domain=rule.dimension,
        title=title,
        explanation=explanation,
        severity=Severity(_PRIORITY_TO_SEVERITY.get(rule.priority, str(Severity.MEDIUM))),
        evidence_ids=evidence_ids,
        possible_causes=[
            "The supporting document or analysis output was not included in the current package",
            "The value is reported in a format or location not captured by the current extractor",
            "The analysis was pending at the data cut-off reflected in the package",
        ],
        missing_information=missing_information,
        recommended_action=(
            "Request " + ", ".join(required_documents) + " and the underlying analysis output."
        ),
        rule_id=rule.rule_id,
        detail={
            "rule_id": rule.rule_id,
            "requirement_id": requirement.id,
            "requirement": requirement.label,
            "dimension": rule.dimension,
            "status": str(status),
            "fields": fields,
            "missing_fields": list(missing_fields),
            "record_count": len(records),
            "documents": documents,
            "evidence_all": all_evidence_ids,
            "evidence_total": len(all_evidence_ids),
            "source_documents": list(rule.required_documents),
            "priority": rule.priority,
            "owner": rule.owner,
            "pack_id": rule.pack_id,
            "pack_version": rule.pack_version,
            "rule_version": rule.version,
            "question_template": rule.question_template,
            "coverage_note": coverage_note,
        },
        dedup_key=_gap_dedup_key(rule, requirement),
    )


def _normalize_injection_flags(raw_flags: Any) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    if not isinstance(raw_flags, (list, tuple)):
        raw_flags = [raw_flags]
    for item in raw_flags:
        if isinstance(item, Mapping):
            flags.append(
                {
                    "page": item.get("page"),
                    "pattern": item.get("pattern") or item.get("flag"),
                    "excerpt": item.get("excerpt") or item.get("text"),
                }
            )
        else:
            flags.append({"page": None, "pattern": str(item), "excerpt": None})
    return flags


def _injection_findings(documents: Sequence[Mapping[str, Any]]) -> list[FindingDraft]:
    """One TECHNICAL_CONCERN per document family carrying injection flags."""
    by_family: dict[str, dict[str, Any]] = {}
    for document in documents:
        meta = document.get("meta") or {}
        raw_flags = meta.get("injection_flags")
        if not raw_flags:
            continue
        family = str(document.get("doc_family") or document.get("doc_type") or document["document_id"])
        entry = by_family.setdefault(
            family,
            {"family": family, "documents": [], "flags": [], "filenames": []},
        )
        entry["documents"].append(document["document_id"])
        entry["filenames"].append(document.get("filename") or document["document_id"])
        entry["flags"].extend(_normalize_injection_flags(raw_flags))

    drafts: list[FindingDraft] = []
    for family in sorted(by_family):
        entry = by_family[family]
        flags = entry["flags"]
        flagged_pages = sorted(
            {flag["page"] for flag in flags if isinstance(flag.get("page"), int)}
        )
        patterns = sorted({str(flag["pattern"]) for flag in flags if flag.get("pattern")})
        filename = ", ".join(sorted(set(entry["filenames"])))
        digest = hashlib.sha256(f"injection|{family}".encode("utf-8")).hexdigest()[:32]
        drafts.append(
            FindingDraft(
                finding_type=FindingType.TECHNICAL_CONCERN,
                domain="technical",
                title=f"Potential prompt-injection content in {filename}",
                explanation=(
                    f"Document '{filename}' contains text that appears to be addressed to "
                    f"automated reviewers rather than to human readers"
                    + (f" (pattern(s): {', '.join(patterns)})" if patterns else "")
                    + (f" on page(s) {', '.join(str(p) for p in flagged_pages)}" if flagged_pages else "")
                    + ". All package text is treated strictly as untrusted data: such "
                    "text is never executed as an instruction, and extraction/analysis "
                    "continue normally with every value verified against the cited page "
                    "text. The finding is logged so a human reviewer can assess document "
                    "integrity and provenance before relying on this document."
                ),
                severity=Severity.HIGH,
                evidence_ids=[],
                possible_causes=[
                    "The document was tampered with or contains content crafted to manipulate automated review",
                    "The text is benign boilerplate (e.g. a quotation or a document-control note) that matches an injection pattern",
                ],
                missing_information=[
                    "Confirmation that the flagged text is part of the original document and not a packaging artifact",
                    "Document provenance / native version of the flagged file",
                ],
                recommended_action=(
                    "Treat the document as untrusted data, review the flagged page(s) "
                    "manually, and ask the seller to confirm the document's provenance."
                ),
                detail={
                    "document_family": family,
                    "document_ids": sorted(set(entry["documents"])),
                    "documents": sorted(set(entry["filenames"])),
                    "flags": flags,
                    "pages": flagged_pages,
                    "patterns": patterns,
                    "handling": (
                        "untrusted-data: flagged text is never interpreted as an "
                        "instruction; analysis proceeds with page-verified evidence"
                    ),
                },
                dedup_key=f"tech:injection:{digest}",
            )
        )
    return drafts


def _incomplete_evidence_findings(
    evidence: Sequence[Mapping[str, Any]], document_map: Mapping[str, Mapping[str, Any]]
) -> list[FindingDraft]:
    """One INCOMPLETE_EVIDENCE finding per document family with failed verification."""
    by_family: dict[str, dict[str, Any]] = {}
    for record in evidence:
        status = str(record.get("verification_status") or "")
        if status not in {"failed", "unverifiable"}:
            continue
        document_id = str(record.get("document_id") or "")
        document = document_map.get(document_id, {})
        family = str(document.get("doc_family") or document.get("doc_type") or document_id)
        entry = by_family.setdefault(
            family,
            {
                "family": family,
                "failed": 0,
                "unverifiable": 0,
                "evidence_ids": [],
                "filenames": set(),
                "document_ids": set(),
            },
        )
        entry[status] += 1
        entry["evidence_ids"].append(str(record.get("evidence_id")))
        entry["filenames"].add(str(record.get("filename") or document_id))
        entry["document_ids"].add(document_id)

    drafts: list[FindingDraft] = []
    for family in sorted(by_family):
        entry = by_family[family]
        total = entry["failed"] + entry["unverifiable"]
        if total <= 0:  # pragma: no cover - defensive
            continue
        severity = Severity.HIGH if entry["failed"] else Severity.MEDIUM
        filenames = ", ".join(sorted(entry["filenames"]))
        digest = hashlib.sha256(f"verification|{family}".encode("utf-8")).hexdigest()[:32]
        drafts.append(
            FindingDraft(
                finding_type=FindingType.INCOMPLETE_EVIDENCE,
                domain="data_quality",
                title=f"Evidence could not be fully verified in {filenames}",
                explanation=(
                    f"{total} evidence item(s) extracted from '{filenames}' could not be "
                    f"verified against the cited page text ({entry['failed']} failed "
                    f"quote/value checks, {entry['unverifiable']} unverifiable). Values "
                    f"from this document should be treated cautiously in the findings and "
                    f"questions until the source table is confirmed; a searchable or "
                    f"native version of the affected table/section is requested."
                ),
                severity=severity,
                evidence_ids=entry["evidence_ids"][:100],
                possible_causes=[
                    "The source table is an image or otherwise not text-searchable",
                    "The extracted quote spans a table cell boundary and does not match the page text verbatim",
                    "The cited value or page number was captured incorrectly by the extractor",
                ],
                missing_information=[
                    "A text-searchable or native version of the affected table/section"
                ],
                recommended_action=(
                    "Ask the seller for a searchable version of the affected tables and "
                    "re-verify the affected evidence items against the source."
                ),
                detail={
                    "document_family": family,
                    "document_ids": sorted(entry["document_ids"]),
                    "documents": sorted(entry["filenames"]),
                    "verification_failed": entry["failed"],
                    "verification_unverifiable": entry["unverifiable"],
                    "total": total,
                    "evidence_ids": entry["evidence_ids"][:100],
                },
                dedup_key=f"tech:verification:{digest}",
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# Stale-finding resolution
# ---------------------------------------------------------------------------


def _resolve_stale(
    session,
    project_id: str,
    current_keys: set[str],
    *,
    resolve_gaps: bool,
    resolve_injection: bool,
    resolve_verification: bool,
) -> int:
    resolved = 0
    for finding in store.list_findings(session, project_id, status="open"):
        key = str(finding.get("dedup_key") or "")
        if not key or key in current_keys:
            continue
        if key.startswith("gap:") and resolve_gaps:
            pass
        elif key.startswith("tech:injection:") and resolve_injection:
            pass
        elif key.startswith("tech:verification:") and resolve_verification:
            pass
        else:
            continue
        updated = store.update_finding(
            session,
            finding["finding_id"],
            actor="system:gap",
            status="resolved",
        )
        if updated is not None:
            resolved += 1
    return resolved


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(session, project_id: str, force: bool = False) -> dict:
    """Compute coverage against the applicable rule pack and persist findings."""
    context = build_context(session, project_id)
    all_documents = store.list_documents(session, project_id, latest_only=True)
    document_map = {document["document_id"]: document for document in all_documents}
    active_ids = set(document_map)
    active_evidence = [
        record
        for record in store.get_evidence(session, project_id)
        if record.get("document_id") in active_ids
    ]
    evidence_by_field: dict[str, list[dict]] = defaultdict(list)
    for record in active_evidence:
        evidence_by_field[str(record.get("field_name"))].append(record)
    project_has_evidence = bool(active_evidence)

    loaded_rules = rules_module.load_rules()
    applicable_rules = rules_module.select_rules(loaded_rules, context)

    coverage_drafts: list[CoverageDraft] = []
    gap_drafts: list[FindingDraft] = []
    counts = {
        str(CoverageStatus.SUFFICIENT): 0,
        str(CoverageStatus.PARTIAL): 0,
        str(CoverageStatus.NOT_FOUND): 0,
        str(CoverageStatus.NOT_APPLICABLE): 0,
        str(CoverageStatus.NOT_REVIEWED): 0,
    }

    for rule in applicable_rules:
        for requirement in rule.required_evidence:
            raw_records: list[Mapping[str, Any]] = []
            for field in requirement.fields:
                raw_records.extend(evidence_by_field.get(field, []))
            records = _filter_acceptable(requirement, raw_records)
            status, note, missing_fields = _evaluate_requirement(
                rule,
                requirement,
                records,
                raw_records,
                project_has_evidence=project_has_evidence,
            )
            counts[str(status)] = counts.get(str(status), 0) + 1
            coverage_drafts.append(
                CoverageDraft(
                    rule_id=rule.rule_id,
                    dimension=rule.dimension,
                    requirement=requirement.label,
                    status=status,
                    evidence_ids=[str(record.get("evidence_id")) for record in records][:200],
                    note=note,
                    priority=Priority(rule.priority),
                )
            )
            if (
                status in {CoverageStatus.PARTIAL, CoverageStatus.NOT_FOUND}
                and rule.priority in _GAP_FINDING_PRIORITIES
            ):
                gap_drafts.append(
                    _gap_finding(rule, requirement, status, records, missing_fields, note)
                )

    not_applicable_count = 0
    for rule in loaded_rules:
        if rules_module.applicability(rule, context) != "not_applicable":
            continue
        reason = _not_applicable_reason(rule, context)
        for requirement in rule.required_evidence:
            not_applicable_count += 1
            counts[str(CoverageStatus.NOT_APPLICABLE)] += 1
            coverage_drafts.append(
                CoverageDraft(
                    rule_id=rule.rule_id,
                    dimension=rule.dimension,
                    requirement=requirement.label,
                    status=CoverageStatus.NOT_APPLICABLE,
                    evidence_ids=[],
                    note=f"Not applicable: {reason}.",
                    priority=Priority.LOW,
                )
            )

    store.replace_coverage(session, project_id, coverage_drafts)

    technical_drafts = _injection_findings(all_documents)
    incomplete_drafts = _incomplete_evidence_findings(active_evidence, document_map)
    saved = store.save_findings(
        session, project_id, [*gap_drafts, *technical_drafts, *incomplete_drafts]
    )

    current_keys = {
        draft.dedup_key for draft in [*gap_drafts, *technical_drafts, *incomplete_drafts] if draft.dedup_key
    }
    resolved = _resolve_stale(
        session,
        project_id,
        current_keys,
        resolve_gaps=bool(applicable_rules) and bool(all_documents),
        resolve_injection=bool(all_documents),
        resolve_verification=project_has_evidence,
    )

    return {
        "coverage_items": len(coverage_drafts),
        "gaps": counts[str(CoverageStatus.PARTIAL)] + counts[str(CoverageStatus.NOT_FOUND)],
        "findings_created": saved["created"],
        "findings_updated": saved["updated"],
        "findings_resolved": resolved,
        "coverage_sufficient": counts[str(CoverageStatus.SUFFICIENT)],
        "coverage_partial": counts[str(CoverageStatus.PARTIAL)],
        "coverage_not_found": counts[str(CoverageStatus.NOT_FOUND)],
        "coverage_not_applicable": counts[str(CoverageStatus.NOT_APPLICABLE)],
        "coverage_not_reviewed": counts[str(CoverageStatus.NOT_REVIEWED)],
        "rules_applicable": len(applicable_rules),
        "rules_total": len(loaded_rules),
        "technical_concerns": len(technical_drafts),
        "incomplete_evidence_families": len(incomplete_drafts),
        "documents_reviewed": len(all_documents),
        "evidence_considered": len(active_evidence),
        "project_context": context,
    }
