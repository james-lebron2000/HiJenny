"""Shared deterministic text machinery for clinical evidence extraction.

Everything in this module is dependency-free (stdlib + pydantic via schemas).
It provides:

* sentence segmentation and *verbatim* quote building on whitespace-normalised
  page text (the validation invariant is ``normalize_ws(quote) in
  normalize_ws(page_text)``);
* canonical dimension detectors (population, assessment method, data cutoff,
  trial registry id, study id, arms) and a per-document context that resolves
  the approved fallback hierarchy with recorded provenance;
* prompt-injection sentence detection reusing parser-dev's single pattern list;
* an ``EvidenceDraft`` factory plus a deduplicating collector.

Design rules (product-critical)
-------------------------------
1. A quote is always a contiguous slice of the normalised page text, so it is a
   verbatim (whitespace-normalised) substring by construction.
2. Dimensions are resolved sentence/table-row first. Document-level defaults
   are only used when the document contains exactly one normalised candidate,
   and the provenance is recorded in ``notes``.
3. Never invent a value, arm or population: unknown dimensions stay ``None``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from pydantic import ValidationError

from hijenny.schemas import (
    EvidenceCategory,
    EvidenceDraft,
    FieldName,
    normalize_date,
    normalize_method,
    normalize_population,
    normalize_trial,
    normalize_ws,
)

# ---------------------------------------------------------------------------
# Prompt-injection detection (single source of truth = parser-dev's list)
# ---------------------------------------------------------------------------
# The parser owns the canonical pattern list and persists ``injection_flags`` on
# the document meta at INGEST.  Reusing the same list here guarantees extraction
# and parsing agree on what counts as injected text (no double counting).
try:  # pragma: no cover - import guard only
    from hijenny.parsers.injection import INJECTION_PATTERNS as _INJECTION_PATTERNS
except Exception:  # noqa: BLE001 - parser package is optional at extraction import time
    _INJECTION_PATTERNS = (
        ("ignore_previous_instructions", r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|directions?|rules?)"),
        ("system_override", r"system\s+override"),
        ("disregard_above", r"disregard\s+(?:all\s+)?(?:the\s+)?(?:above|previous|prior|earlier)"),
        ("you_are_now", r"you\s+are\s+now"),
        ("reveal_system_prompt", r"reveal\s+(?:the\s+)?(?:system\s+prompt|hidden\s+prompt|prompt)"),
        ("report_fabricated_value", r"report\s+[^.\n]{0,60}?\s+as\s+\d+(?:\.\d+)?\s*%"),
        ("new_instructions", r"new\s+instructions?\s*[:\-]"),
        ("developer_mode", r"(?:developer|god|admin)\s+mode\b"),
    )

_INJECTION_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (flag, re.compile(pattern, re.IGNORECASE)) for flag, pattern in _INJECTION_PATTERNS
)

INJECTION_FLAG_IDS: tuple[str, ...] = tuple(flag for flag, _ in _INJECTION_PATTERNS)


def injection_flags_in(text: str) -> list[str]:
    """Return the sorted injection flag ids present in *text* (deterministic)."""
    if not text:
        return []
    return [flag for flag, regex in _INJECTION_COMPILED if regex.search(text)]


def is_injected(text: str) -> bool:
    return bool(injection_flags_in(text))


# ---------------------------------------------------------------------------
# Sentence segmentation over whitespace-normalised page text
# ---------------------------------------------------------------------------
INJECTION_EXCERPT_PAD = 120

_ABBREVIATIONS = frozenset(
    {
        "e.g", "i.e", "vs", "fig", "figs", "no", "nos", "ref", "refs", "dr",
        "prof", "inc", "ltd", "co", "approx", "al", "eq", "ca", "cf", "resp",
        "min", "max", "vol", "p", "pp", "sec", "art", "tab", "tbl", "st",
        "mr", "mrs", "ms", "dept", "univ", "est", "vs.", "etc",
    }
)
_SENTENCE_END = frozenset(".!?")


@dataclass(frozen=True)
class Segment:
    """One sentence, expressed as a slice of the normalised page text."""

    text: str
    start: int
    end: int


def _word_before(text: str, index: int) -> str:
    j = index - 1
    while j >= 0 and (text[j].isalnum() or text[j] == "."):
        j -= 1
    return text[j + 1 : index].lower().strip(".")


def split_sentences(page_norm: str) -> list[Segment]:
    """Deterministic sentence splitting for flattened PDF/PPT/XLSX page text.

    Only splits on ``. ! ?`` followed by whitespace and an upper-case / digit /
    bracket start, so decimals (``42.0``), abbreviations (``e.g.``) and file
    versions (``v1.0``) never split a sentence.
    """
    spans: list[Segment] = []
    n = len(page_norm)
    if not n:
        return spans
    start = 0
    i = 0
    while i < n:
        ch = page_norm[i]
        if ch not in _SENTENCE_END:
            i += 1
            continue
        if ch == "." and 0 < i < n - 1 and page_norm[i - 1].isdigit() and page_norm[i + 1].isdigit():
            i += 1
            continue
        k = i + 1
        while k < n and page_norm[k] == " ":
            k += 1
        if k == i + 1 or k >= n:
            i += 1
            continue
        nxt = page_norm[k]
        if not (nxt.isupper() or nxt.isdigit() or nxt in "(\"'"):
            i += 1
            continue
        if _word_before(page_norm, i) in _ABBREVIATIONS:
            i += 1
            continue
        spans.append(Segment(page_norm[start : i + 1], start, i + 1))
        start = k
        i = k
    if start < n:
        spans.append(Segment(page_norm[start:n], start, n))
    return spans


def build_quote(
    page_norm: str,
    span_start: int,
    span_end: int,
    *,
    segment: Segment | None = None,
    before: int = 240,
    after: int = 200,
    max_len: int = 1200,
) -> str:
    """Return a verbatim quote (a contiguous slice of *page_norm*) containing the span."""
    lo_bound = segment.start if segment else 0
    hi_bound = segment.end if segment else len(page_norm)
    span_start = max(lo_bound, min(span_start, hi_bound))
    span_end = max(span_start, min(span_end, hi_bound))
    lo = max(lo_bound, span_start - before)
    hi = min(hi_bound, span_end + after)
    if hi - lo > max_len:
        lo = max(lo_bound, span_start - 60)
        hi = min(hi_bound, lo + max_len)
    lo = min(lo, span_start)
    hi = max(hi, span_end)
    # Trim partial words at the edges without ever cutting through the span.
    if lo > 0 and page_norm[lo - 1] != " ":
        nxt = page_norm.find(" ", lo, span_start)
        if nxt != -1:
            lo = nxt + 1
    if hi < len(page_norm) and page_norm[hi] != " ":
        prev = page_norm.rfind(" ", span_end, hi)
        if prev != -1:
            hi = prev
    return page_norm[lo:hi].strip()


# ---------------------------------------------------------------------------
# Canonical detectors
# ---------------------------------------------------------------------------
DATE_TOKEN = (
    r"(?:\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}|"
    r"\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"[a-z]*\.?,?\s+\d{4}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"[a-z]*\.?\s+\d{1,2},?\s+\d{4})"
)
DATE_RE = re.compile(DATE_TOKEN, re.IGNORECASE)

_CUTOFF_PATTERNS = (
    re.compile(r"(?:data\s+)?cut[\s-]?off(?:\s+date)?\s*(?:of|was|is|on)?\s*[:=]?\s*(" + DATE_TOKEN + r")", re.I),
    re.compile(r"\bas\s+of\s+(" + DATE_TOKEN + r")", re.I),
    re.compile(r"(" + DATE_TOKEN + r")\s+(?:data\s+)?cut[\s-]?off", re.I),
    re.compile(r"reflects?\s+(?:a\s+)?(?:data\s+)?cut[\s-]?off\s+of\s+(" + DATE_TOKEN + r")", re.I),
)

NCT_RE = re.compile(r"\bNCT\s*0*\d{4,8}\b", re.IGNORECASE)
STUDY_ID_RE = re.compile(r"\b[A-Z]{2,6}-\d{2,5}-\d{2,4}\b")

# Ordered most-specific first so "iRECIST" is not misread as "RECIST 1.1".
_METHOD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\birecist\b", re.I), "iRECIST"),
    (re.compile(r"\brecist\s*(?:v(?:ersion)?\.?\s*)?1\.1\b", re.I), "RECIST 1.1"),
    (re.compile(r"\brecist\s*(?:v(?:ersion)?\.?\s*)?1\.0\b", re.I), "RECIST 1.0"),
    (re.compile(r"\blugano\b", re.I), "Lugano"),
    (re.compile(r"\bchoi\b", re.I), "Choi"),
    (re.compile(r"\brano\b", re.I), "RANO"),
    (re.compile(r"\bwho\s+(?:criteria|response|classification)\b", re.I), "WHO"),
)

METHOD_SENSITIVE_TOKENS = frozenset({"ORR", "DCR", "CR", "PR", "SD", "PD", "DOR", "PFS", "OS", "TTR"})

# (regex, canonical population).  Long forms first so flags include context.
POPULATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bintention[-\s]to[-\s]treat\b", re.I), "ITT"),
    (re.compile(r"\bITT\b"), "ITT"),
    (re.compile(r"\bfull\s+analysis\s+set\b", re.I), "FAS"),
    (re.compile(r"\bFAS\b"), "FAS"),
    (re.compile(r"\bmodified\s+intention[-\s]to[-\s]treat\b", re.I), "mITT"),
    (re.compile(r"\bmITT\b"), "mITT"),
    (re.compile(r"\bper[-\s]protocol\b", re.I), "PP"),
    (re.compile(r"\bPP\b"), "PP"),
    (re.compile(r"\bsafety\s+(?:population|set|analysis\s+set)\b", re.I), "SAFETY"),
    (re.compile(r"\bSAFETY\b"), "SAFETY"),
    (re.compile(r"\bresponse[-\s]evaluable\b", re.I), "RESPONSE_EVALUABLE"),
    (re.compile(r"\ball[-\s]comers\b", re.I), "ALL"),
)

_POP_DECLARATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:the\s+)?(ITT|FAS|mITT|PP)\s+(?:analysis\s+)?(?:population|set)\b", re.I),
    re.compile(r"\b(?:in|of|for)\s+the\s+(ITT|FAS|mITT|PP)\s+(?:population|analysis\s+set)\b", re.I),
)

_POP_CANONICAL = {"ITT": "ITT", "FAS": "FAS", "MITT": "mITT", "PP": "PP"}


def detect_methods(text: str) -> set[str]:
    if not text:
        return set()
    found: set[str] = set()
    for pattern, canonical in _METHOD_PATTERNS:
        if pattern.search(text):
            # iRECIST contains "RECIST": suppress the less specific match.
            if canonical == "RECIST 1.1" and "iRECIST" in found:
                continue
            found.add(canonical)
    if "iRECIST" in found:
        found.discard("RECIST 1.1")
    return found


def detect_method(text: str) -> str | None:
    methods = detect_methods(text)
    return next(iter(methods)) if len(methods) == 1 else None


def find_cutoffs(text: str) -> list[str]:
    """Return normalised ISO dates mentioned in a data-cutoff context (in order)."""
    if not text:
        return []
    found: list[str] = []
    for pattern in _CUTOFF_PATTERNS:
        for match in pattern.finditer(text):
            iso = normalize_date(match.group(1))
            if iso and iso not in found:
                found.append(iso)
    return found


def cutoff_near(text: str) -> str | None:
    found = find_cutoffs(text)
    return found[0] if found else None


def detect_ncts(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for match in NCT_RE.finditer(text):
        trial = normalize_trial(match.group(0))
        if trial and trial not in out:
            out.append(trial)
    return out


def detect_study_ids(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for match in STUDY_ID_RE.finditer(text):
        value = normalize_ws(match.group(0)).upper()
        if value not in out:
            out.append(value)
    return out


def population_mentions(text: str) -> list[tuple[int, int, str]]:
    """All population mentions in *text* as (start, end, canonical)."""
    mentions: list[tuple[int, int, str]] = []
    for pattern, canonical in POPULATION_PATTERNS:
        for match in pattern.finditer(text):
            mentions.append((match.start(), match.end(), canonical))
    mentions.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    return mentions


def nearest_population(text: str, span: tuple[int, int], window: int = 160) -> str | None:
    """Population mention closest to *span* in the same sentence (or None)."""
    if not text:
        return None
    start, end = span
    best: tuple[int, str] | None = None
    for m_start, m_end, canonical in population_mentions(text):
        if m_end <= start:
            distance = start - m_end
        elif m_start >= end:
            distance = m_start - end
        else:
            distance = 0
        if distance > window:
            continue
        if best is None or distance < best[0]:
            best = (distance, canonical)
    return best[1] if best else None


_MULTI_ARM_RE = re.compile(
    r"\b(?:"
    r"randomi[sz]ed\s+(?:in\s+a\s+)?(?:1:1|2:1|1:2|3:1|1:3)"
    r"|(?:two|three|four|multi)[-\s]arm"
    r"|Arm\s+A\b[^.]{0,100}\bArm\s+B\b"
    r"|placebo[-\s]controlled"
    r"|control\s+(?:arm|group)"
    r"|randomi[sz]ed\s+(?:placebo|controlled)"
    r")\b",
    re.I,
)

ARM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bArm\s+([A-Z0-9]{1,3})\b"),
    re.compile(
        r"\b(?:treatment|control|placebo|experimental|investigational|comparator|combination|"
        r"monotherapy|active[-\s]control|dose[-\s]escalation|dose[-\s]expansion|"
        r"low[-\s]dose|high[-\s]dose)\s+(?:arm|group)\b",
        re.I,
    ),
)


def nearest_arm(text: str, span: tuple[int, int], window: int = 140) -> str | None:
    if not text:
        return None
    start, end = span
    best: tuple[int, str] | None = None
    for pattern in ARM_PATTERNS:
        for match in pattern.finditer(text):
            if match.end() <= start:
                distance = start - match.end()
            elif match.start() >= end:
                distance = match.start() - end
            else:
                distance = 0
            if distance > window:
                continue
            label = normalize_ws(match.group(0))
            if match.re is ARM_PATTERNS[0]:
                label = f"Arm {match.group(1)}"
            if best is None or distance < best[0]:
                best = (distance, label)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Endpoint canonicalisation / field categories
# ---------------------------------------------------------------------------
ENDPOINT_TOKENS: dict[FieldName, str] = {
    FieldName.ORR: "ORR",
    FieldName.DCR: "DCR",
    FieldName.CR: "CR",
    FieldName.PR: "PR",
    FieldName.SD: "SD",
    FieldName.PD: "PD",
    FieldName.DOR: "DOR",
    FieldName.PFS: "PFS",
    FieldName.OS: "OS",
    FieldName.HR: "HR",
    FieldName.TTR: "TTR",
    FieldName.ANY_AE: "ANY_AE",
    FieldName.GRADE3_AE: "GRADE3_AE",
    FieldName.TREATMENT_RELATED_AE: "TREATMENT_RELATED_AE",
    FieldName.SAE: "SAE",
    FieldName.TREATMENT_RELATED_DEATH: "TREATMENT_RELATED_DEATH",
    FieldName.DOSE_REDUCTION: "DOSE_REDUCTION",
    FieldName.DOSE_INTERRUPTION: "DOSE_INTERRUPTION",
    FieldName.DISCONTINUATION: "DISCONTINUATION",
    FieldName.AESI: "AESI",
}

FIELD_CATEGORY: dict[FieldName, EvidenceCategory] = {
    FieldName.ASSET_NAME: EvidenceCategory.STUDY_DESIGN,
    FieldName.MECHANISM: EvidenceCategory.STUDY_DESIGN,
    FieldName.TARGET: EvidenceCategory.STUDY_DESIGN,
    FieldName.MODALITY: EvidenceCategory.STUDY_DESIGN,
    FieldName.NCT_ID: EvidenceCategory.STUDY_DESIGN,
    FieldName.PHASE: EvidenceCategory.STUDY_DESIGN,
    FieldName.INDICATION: EvidenceCategory.STUDY_DESIGN,
    FieldName.DESIGN: EvidenceCategory.STUDY_DESIGN,
    FieldName.ARMS: EvidenceCategory.STUDY_DESIGN,
    FieldName.COMPARATOR: EvidenceCategory.STUDY_DESIGN,
    FieldName.RANDOMIZATION: EvidenceCategory.STUDY_DESIGN,
    FieldName.BLINDING: EvidenceCategory.STUDY_DESIGN,
    FieldName.PRIMARY_ENDPOINT: EvidenceCategory.STUDY_DESIGN,
    FieldName.SECONDARY_ENDPOINTS: EvidenceCategory.STUDY_DESIGN,
    FieldName.DATA_CUTOFF: EvidenceCategory.STUDY_DESIGN,
    FieldName.SAMPLE_SIZE: EvidenceCategory.STUDY_DESIGN,
    FieldName.N_ITT: EvidenceCategory.STUDY_DESIGN,
    FieldName.N_SAFETY: EvidenceCategory.STUDY_DESIGN,
    FieldName.N_PP: EvidenceCategory.STUDY_DESIGN,
    FieldName.N_FAS: EvidenceCategory.STUDY_DESIGN,
    FieldName.AGE_MEDIAN: EvidenceCategory.PATIENT_BASELINE,
    FieldName.MALE_PCT: EvidenceCategory.PATIENT_BASELINE,
    FieldName.RESPONSE_CRITERIA: EvidenceCategory.STATISTICS,
    FieldName.SAP_VERSION: EvidenceCategory.STATISTICS,
    FieldName.MISSING_DATA_METHOD: EvidenceCategory.STATISTICS,
    FieldName.MULTIPLICITY: EvidenceCategory.STATISTICS,
    FieldName.SUBGROUP_ANALYSIS: EvidenceCategory.STATISTICS,
    FieldName.PROTOCOL_DEVIATION: EvidenceCategory.STATISTICS,
    FieldName.AUDIT_SUMMARY: EvidenceCategory.STATISTICS,
}

_SAFETY_FIELDS = {
    FieldName.ANY_AE,
    FieldName.GRADE3_AE,
    FieldName.TREATMENT_RELATED_AE,
    FieldName.SAE,
    FieldName.TREATMENT_RELATED_DEATH,
    FieldName.DOSE_REDUCTION,
    FieldName.DOSE_INTERRUPTION,
    FieldName.DISCONTINUATION,
    FieldName.AESI,
}
_EFFICACY_FIELDS = {
    FieldName.ORR,
    FieldName.DCR,
    FieldName.CR,
    FieldName.PR,
    FieldName.SD,
    FieldName.PD,
    FieldName.DOR,
    FieldName.PFS,
    FieldName.OS,
    FieldName.HR,
    FieldName.CI_LOWER,
    FieldName.CI_UPPER,
    FieldName.P_VALUE,
    FieldName.FOLLOW_UP,
    FieldName.TTR,
}

for _field in _SAFETY_FIELDS:
    FIELD_CATEGORY[_field] = EvidenceCategory.CLINICAL_SAFETY
for _field in _EFFICACY_FIELDS:
    FIELD_CATEGORY[_field] = EvidenceCategory.CLINICAL_EFFICACY


def field_category(field_name: FieldName) -> EvidenceCategory:
    return FIELD_CATEGORY.get(field_name, EvidenceCategory.GENERAL)


def canonical_endpoint_def(token: str, field_name: FieldName, method: str | None) -> str:
    """Canonical endpoint definition: ``ORR per RECIST 1.1`` / ``SAE``."""
    token = (token or "").strip().upper()
    if not token:
        token = ENDPOINT_TOKENS.get(field_name, str(field_name).upper())
    if method and token in METHOD_SENSITIVE_TOKENS:
        return f"{token} per {method}"
    return token


# ---------------------------------------------------------------------------
# Draft factory + deduplicating collector
# ---------------------------------------------------------------------------
# Non-numeric design/statistics facts: one occurrence per document (avoid
# emitting the same boilerplate claim from every header/footer repetition).
_LOW_CARDINALITY_FIELDS = {
    FieldName.NCT_ID,
    FieldName.PHASE,
    FieldName.INDICATION,
    FieldName.DESIGN,
    FieldName.ARMS,
    FieldName.COMPARATOR,
    FieldName.RANDOMIZATION,
    FieldName.BLINDING,
    FieldName.PRIMARY_ENDPOINT,
    FieldName.SECONDARY_ENDPOINTS,
    FieldName.RESPONSE_CRITERIA,
    FieldName.SAP_VERSION,
    FieldName.MISSING_DATA_METHOD,
    FieldName.MULTIPLICITY,
    FieldName.SUBGROUP_ANALYSIS,
    FieldName.PROTOCOL_DEVIATION,
    FieldName.AUDIT_SUMMARY,
    FieldName.AESI,
    FieldName.ASSET_NAME,
    FieldName.MECHANISM,
    FieldName.TARGET,
    FieldName.MODALITY,
}


def make_draft(
    field_name: FieldName,
    *,
    page: int,
    quote: str,
    value_num: float | None = None,
    value_text: str | None = None,
    unit: str | None = None,
    population: str | None = None,
    arm: str | None = None,
    trial_key: str | None = None,
    data_cutoff: str | None = None,
    endpoint_def: str | None = None,
    assessment_method: str | None = None,
    table: str | None = None,
    confidence: float = 0.8,
    extractor: str = "rules",
    notes: str | None = None,
) -> EvidenceDraft | None:
    """Build an EvidenceDraft, returning ``None`` for contract-invalid facts."""
    try:
        return EvidenceDraft(
            field_name=field_name,
            category=field_category(field_name),
            value_num=value_num,
            value_text=value_text,
            unit=unit,
            population=population,
            arm=arm,
            trial_key=trial_key,
            data_cutoff=data_cutoff,
            endpoint_def=endpoint_def,
            assessment_method=assessment_method,
            page=page,
            table=table,
            quote=quote,
            confidence=confidence,
            extractor=extractor,
            notes=notes,
        )
    except ValidationError:
        return None
    except ValueError:
        return None


def _fact_key(draft: EvidenceDraft) -> tuple:
    field = str(draft.field_name)
    number = "" if draft.value_num is None else f"{draft.value_num:.6g}"
    text_norm = normalize_ws(draft.value_text or "").lower()
    if draft.field_name in _LOW_CARDINALITY_FIELDS:
        return (field, "low", number, text_norm)
    return (
        field,
        number,
        text_norm,
        draft.population,
        (draft.arm or "").lower(),
        draft.data_cutoff,
        (draft.endpoint_def or "").lower(),
        draft.assessment_method,
        draft.page,
    )


class DraftCollector:
    """Per-document collector: dedupes identical facts and boilerplate repeats."""

    def __init__(self) -> None:
        self._drafts: dict[tuple, EvidenceDraft] = {}
        self._quote_seen: set[tuple[str, str, str]] = set()

    def add(self, draft: EvidenceDraft | None) -> bool:
        if draft is None:
            return False
        quote_norm = normalize_ws(draft.quote or "").lower()
        number = "" if draft.value_num is None else f"{draft.value_num:.6g}"
        quote_key = (str(draft.field_name), number, quote_norm)
        if len(quote_norm) >= 40 and quote_key in self._quote_seen:
            return False
        key = _fact_key(draft)
        if key in self._drafts:
            old = self._drafts[key]
            if self._prefer(draft, old):
                self._drafts[key] = draft
                self._quote_seen.add(quote_key)
                return True
            return False
        self._drafts[key] = draft
        self._quote_seen.add(quote_key)
        return True

    @staticmethod
    def _prefer(new: EvidenceDraft, old: EvidenceDraft) -> bool:
        if new.extractor == "table" and old.extractor != "table":
            return True
        return new.confidence > old.confidence + 0.05

    def drafts(self) -> list[EvidenceDraft]:
        items = list(self._drafts.values())
        items.sort(
            key=lambda d: (
                d.page,
                str(d.field_name),
                d.value_num if d.value_num is not None else -1.0,
                d.data_cutoff or "",
                d.population or "",
            )
        )
        return items

    def __len__(self) -> int:
        return len(self._drafts)


# ---------------------------------------------------------------------------
# Per-document context: block helpers, defaults and dimension resolution
# ---------------------------------------------------------------------------
def block_text(block) -> str:
    if isinstance(block, dict):
        return str(block.get("text") or "")
    return str(getattr(block, "text", "") or "")


def block_type(block) -> str:
    if isinstance(block, dict):
        return str(block.get("type") or block.get("block_type") or "paragraph")
    return str(getattr(block, "block_type", "paragraph") or "paragraph")


def block_bbox(block) -> list[float]:
    raw = block.get("bbox") if isinstance(block, dict) else getattr(block, "bbox", [])
    try:
        return [float(v) for v in (raw or [])]
    except (TypeError, ValueError):
        return []


def _most_common(values: Sequence[str]) -> str | None:
    if not values:
        return None
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    for idx, value in enumerate(values):
        counts[value] = counts.get(value, 0) + 1
        order.setdefault(value, idx)
    return max(counts, key=lambda v: (counts[v], -order[v]))


@dataclass
class PageContext:
    page_no: int
    text: str
    norm: str
    segments: list[Segment] = field(default_factory=list)
    blocks: list = field(default_factory=list)


@dataclass
class DocumentContext:
    document_id: str = ""
    filename: str = ""
    doc_type: str = "OTHER"
    pages: list[PageContext] = field(default_factory=list)
    trial_key: str | None = None
    study_id: str | None = None
    method_default: str | None = None
    cutoff_default: str | None = None
    safety_population: str | None = None
    multi_arm: bool = False
    population_starts: dict[int, str | None] = field(default_factory=dict)
    population_declarations: dict[int, list[tuple[int, str]]] = field(default_factory=dict)

    def page(self, page_no: int) -> PageContext | None:
        for page in self.pages:
            if page.page_no == page_no:
                return page
        return None

    # -- dimension fallbacks -------------------------------------------------
    def population_at(self, page_no: int, segment_index: int) -> tuple[str | None, dict | None]:
        """Page-forward population declaration lookup within the document."""
        declarations = self.population_declarations.get(page_no) or []
        chosen: tuple[int, str] | None = None
        for index, population in declarations:
            if index <= segment_index:
                chosen = (index, population)
        if chosen is not None:
            return chosen[1], {"fallback": "page_forward", "dim": "population", "source_page": page_no}
        inherited = self.population_starts.get(page_no)
        if inherited is not None:
            return inherited, {"fallback": "doc_level", "dim": "population", "source_page": self._population_source_page(page_no)}
        return None, None

    def _population_source_page(self, page_no: int) -> int:
        source = 1
        for candidate in sorted(self.population_starts):
            if candidate <= page_no and self.population_starts[candidate] is not None:
                source = candidate
        return source

    def cutoff_window(self, page_no: int, segment_index: int) -> tuple[str | None, dict | None]:
        """Data cutoff from a +/-3 sentence window on the same page."""
        page = self.page(page_no)
        if page is None:
            return None, None
        found: list[tuple[int, str]] = []
        for index in range(max(0, segment_index - 3), min(len(page.segments), segment_index + 4)):
            cutoff = cutoff_near(page.segments[index].text)
            if cutoff:
                found.append((abs(index - segment_index), cutoff))
        unique = {value for _, value in found}
        if len(unique) == 1:
            cutoff = found[0][1]
            if cutoff != self.cutoff_default:
                return cutoff, None
            return cutoff, None
        return None, None

    def resolve(
        self,
        *,
        page_no: int,
        segment_index: int,
        segment: Segment,
        field_name: FieldName,
        span: tuple[int, int],
        endpoint_token: str | None = None,
    ) -> dict:
        """Resolve dimension values for one fact and record fallback provenance."""
        category = field_category(field_name)
        provenance: dict = {}

        design_like = category in (
            EvidenceCategory.STUDY_DESIGN,
            EvidenceCategory.STATISTICS,
            EvidenceCategory.GENERAL,
        )
        population = nearest_population(segment.text, span)
        if population:
            population = normalize_population(population)
        if category == EvidenceCategory.CLINICAL_SAFETY and population not in (None, "SAFETY"):
            # AE/SAE rates are reported for the treated (safety) population; a
            # stray efficacy-population mention in a flattened synopsis row must
            # not relabel a safety denominator.
            population = self.safety_population or None
        if not population:
            if category == EvidenceCategory.CLINICAL_SAFETY:
                if self.safety_population:
                    population = self.safety_population
                    provenance["population"] = {
                        "fallback": "doc_level",
                        "dim": "population",
                        "source_page": 1,
                        "basis": "document declares a safety population",
                    }
            elif category in (EvidenceCategory.CLINICAL_EFFICACY, EvidenceCategory.PATIENT_BASELINE):
                population, note = self.population_at(page_no, segment_index)
                if population and note:
                    provenance["population"] = note

        if design_like:
            population = None
        arm = nearest_arm(segment.text, span) if self.multi_arm else None
        if arm:
            arm = normalize_ws(arm)
        if design_like:
            arm = None

        cutoff, note = self.cutoff_window(page_no, segment_index) if not design_like else (None, None)
        if cutoff:
            if note:
                provenance["data_cutoff"] = note
        elif category in (EvidenceCategory.CLINICAL_EFFICACY, EvidenceCategory.CLINICAL_SAFETY) and self.cutoff_default:
            cutoff = self.cutoff_default
            provenance["data_cutoff"] = {
                "fallback": "doc_level",
                "dim": "data_cutoff",
                "source_page": self._cutoff_source_page(),
                "basis": "unique data cutoff in document",
            }

        method = (
            detect_method(segment.text)
            if category == EvidenceCategory.CLINICAL_EFFICACY
            else None
        )
        if method:
            method = normalize_method(method) or method
        elif category == EvidenceCategory.CLINICAL_EFFICACY and self.method_default:
            method = self.method_default
            provenance["assessment_method"] = {
                "fallback": "doc_level",
                "dim": "assessment_method",
                "source_page": self._method_source_page(),
                "basis": "unique assessment method in document",
            }

        endpoint_def = None
        if not design_like and category in (EvidenceCategory.CLINICAL_EFFICACY, EvidenceCategory.CLINICAL_SAFETY):
            token = endpoint_token or ENDPOINT_TOKENS.get(field_name, str(field_name).upper())
            endpoint_def = canonical_endpoint_def(token, field_name, method)

        notes = None
        if provenance:
            notes = json.dumps({"fallback": "dimension_resolution", "dims": provenance}, sort_keys=True)
        return {
            "population": population,
            "arm": arm,
            "data_cutoff": cutoff,
            "assessment_method": method,
            "endpoint_def": endpoint_def,
            "trial_key": self.trial_key,
            "notes": notes,
        }

    def _cutoff_source_page(self) -> int:
        for page in self.pages:
            if cutoff_near(page.norm):
                return page.page_no
        return 1

    def _method_source_page(self) -> int:
        for page in self.pages:
            if detect_method(page.norm):
                return page.page_no
        return 1


def build_document_context(
    document: dict,
    pages: Sequence[dict],
    *,
    project_trial_key: str | None = None,
    project_study_id: str | None = None,
) -> DocumentContext:
    """Build the per-document context used by rules/table extractors."""
    page_ctxs: list[PageContext] = []
    for idx, page in enumerate(pages):
        text = str(page.get("text") or "")
        norm = normalize_ws(text)
        try:
            page_no = int(page.get("page_no") or idx + 1)
        except (TypeError, ValueError):
            page_no = idx + 1
        page_ctxs.append(
            PageContext(
                page_no=page_no,
                text=text,
                norm=norm,
                segments=split_sentences(norm),
                blocks=list(page.get("blocks") or []),
            )
        )
    page_ctxs.sort(key=lambda p: p.page_no)
    all_norm = " ".join(p.norm for p in page_ctxs)

    methods = detect_methods(all_norm)
    method_default = next(iter(methods)) if len(methods) == 1 else None

    cutoff_values: list[str] = []
    for page in page_ctxs:
        for value in find_cutoffs(page.norm):
            if value not in cutoff_values:
                cutoff_values.append(value)
    cutoff_default = cutoff_values[0] if len(cutoff_values) == 1 else None

    safety_population = (
        "SAFETY" if re.search(r"\bsafety\s+(?:population|set|analysis\s+set)\b", all_norm, re.I) else None
    )
    multi_arm = bool(_MULTI_ARM_RE.search(all_norm))

    ncts: list[str] = []
    study_ids: list[str] = []
    for page in page_ctxs:
        ncts.extend(detect_ncts(page.norm))
        study_ids.extend(detect_study_ids(page.norm))
    doc_trial_key = _most_common(ncts) or project_trial_key
    study_id = _most_common(study_ids) or project_study_id

    population_starts: dict[int, str | None] = {}
    population_declarations: dict[int, list[tuple[int, str]]] = {}
    current_population: str | None = None
    for page in page_ctxs:
        population_starts[page.page_no] = current_population
        declarations: list[tuple[int, str]] = []
        for seg_index, segment in enumerate(page.segments):
            for pattern in _POP_DECLARATION_PATTERNS:
                for match in pattern.finditer(segment.text):
                    raw = match.group(1).upper()
                    canonical = _POP_CANONICAL.get(raw)
                    if canonical and canonical != current_population:
                        current_population = canonical
                        declarations.append((seg_index, canonical))
                    elif canonical and canonical != current_population:
                        current_population = canonical
        if declarations:
            population_declarations[page.page_no] = declarations

    return DocumentContext(
        document_id=str(document.get("document_id") or ""),
        filename=str(document.get("filename") or ""),
        doc_type=str(document.get("doc_type") or "OTHER"),
        pages=page_ctxs,
        trial_key=doc_trial_key,
        study_id=study_id,
        method_default=method_default,
        cutoff_default=cutoff_default,
        safety_population=safety_population,
        multi_arm=multi_arm,
        population_starts=population_starts,
        population_declarations=population_declarations,
    )


def page_texts(pages: Sequence[dict]) -> dict[int, str]:
    out: dict[int, str] = {}
    for idx, page in enumerate(pages):
        try:
            page_no = int(page.get("page_no") or idx + 1)
        except (TypeError, ValueError):
            page_no = idx + 1
        out[page_no] = str(page.get("text") or "")
    return out


def iter_blocks(pages: Sequence[dict]) -> Iterable[tuple[int, dict]]:
    for page in pages:
        try:
            page_no = int(page.get("page_no") or 1)
        except (TypeError, ValueError):
            page_no = 1
        for block in page.get("blocks") or []:
            yield page_no, block


__all__ = [
    "ARM_PATTERNS",
    "DATE_RE",
    "DocumentContext",
    "DraftCollector",
    "Segment",
    "PageContext",
    "build_document_context",
    "build_quote",
    "canonical_endpoint_def",
    "cutoff_near",
    "detect_method",
    "detect_methods",
    "detect_ncts",
    "detect_study_ids",
    "field_category",
    "find_cutoffs",
    "injection_flags_in",
    "is_injected",
    "make_draft",
    "nearest_arm",
    "nearest_population",
    "normalize_ws",
    "page_texts",
    "split_sentences",
]
