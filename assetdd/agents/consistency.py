"""Evidence consistency agent (analysis-dev).

Compares structured evidence records across *active* documents of a project and
classifies each discrepancy by its comparability vector::

    (trial_key, arm, population, data_cutoff, endpoint_def, assessment_method)

Classification policy (deliberately conservative — an incomparable pair must
never be reported as a contradiction):

``skip``
    Trial or arm are both stated and differ: these are different studies /
    cohorts, so the numbers are legitimately different and no finding is raised.
``TYPE C  definition_difference``
    Analysis population, endpoint definition or assessment method are both
    stated and differ.  The values are not measuring the same quantity.  Same
    document pairs are allowed here (e.g. a primary ITT analysis versus a FAS /
    BICR sensitivity analysis reported in the same CSR) because the definition
    difference is exactly what the reader needs to see.
``TYPE B  version_difference``
    All compared dimensions other than ``data_cutoff`` are compatible, and the
    data cut-offs are both stated and differ.  This is an updated-data /
    version difference, not a contradiction.
``TYPE U  comparability_unclear``
    At least one comparability dimension is stated in one record but not the
    other (or otherwise cannot be aligned).  The pair is never labelled Type A;
    the finding asks for the missing metadata (low severity).
``TYPE A  data_inconsistency``
    Every comparability dimension is equal (``None == None`` counts as equal),
    both records come from **different** documents, and the values differ beyond
    tolerance.  Same-document repetitions are suppressed (a document repeating
    its own value is not a sell-side inconsistency).

Numeric tolerance: relative 2% (``TOLERANCE_RELATIVE``) of the larger absolute
value for percentages / medians / ratios; exact match for patient counts
(``sample_size``, ``n_itt``, ``n_safety``, ``n_fas``, ``n_pp``).  Text fields
(``primary_endpoint``) use whitespace-normalised equality.

Findings are deterministic and deduplicated per (field, evidence group):
``dedup_key = hash(field, sorted evidence ids)``, so re-running on an unchanged
package updates the same rows instead of duplicating them.  Superseded
documents (``store.list_documents(..., latest_only=True)``) are excluded, so a
round-2 upload resolves stale version findings instead of re-surfacing them.

The entry point is :func:`run(session, project_id, force=False) -> stats`.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

from assetdd import store
from assetdd.schemas import (
    COUNT_FIELDS,
    FindingDraft,
    FindingType,
    Severity,
    normalize_date,
    normalize_method,
    normalize_population,
    normalize_ws,
    normalize_trial,
)

# Relative tolerance for percentage / median / ratio fields (2%).
TOLERANCE_RELATIVE = 0.02
TOLERANCE_PCT = TOLERANCE_RELATIVE * 100.0
# Absolute floor used only when both values are zero (cannot divide).
_ZERO_EPS = 1e-12

DIMENSIONS: tuple[str, ...] = (
    "trial_key",
    "arm",
    "population",
    "data_cutoff",
    "endpoint_def",
    "assessment_method",
)
ANCHOR_DIMENSIONS: tuple[str, ...] = ("trial_key", "arm")
DEFINITION_DIMENSIONS: tuple[str, ...] = ("population", "endpoint_def", "assessment_method")
CUTOFF_DIMENSION = "data_cutoff"

CHECK_FIELDS: tuple[str, ...] = (
    "orr",
    "dcr",
    "pfs",
    "os",
    "hr",
    "sample_size",
    "n_itt",
    "n_safety",
    "n_fas",
    "n_pp",
    "grade3_ae",
    "sae",
    "treatment_related_death",
    "primary_endpoint",
    "data_cutoff",
)

TEXT_FIELDS: frozenset[str] = frozenset({"primary_endpoint"})
EFFICACY_FIELDS: frozenset[str] = frozenset({"orr", "dcr", "pfs", "os", "hr"})
SIZE_FIELDS: frozenset[str] = frozenset(
    {"sample_size", "n_itt", "n_safety", "n_fas", "n_pp"}
)
SAFETY_FIELDS: frozenset[str] = frozenset(
    {"grade3_ae", "sae", "treatment_related_death"}
)

_COUNT_FIELD_VALUES: frozenset[str] = frozenset(str(f) for f in COUNT_FIELDS)

FIELD_LABELS: dict[str, str] = {
    "orr": "objective response rate (ORR)",
    "dcr": "disease control rate (DCR)",
    "pfs": "median progression-free survival (PFS)",
    "os": "median overall survival (OS)",
    "hr": "hazard ratio (HR)",
    "sample_size": "sample size",
    "n_itt": "ITT population size",
    "n_safety": "safety population size",
    "n_fas": "FAS population size",
    "n_pp": "per-protocol population size",
    "grade3_ae": "grade >=3 adverse event rate",
    "sae": "serious adverse event rate",
    "treatment_related_death": "treatment-related death rate",
    "primary_endpoint": "primary endpoint definition",
    "data_cutoff": "data cut-off",
}

FIELD_DOMAINS: dict[str, str] = {
    "orr": "efficacy",
    "dcr": "efficacy",
    "pfs": "efficacy",
    "os": "efficacy",
    "hr": "efficacy",
    "sample_size": "population",
    "n_itt": "population",
    "n_safety": "population",
    "n_fas": "population",
    "n_pp": "population",
    "grade3_ae": "safety",
    "sae": "safety",
    "treatment_related_death": "safety",
    "primary_endpoint": "endpoints",
    "data_cutoff": "data_maturity",
}

DIMENSION_LABELS: dict[str, str] = {
    "trial_key": "trial",
    "arm": "treatment arm",
    "population": "analysis population",
    "data_cutoff": "data cut-off",
    "endpoint_def": "endpoint definition",
    "assessment_method": "assessment method",
}


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _dim_value(evidence: Mapping[str, Any], dimension: str) -> str | None:
    """Normalise one comparability dimension of a persisted evidence record."""
    raw = evidence.get(dimension)
    if raw is None:
        return None
    text = normalize_ws(str(raw))
    if not text:
        return None
    if dimension == "trial_key":
        return normalize_trial(text) or text.upper()
    if dimension == "arm":
        return text.lower()
    if dimension == "population":
        return normalize_population(text)
    if dimension == "data_cutoff":
        return normalize_date(text) or text.lower()
    if dimension == "assessment_method":
        return normalize_method(text) or text.lower()
    return text.lower()


def _dims(evidence: Mapping[str, Any]) -> dict[str, str | None]:
    return {dimension: _dim_value(evidence, dimension) for dimension in DIMENSIONS}


def _sort_key(evidence: Mapping[str, Any]) -> tuple:
    return (
        str(evidence.get("document_id") or ""),
        int(evidence.get("page") or 0),
        str(evidence.get("evidence_id") or ""),
    )


def _numeric_value(evidence: Mapping[str, Any]) -> float | None:
    value = evidence.get("value_num")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _display_value(field: str, evidence: Mapping[str, Any]) -> Any:
    value = _numeric_value(evidence)
    if value is not None:
        return value
    return evidence.get("value_text")


def _normalized_text(field: str, evidence: Mapping[str, Any]) -> str:
    text = evidence.get("value_text")
    if not text:
        text = evidence.get("quote") or ""
    normalized = normalize_ws(str(text)).lower()
    if field == "data_cutoff":
        return normalize_date(str(text)) or normalized
    return normalized


def _values_differ(field: str, left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """True when the two evidence values disagree beyond the field tolerance."""
    if field in TEXT_FIELDS or field == "data_cutoff":
        return _normalized_text(field, left) != _normalized_text(field, right)
    left_num = _numeric_value(left)
    right_num = _numeric_value(right)
    if left_num is None or right_num is None:
        return _normalized_text(field, left) != _normalized_text(field, right)
    if field in _COUNT_FIELD_VALUES:
        return abs(left_num - right_num) > 1e-9
    denominator = max(abs(left_num), abs(right_num))
    if denominator <= _ZERO_EPS:
        return False
    return (abs(left_num - right_num) / denominator) > TOLERANCE_RELATIVE + 1e-12


def _relative_delta(left: float, right: float) -> float:
    denominator = max(abs(left), abs(right))
    if denominator <= _ZERO_EPS:
        return 0.0
    return abs(left - right) / denominator


def _fmt_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return f"{value:.0f}"
    return str(value)


def _fmt_value(field: str, evidence: Mapping[str, Any]) -> str:
    displayed = _display_value(field, evidence)
    if displayed is None:
        return "value not captured"
    if isinstance(displayed, (int, float)):
        unit = str(evidence.get("unit") or "")
        text = _fmt_number(float(displayed))
        if unit == "%":
            return f"{text}%"
        if unit:
            return f"{text} {unit}"
        return text
    return str(displayed)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_pair(
    field: str, left: Mapping[str, Any], right: Mapping[str, Any]
) -> str:
    """Classify a cross-record pair as ``skip`` / ``A`` / ``B`` / ``C`` / ``U``.

    The function intentionally does *not* look at the values: callers only call
    it for pairs whose values disagree, and Type A is additionally suppressed
    for same-document pairs by the caller.
    """
    left_dims = _dims(left)
    right_dims = _dims(right)

    for dimension in ANCHOR_DIMENSIONS:
        left_value = left_dims[dimension]
        right_value = right_dims[dimension]
        if left_value is not None and right_value is not None and left_value != right_value:
            return "skip"  # different study / cohort: legitimately different
        if (left_value is None) != (right_value is None):
            return "U"  # cohort cannot be anchored

    definition_differs = any(
        left_dims[dimension] is not None
        and right_dims[dimension] is not None
        and left_dims[dimension] != right_dims[dimension]
        for dimension in DEFINITION_DIMENSIONS
    )
    left_cutoff = left_dims[CUTOFF_DIMENSION]
    right_cutoff = right_dims[CUTOFF_DIMENSION]
    cutoff_differs = (
        left_cutoff is not None and right_cutoff is not None and left_cutoff != right_cutoff
    )

    if definition_differs and cutoff_differs:
        # Two confounded explanations (definition + data snapshot): request metadata
        # rather than attributing the difference to either one.
        return "U"
    if definition_differs:
        return "C"
    if cutoff_differs:
        return "B"

    for dimension in DIMENSIONS:
        if (left_dims[dimension] is None) != (right_dims[dimension] is None):
            return "U"

    return "A"


def _missing_signature(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[str, ...]:
    left_dims = _dims(left)
    right_dims = _dims(right)
    return tuple(
        dimension
        for dimension in DIMENSIONS
        if (left_dims[dimension] is None) != (right_dims[dimension] is None)
    )


def _definition_signature(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> tuple[str, ...]:
    left_dims = _dims(left)
    right_dims = _dims(right)
    return tuple(
        dimension
        for dimension in DEFINITION_DIMENSIONS
        if left_dims[dimension] is not None
        and right_dims[dimension] is not None
        and left_dims[dimension] != right_dims[dimension]
    )


# ---------------------------------------------------------------------------
# Dedup keys and detail payloads
# ---------------------------------------------------------------------------


def dedup_key(field: str, comparison_class: str, evidence_ids: Iterable[str]) -> str:
    payload = "|".join([field, comparison_class, *sorted(str(e) for e in evidence_ids)])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"consistency:{comparison_class}:{digest}"


def _values_detail(field: str, evidence: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per (document, value) pair; repeated extraction rows are collapsed.

    The same value can be emitted several times from one document (body, table,
    appendix).  The UI/report needs the distinct comparisons, while every raw
    evidence row stays linked through the finding's ``evidence_ids``.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record in evidence:
        document_id = str(record.get("document_id") or "")
        display = _display_value(field, record)
        key = (document_id, str(display))
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {
                "evidence_id": record.get("evidence_id"),
                "evidence_ids": [record.get("evidence_id")],
                "value": display,
                "value_label": _fmt_value(field, record),
                "document": record.get("filename") or document_id,
                "document_id": document_id,
                "page": record.get("page"),
                "pages": [record.get("page")],
                "cutoff": record.get("data_cutoff"),
                "population": record.get("population"),
                "method": record.get("assessment_method"),
                "trial_key": record.get("trial_key"),
                "arm": record.get("arm"),
                "endpoint_def": record.get("endpoint_def"),
                "unit": record.get("unit"),
            }
        else:
            if record.get("evidence_id") not in entry["evidence_ids"]:
                entry["evidence_ids"].append(record.get("evidence_id"))
            if record.get("page") not in entry["pages"]:
                entry["pages"].append(record.get("page"))
    return [grouped[key] for key in grouped]


def _dims_detail(field: str, evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    for dimension in DIMENSIONS:
        values = [_dim_value(record, dimension) for record in evidence]
        distinct = sorted({value for value in values if value is not None})
        if len(distinct) <= 1 and all(value == values[0] for value in values):
            detail[dimension] = values[0] if values else None
        else:
            detail[dimension] = distinct
    return detail


def _delta_detail(field: str, evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if field in TEXT_FIELDS:
        texts = sorted({_normalized_text(field, record) for record in evidence})
        return {"texts": texts}
    if field == "data_cutoff":
        cutoffs = sorted({_normalized_text(field, record) for record in evidence})
        dates = [normalize_date(_normalized_text(field, record)) for record in evidence]
        parsed = [value for value in dates if value]
        detail: dict[str, Any] = {"cutoffs": cutoffs}
        if len(parsed) >= 2:
            ordinals = [_date_ordinal(value) for value in parsed]
            detail["max_days_apart"] = max(ordinals) - min(ordinals)
        return detail
    numbers = [value for value in (_numeric_value(record) for record in evidence) if value is not None]
    if len(numbers) < 2:
        return {"absolute": 0.0, "relative_pct": 0.0, "tolerance_pct": TOLERANCE_PCT}
    absolute = max(numbers) - min(numbers)
    relative = 0.0
    for left, right in combinations(numbers, 2):
        relative = max(relative, _relative_delta(left, right))
    return {
        "absolute": round(absolute, 6),
        "relative_pct": round(relative * 100.0, 4),
        "tolerance_pct": TOLERANCE_PCT,
        "unit": evidence[0].get("unit"),
    }


def _date_ordinal(iso_date: str) -> int:
    from datetime import date

    parsed = date.fromisoformat(iso_date)
    return parsed.toordinal()


def _documents_detail(evidence: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for record in evidence:
        document_id = str(record.get("document_id") or "")
        if document_id in seen:
            continue
        seen[document_id] = {
            "document_id": document_id,
            "filename": record.get("filename") or document_id,
            "document_type": record.get("document_type"),
        }
    return [seen[key] for key in sorted(seen)]


# ---------------------------------------------------------------------------
# Finding construction
# ---------------------------------------------------------------------------


def _severity_for_a(field: str) -> str:
    if field in EFFICACY_FIELDS or field in SIZE_FIELDS:
        return str(Severity.CRITICAL)
    if field in SAFETY_FIELDS or field == "primary_endpoint":
        return str(Severity.HIGH)
    return str(Severity.HIGH)


def _severity_for(comparison_class: str, field: str) -> str:
    if comparison_class == "A":
        return _severity_for_a(field)
    if comparison_class == "B":
        return str(Severity.LOW)  # version difference: informational
    if comparison_class == "C":
        return str(Severity.MEDIUM)
    return str(Severity.LOW)


def _dim_summary(detail: dict[str, Any]) -> str:
    parts: list[str] = []
    for dimension in DIMENSIONS:
        value = detail.get(dimension)
        if value is None:
            continue
        if isinstance(value, list):
            parts.append(f"{DIMENSION_LABELS[dimension]}={', '.join(value)}")
        else:
            parts.append(f"{DIMENSION_LABELS[dimension]}={value}")
    return "; ".join(parts) if parts else "no comparability metadata stated"


def _build_finding(
    field: str,
    comparison_class: str,
    evidence: Sequence[Mapping[str, Any]],
    *,
    signature: tuple[str, ...] = (),
) -> FindingDraft:
    label = FIELD_LABELS.get(field, field)
    values = sorted(evidence, key=_sort_key)
    value_labels = "; ".join(
        f"{_fmt_value(field, record)} in {record.get('filename') or record.get('document_id')} "
        f"(page {record.get('page')})"
        for record in values
    )
    dims = {dimension: _dim_value(values[0], dimension) for dimension in DIMENSIONS} if values else {}
    dims_detail = _dims_detail(field, values)
    delta = _delta_detail(field, values)
    detail = {
        "field": field,
        "field_label": FIELD_LABELS.get(field, field),
        "class": comparison_class,
        "values": _values_detail(field, values),
        "dims": dims_detail,
        "delta": delta,
        "documents": _documents_detail(values),
        "tolerance": {
            "relative_pct": TOLERANCE_PCT if comparison_class == "A" else None,
            "mode": "exact" if field in _COUNT_FIELD_VALUES else "relative",
        },
        "missing_dimensions": list(signature) if comparison_class == "U" else [],
        "definition_dimensions": list(signature) if comparison_class == "C" else [],
    }
    evidence_ids = [str(record.get("evidence_id")) for record in values]

    if comparison_class == "A":
        title = f"Data inconsistency: {label} differs across documents"
        explanation = (
            f"{label[:1].upper()}{label[1:]} is reported inconsistently across sources that share "
            f"the same comparability dimensions. Values: {value_labels}. The difference exceeds "
            f"the {TOLERANCE_PCT:.1f}% relative tolerance (counts must match exactly), and both "
            f"records come from different documents with the same trial/arm, analysis population, "
            f"data cut-off, endpoint definition and assessment method."
        )
        possible_causes = [
            "Reporting or transcription error in one of the documents",
            "One source was not updated after a re-analysis or data refresh",
            "An unstated population or analysis subset is used under the same label",
        ]
        missing_information = [
            "Which value represents the final analysis, and the source table / analysis output supporting it",
            "Confirmation of the analysis population, data cut-off and assessment method for each value",
        ]
        action = (
            "Ask the seller to reconcile the values, confirm the authoritative analysis and "
            "provide the supporting output."
        )
    elif comparison_class == "B":
        if field == "data_cutoff":
            title = "Version difference: data cut-off differs across documents"
            explanation = (
                f"The data cut-off stated in different documents differs. Values: {value_labels}. "
                f"This is a document-version / data-snapshot difference rather than a contradiction; "
                f"the authoritative cut-off for the current analysis should be confirmed."
            )
        else:
            title = f"Version difference: {label} differs by data cut-off"
            explanation = (
                f"{label[:1].upper()}{label[1:]} differs between sources that use different data "
                f"cut-offs. Values: {value_labels}. The cut-off difference is a plausible explanation "
                f"for the changed value, so this is classified as a version difference rather than a "
                f"data contradiction; the authoritative data cut-off should still be confirmed."
            )
        possible_causes = [
            "Data cut-off differs between the documents (earlier snapshot vs current analysis)",
            "Updated efficacy/safety data not yet reflected in all documents",
        ]
        missing_information = [
            "The authoritative data cut-off date for the asset's current analysis",
            "Updated tables reflecting the most recent cut-off",
        ]
        action = "Ask the seller to confirm the current data cut-off and provide the updated analysis."
    elif comparison_class == "C":
        title = f"Definition difference: {label} differs by analysis definition"
        dimension_labels = ", ".join(
            DIMENSION_LABELS.get(dimension, dimension) for dimension in signature
        ) or "analysis definition"
        explanation = (
            f"{label[:1].upper()}{label[1:]} differs between records whose {dimension_labels} "
            f"differ. Values: {value_labels}. Because the records do not measure the same quantity, "
            f"the difference is attributable to the stated definitional difference and is not "
            f"treated as a contradiction; the comparison should be made on a like-for-like basis."
        )
        possible_causes = [
            "Primary versus sensitivity analysis using a different population or assessor",
            "Different endpoint definition or response criteria between documents",
        ]
        missing_information = [
            "Which analysis population / assessment method / endpoint definition applies to each value",
            "A like-for-like comparison of both analyses",
        ]
        action = (
            "Ask the seller to clarify the definitions and provide a like-for-like analysis "
            "before comparing the values."
        )
    else:
        title = f"Comparability unclear: {label} reported with missing metadata"
        missing_labels = ", ".join(
            DIMENSION_LABELS.get(dimension, dimension) for dimension in signature
        ) or "one or more comparability dimensions"
        explanation = (
            f"{label[:1].upper()}{label[1:]} differs across sources, but {missing_labels} is not "
            f"stated in at least one of them, so comparability cannot be established. Values: "
            f"{value_labels}. The records are not labelled as contradictory; the missing analysis "
            f"metadata is requested before any comparison is made."
        )
        possible_causes = [
            "One source omits the analysis population, data cut-off or assessment method",
            "The value is reported at a different level of granularity (study vs arm vs subgroup)",
        ]
        missing_information = [
            f"Missing comparability metadata: {missing_labels}",
            "The source table supporting each reported value",
        ]
        action = (
            "Ask the seller to confirm the analysis metadata for each value so the records can "
            "be compared."
        )

    return FindingDraft(
        finding_type={
            "A": FindingType.DATA_INCONSISTENCY,
            "B": FindingType.VERSION_DIFFERENCE,
            "C": FindingType.DEFINITION_DIFFERENCE,
        }.get(comparison_class, FindingType.COMPARABILITY_UNCLEAR),
        domain=FIELD_DOMAINS.get(field, "clinical"),
        title=title,
        explanation=explanation,
        severity=Severity(_severity_for(comparison_class, field)),
        evidence_ids=evidence_ids,
        possible_causes=possible_causes,
        missing_information=missing_information,
        recommended_action=action,
        detail=detail,
        dedup_key=dedup_key(field, comparison_class, evidence_ids),
    )


# ---------------------------------------------------------------------------
# Pair collection / grouping
# ---------------------------------------------------------------------------


def _collect_pairs(
    field: str, evidence: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Classify every cross-record pair whose values disagree."""
    pairs: list[dict[str, Any]] = []
    for left, right in combinations(evidence, 2):
        if not _values_differ(field, left, right):
            continue
        comparison_class = classify_pair(field, left, right)
        if comparison_class == "skip":
            continue
        if comparison_class == "A" and left.get("document_id") == right.get("document_id"):
            # A document repeating its own value is not a sell-side inconsistency.
            continue
        pairs.append(
            {
                "left": left,
                "right": right,
                "class": comparison_class,
                "signature": (
                    _missing_signature(left, right)
                    if comparison_class == "U"
                    else _definition_signature(left, right)
                    if comparison_class == "C"
                    else ()
                ),
            }
        )
    return pairs


