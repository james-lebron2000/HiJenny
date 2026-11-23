"""Evidence consistency agent (analysis-dev).

Compares structured evidence records across *active* documents of a project and
classifies each discrepancy by its comparability vector::

    (trial_key, arm, population, data_cutoff, endpoint_def, assessment_method)

Partition policy (per field, per ``(trial_key, arm)`` cloud)
------------------------------------------------------------
1. The **largest exact-dimension group** is the *reference* analysis (ties are
   broken in favour of the latest data cut-off, then a deterministic signature
   order).
2. Reference records that state the same dimensions but a different value from a
   *different document* form a **Type A data_inconsistency** finding (a document
   repeating its own value is not a sell-side inconsistency).
3. Records differing **only** in ``data_cutoff`` form a **Type B
   version_difference** finding (informational, not a contradiction).
4. Records differing in population / endpoint definition / assessment method
   form a **Type C definition_difference** finding.  If the cut-off differs as
   well the definition difference dominates (priority ``A > C > B > U``).
5. Records missing a field's *required* dimensions (or groups whose difference
   cannot be attributed) form a **Type U comparability_unclear** finding.  A U
   finding is only emitted when it actually has missing dimensions; a pair that
   merely differs on two stated dimensions is a C/B difference, never U.

Each record appears in **at most one** of A/B/C/U.  Type B/C findings list only
the deviating records; the reference analysis is carried separately in
``detail["reference_values"]`` so a reader still sees both sides of the
comparison without the same record being asserted as contradictory twice.

Definition-only fields (``primary_endpoint`` and friends) are not measured
values: they are canonicalised to endpoint concepts (ORR / PFS / OS / ...) and
only emit a Type C finding when the documents genuinely name different endpoints
(e.g. ORR vs PFS).  They never enter Type A.

Numeric tolerance: relative 2% (``TOLERANCE_RELATIVE``) of the larger absolute
value for percentages / medians / ratios; exact match for patient counts.
Findings are deterministic and deduplicated per (field, evidence group), and
``evidence_ids`` is capped for readability with the full list kept in
``detail["evidence_all"]``.  Superseded documents are excluded, so a round-2
upload resolves stale findings instead of re-surfacing them.

Entry point: :func:`run(session, project_id, force=False) -> stats`.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from itertools import combinations
from typing import Any, Mapping, Sequence

from hijenny import store
from hijenny.schemas import (
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
_ZERO_EPS = 1e-12

DIMENSIONS: tuple[str, ...] = (
    "trial_key",
    "arm",
    "population",
    "data_cutoff",
    "endpoint_def",
    "assessment_method",
)
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
DEFINITION_ONLY_FIELDS: frozenset[str] = frozenset({"primary_endpoint"})

_COUNT_FIELD_VALUES: frozenset[str] = frozenset(str(f) for f in COUNT_FIELDS)

# Dimensions that must be stated for a value to be meaningfully comparable.
# Safety rates implicitly refer to the safety population (extraction does not
# always repeat it), so only the cut-off is mandatory there.  Counts are
# study-level and carry no required dimension.
FIELD_REQUIRED_DIMS: dict[str, tuple[str, ...]] = {
    **{field: ("population", CUTOFF_DIMENSION) for field in EFFICACY_FIELDS},
    **{field: () for field in SIZE_FIELDS},
    **{field: (CUTOFF_DIMENSION,) for field in SAFETY_FIELDS},
}
# Dimensions whose difference is a definitional (Type C) difference.
FIELD_DEFINITION_DIMS: dict[str, tuple[str, ...]] = {
    **{
        field: ("population", "endpoint_def", "assessment_method")
        for field in EFFICACY_FIELDS
    },
    **{field: ("population",) for field in SIZE_FIELDS},
    **{field: ("population",) for field in SAFETY_FIELDS},
}

# UI/report readability: keep at most this many evidence rows on a finding; the
# full list stays available in ``detail["evidence_all"]`` for auditing.
MAX_FINDING_EVIDENCE = 6

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

# Free-text endpoint definitions are compared after expanding common
# abbreviations, so "objective response rate" and "ORR per RECIST 1.1" are
# recognised as the same definition rather than a contradiction.
_ENDPOINT_TEXT_ALIASES: tuple[tuple[str, str], ...] = (
    ("objective response rate", "orr"),
    ("overall response rate", "orr"),
    ("disease control rate", "dcr"),
    ("progression-free survival", "pfs"),
    ("progression free survival", "pfs"),
    ("overall survival", "os"),
    ("serious adverse event", "sae"),
    ("duration of response", "dor"),
)

# Concept extraction used only for definition-only fields: two documents conflict
# only when they describe genuinely different endpoints.
_ENDPOINT_CONCEPT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("orr", r"\borr\b|objective response rate|overall response rate"),
    ("dcr", r"\bdcr\b|disease control rate"),
    ("pfs", r"\bpfs\b|progression[- ]free survival"),
    ("os", r"\bos\b|overall survival"),
    ("hr", r"\bhr\b|hazard ratio"),
    ("dor", r"\bdor\b|duration of response"),
    ("sae", r"\bsae\b|serious adverse event"),
    ("ae", r"\bae\b|adverse event"),
)


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
    if field in TEXT_FIELDS:
        for alias, canonical in _ENDPOINT_TEXT_ALIASES:
            normalized = normalized.replace(alias, canonical)
        # "objective response rate (ORR)" expands to "orr (orr)"; collapse the
        # duplicated parenthetical so it matches the plain "ORR" definition.
        normalized = re.sub(r"\b([a-z0-9]+)\s*\(\1\)", r"\1", normalized)
        normalized = normalize_ws(normalized)
    return normalized


def _values_differ(field: str, left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """True when the two evidence values disagree beyond the field tolerance."""
    if field in TEXT_FIELDS:
        left_text = _normalized_text(field, left)
        right_text = _normalized_text(field, right)
        if left_text == right_text:
            return False
        # Free-text definitions are frequently abbreviated in one source
        # ("ORR" vs "ORR per RECIST 1.1").  Containment means the shorter form
        # does not contradict the fuller one.
        return not (left_text in right_text or right_text in left_text)
    if field == "data_cutoff":
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
# Signature / partition helpers
# ---------------------------------------------------------------------------


def _signature(evidence: Mapping[str, Any]) -> tuple[str | None, ...]:
    dims = _dims(evidence)
    return tuple(dims[dimension] for dimension in DIMENSIONS)


def _signature_string(signature: Sequence[str | None]) -> str:
    return "|".join("" if value is None else value for value in signature)


def _signature_dims(signature: Sequence[str | None]) -> dict[str, str | None]:
    return dict(zip(DIMENSIONS, signature))


def _cloud_key(evidence: Mapping[str, Any]) -> tuple[str | None, str | None]:
    return (_dim_value(evidence, "trial_key"), _dim_value(evidence, "arm"))


def _missing_required_dims(
    field: str, evidence: Mapping[str, Any]
) -> tuple[str, ...]:
    required = FIELD_REQUIRED_DIMS.get(field, ())
    dims = _dims(evidence)
    return tuple(dimension for dimension in required if dims[dimension] is None)


def _conflicting_records(
    field: str, records: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """Records that participate in a cross-document value conflict."""
    involved: dict[str, Mapping[str, Any]] = {}
    for left, right in combinations(records, 2):
        if left.get("document_id") == right.get("document_id"):
            continue  # a document repeating its own value is not a sell-side issue
        if _values_differ(field, left, right):
            involved[str(left.get("evidence_id"))] = left
            involved[str(right.get("evidence_id"))] = right
    return sorted(involved.values(), key=_sort_key)


def _conflicts_with_reference(
    field: str,
    records: Sequence[Mapping[str, Any]],
    reference: Sequence[Mapping[str, Any]],
) -> bool:
    for record in records:
        for ref_record in reference:
            if _values_differ(field, record, ref_record):
                return True
    return False


def _differing_dims(
    group_dims: Mapping[str, str | None],
    reference_dims: Mapping[str, str | None],
) -> set[str]:
    """Dims where both sides state a value and the values differ.

    One-sided missing dimensions are not counted as differences here: required
    ones already send the record to the unclear bucket, optional ones do not by
    themselves make the records incomparable.
    """
    differing: set[str] = set()
    for dimension in DIMENSIONS:
        group_value = group_dims[dimension]
        reference_value = reference_dims[dimension]
        if group_value is None or reference_value is None:
            continue
        if group_value != reference_value:
            differing.add(dimension)
    return differing


def _select_reference_signature(
    groups: Mapping[tuple, Sequence[Mapping[str, Any]]]
) -> tuple:
    """Largest group wins; ties prefer the latest data cut-off, then stable order."""
    largest = max(len(records) for records in groups.values())
    candidates = [signature for signature, records in groups.items() if len(records) == largest]
    latest_cutoff = max(
        (_signature_dims(signature).get(CUTOFF_DIMENSION) or "") for signature in candidates
    )
    preferred = [
        signature
        for signature in candidates
        if (_signature_dims(signature).get(CUTOFF_DIMENSION) or "") == latest_cutoff
    ]
    return sorted(preferred, key=_signature_string)[0]


# ---------------------------------------------------------------------------
# Dedup keys and detail payloads
# ---------------------------------------------------------------------------


def dedup_key(field: str, comparison_class: str, evidence_ids: Sequence[str]) -> str:
    payload = "|".join([field, comparison_class, *sorted(str(e) for e in evidence_ids)])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"consistency:{comparison_class}:{digest}"


def _values_detail(field: str, evidence: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per (document, value) pair; repeated extraction rows are collapsed."""
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


