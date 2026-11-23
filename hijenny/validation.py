"""VALIDATE stage handler: independent source verification of evidence.

Each pending evidence row is verified on its own merits — no cross-document
mutual agreement required:

(a) quote is a verbatim whitespace-normalised substring of the cited page text;
(b) the numeric value / date / distinctive text appears in that quote;
(c) numeric plausibility (percent 0-100, positive counts, CI lower < upper, ...);
(d) arithmetic consistency where derivable (CR+PR vs ORR within 0.5 pp,
    n_itt/n_safety <= sample_size, DCR = CR+PR+SD, CI ordering, arm sums);
(e) duplicate detection (same document/page/field/value/comparability vector);
(f) unit sanity (percentage fields in %, time fields in months/weeks, ...).

The stage only updates ``verification_status`` / ``verification_note`` via
``store.update_evidence_verification``; it never creates findings (analysis
agents own findings).
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from hijenny import store
from hijenny.schemas import (
    COUNT_FIELDS,
    PERCENT_FIELDS,
    TIME_FIELDS,
    normalize_date,
    normalize_ws,
)

_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_DATE_TEXT_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}",
    re.IGNORECASE,
)
_PHASE_TEXT_RE = re.compile(r"\bphase\s*(1/2|2/3|1b/2|1b|2a|2b|IV|III|II|I|4|3|2|1)\b", re.IGNORECASE)
_ROMAN_TO_NUMBER = {"I": "1", "II": "2", "III": "3", "IV": "4"}

_ARITHMETIC_TOLERANCE = 0.5

# Canonical abbreviations are expanded when checking that a text value is
# supported by its quote ("ORR per RECIST 1.1" vs "objective response rate ...").
_ABBREVIATION_ALIASES = {
    "ORR": ("objective response rate", "overall response rate"),
    "DCR": ("disease control rate",),
    "DOR": ("duration of response",),
    "PFS": ("progression-free survival", "progression free survival"),
    "OS": ("overall survival",),
    "HR": ("hazard ratio",),
    "CR": ("complete response",),
    "PR": ("partial response",),
    "SD": ("stable disease",),
    "PD": ("progressive disease",),
    "BICR": ("blinded independent central review",),
    "TTR": ("time to response",),
    "ITT": ("intention-to-treat", "intention to treat"),
    "FAS": ("full analysis set",),
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _number_tokens(value: float) -> list[str]:
    tokens = [f"{value:g}", f"{value:.1f}", f"{value:.2f}", f"{value:.3f}"]
    if abs(value - round(value)) < 1e-9:
        tokens.extend([f"{int(round(value))}", f"{int(round(value)):,}"])
    return list(dict.fromkeys(tokens))


def value_appears_in_quote(evidence: Mapping[str, Any], quote: str) -> tuple[bool, str]:
    quote_norm = normalize_ws(quote).lower()
    value_num = evidence.get("value_num")
    value_text = evidence.get("value_text")
    field = str(evidence.get("field_name") or "")
    if field == "data_cutoff":
        target = normalize_date(value_text) if value_text else None
        if target is None:
            return False, "data_cutoff has no parseable date"
        for token in _DATE_TEXT_RE.findall(quote):
            if normalize_date(token) == target:
                return True, ""
        if target in quote_norm:
            return True, ""
        return False, f"date {target} not found in quote"
    if value_num is not None:
        for token in _number_tokens(float(value_num)):
            if token.lower() in quote_norm:
                return True, ""
        return False, f"value {value_num:g} not found in quote"
    if value_text:
        needle = normalize_ws(value_text).lower()
        if not needle:
            return False, "empty value_text"
        if needle in quote_norm:
            return True, ""
        if field == "phase":
            match = _PHASE_TEXT_RE.search(quote)
            if match:
                raw = match.group(1).upper()
                number = _ROMAN_TO_NUMBER.get(raw, raw.lower())
                if f"phase {number}" in needle:
                    return True, ""
                return False, f"phase {number} not found in quote"
        tokens = [token for token in re.split(r"[^a-z0-9.\-]+", needle) if len(token) >= 3]
        if not tokens:
            tokens = [token for token in re.split(r"[^a-z0-9.\-]+", needle) if token]
        present = 0
        for token in tokens[:8]:
            if token in quote_norm:
                present += 1
                continue
            for alias in _ABBREVIATION_ALIASES.get(token.upper(), ()):  # canonical acronym
                if alias in quote_norm:
                    present += 1
                    break
        if tokens and present >= min(2, len(tokens)):
            return True, ""
        return False, "value text not found in quote"
    return False, "evidence has no value"


def _plausibility(evidence: Mapping[str, Any]) -> tuple[bool, str]:
    field = str(evidence.get("field_name") or "")
    value = evidence.get("value_num")
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError):
        return False, "value_num is not numeric"
    if number is None:
        return True, ""
    if field in PERCENT_FIELDS:
        if not (0.0 <= number <= 100.0):
            return False, f"{field} percentage {number:g} outside 0-100"
    elif field in TIME_FIELDS:
        if not (0.0 < number <= 600.0):
            return False, f"{field} duration {number:g} implausible (months)"
    elif field in COUNT_FIELDS:
        if not (0.0 < number <= 1_000_000.0):
            return False, f"{field} count {number:g} implausible"
        if abs(number - round(number)) > 1e-6:
            return False, f"{field} count {number:g} is not an integer"
    elif field == "hr":
        if not (0.0 < number <= 10.0):
            return False, f"HR {number:g} implausible"
    elif field == "p_value":
        if not (0.0 <= number <= 1.0):
            return False, f"p-value {number:g} outside 0-1"
    elif field == "age_median":
        if not (0.0 < number <= 120.0):
            return False, f"median age {number:g} implausible"
    elif field in {"ci_lower", "ci_upper"}:
        if not (0.0 <= number <= 1000.0):
            return False, f"{field} out of range"
    return True, ""


def _unit_sanity(evidence: Mapping[str, Any]) -> tuple[bool, str]:
    field = str(evidence.get("field_name") or "")
    unit = evidence.get("unit")
    unit_norm = normalize_ws(unit).lower() if unit else None
    if field in PERCENT_FIELDS:
        if unit_norm not in {"%", "percent", "percentage"}:
            return False, f"{field} unit {unit!r} is not a percentage"
    elif field in TIME_FIELDS:
        if unit_norm not in {"months", "month", "weeks", "week", "days", "day", "years", "year"}:
            return False, f"{field} unit {unit!r} is not a duration"
    elif field in COUNT_FIELDS:
        if unit_norm not in {"patients", "patient", "subjects", "subject", "participants", "participant"}:
            return False, f"{field} unit {unit!r} is not a patient count"
    elif field == "hr":
        if unit_norm not in {"ratio", "hr", None}:
            return False, f"HR unit {unit!r} is not a ratio"
    elif field == "data_cutoff":
        if unit_norm not in {"date", None}:
            return False, f"data_cutoff unit {unit!r} is not a date"
    return True, ""


def verify_evidence(
    evidence: Mapping[str, Any], page_text: str | None
) -> tuple[str, str]:
    """Return ``(status, note)`` for one evidence row (no side effects)."""
    if page_text is None:
        return "unverifiable", f"cited page {evidence.get('page')} not present in parsed document"
    page_norm = normalize_ws(page_text)
    quote_norm = normalize_ws(evidence.get("quote") or "")
    if not quote_norm:
        return "failed", "empty quote"
    if quote_norm not in page_norm:
        return "failed", f"quote not found verbatim on page {evidence.get('page')}"
    ok, reason = value_appears_in_quote(evidence, quote_norm)
    if not ok:
        return "failed", reason
    ok, reason = _plausibility(evidence)
    if not ok:
        return "failed", reason
    ok, reason = _unit_sanity(evidence)
    if not ok:
        return "failed", reason
    return "verified", "quote and value verified on cited page"


# ---------------------------------------------------------------------------
# Arithmetic / duplicate checks
# ---------------------------------------------------------------------------
def _dims_key(evidence: Mapping[str, Any]) -> tuple:
    return (
        evidence.get("document_id") or "",
        evidence.get("trial_key") or "",
        (evidence.get("arm") or "").lower(),
        evidence.get("population") or "",
        evidence.get("data_cutoff") or "",
        evidence.get("assessment_method") or "",
    )


def _one_value(rows: Sequence[Mapping[str, Any]]) -> float | None:
    values = {
        round(float(row["value_num"]), 4)
        for row in rows
        if row.get("value_num") is not None
    }
    if len(values) != 1:
        return None
    return values.pop()


def _arithmetic_checks(
    evidence: Sequence[Mapping[str, Any]], statuses: dict[str, list[str]]
) -> tuple[dict[str, str], int]:
    """Return ``{evidence_id: reason}`` for arithmetic failures + flag count."""
    failures: dict[str, str] = {}
    flags = 0
    by_dims: dict[tuple, dict[str, list[Mapping[str, Any]]]] = {}
    for row in evidence:
        if row["verification_status"] == "failed" and row["evidence_id"] not in statuses:
            continue
        candidate = statuses.get(row["evidence_id"])
        if candidate and candidate[0] == "failed":
            continue
        by_dims.setdefault(_dims_key(row), {}).setdefault(str(row.get("field_name")), []).append(row)

    for _key, fields in by_dims.items():
        orr = _one_value(fields.get("orr", []))
        cr = _one_value(fields.get("cr", []))
        pr = _one_value(fields.get("pr", []))
        sd = _one_value(fields.get("sd", []))
        dcr = _one_value(fields.get("dcr", []))
        if orr is not None and cr is not None and pr is not None:
            if abs((cr + pr) - orr) > _ARITHMETIC_TOLERANCE:
                reason = (
                    f"arithmetic: CR+PR = {cr + pr:.1f}% differs from ORR {orr:.1f}% "
                    f"by {abs(cr + pr - orr):.1f} pp (> {_ARITHMETIC_TOLERANCE} pp)"
                )
                for row in fields.get("orr", []):
                    failures[row["evidence_id"]] = reason
                flags += 1
        sample_size = _one_value(fields.get("sample_size", []))
        if sample_size is not None:
            for field_name in ("n_itt", "n_safety", "n_fas", "n_pp"):
                size = _one_value(fields.get(field_name, []))
                if size is not None and size > sample_size:
                    reason = f"arithmetic: {field_name} {size:g} exceeds sample_size {sample_size:g}"
                    for row in fields.get(field_name, []):
                        failures[row["evidence_id"]] = reason
                    flags += 1
        # Arm-level N sums must not exceed the overall sample size.
        if sample_size is not None:
            arm_sizes = [
                row
                for row in fields.get("n_itt", []) + fields.get("n_safety", [])
                if row.get("arm") and row.get("value_num") is not None
            ]
            arms = {(row.get("arm") or "").lower() for row in arm_sizes}
            if len(arms) > 1:
                total = sum(float(row["value_num"]) for row in arm_sizes if row.get("arm"))
                if total > sample_size:
                    reason = f"arithmetic: arm sample sizes sum to {total:g} > sample_size {sample_size:g}"
                    for row in arm_sizes:
                        failures[row["evidence_id"]] = reason
                    flags += 1

    # CI ordering: pair lower/upper rows by comparable_key within a document.
    ci_groups: dict[tuple, dict[str, list[Mapping[str, Any]]]] = {}
    for row in evidence:
        candidate = statuses.get(row["evidence_id"])
        if candidate and candidate[0] == "failed":
            continue
        field = str(row.get("field_name"))
        if field not in {"ci_lower", "ci_upper"}:
            continue
        ci_groups.setdefault(
            (row.get("document_id"), row.get("comparable_key"), row.get("endpoint_def")),
            {},
        ).setdefault(field, []).append(row)
    for fields in ci_groups.values():
        lower = _one_value(fields.get("ci_lower", []))
        upper = _one_value(fields.get("ci_upper", []))
        if lower is not None and upper is not None and lower >= upper:
            reason = f"arithmetic: CI lower ({lower:g}) is not below upper ({upper:g})"
            for row in fields.get("ci_lower", []) + fields.get("ci_upper", []):
                failures[row["evidence_id"]] = reason
            flags += 1
    return failures, flags


def _duplicate_checks(
    evidence: Sequence[Mapping[str, Any]], statuses: dict[str, list[str]]
) -> tuple[dict[str, str], int]:
    duplicates: dict[str, str] = {}
    seen: dict[tuple, str] = {}
    count = 0
    for row in evidence:
        candidate = statuses.get(row["evidence_id"])
        if candidate and candidate[0] == "failed":
            continue
        number = None
        if row.get("value_num") is not None:
            number = round(float(row["value_num"]), 6)
        key = (
            row.get("document_id"),
            row.get("page"),
            str(row.get("field_name")),
            number,
            normalize_ws(row.get("value_text") or "").lower(),
            row.get("comparable_key"),
        )
        if key in seen:
            duplicates[row["evidence_id"]] = f"duplicate of evidence {seen[key]} (same page/field/value/dims)"
            count += 1
        else:
            seen[key] = row["evidence_id"]
    return duplicates, count


# ---------------------------------------------------------------------------
# Stage handler
# ---------------------------------------------------------------------------
def validate_project(session: Session, project_id: str, force: bool = False) -> dict:
    """VALIDATE stage: verify pending evidence (all evidence when ``force``)."""
    all_evidence = store.get_evidence(session, project_id, limit=100000)
    targets = all_evidence if force else [
        row for row in all_evidence if row.get("verification_status") == "pending"
    ]
    stats: dict = {
        "checked": len(targets),
        "verified": 0,
        "failed": 0,
        "unverifiable": 0,
        "flagged": 0,
        "duplicates": 0,
        "arithmetic_flags": 0,
        "evidence_total": len(all_evidence),
    }
    if not targets:
        return stats

    page_cache: dict[str, dict[int, str]] = {}
    statuses: dict[str, list[str]] = {}
    for row in targets:
        document_id = row["document_id"]
        if document_id not in page_cache:
            page_cache[document_id] = store.get_pages_text(session, document_id)
        try:
            page_no = int(row.get("page") or 0)
        except (TypeError, ValueError):
            page_no = 0
        page_text = page_cache[document_id].get(page_no)
        status, note = verify_evidence(row, page_text)
        statuses[row["evidence_id"]] = [status, note]

    arithmetic_failures, arithmetic_flags = _arithmetic_checks(all_evidence, statuses)
    duplicates, duplicate_count = _duplicate_checks(all_evidence, statuses)
    stats["arithmetic_flags"] = arithmetic_flags
    stats["duplicates"] = duplicate_count

    for row in targets:
        evidence_id = row["evidence_id"]
        status, note = statuses[evidence_id]
        if evidence_id in arithmetic_failures:
            status, note = "failed", arithmetic_failures[evidence_id]
        elif evidence_id in duplicates and status == "verified":
            note = f"{note}; {duplicates[evidence_id]}"
            stats["flagged"] += 1
        store.update_evidence_verification(session, evidence_id, status, note or None)
        if status == "verified":
            stats["verified"] += 1
        elif status == "failed":
            stats["failed"] += 1
        else:
            stats["unverifiable"] += 1
    stats["flagged"] += arithmetic_flags
    stats["verification_by_field"] = _by_field_summary(targets, statuses, arithmetic_failures)
    return stats


def _by_field_summary(
    targets: Iterable[Mapping[str, Any]],
    statuses: Mapping[str, Sequence[str]],
    arithmetic_failures: Mapping[str, str],
) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for row in targets:
        field = str(row.get("field_name") or "")
        bucket = summary.setdefault(field, {"checked": 0, "verified": 0, "failed": 0, "unverifiable": 0})
        bucket["checked"] += 1
        status = statuses[row["evidence_id"]][0]
        if row["evidence_id"] in arithmetic_failures:
            status = "failed"
        if status in bucket:
            bucket[status] += 1
    return summary


__all__ = ["validate_project", "verify_evidence", "value_appears_in_quote"]