def _group_pairs(
    field: str, pairs: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Aggregate pairs into one finding per (field, comparison) group."""
    groups: dict[tuple, dict[str, Any]] = {}
    for pair in pairs:
        left = pair["left"]
        right = pair["right"]
        comparison_class = pair["class"]
        left_dims = _dims(left)
        signature = pair["signature"]
        if comparison_class == "A":
            key = (field, "A", *(left_dims[dimension] for dimension in DIMENSIONS))
        elif comparison_class == "B":
            key = (
                field,
                "B",
                *(left_dims[dimension] for dimension in DIMENSIONS if dimension != CUTOFF_DIMENSION),
            )
        elif comparison_class == "C":
            key = (field, "C", left_dims["trial_key"], left_dims["arm"], signature)
        else:
            # A clarification request is per field: collapse the (usually many)
            # pairwise missing-dimension combinations into one low-severity finding.
            key = (field, "U")
        group = groups.get(key)
        if group is None:
            group = {
                "class": comparison_class,
                "signature": set(signature),
                "evidence": {},
            }
            groups[key] = group
        else:
            group["signature"].update(signature)
        group["evidence"][str(left.get("evidence_id"))] = left
        group["evidence"][str(right.get("evidence_id"))] = right
    return list(groups.values())


def _group_cutoff_field(
    evidence: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Data cut-off statements are compared per trial/arm cohort as version differences."""
    buckets: dict[tuple, list[Mapping[str, Any]]] = defaultdict(list)
    for record in evidence:
        buckets[(_dim_value(record, "trial_key"), _dim_value(record, "arm"))].append(record)
    groups: list[dict[str, Any]] = []
    for bucket in buckets.values():
        distinct = {_normalized_text("data_cutoff", record) for record in bucket}
        if len(distinct) >= 2:
            groups.append(
                {
                    "class": "B",
                    "signature": (),
                    "evidence": {
                        str(record.get("evidence_id")): record for record in bucket
                    },
                }
            )
    return groups


def _resolve_stale_findings(
    session, project_id: str, current_keys: set[str]
) -> int:
    """Mark auto-generated consistency findings that are no longer reproduced as resolved.

    Only findings created by this agent (``consistency:`` dedup prefix, open
    status) are touched; human-dismissed/confirmed findings are left alone.  This
    is what makes a round-2 upload close resolved discrepancies instead of
    leaving them open forever.
    """
    resolved = 0
    managed_types = {
        str(FindingType.DATA_INCONSISTENCY),
        str(FindingType.VERSION_DIFFERENCE),
        str(FindingType.DEFINITION_DIFFERENCE),
        str(FindingType.COMPARABILITY_UNCLEAR),
    }
    for finding in store.list_findings(session, project_id, status="open"):
        if str(finding.get("finding_type")) not in managed_types:
            continue
        key = str(finding.get("dedup_key") or "")
        if not key.startswith("consistency:"):
            continue
        if key in current_keys:
            continue
        updated = store.update_finding(
            session,
            finding["finding_id"],
            actor="system:consistency",
            status="resolved",
        )
        if updated is not None:
            resolved += 1
    return resolved


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(session, project_id: str, force: bool = False) -> dict:
    """Compare evidence for *project_id* and persist consistency findings.

    ``force`` is accepted for the pipeline stage-handler contract; findings are
    upserted by stable dedup key, so a forced re-run recomputes the same rows and
    additionally resolves stale prior findings (see module docstring).
    """
    active_documents = store.list_documents(session, project_id, latest_only=True)
    active_ids = {document["document_id"] for document in active_documents}
    all_evidence = store.get_evidence(session, project_id)
    evidence = [
        record
        for record in all_evidence
        if record.get("document_id") in active_ids
        and str(record.get("field_name")) in CHECK_FIELDS
        and str(record.get("verification_status")) != "failed"
    ]
    by_field: dict[str, list[dict]] = defaultdict(list)
    for record in evidence:
        by_field[str(record["field_name"])].append(record)
    for records in by_field.values():
        records.sort(key=_sort_key)

    drafts: list[FindingDraft] = []
    comparisons = 0
    for field in CHECK_FIELDS:
        records = by_field.get(field, [])
        if len(records) < 2:
            continue
        if field == "data_cutoff":
            groups = _group_cutoff_field(records)
            comparisons += len(groups)
        else:
            pairs = _collect_pairs(field, records)
            comparisons += len(pairs)
            groups = _group_pairs(field, pairs)
        for group in groups:
            group_evidence = [group["evidence"][key] for key in sorted(group["evidence"])]
            signature = tuple(sorted(group["signature"]))
            drafts.append(
                _build_finding(
                    field,
                    group["class"],
                    group_evidence,
                    signature=signature,
                )
            )

    saved = store.save_findings(session, project_id, drafts)
    current_keys = {draft.dedup_key for draft in drafts if draft.dedup_key}
    resolved = 0
    if evidence:
        resolved = _resolve_stale_findings(session, project_id, current_keys)

    return {
        "findings_created": saved["created"],
        "findings_updated": saved["updated"],
        "findings_resolved": resolved,
        "comparisons": comparisons,
        "evidence_compared": len(evidence),
        "fields_compared": len(by_field),
        "documents_considered": len(active_ids),
    }