def _date_ordinal(iso_date: str) -> int:
    from datetime import date

    return date.fromisoformat(iso_date).toordinal()


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


def _select_finding_evidence(
    field: str, evidence: Sequence[Mapping[str, Any]]
) -> tuple[list[Mapping[str, Any]], list[str]]:
    """Pick the most relevant records for display; return (selected, all_ids)."""
    ordered = sorted(evidence, key=_sort_key)
    selected: dict[str, Mapping[str, Any]] = {}
    if field not in TEXT_FIELDS and field != "data_cutoff":
        numeric = [
            (value, record)
            for record in ordered
            if (value := _numeric_value(record)) is not None
        ]
        if numeric:
            low = min(numeric, key=lambda pair: (pair[0], _sort_key(pair[1])))[1]
            high = max(numeric, key=lambda pair: (pair[0], _sort_key(pair[1])))[1]
            selected[str(low.get("evidence_id"))] = low
            if len(selected) < MAX_FINDING_EVIDENCE:
                selected[str(high.get("evidence_id"))] = high
    seen_documents: set[str] = set()
    for record in ordered:
        if len(selected) >= MAX_FINDING_EVIDENCE:
            break
        document_id = str(record.get("document_id") or "")
        if document_id in seen_documents:
            continue
        seen_documents.add(document_id)
        selected.setdefault(str(record.get("evidence_id")), record)
    for record in ordered:
        if len(selected) >= MAX_FINDING_EVIDENCE:
            break
        selected.setdefault(str(record.get("evidence_id")), record)
    chosen = [record for record in ordered if str(record.get("evidence_id")) in selected]
    return chosen[:MAX_FINDING_EVIDENCE], [str(record.get("evidence_id")) for record in ordered]


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
    return str(Severity.HIGH)


