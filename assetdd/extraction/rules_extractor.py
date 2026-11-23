"""Deterministic rule-based clinical evidence extraction (offline default).

The extractor walks normalised page text sentence by sentence and emits
``EvidenceDraft`` objects.  It never writes to the database and never invents a
fact: a value is only emitted when a clinical label and its numeric/text value
occur in the same sentence (or the same table row, delegated to
``table_extractor``).

Critical invariants enforced here
---------------------------------
* ``quote`` is a contiguous slice of the normalised page text, hence verbatim;
* population / arm / data cutoff / assessment method are resolved from the same
  sentence first; approved per-document fallbacks are recorded in ``notes``;
* prompt-injection sentences are skipped (document text is data, not orders);
* exactly one fact per (field, value, dims, page) is emitted -- boilerplate
  repetitions of the same quote are dropped by ``DraftCollector``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from assetdd.extraction.common import (
    DocumentContext,
    DraftCollector,
    PageContext,
    Segment,
    build_quote,
    cutoff_near,
    detect_method,
    detect_methods,
    injection_flags_in,
    make_draft,
    nearest_population,
    normalize_ws,
)
from assetdd.schemas import EvidenceDraft, FieldName

# ---------------------------------------------------------------------------
# Numeric value tokenisers
# ---------------------------------------------------------------------------
PCT_RE = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*%")
MONTHS_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(months?|mo|weeks?|wks?|days?)\b", re.IGNORECASE)
RATIO_RE = re.compile(r"(\d(?:\.\d+)?)\b")
CI_RE = re.compile(
    r"(?:95\s*%\s*(?:CI|confidence\s+interval)|CI)\s*(?:,|:|=|was|were|is)?\s*"
    r"(\d{1,3}(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d{1,3}(?:\.\d+)?)",
    re.IGNORECASE,
)
P_VALUE_RE = re.compile(r"\bp\s*(?:=|==|<|>|≤|≥|<=|>=)\s*(\d*\.?\d+)", re.IGNORECASE)


def _unit_for_time(raw: str) -> str:
    raw = raw.lower()
    if raw.startswith(("mo",)):
        return "months"
    if raw.startswith(("w",)):
        return "weeks"
    return "days"


def _pct_tokens(text: str) -> int:
    return len(PCT_RE.findall(text))


def _looks_like_ae_table_dump(text: str) -> bool:
    """Guard: a flattened multi-column AE table must not yield safety metrics.

    Pattern: an "any grade" column header followed by a "grade >=3" header in the
    same segment plus several percentages -- i.e. interleaved table columns that
    the sentence-level extractor cannot attribute reliably.  The table extractor
    handles these rows instead.
    """
    if _pct_tokens(text) < 3:
        return False
    return bool(
        re.search(
            r"any[-\s]?grade[^.;]{0,40}?grade\s*(?:>=|≥|>)?\s*(?:3|III)\b",
            text,
            re.IGNORECASE,
        )
    )


# ---------------------------------------------------------------------------
# Metric specifications (efficacy + safety)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MetricSpec:
    field_name: FieldName
    pattern: re.Pattern[str]
    kind: str  # "percent" | "months" | "ratio"
    context: re.Pattern[str] | None = None
    short: bool = False
    allow_backward: bool = False
    endpoint_token: str | None = None


_RESPONSE_CONTEXT = re.compile(r"response|rate|tumou?r|disease|evaluat|recist|progress", re.IGNORECASE)
_RATE_CONTEXT_FIELDS = {FieldName.ANY_AE, FieldName.GRADE3_AE, FieldName.SAE, FieldName.TREATMENT_RELATED_AE}
_SAFETY_RATE_PHRASE_RE = re.compile(
    r"\b(?:reported\s+in|occurred\s+in|observed\s+in|rate\s+(?:was|of)|"
    r"in\s+\d+\s+(?:patients|subjects)|of\s+(?:patients|subjects))\b",
    re.IGNORECASE,
)

METRIC_SPECS: tuple[MetricSpec, ...] = (
    # -- efficacy ---------------------------------------------------------
    MetricSpec(FieldName.ORR, re.compile(r"(?:\bobjective\s+response\s+rate\b|\boverall\s+response\s+rate\b|\bORR\b)"), "percent", endpoint_token="ORR"),
    MetricSpec(FieldName.DCR, re.compile(r"(?:\bdisease\s+control\s+rate\b|\bDCR\b)"), "percent", endpoint_token="DCR"),
    MetricSpec(FieldName.CR, re.compile(r"(?:\bcomplete\s+response\b|\bCR\b)"), "percent", context=_RESPONSE_CONTEXT, short=True, endpoint_token="CR"),
    MetricSpec(FieldName.PR, re.compile(r"(?:\bpartial\s+response\b|\bPR\b)"), "percent", context=_RESPONSE_CONTEXT, short=True, endpoint_token="PR"),
    MetricSpec(FieldName.SD, re.compile(r"(?:\bstable\s+disease\b|\bSD\b)"), "percent", context=_RESPONSE_CONTEXT, short=True, endpoint_token="SD"),
    MetricSpec(FieldName.PD, re.compile(r"(?:\bprogressive\s+disease\b|\bPD\b(?![- ]?L))"), "percent", context=_RESPONSE_CONTEXT, short=True, endpoint_token="PD"),
    MetricSpec(FieldName.DOR, re.compile(r"(?:\bduration\s+of\s+response\b|\bDoR\b|\bDOR\b)"), "months", endpoint_token="DOR"),
    MetricSpec(FieldName.PFS, re.compile(r"(?:\bprogression[-\s]free\s+survival\b|\bPFS\b)"), "months", endpoint_token="PFS"),
    MetricSpec(FieldName.OS, re.compile(r"(?:\boverall\s+survival\b|\bOS\b)"), "months", endpoint_token="OS"),
    MetricSpec(FieldName.FOLLOW_UP, re.compile(r"\b(?:median\s+)?follow[-\s]up\b"), "months", endpoint_token="FOLLOW_UP"),
    MetricSpec(FieldName.HR, re.compile(r"(?:\bhazard\s+ratio\b|\bHR\b)"), "ratio", endpoint_token="HR"),
    # -- safety -----------------------------------------------------------
    MetricSpec(
        FieldName.ANY_AE,
        re.compile(
            r"(?:\bany[-\s]grade\s+(?:adverse\s+events?|AEs?|TEAEs?)\b"
            r"|\b(?:adverse\s+events?|AEs?)\s+of\s+any\s+grade\b"
            r"|\btreatment[-\s]emergent\s+(?:adverse\s+events?|AEs?|TEAEs?)\b"
            r"|\bTEAEs?\b)"
        ),
        "percent",
        endpoint_token="ANY_AE",
    ),
    MetricSpec(
        FieldName.GRADE3_AE,
        re.compile(
            r"(?:\bgrade\s*(?:>=|≥|>|=)?\s*(?:3|III)\s*(?:or\s+(?:higher|greater))?\s*(?:adverse\s+events?|AEs?|TEAEs?)\b"
            r"|\b(?:adverse\s+events?|AEs?|TEAEs?)\s+(?:of\s+)?(?:CTCAE\s+)?(?:grade\s*)?(?:>=|≥)?\s*3\s*(?:or\s+(?:higher|greater))?\+?\b"
            r"|\b(?:>=|≥)\s*(?:CTCAE\s+)?grade\s*(?:3|III)\b)"
        ),
        "percent",
        endpoint_token="GRADE3_AE",
    ),
    MetricSpec(
        FieldName.SAE,
        re.compile(r"(?:\bserious\s+adverse\s+events?\b|\bserious\s+AEs?\b|\bSAEs?\b)"),
        "percent",
        endpoint_token="SAE",
    ),
    MetricSpec(
        FieldName.TREATMENT_RELATED_AE,
        re.compile(
            r"(?:\btreatment[-\s]related\s+(?:adverse\s+events?|AEs?)\b"
            r"|\bdrug[-\s]related\s+(?:adverse\s+events?|AEs?)\b|\bTRAEs?\b)"
        ),
        "percent",
        endpoint_token="TREATMENT_RELATED_AE",
    ),
    MetricSpec(
        FieldName.TREATMENT_RELATED_DEATH,
        re.compile(
            r"(?:\btreatment[-\s]related\s+deaths?\b"
            r"|\bfatal\s+(?:adverse\s+event|AE)s?\b"
            r"|\bdeaths?\s+(?:related\s+to|due\s+to)\s+(?:study\s+drug|study\s+treatment|treatment)\b)"
        ),
        "percent",
        endpoint_token="TREATMENT_RELATED_DEATH",
    ),
    MetricSpec(FieldName.DOSE_REDUCTION, re.compile(r"\bdose\s+reductions?\b"), "percent", endpoint_token="DOSE_REDUCTION"),
    MetricSpec(FieldName.DOSE_INTERRUPTION, re.compile(r"\bdose\s+interruptions?\b"), "percent", endpoint_token="DOSE_INTERRUPTION"),
    MetricSpec(
        FieldName.DISCONTINUATION,
        re.compile(
            r"(?:\b(?:treatment\s+)?discontinuations?\b[^.;]{0,45}?(?:adverse|AEs?|toxicit)"
            r"|\bdiscontinuation\s+of\s+(?:study\s+)?treatment\b[^.;]{0,45}?(?:adverse|AEs?|toxicit)"
            r"|\bdiscontinu(?:ed|ation)\b[^.;]{0,30}?(?:due\s+to|because\s+of)\s+(?:adverse|AEs?|toxicit))"
        ),
        "percent",
        allow_backward=True,
        endpoint_token="DISCONTINUATION",
    ),
    MetricSpec(
        FieldName.AESI,
        re.compile(r"(?:\badverse\s+events?\s+of\s+special\s+interest\b|\bAESIs?\b)"),
        "percent",
        endpoint_token="AESI",
    ),
)


@dataclass
class Occurrence:
    start: int
    end: int
    spec: MetricSpec


@dataclass
class MetricHit:
    field_name: FieldName
    value_num: float
    unit: str
    label_start: int
    label_end: int
    value_start: int
    value_end: int
    endpoint_token: str


_LINK_TEXT_RE = re.compile(r"^\s*[\(\[]?\s*(?:and|or|/|&|\+|,)?\s*[\)\]]?\s*$")
_SHORT_RESPONSE_TOKENS = re.compile(r"\b(?:CR|PR|SD|PD)\b")


def _is_definitional(text: str, occurrence: Occurrence) -> bool:
    """Detect endpoint abbreviations used inside a definition/conjunction list."""
    spec = occurrence.spec
    before = text[max(0, occurrence.start - 120) : occurrence.start].lower()
    if re.search(r"(?:defined|defines|refers|means|i\.e\.|that is)\b[^.;]{0,60}$", before):
        return True
    if spec.short:
        after = text[occurrence.end : occurrence.end + 18]
        if re.match(r"\s*(?:or|and|/|,|\(|\+)\s*(?:CR|PR|SD|PD)\b", after):
            return True
        before_tail = text[max(0, occurrence.start - 18) : occurrence.start]
        if re.search(r"\b(?:CR|PR|SD|PD)\s*(?:or|and|/|,|\+)\s*$", before_tail):
            return True
    return False


def _claim_values(text: str, start: int, end: int, kind: str) -> list[tuple[float, int, int, str]]:
    """Return plausible values of *kind* in ``text[start:end]`` as (value, s, e, unit)."""
    values: list[tuple[float, int, int, str]] = []
    if end <= start:
        return values
    if kind == "percent":
        for match in PCT_RE.finditer(text, start, end):
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if 0.0 <= value <= 100.0:
                values.append((value, match.start(), match.end(), "%"))
    elif kind == "months":
        for match in MONTHS_RE.finditer(text, start, end):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if 0.0 < value <= 600.0:
                values.append((value, match.start(), match.end(), _unit_for_time(match.group(2))))
    elif kind == "ratio":
        for match in RATIO_RE.finditer(text, start, end):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if 0.0 < value <= 10.0:
                values.append((value, match.start(), match.end(), "ratio"))
    return values


def _group_occurrences(text: str, occurrences: Sequence[Occurrence]) -> list[list[Occurrence]]:
    if not occurrences:
        return []
    groups: list[list[Occurrence]] = []
    current: list[Occurrence] = [occurrences[0]]
    for occurrence in occurrences[1:]:
        between = text[current[-1].end : occurrence.start]
        if len(between) <= 16 and _LINK_TEXT_RE.match(between):
            current.append(occurrence)
        else:
            groups.append(current)
            current = [occurrence]
    groups.append(current)
    return groups


def iter_metric_hits(text: str, *, safety_allowed: bool = True) -> list[MetricHit]:
    """Extract labelled metric values from one sentence (deterministic order)."""
    occurrences: list[Occurrence] = []
    seen_spans: set[tuple[int, int]] = set()
    for spec in METRIC_SPECS:
        if not safety_allowed and spec.field_name in _SAFETY_FIELDS:
            continue
        for match in spec.pattern.finditer(text):
            if spec.context and not spec.context.search(text):
                continue
            if _is_definitional(text, Occurrence(match.start(), match.end(), spec)):
                continue
            span = (match.start(), match.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)
            occurrences.append(Occurrence(match.start(), match.end(), spec))
    if not occurrences:
        return []
    occurrences.sort(key=lambda item: (item.start, -(item.end - item.start)))

    groups = _group_occurrences(text, occurrences)
    hits: list[MetricHit] = []
    for index, group in enumerate(groups):
        window_end = groups[index + 1][0].start if index + 1 < len(groups) else len(text)
        window_start = group[-1].end
        kinds = {occurrence.spec.kind for occurrence in group}
        if len(kinds) > 1:
            # Mixed units in one linked list: attribute per label with a tight window.
            for occurrence in group:
                local_end = min(window_end, occurrence.end + 120)
                values = _claim_values(text, occurrence.end, local_end, occurrence.spec.kind)
                if values:
                    value, value_start, value_end, unit = values[0]
                    hits.append(
                        MetricHit(
                            field_name=occurrence.spec.field_name,
                            value_num=value,
                            unit=unit,
                            label_start=occurrence.start,
                            label_end=occurrence.end,
                            value_start=value_start,
                            value_end=value_end,
                            endpoint_token=occurrence.spec.endpoint_token or str(occurrence.spec.field_name).upper(),
                        )
                    )
            continue
        kind = group[0].spec.kind
        unique: list[Occurrence] = []
        seen_fields: set[FieldName] = set()
        for occurrence in group:
            if occurrence.spec.field_name in seen_fields:
                continue
            seen_fields.add(occurrence.spec.field_name)
            unique.append(occurrence)
        # Keep the *last* occurrence of a same-field linked pair (e.g. "objective
        # response rate (ORR)" -> the short label is closest to the value).
        last_by_field: dict[FieldName, Occurrence] = {}
        for occurrence in group:
            last_by_field[occurrence.spec.field_name] = occurrence
        unique = list(last_by_field.values())
        unique.sort(key=lambda item: item.start)
        if not unique:
            continue
        window_end = min(window_end, window_start + 220)
        values = _claim_values(text, window_start, window_end, kind)
        assignments: list[tuple[Occurrence, tuple[float, int, int, str]]] = []
        if len(values) >= len(unique):
            assignments = list(zip(unique, values))
        elif len(unique) == 1 and values:
            assignments = [(unique[0], values[0])]
        elif len(unique) == 1 and not values and unique[0].spec.allow_backward:
            backward = _claim_values(text, max(0, unique[0].start - 40), unique[0].start, kind)
            if backward:
                assignments = [(unique[0], backward[-1])]
        for occurrence, (value, value_start, value_end, unit) in assignments:
            hits.append(
                MetricHit(
                    field_name=occurrence.spec.field_name,
                    value_num=value,
                    unit=unit,
                    label_start=occurrence.start,
                    label_end=occurrence.end,
                    value_start=value_start,
                    value_end=value_end,
                    endpoint_token=occurrence.spec.endpoint_token or str(occurrence.spec.field_name).upper(),
                )
            )
    hits.sort(key=lambda hit: hit.label_start)
    # Guard: a rate whose tail lists several further percentages is a per-term
    # event list ("pneumonia (4.0%), anaemia (3.2%), ..."), not the overall rate.
    filtered: list[MetricHit] = []
    for hit in hits:
        next_start = len(text)
        for occurrence in occurrences:
            if occurrence.start > hit.label_start:
                next_start = occurrence.start
                break
        tail = text[hit.value_end : next_start]
        if hit.unit == "%" and _non_ci_percent_count(tail) >= 2:
            continue
        filtered.append(hit)
    return filtered


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

# ---------------------------------------------------------------------------
# Study-design / statistics text detectors
# ---------------------------------------------------------------------------
_NCT_RE = re.compile(r"\bNCT\s*0*\d{4,8}\b", re.IGNORECASE)
_PHASE_RE = re.compile(r"\b(?:phase|ph\.?)\s*(1/2|2/3|1b/2|1b|2a|2b|IV|III|II|I|4|3|2|1)\b", re.IGNORECASE)
_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4"}

_METHOD_PATTERNS_LOCAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\birecist\b", re.I), "iRECIST"),
    (re.compile(r"\brecist\s*(?:v(?:ersion)?\.?\s*)?1\.1\b", re.I), "RECIST 1.1"),
    (re.compile(r"\brecist\s*(?:v(?:ersion)?\.?\s*)?1\.0\b", re.I), "RECIST 1.0"),
    (re.compile(r"\blugano\b", re.I), "Lugano"),
    (re.compile(r"\bchoi\b", re.I), "Choi"),
    (re.compile(r"\brano\b", re.I), "RANO"),
)

_INDICATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bindication(?:s)?\s*[:=]\s*([A-Za-z][^.;|]{3,90})", re.I),
    re.compile(
        r"\b(?:in|for)\s+(?:the\s+)?(?:treatment\s+of\s+)?(?:patients|subjects|adults|participants|people)?\s*with\s+"
        r"([A-Za-z][A-Za-z0-9 ,\-/'()]{3,80}?)"
        r"(?=\s+(?:who|whose|after|previously|refractory|harbouring|harboring|and|treated|were|was|Study|Protocol|"
        r"Sponsor|Report|Data|Edition|Version|NCT)\b|[.;]|$)",
        re.I,
    ),
    re.compile(r"\b(?:study|trial)\s+in\s+([A-Za-z][A-Za-z0-9 ,\-/'()]{3,80}?)(?=[.;]|$)", re.I),
)

_DESIGN_KEYWORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brandomi[sz]ed\b", re.I), "randomized"),
    (re.compile(r"\bdouble[-\s]blind\b", re.I), "double-blind"),
    (re.compile(r"\bsingle[-\s]blind\b", re.I), "single-blind"),
    (re.compile(r"\bopen[-\s]label\b", re.I), "open-label"),
    (re.compile(r"\bsingle[-\s]arm\b", re.I), "single-arm"),
    (re.compile(r"\bplacebo[-\s]controlled\b", re.I), "placebo-controlled"),
    (re.compile(r"\bmulti[-\s]cent(?:er|re)\b", re.I), "multicenter"),
    (re.compile(r"\bdose[-\s]escalation\b", re.I), "dose-escalation"),
    (re.compile(r"\bdose[-\s]expansion\b", re.I), "dose-expansion"),
    (re.compile(r"\badaptive\b", re.I), "adaptive"),
    (re.compile(r"\bcrossover\b", re.I), "crossover"),
    (re.compile(r"\bfirst[-\s]in[-\s]human\b", re.I), "first-in-human"),
)
_BLINDING_RE = re.compile(r"\b(double[-\s]blind|single[-\s]blind|triple[-\s]blind|open[-\s]label|unblinded)\b", re.I)
_RANDOMIZATION_RE = re.compile(r"\brandomi[sz]ed\s+(?:in\s+a\s+)?(\d+:\d+)\b", re.I)
_COMPARATOR_RE = re.compile(
    r"(?:\bcomparator\s*[:=]\s*|\bcompared\s+(?:with|to)\s+|\bversus\s+|\bvs\.?\s+)"
    r"([A-Za-z][A-Za-z0-9][^.;,]{2,40})",
    re.I,
)

_PRIMARY_ENDPOINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bprimary\s+(?:efficacy\s+)?(?:end\s?points?|objectives?|outcomes?)\s*(?:is|was|were|are|:|=)\s*([^.;]{3,200})", re.I),
    re.compile(r"\bprimary\s+(?:efficacy\s+)?end\s?point\s+of\s+([^.;]{3,200})", re.I),
)
_SECONDARY_ENDPOINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsecondary\s+(?:efficacy\s+)?(?:end\s?points?|objectives?|outcomes?)\s*(?:is|was|were|are|include[ds]?|:|=)\s*([^.;]{3,220})", re.I),
)

_ENDPOINT_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bobjective\s+response\s+rate\b|\boverall\s+response\s+rate\b|\bORR\b", re.I), "ORR"),
    (re.compile(r"\bdisease\s+control\s+rate\b|\bDCR\b", re.I), "DCR"),
    (re.compile(r"\bduration\s+of\s+response\b|\bDoR\b|\bDOR\b", re.I), "DOR"),
    (re.compile(r"\bprogression[-\s]free\s+survival\b|\bPFS\b", re.I), "PFS"),
    (re.compile(r"\boverall\s+survival\b|\bOS\b", re.I), "OS"),
    (re.compile(r"\bcomplete\s+response\b|\bCR\b", re.I), "CR"),
    (re.compile(r"\bpartial\s+response\b|\bPR\b", re.I), "PR"),
    (re.compile(r"\bstable\s+disease\b|\bSD\b", re.I), "SD"),
    (re.compile(r"\bprogressive\s+disease\b|\bPD\b", re.I), "PD"),
    (re.compile(r"\bhazard\s+ratio\b|\bHR\b", re.I), "HR"),
    (re.compile(r"\btime\s+to\s+response\b|\bTTR\b", re.I), "TTR"),
)

_SAMPLE_SIZE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:a\s+)?total\s+of\s+(\d{2,5})\s+(?:patients|subjects|participants)\s+were\s+(?:enrolled|randomi[sz]ed|treated|included)", re.I),
    re.compile(r"\b(\d{2,5})\s+(?:patients|subjects|participants)\s+were\s+(?:enrolled|randomi[sz]ed|randomised|treated|included)", re.I),
    re.compile(r"\b(?:enrolled|randomi[sz]ed|included|treated)\s+(?:a\s+total\s+of\s+)?(\d{2,5})\s+(?:patients|subjects|participants)", re.I),
    re.compile(r"\b(?:planned|target(?:ed)?|actual)\s+(?:sample\s+size|enrollment|enrolment)\s*(?:of|was|is|:)?\s*(\d{2,5})", re.I),
    re.compile(r"\b(?:sample\s+size)\s*(?:of|was|is|:)?\s*(\d{2,5})", re.I),
)

_SET_SIZE_SPECS: tuple[tuple[FieldName, tuple[re.Pattern[str], ...]], ...] = (
    (
        FieldName.N_ITT,
        (
            re.compile(r"\bITT\s+(?:population|set|analysis\s+set)[^.;]{0,50}?\bn\s*=\s*(\d{2,5})", re.I),
            re.compile(r"\bn\s*=\s*(\d{2,5})[^.;]{0,60}?\bITT\b", re.I),
            re.compile(r"\bthe\s+ITT\s+population\s+(?:comprised|included|consisted\s+of)\s+(\d{2,5})\s+(?:patients|subjects)", re.I),
        ),
    ),
    (
        FieldName.N_SAFETY,
        (
            re.compile(r"\bsafety\s+(?:population|set|analysis\s+set)[^.;]{0,50}?\bn\s*=\s*(\d{2,5})", re.I),
            re.compile(r"\bn\s*=\s*(\d{2,5})[^.;]{0,60}?\bsafety\b", re.I),
            re.compile(r"\bthe\s+safety\s+population\s+(?:comprised|included|consisted\s+of)\s+(\d{2,5})\s+(?:patients|subjects)", re.I),
        ),
    ),
    (
        FieldName.N_FAS,
        (
            re.compile(r"\bFAS\s+(?:population|set|analysis\s+set)[^.;]{0,50}?\bn\s*=\s*(\d{2,5})", re.I),
            re.compile(r"\bn\s*=\s*(\d{2,5})[^.;]{0,60}?\bFAS\b", re.I),
            re.compile(r"\bthe\s+FAS\s+population\s+(?:comprised|included|consisted\s+of)\s+(\d{2,5})\s+(?:patients|subjects)", re.I),
        ),
    ),
    (
        FieldName.N_PP,
        (
            re.compile(r"\b(?:per[-\s]protocol|PP)\s+(?:population|set|analysis\s+set)[^.;]{0,50}?\bn\s*=\s*(\d{2,5})", re.I),
            re.compile(r"\bthe\s+(?:per[-\s]protocol|PP)\s+population\s+(?:comprised|included|consisted\s+of)\s+(\d{2,5})\s+(?:patients|subjects)", re.I),
        ),
    ),
)

_ASSUMPTION_RE = re.compile(
    r"\b(?:null\s+hypothesis|null\s+orr|alternative\s+hypothesis|hypothesi[sz]ed|assumed|assume|"
    r"power(?:ed)?\s+(?:to|of|calculation|assumption)|designed\s+to\s+detect|"
    r"target(?:ed)?\s+(?:orr|response|effect)|sample[\s-]size\s+(?:calculation|assumption))\b",
    re.I,
)
_PHASE_FORBIDDEN_RE = re.compile(r"\b(?:prior|previous|planned|planning|required|would\s+be|experience|other)\b", re.I)
_AGE_RE = re.compile(r"\bmedian\s+age\s+(?:was|of)\s*(\d{1,3}(?:\.\d+)?)\s*(?:years|yrs)", re.I)
_MALE_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:of\s+(?:all\s+)?(?:patients|subjects)\s+)?(?:were\s+|was\s+)?male\b", re.I)

_SAP_VERSION_RE = re.compile(r"\b(?:SAP|statistical\s+analysis\s+plan)\b[^.;]{0,60}?\bversion\s*(\d+(?:\.\d+)*)", re.I)
_MISSING_DATA_RE = re.compile(
    r"\b(?:multiple\s+imputation|mixed[-\s]effect\s+model\s+for\s+repeated\s+measures|MMRM|"
    r"last\s+observation\s+carried\s+forward|LOCF|non[-\s]?responder\s+imputation|NRI|"
    r"complete[-\s]case\s+analysis|worst[-\s]case\s+(?:imputation|scenario)|"
    r"no\s+imputation|imputation)\b",
    re.I,
)
_MULTIPLICITY_RE = re.compile(
    r"\b(multiplicity(?:\s+(?:adjustment|control))?|hierarchical\s+(?:testing|test\s+procedure)|"
    r"alpha[-\s]spending|gatekeeping|Bonferroni|Hochberg|graphical\s+approach|"
    r"fixed[-\s]sequence\s+testing|type\s+I\s+error)\b",
    re.I,
)
_MULTIPLICITY_NEGATED_RE = re.compile(r"\b(?:no|not|without)\b[^.;]{0,40}?(?:multiplicity|adjustment)", re.I)
_ALPHA_RE = re.compile(
    r"((?:one|two)[-\s]sided\s+alpha)[^.;]{0,20}?(0?\.\d+)", re.IGNORECASE
)
_SUBGROUP_RE = re.compile(r"\bsubgroup\s+analys[ie]s\b\s+(?:are|were|is|was)\s+([^.;]{3,170})", re.I)
_DEVIATION_RE = re.compile(r"\b(?:a\s+)?total\s+of\s+(\d{1,5})\s+(major\s+)?protocol\s+(?:deviations?|violations?)\b", re.I)
_DEVIATION_ALT_RE = re.compile(r"\b(\d{1,5})\s+(major\s+)?protocol\s+(?:deviations?|violations?)\s+(?:were|was)\s+(?:reported|identified)", re.I)
_RESPONSE_CRITERIA_ASSESSOR_RE = re.compile(
    r"\b(BICR|blinded\s+independent\s+central\s+review|INV|investigator(?:-assessed)?)\b", re.I
)


def canonical_endpoint_phrase(text: str, method: str | None) -> str | None:
    """Canonicalise free-text endpoint prose to e.g. ``ORR per RECIST 1.1``."""
    text = re.split(r"\bdefined\s+as\b", text, maxsplit=1, flags=re.IGNORECASE)[0]
    tokens: list[tuple[int, str]] = []
    for pattern, token in _ENDPOINT_ALIASES:
        match = pattern.search(text)
        if match and all(token != existing for _, existing in tokens):
            tokens.append((match.start(), token))
    tokens.sort(key=lambda item: item[0])
    names = [token for _, token in tokens]
    if not names:
        return None
    if len(names) == 1:
        return f"{names[0]} per {method}" if method else names[0]
    return ", ".join(names)


def _phase_context_ok(text: str, match: re.Match[str]) -> bool:
    head = text[max(0, match.start() - 50) : match.start()]
    tail = text[match.end() : match.end() + 60]
    if _PHASE_FORBIDDEN_RE.search(head[-50:]) or _PHASE_FORBIDDEN_RE.search(tail[:40]):
        return False
    return bool(re.search(r"\b(?:study|trial|program|programme|portion|part|design)\b", head + tail, re.I))


def _clean_indication(raw: str) -> str | None:
    value = normalize_ws(raw).strip(" .,;:-")
    value = re.split(
        r",?\s+(?:with|and|who|whose|treated|after|previously|refractory|harbouring|harboring|in\s+that)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .,;:-")
    lowered = value.lower()
    if len(value) < 4 or len(value) > 90:
        return None
    if re.search(r"\d|%|median|months|study|trial|cohort", lowered):
        return None
    for bad in (
        "this study",
        "the study",
        "accordance",
        "order to",
        "all patients",
        "patients were",
        "patients who",
        "combination with",
        "the protocol",
    ):
        if bad in lowered:
            return None
    if lowered.endswith((" and", " with", " or", " the")):
        return None
    return value


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------
class RulesExtractor:
    """Sentence-level deterministic extractor for one parsed document."""

    def __init__(self, context: DocumentContext) -> None:
        self.context = context
        self.collector = DraftCollector()
        self.injection_flags: list[dict] = []
        self.skipped_injected_segments = 0

    # -- public API ---------------------------------------------------------
    def extract(self) -> list[EvidenceDraft]:
        for page in self.context.pages:
            for segment_index, segment in enumerate(page.segments):
                flags = injection_flags_in(segment.text)
                if flags:
                    self.skipped_injected_segments += 1
                    for flag in flags:
                        self.injection_flags.append(
                            {
                                "page": page.page_no,
                                "pattern": flag,
                                "excerpt": self._injection_excerpt(page, segment),
                            }
                        )
                    continue
                self._extract_segment(page, segment_index, segment)
        return self.collector.drafts()

    # -- emitter ------------------------------------------------------------
    def _emit(
        self,
        page: PageContext,
        segment_index: int,
        segment: Segment,
        field_name: FieldName,
        *,
        value_num: float | None = None,
        value_text: str | None = None,
        unit: str | None = None,
        span: tuple[int, int],
        endpoint_token: str | None = None,
        confidence: float = 0.8,
        override: dict | None = None,
    ) -> None:
        span_start = max(0, min(span[0], len(segment.text)))
        span_end = max(span_start, min(span[1], len(segment.text)))
        absolute_start = segment.start + span_start
        absolute_end = segment.start + span_end
        quote = build_quote(page.norm, absolute_start, absolute_end, segment=segment)
        if not quote:
            return
        dims = self.context.resolve(
            page_no=page.page_no,
            segment_index=segment_index,
            segment=segment,
            field_name=field_name,
            span=(span_start, span_end),
            endpoint_token=endpoint_token,
        )
        if override:
            dims.update(override)
        draft = make_draft(
            field_name,
            page=page.page_no,
            quote=quote,
            value_num=value_num,
            value_text=value_text,
            unit=unit,
            population=dims.get("population"),
            arm=dims.get("arm"),
            trial_key=dims.get("trial_key"),
            data_cutoff=dims.get("data_cutoff"),
            endpoint_def=dims.get("endpoint_def"),
            assessment_method=dims.get("assessment_method"),
            confidence=confidence,
            notes=dims.get("notes"),
        )
        self.collector.add(draft)

    @staticmethod
    def _injection_excerpt(page: PageContext, segment: Segment) -> str:
        pad = 120
        lo = max(0, segment.start - pad)
        hi = min(len(page.norm), segment.end + pad)
        return page.norm[lo:hi].strip()

    # -- per-sentence extraction -------------------------------------------
    def _extract_segment(self, page: PageContext, segment_index: int, segment: Segment) -> None:
        text = segment.text
        if not text:
            return
        safety_allowed = not _looks_like_ae_table_dump(text)
        if not _ASSUMPTION_RE.search(text):
            self._extract_metrics(page, segment_index, segment, safety_allowed=safety_allowed)
        self._extract_study_design(page, segment_index, segment)
        self._extract_sizes(page, segment_index, segment)
        self._extract_statistics(page, segment_index, segment)

    # -- efficacy / safety metrics + CI/p --------------------------------
    def _extract_metrics(
        self, page: PageContext, segment_index: int, segment: Segment, *, safety_allowed: bool
    ) -> None:
        hits = iter_metric_hits(segment.text, safety_allowed=safety_allowed)
        for hit in hits:
            if hit.field_name in _RATE_CONTEXT_FIELDS and not _SAFETY_RATE_PHRASE_RE.search(segment.text):
                # "serious adverse events were pneumonia (4.0%)" is a per-term
                # list, not an overall rate: require explicit rate phrasing.
                continue
            category = (
                "safety" if hit.field_name in _SAFETY_FIELDS else "efficacy"
            )
            value_text = None
            if hit.field_name == FieldName.TREATMENT_RELATED_DEATH:
                value_text = None
            self._emit(
                page,
                segment_index,
                segment,
                hit.field_name,
                value_num=hit.value_num,
                value_text=value_text,
                unit=hit.unit,
                span=(hit.label_start, hit.value_end),
                endpoint_token=hit.endpoint_token,
                confidence=0.88 if category == "efficacy" else 0.86,
            )
        if not hits:
            return
        # Associate 95% CI pairs with the nearest preceding metric in the sentence.
        for match in CI_RE.finditer(segment.text):
            owner = _owner_before(hits, match.start())
            if owner is None:
                continue
            try:
                lower = float(match.group(1))
                upper = float(match.group(2))
            except ValueError:
                continue
            if not (0.0 <= lower < upper <= 1000.0):
                continue
            ci_unit = owner.unit if owner.unit in {"%", "months", "weeks", "days"} else "ratio"
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.CI_LOWER,
                value_num=lower,
                unit=ci_unit,
                span=(owner.label_start, match.end()),
                endpoint_token=owner.endpoint_token,
                confidence=0.82,
            )
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.CI_UPPER,
                value_num=upper,
                unit=ci_unit,
                span=(owner.label_start, match.end()),
                endpoint_token=owner.endpoint_token,
                confidence=0.82,
            )
        for match in P_VALUE_RE.finditer(segment.text):
            owner = _owner_before(hits, match.start())
            if owner is None:
                continue
            try:
                p_value = float(match.group(1))
            except ValueError:
                continue
            if not (0.0 <= p_value <= 1.0):
                continue
            operator = segment.text[match.start() : match.end()].split("p")[-1]
            operator = normalize_ws(operator).split("0")[0].strip() if "0" in operator else ""
            value_text = f"{operator}{match.group(1)}".strip() if operator else match.group(1)
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.P_VALUE,
                value_num=p_value,
                value_text=value_text,
                span=(owner.label_start, match.end()),
                endpoint_token=owner.endpoint_token,
                confidence=0.8,
            )

    # -- study design ------------------------------------------------------
    def _extract_study_design(self, page: PageContext, segment_index: int, segment: Segment) -> None:
        text = segment.text

        nct_match = _NCT_RE.search(text)
        if nct_match:
            nct_value = normalize_ws(nct_match.group(0)).upper().replace(" ", "")
            if not nct_value.startswith("NCT"):
                nct_value = "NCT" + nct_value[3:]
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.NCT_ID,
                value_text=nct_value,
                unit="id",
                span=(nct_match.start(), nct_match.end()),
                confidence=0.95,
                override={"trial_key": nct_value},
            )

        phase_match = _PHASE_RE.search(text)
        if phase_match and not _phase_context_ok(text, phase_match):
            phase_match = None
        if phase_match:
            raw = phase_match.group(1).upper()
            number = _ROMAN.get(raw, raw.lower())
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.PHASE,
                value_text=f"Phase {number}",
                unit="phase",
                span=(phase_match.start(), phase_match.end()),
                confidence=0.9,
            )

        for pattern in _INDICATION_PATTERNS:
            if page.page_no > 3:
                break
            match = pattern.search(text)
            if not match:
                continue
            indication = _clean_indication(match.group(1))
            if indication:
                self._emit(
                    page,
                    segment_index,
                    segment,
                    FieldName.INDICATION,
                    value_text=indication,
                    span=(match.start(1), match.end(1)),
                    confidence=0.8,
                )
            break

        design_span = None if _DESIGN_HYPOTHETICAL_RE.search(text) else _design_span(text)
        if design_span:
            design_text = normalize_ws(text[design_span[0] : design_span[1]])
            if design_text:
                self._emit(
                    page,
                    segment_index,
                    segment,
                    FieldName.DESIGN,
                    value_text=design_text,
                    span=design_span,
                    confidence=0.82,
                )

        blinding_match = _BLINDING_RE.search(text)
        if blinding_match:
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.BLINDING,
                value_text=normalize_ws(blinding_match.group(1)).lower(),
                span=(blinding_match.start(), blinding_match.end()),
                confidence=0.8,
            )

        randomization_match = _RANDOMIZATION_RE.search(text)
        if randomization_match:
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.RANDOMIZATION,
                value_text=randomization_match.group(1),
                span=(randomization_match.start(), randomization_match.end()),
                confidence=0.85,
            )

        if self.context.multi_arm:
            arms_match = re.search(
                r"\brandomi[sz]ed\s+(?:in\s+a\s+\d+:\d+\s+fashion\s+)?to\s+(?:receive\s+)?([^.;]{3,160})",
                text,
                re.I,
            )
            if arms_match:
                arms_text = normalize_ws(arms_match.group(1))
                if arms_text:
                    self._emit(
                        page,
                        segment_index,
                        segment,
                        FieldName.ARMS,
                        value_text=arms_text,
                        span=(arms_match.start(1), arms_match.end(1)),
                        confidence=0.75,
                    )

        for pattern in _PRIMARY_ENDPOINT_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            span = _endpoint_phrase_span(text, match.start(1), match.end(1))
            value_text = normalize_ws(text[span[0] : span[1]]).strip(" .,;:") or None
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.PRIMARY_ENDPOINT,
                value_text=value_text,
                span=span,
                confidence=0.85,
            )
            break

        for pattern in _SECONDARY_ENDPOINT_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            span = _endpoint_phrase_span(text, match.start(1), match.end(1), include_all_tokens=True)
            value_text = normalize_ws(text[span[0] : span[1]]).strip(" .,;:") or None
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.SECONDARY_ENDPOINTS,
                value_text=value_text,
                span=span,
                confidence=0.78,
            )
            break

        comparator_match = _COMPARATOR_RE.search(text)
        if comparator_match:
            comparator = normalize_ws(comparator_match.group(1)).strip(" .,;:")
            if comparator and comparator.lower() not in {"baseline", "historical controls", "historical control", "prior therapy"}:
                self._emit(
                    page,
                    segment_index,
                    segment,
                    FieldName.COMPARATOR,
                    value_text=comparator,
                    span=(comparator_match.start(1), comparator_match.end(1)),
                    confidence=0.75,
                )

        cutoff = cutoff_near(text)
        if cutoff:
            cutoff_match = None
            for pattern in (
                re.compile(r"cut[\s-]?off", re.I),
                re.compile(r"as\s+of", re.I),
            ):
                cutoff_match = pattern.search(text)
                if cutoff_match:
                    break
            span = (cutoff_match.start(), cutoff_match.end()) if cutoff_match else (0, len(text))
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.DATA_CUTOFF,
                value_text=cutoff,
                unit="date",
                span=span,
                confidence=0.9,
                override={"data_cutoff": None},
            )

    # -- sample sizes / baseline ------------------------------------------
    def _extract_sizes(self, page: PageContext, segment_index: int, segment: Segment) -> None:
        text = segment.text
        for pattern in _SAMPLE_SIZE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            try:
                size = int(match.group(1))
            except ValueError:
                continue
            if 0 < size <= 1_000_000:
                self._emit(
                    page,
                    segment_index,
                    segment,
                    FieldName.SAMPLE_SIZE,
                    value_num=float(size),
                    value_text=str(size),
                    unit="patients",
                    span=(match.start(1), match.end(1)),
                    confidence=0.85,
                )
            break
        for field_name, patterns in _SET_SIZE_SPECS:
            for pattern in patterns:
                match = pattern.search(text)
                if not match:
                    continue
                try:
                    size = int(match.group(1))
                except ValueError:
                    continue
                if 0 < size <= 1_000_000:
                    self._emit(
                        page,
                        segment_index,
                        segment,
                        field_name,
                        value_num=float(size),
                        value_text=str(size),
                        unit="patients",
                        span=(match.start(1), match.end(1)),
                        confidence=0.85,
                    )
                break

        age_match = _AGE_RE.search(text)
        if age_match:
            try:
                age = float(age_match.group(1))
            except ValueError:
                age = None
            if age is not None and 0 < age < 120:
                self._emit(
                    page,
                    segment_index,
                    segment,
                    FieldName.AGE_MEDIAN,
                    value_num=age,
                    value_text=age_match.group(1),
                    unit="years",
                    span=(age_match.start(1), age_match.end(1)),
                    confidence=0.85,
                )

        male_match = _MALE_RE.search(text)
        if male_match:
            try:
                male = float(male_match.group(1))
            except ValueError:
                male = None
            if male is not None and 0 <= male <= 100:
                self._emit(
                    page,
                    segment_index,
                    segment,
                    FieldName.MALE_PCT,
                    value_num=male,
                    unit="%",
                    span=(male_match.start(1), male_match.end(1)),
                    confidence=0.85,
                )

    # -- statistics / quality ---------------------------------------------
    def _extract_statistics(self, page: PageContext, segment_index: int, segment: Segment) -> None:
        text = segment.text

        methods = detect_methods(text)
        if methods:
            method = detect_method(text) or next(iter(methods))
            assessor_match = _RESPONSE_CRITERIA_ASSESSOR_RE.search(text)
            method_span = _method_span(text)
            if assessor_match and method_span is not None:
                value_text = normalize_ws(text[assessor_match.start() : method_span[1]])
            elif method_span is not None:
                value_text = normalize_ws(text[method_span[0] : method_span[1]])
            else:
                value_text = method
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.RESPONSE_CRITERIA,
                value_text=value_text,
                span=(0, len(text)),
                confidence=0.82,
            )

        sap_match = _SAP_VERSION_RE.search(text)
        if sap_match:
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.SAP_VERSION,
                value_text=f"SAP version {sap_match.group(1)}",
                span=(sap_match.start(), sap_match.end()),
                confidence=0.8,
            )

        missing_match = _MISSING_DATA_RE.search(text)
        if missing_match:
            clause = normalize_ws(text[max(0, missing_match.start() - 40) : missing_match.end() + 40])
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.MISSING_DATA_METHOD,
                value_text=normalize_ws(missing_match.group(0)),
                span=(missing_match.start(), missing_match.end()),
                confidence=0.75,
            )
            _ = clause

        multiplicity_match = _MULTIPLICITY_RE.search(text)
        if multiplicity_match:
            value_text = normalize_ws(multiplicity_match.group(1))
            if _MULTIPLICITY_NEGATED_RE.search(text) or re.search(
                r"\bno\s+multiplicity", text[max(0, multiplicity_match.start() - 25) : multiplicity_match.end() + 30], re.I
            ):
                value_text = "no multiplicity adjustment"
                alpha = _alpha_phrase_near(page, segment_index)
                if alpha:
                    value_text = f"{value_text}; {alpha}"
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.MULTIPLICITY,
                value_text=value_text,
                span=(multiplicity_match.start(), multiplicity_match.end()),
                confidence=0.8,
            )

        subgroup_match = _SUBGROUP_RE.search(text)
        if subgroup_match:
            self._emit(
                page,
                segment_index,
                segment,
                FieldName.SUBGROUP_ANALYSIS,
                value_text=normalize_ws(subgroup_match.group(1)),
                span=(subgroup_match.start(1), subgroup_match.end(1)),
                confidence=0.85,
            )

        deviation_match = _DEVIATION_RE.search(text) or _DEVIATION_ALT_RE.search(text)
        if deviation_match:
            try:
                count = int(deviation_match.group(1))
            except ValueError:
                count = 0
            if count > 0:
                major = "major" if deviation_match.group(2) else ""
                value_text = normalize_ws(f"{count} {major} deviations")
                self._emit(
                    page,
                    segment_index,
                    segment,
                    FieldName.PROTOCOL_DEVIATION,
                    value_num=float(count),
                    value_text=value_text,
                    unit="deviations",
                    span=(deviation_match.start(1), deviation_match.end(1)),
                    confidence=0.85,
                )


_CI_AFTER_RE = re.compile(r"\s*(?:ci|confidence)\b", re.IGNORECASE)
_CI_BEFORE_RE = re.compile(r"(?:ci|confidence\s+interval)\s*[:=]?\s*[\d.\s]*$", re.IGNORECASE)


def _non_ci_percent_count(tail: str) -> int:
    """Percent tokens in *tail* excluding the "95% CI" marker and CI bounds."""
    count = 0
    for match in PCT_RE.finditer(tail):
        if _CI_AFTER_RE.match(tail[match.end() : match.end() + 16]):
            continue
        if _CI_BEFORE_RE.search(tail[max(0, match.start() - 24) : match.start()]):
            continue
        count += 1
    return count


def _endpoint_phrase_span(
    text: str, capture_start: int, capture_end: int, *, include_all_tokens: bool = False
) -> tuple[int, int]:
    """Verbatim span from the endpoint token(s) to the assessment method (if any).

    The capture regex stops at the decimal point of version numbers ("RECIST 1.1"),
    so the method search window is allowed to extend a few characters past the
    captured span.
    """
    token_spans: list[tuple[int, int]] = []
    for pattern, _token in _ENDPOINT_ALIASES:
        for match in pattern.finditer(text, capture_start, capture_end):
            token_spans.append((match.start(), match.end()))
            break
    method_window_end = min(len(text), capture_end + 16)
    method_span = _method_span(text[capture_start:method_window_end])
    if method_span is not None:
        token_spans.append((capture_start + method_span[0], capture_start + method_span[1]))
    if not token_spans:
        return (capture_start, capture_end)
    token_spans.sort(key=lambda span: span[0])
    if include_all_tokens:
        start = min(span[0] for span in token_spans)
        end = max(span[1] for span in token_spans)
    else:
        first = token_spans[0]
        start, end = first[0], first[1]
        for span in token_spans[1:]:
            if span[0] <= end + 25:
                end = max(end, span[1])
    return (start, min(end, len(text)))


def _method_span(text: str) -> tuple[int, int] | None:
    """Span of the first assessment-method mention (matched, not synthesised)."""
    best: tuple[int, int] | None = None
    for pattern, _canonical in _METHOD_PATTERNS_LOCAL:
        match = pattern.search(text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), match.end())
    return best


def _alpha_phrase_near(page: PageContext, segment_index: int) -> str | None:
    """Canonical 'one-sided alpha 0.05' phrase from the local sentence window."""
    for index in range(max(0, segment_index - 2), min(len(page.segments), segment_index + 4)):
        match = _ALPHA_RE.search(page.segments[index].text)
        if match:
            try:
                number = float(match.group(2))
            except ValueError:
                continue
            side = "one-sided" if "one" in match.group(1).lower() else "two-sided"
            return f"{side} alpha {number:g}"
    return None


def _owner_before(hits: Sequence[MetricHit], position: int) -> MetricHit | None:
    owner: MetricHit | None = None
    for hit in hits:
        if hit.value_end <= position and position - hit.value_end <= 260:
            owner = hit
    return owner


_DESIGN_HYPOTHETICAL_RE = re.compile(
    r"\b(?:recommended|advised|would\s+be\s+required|required\s+for|should|could|may\s+support)\b", re.I
)
_STRONG_SINGLE_DESIGN = {"single-arm", "open-label", "double-blind", "single-blind", "placebo-controlled", "first-in-human"}


def _design_span(text: str) -> tuple[int, int] | None:
    matches: list[tuple[int, int, str]] = []
    for pattern, label in _DESIGN_KEYWORDS:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), label))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    # Deduplicate overlapping keyword matches ("randomized" vs "randomised" etc).
    deduped: list[tuple[int, int, str]] = []
    for item in matches:
        if deduped and item[0] < deduped[-1][1]:
            continue
        deduped.append(item)
    if len(deduped) >= 2:
        start, end = deduped[0][0], deduped[-1][1]
        if end - start <= 140:
            return (start, end)
        return (deduped[0][0], deduped[0][1])
    label = deduped[0][2]
    if label in _STRONG_SINGLE_DESIGN:
        lowered = text.lower()
        if ("study" in lowered or "design" in lowered or "trial" in lowered) and len(deduped[0][2]) <= 40:
            return (deduped[0][0], deduped[0][1])
    return None


__all__ = ["METRIC_SPECS", "RulesExtractor", "canonical_endpoint_phrase", "iter_metric_hits"]