def _severity_for(comparison_class: str, field: str) -> str:
    if comparison_class == "A":
        return _severity_for_a(field)
    if comparison_class == "B":
        return str(Severity.LOW)  # version difference: informational
    if comparison_class == "C":
        return str(Severity.MEDIUM)
    return str(Severity.LOW)


def _build_finding(
    field: str,
    comparison_class: str,
    evidence: Sequence[Mapping[str, Any]],
    *,
    signature: Sequence[str] = (),
    reference: Sequence[Mapping[str, Any]] = (),
) -> FindingDraft:
    label = FIELD_LABELS.get(field, field)
    values, evidence_all = _select_finding_evidence(field, evidence)
    reference_sorted = sorted(reference, key=_sort_key)
    reference_values, reference_all = (
        _select_finding_evidence(field, reference_sorted) if reference_sorted else ([], [])
    )
    combined = sorted([*values, *reference_values], key=_sort_key) if reference_sorted else values
    value_labels = "; ".join(
        f"{_fmt_value(field, record)} in {record.get('filename') or record.get('document_id')} "
        f"(page {record.get('page')})"
        for record in values
    )
    dims_detail = _dims_detail(field, combined)
    delta = _delta_detail(field, combined)
    detail = {
        "field": field,
        "field_label": label,
        "class": comparison_class,
        "values": _values_detail(field, values),
        "reference_values": _values_detail(field, reference_values),
        "reference_evidence_ids": reference_all,
        "reference_dimensions": (
            {dimension: _dim_value(reference_sorted[0], dimension) for dimension in DIMENSIONS}
            if reference_sorted
            else {}
        ),
        "dims": dims_detail,
        "delta": delta,
        "documents": _documents_detail(combined),
        "document_ids": sorted({str(record.get("document_id") or "") for record in combined}),
        "tolerance": {
            "relative_pct": TOLERANCE_PCT if comparison_class == "A" else None,
            "mode": "exact" if field in _COUNT_FIELD_VALUES else "relative",
        },
        "missing_dimensions": list(signature) if comparison_class == "U" else [],
        "definition_dimensions": list(signature) if comparison_class == "C" else [],
        "evidence_all": evidence_all,
        "evidence_total": len(evidence_all),
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
                f"{label[:1].upper()}{label[1:]} differs from the reference analysis by data "
                f"cut-off only. Values: {value_labels}. The cut-off difference is a plausible "
                f"explanation for the changed value, so this is classified as a version difference "
                f"rather than a data contradiction; the authoritative data cut-off should still be "
                f"confirmed."
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
            f"{label[:1].upper()}{label[1:]} differs from the reference analysis because the "
            f"{dimension_labels} differs. Values: {value_labels}. Because the records do not "
            f"measure the same quantity, the difference is attributable to the stated definitional "
            f"difference and is not treated as a contradiction; the comparison should be made on a "
            f"like-for-like basis."
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


def _definition_concepts(evidence: Mapping[str, Any]) -> frozenset[str]:
    text = normalize_ws(str(evidence.get("value_text") or evidence.get("quote") or "")).lower()
    return frozenset(
        concept
        for concept, pattern in _ENDPOINT_CONCEPT_PATTERNS
        if re.search(pattern, text)
    )


def _definition_conflict_findings(
    field: str, records: Sequence[Mapping[str, Any]]
) -> list[FindingDraft]:
    """Definition-only fields: only genuinely conflicting endpoint concepts are findings."""
    involved: dict[str, Mapping[str, Any]] = {}
    concepts_by_document: dict[str, set[str]] = defaultdict(set)
    for record in records:
        concepts_by_document[str(record.get("document_id") or "")].update(
            _definition_concepts(record)
        )
    for left, right in combinations(records, 2):
        if left.get("document_id") == right.get("document_id"):
            continue
        left_concepts = _definition_concepts(left)
        right_concepts = _definition_concepts(right)
        if not left_concepts or not right_concepts:
            continue
        if left_concepts & right_concepts:
            continue
        # ORR vs PFS is a real conflict; a fuller description of the same metric
        # is recognised by the shared concept and not flagged.
        involved[str(left.get("evidence_id"))] = left
        involved[str(right.get("evidence_id"))] = right
    if not involved:
        return []
    finding = _build_finding(field, "C", sorted(involved.values(), key=_sort_key))
    finding.title = "Endpoint definition conflict across documents"
    finding.explanation = (
        "The documents describe the primary endpoint with different endpoint concepts "
        "(for example ORR versus PFS or OS). Free-text endpoint descriptors are not "
        "measured values, so this is classified as a definition difference (medium) "
        "rather than a data inconsistency; the endpoint definitions should be confirmed."
    )
    finding.recommended_action = (
        "Ask the seller to confirm the protocol-defined primary endpoint and align the "
        "endpoint wording across the package."
    )
    finding.detail["concepts_by_document"] = {
        document_id: sorted(concepts)
        for document_id, concepts in sorted(concepts_by_document.items())
    }
    return [finding]


# ---------------------------------------------------------------------------
# Per-field partition
# ---------------------------------------------------------------------------


def _partition_field(
    field: str, records: Sequence[Mapping[str, Any]]
) -> list[FindingDraft]:
    """Partition one field's records into disjoint A / B / C / U findings."""
    required_dims = FIELD_REQUIRED_DIMS.get(field, ())
    definition_dims = set(FIELD_DEFINITION_DIMS.get(field, ()))

    clouds: dict[tuple[str | None, str | None], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        clouds[_cloud_key(record)].append(record)

    a_findings: list[FindingDraft] = []
    b_findings: list[FindingDraft] = []
    c_findings: list[FindingDraft] = []
    unclear_records: dict[str, Mapping[str, Any]] = {}

    for cloud_records in clouds.values():
        eligible: list[Mapping[str, Any]] = []
        missing_required: list[Mapping[str, Any]] = []
        for record in cloud_records:
            if _missing_required_dims(field, record):
                missing_required.append(record)
            else:
                eligible.append(record)

        signature_groups: dict[tuple, list[Mapping[str, Any]]] = defaultdict(list)
        for record in eligible:
            signature_groups[_signature(record)].append(record)

        reference_records: list[Mapping[str, Any]] = []
        reference_dims: dict[str, str | None] = {}
        if signature_groups:
            reference_signature = _select_reference_signature(signature_groups)
            reference_records = signature_groups[reference_signature]
            reference_dims = _signature_dims(reference_signature)

            # Type A: exact-dimension reference records that disagree across docs.
            conflicting = _conflicting_records(field, reference_records)
            if conflicting:
                a_findings.append(_build_finding(field, "A", conflicting))

            for signature, group_records in signature_groups.items():
                if signature == reference_signature:
                    continue
                dims = _signature_dims(signature)
                differing = _differing_dims(dims, reference_dims)
                if not _conflicts_with_reference(field, group_records, reference_records):
                    continue  # dimension differs but the value is consistent: no finding
                if not differing:
                    # Only an optional dimension is stated on one side; remain
                    # conservative and ask for the metadata (never Type A).
                    for record in group_records:
                        unclear_records[str(record.get("evidence_id"))] = record
                    continue
                if differing <= {CUTOFF_DIMENSION}:
                    b_findings.append(
                        _build_finding(
                            field,
                            "B",
                            group_records,
                            signature=tuple(sorted(differing)),
                            reference=reference_records,
                        )
                    )
                elif differing <= definition_dims or differing <= definition_dims | {CUTOFF_DIMENSION}:
                    c_findings.append(
                        _build_finding(
                            field,
                            "C",
                            group_records,
                            signature=tuple(sorted(differing)),
                            reference=reference_records,
                        )
                    )
                else:
                    for record in group_records:
                        unclear_records[str(record.get("evidence_id"))] = record

        if missing_required:
            if reference_records:
                for record in missing_required:
                    if _conflicts_with_reference(field, [record], reference_records):
                        unclear_records[str(record.get("evidence_id"))] = record
            else:
                # No reference in this cloud: any cross-document disagreement is unclear.
                conflicting = _conflicting_records(field, missing_required)
                for record in conflicting or missing_required:
                    unclear_records[str(record.get("evidence_id"))] = record

    findings = [*a_findings, *c_findings, *b_findings]
    if unclear_records:
        unclear = sorted(unclear_records.values(), key=_sort_key)
        missing_dims = sorted(
            {
                dimension
                for record in unclear
                for dimension in DIMENSIONS
                if _dim_value(record, dimension) is None
                and (dimension in required_dims or dimension in definition_dims)
            }
        )
        # A comparability-unclear finding must actually have missing metadata;
        # a record set that merely differs on multiple stated dimensions is a
        # C/B difference (handled above), never U.
        if missing_dims:
            findings.append(
                _build_finding(field, "U", unclear, signature=tuple(missing_dims))
            )
    return findings


# ---------------------------------------------------------------------------
# Data cut-off detail field
# ---------------------------------------------------------------------------


def _cutoff_field_findings(records: Sequence[Mapping[str, Any]]) -> list[FindingDraft]:
    """Data cut-off statements are compared per trial/arm cohort as version differences."""
    buckets: dict[tuple, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        buckets[(_dim_value(record, "trial_key"), _dim_value(record, "arm"))].append(record)
    findings: list[FindingDraft] = []
    for bucket in buckets.values():
        distinct = {_normalized_text("data_cutoff", record) for record in bucket}
        if len(distinct) >= 2:
            findings.append(_build_finding("data_cutoff", "B", bucket))
    return findings


# ---------------------------------------------------------------------------
# Stale-finding resolution
# ---------------------------------------------------------------------------


def _resolve_stale_findings(session, project_id: str, current_keys: set[str]) -> int:
    """Mark auto consistency findings that are no longer reproduced as resolved."""
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
        # Resolve any auto-managed consistency finding that this run did not reproduce,
        # including rows created by earlier code versions with a different dedup-key format.
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
        comparisons += len(records)
        if field == "data_cutoff":
            drafts.extend(_cutoff_field_findings(records))
        elif field in DEFINITION_ONLY_FIELDS:
            drafts.extend(_definition_conflict_findings(field, records))
        else:
            drafts.extend(_partition_field(field, records))

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
