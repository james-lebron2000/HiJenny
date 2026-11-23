"""Due-diligence rule packs: loading, validation, applicability (analysis-dev).

Rule packs are **data**, never code.  A pack is a YAML file that lists generic
due-diligence checklist items for a therapeutic area / phase / modality.  Each
checklist item declares the structured evidence it expects (field names from
:class:`hijenny.schemas.FieldName`), how much is needed (``min_documents`` /
``min_records``), the seller question template to use when the evidence is
missing, and the domain owner of the follow-up.

Python only implements the small fixed set of *check predicates* in
:data:`CHECKS`; a pack may reference a predicate id but can never carry code.
This keeps the DD content editable by clinical/BD reviewers without touching the
analysis code, while keeping evaluation deterministic and auditable.

Public API
----------
``load_rules(rules_dir=None) -> list[Rule]``
    Recursively load and validate every ``*.yaml`` / ``*.yml`` pack.
``select_rules(rules, context) -> list[Rule]``
    Filter rules for a project context ``{therapeutic_area, phase, modality}``.
``applicability(rule, context) -> "applicable" | "not_applicable" | "uncertain"``
    Three-valued applicability used by the gap agent to report ``not_applicable``
    coverage rows without silently dropping them.
``evaluate_checks(rule, requirement, records)``
    Run the rule's declared check predicates against matching evidence.
``infer_therapeutic_area(*texts)``
    Deterministic keyword-based therapeutic-area hint from free-text indication.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml

from hijenny import config
from hijenny.schemas import FieldName, Owner, Priority, normalize_ws

RULES_SCHEMA_VERSION = "rule-pack-1.0"

# Phase-specific field is stored as integer (1..4).
PHASE_VALUES: tuple[int, ...] = (1, 2, 3, 4)

# Rule files may only reference these check ids.  Implemented here as data
# predicates so that adding a checklist item never requires new Python.
DEFAULT_CHECKS: tuple[str, ...] = ("presence", "min_records", "min_documents")

_RULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")

_OWNER_BY_LOWER = {o.value.lower(): o.value for o in Owner}
_PRIORITY_VALUES = {p.value for p in Priority}
_FIELD_VALUES = {f.value for f in FieldName}

_TOP_LEVEL_PACK_KEYS = {
    "pack_id", "name", "version", "schema_version", "therapeutic_area",
    "phase", "modality", "description",
}
_APPLIES_TO_KEYS = {"therapeutic_area", "phase", "modality"}
_RULE_KEYS = {
    "rule_id", "name", "version", "applies_to", "dimension",
    "required_evidence", "question_template", "priority", "owner",
    "required_documents", "notes", "checks",
}
_REQUIREMENT_KEYS = {
    "id", "label", "fields", "min_documents", "min_records", "acceptable_texts",
}

# Keyword hints for deriving a therapeutic area from a project indication when
# the caller does not pass one explicitly.  Only used as a hint: an unknown area
# never excludes a rule (applicability becomes "uncertain", not False).
_AREA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "oncology": (
        "oncology", "oncolog", "cancer", "carcinoma", "tumor", "tumour",
        "neoplasm", "melanoma", "lymphoma", "leukaemia", "leukemia",
        "sarcoma", "myeloma", "nsclc", "sclc", "glioma", "glioblastoma",
        "adenocarcinoma", "solid tumor", "matp",
    ),
    "immunology": (
        "immunolog", "autoimmune", "arthritis", "psoriasis", "lupus", "ibd",
        "crohn", "colitis", "atopic", "dermatitis",
    ),
    "neurology": (
        "neurolog", "alzheimer", "parkinson", "epilep", "sclerosis",
        "migraine", "seizure", "neuropathy",
    ),
    "cardiology": (
        "cardiolog", "cardiovascular", "heart failure", "hypertension",
        "arrhythmia", "atherosclerosis",
    ),
    "metabolic": ("metabolic", "diabetes", "obesity", "dyslipidemia", "nash"),
    "infectious_disease": (
        "infectious", "antiviral", "antibiotic", "vaccine", "hepatitis", "hiv",
    ),
    "respiratory": ("respiratory", "asthma", "copd", "pulmonary fibrosis"),
}


class RuleValidationError(ValueError):
    """Raised when a rule pack is malformed; message names the offending file."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """One expected evidence item inside a rule."""

    id: str
    label: str
    fields: tuple[str, ...]
    min_documents: int = 1
    min_records: int = 1
    acceptable_texts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "fields": list(self.fields),
            "min_documents": self.min_documents,
            "min_records": self.min_records,
            "acceptable_texts": list(self.acceptable_texts),
        }


@dataclass(frozen=True)
class Rule:
    """One due-diligence checklist item loaded from a YAML pack."""

    rule_id: str
    name: str
    version: str
    applies_to: dict[str, tuple[str, ...] | tuple[int, ...]]
    dimension: str
    required_evidence: tuple[Requirement, ...]
    question_template: str
    priority: str
    owner: str
    required_documents: tuple[str, ...] = ()
    notes: str = ""
    checks: tuple[str, ...] = DEFAULT_CHECKS
    pack_id: str = ""
    pack_name: str = ""
    pack_version: str = ""
    source: str = ""

    # Convenience accessors used by the gap agent ---------------------------
    @property
    def therapeutic_areas(self) -> tuple[str, ...]:
        return tuple(self.applies_to.get("therapeutic_area", ()))  # type: ignore[arg-type]

    @property
    def phases(self) -> tuple[int, ...]:
        return tuple(self.applies_to.get("phase", ()))  # type: ignore[arg-type]

    @property
    def modalities(self) -> tuple[str, ...]:
        return tuple(self.applies_to.get("modality", ()))  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "version": self.version,
            "applies_to": {k: list(v) for k, v in self.applies_to.items()},
            "dimension": self.dimension,
            "required_evidence": [r.to_dict() for r in self.required_evidence],
            "question_template": self.question_template,
            "priority": self.priority,
            "owner": self.owner,
            "required_documents": list(self.required_documents),
            "notes": self.notes,
            "checks": list(self.checks),
            "pack_id": self.pack_id,
            "pack_name": self.pack_name,
            "pack_version": self.pack_version,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Check predicates (the only executable part of a rule)
# ---------------------------------------------------------------------------


def _record_text(record: Mapping[str, Any]) -> str:
    parts = [
        str(record.get("value_text") or ""),
        str(record.get("quote") or ""),
        str(record.get("filename") or ""),
    ]
    return normalize_ws(" ".join(parts)).lower()


def _check_presence(
    requirement: Requirement, records: Sequence[Mapping[str, Any]], doc_ids: set[str]
) -> bool:
    return len(records) > 0


def _check_min_records(
    requirement: Requirement, records: Sequence[Mapping[str, Any]], doc_ids: set[str]
) -> bool:
    return len(records) >= requirement.min_records


def _check_min_documents(
    requirement: Requirement, records: Sequence[Mapping[str, Any]], doc_ids: set[str]
) -> bool:
    return len(doc_ids) >= requirement.min_documents


def _check_acceptable_texts(
    requirement: Requirement, records: Sequence[Mapping[str, Any]], doc_ids: set[str]
) -> bool:
    if not requirement.acceptable_texts:
        return True
    if not records:
        return False
    wanted = tuple(normalize_ws(t).lower() for t in requirement.acceptable_texts)
    return all(any(token in _record_text(r) for token in wanted) for r in records)


CHECKS: dict[
    str, Callable[[Requirement, Sequence[Mapping[str, Any]], set[str]], bool]
] = {
    "presence": _check_presence,
    "min_records": _check_min_records,
    "min_documents": _check_min_documents,
    "acceptable_texts": _check_acceptable_texts,
}


def evaluate_checks(
    rule: Rule, requirement: Requirement, records: Sequence[Mapping[str, Any]]
) -> dict[str, bool]:
    """Run the rule's declared predicates against already-matched records."""
    doc_ids = {str(r.get("document_id") or "") for r in records if r.get("document_id")}
    return {
        check_id: bool(CHECKS[check_id](requirement, records, doc_ids))
        for check_id in rule.checks
        if check_id in CHECKS
    }


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _norm_phase_value(value: Any, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise RuleValidationError(f"{where}: phase must be an integer-ish value, got bool")
    if isinstance(value, int):
        if value in PHASE_VALUES:
            return value
        raise RuleValidationError(f"{where}: unsupported phase {value!r}")
    if isinstance(value, float) and value.is_integer():
        return _norm_phase_value(int(value), where)
    text = normalize_ws(str(value)).lower()
    if not text:
        return None
    text = re.sub(r"\bphase\b", " ", text)
    text = text.replace("_", " ").replace("-", " ")
    match = re.search(r"(\d+)", text)
    if match is None:
        raise RuleValidationError(f"{where}: cannot parse phase {value!r}")
    phase = int(match.group(1))
    if phase not in PHASE_VALUES:
        raise RuleValidationError(f"{where}: unsupported phase {phase}")
    return phase


def _norm_phases(value: Any, where: str) -> tuple[int, ...]:
    if value is None:
        return ()
    items = value if isinstance(value, (list, tuple, set)) else [value]
    phases = []
    for item in items:
        phase = _norm_phase_value(item, where)
        if phase is not None and phase not in phases:
            phases.append(phase)
    return tuple(phases)


def _norm_text_list(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in items:
        text = normalize_ws(str(item)).lower()
        if not text:
            continue
        if text not in out:
            out.append(text)
    return tuple(out)


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuleValidationError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def _require_str(value: Any, where: str, *, min_length: int = 1) -> str:
    if not isinstance(value, str) or len(normalize_ws(value)) < min_length:
        raise RuleValidationError(f"{where}: expected a non-empty string")
    return value.strip()


# ---------------------------------------------------------------------------
# Parsing / validation
# ---------------------------------------------------------------------------


def _parse_requirement(raw: Any, where: str) -> Requirement:
    item = _require_mapping(raw, where)
    unknown = set(item) - _REQUIREMENT_KEYS
    if unknown:
        raise RuleValidationError(f"{where}: unknown key(s) {sorted(unknown)}")
    req_id = _require_str(item.get("id"), f"{where}.id")
    label = _require_str(item.get("label"), f"{where}.label")
    fields_raw = item.get("fields")
    if not fields_raw:
        raise RuleValidationError(f"{where}.fields: at least one field name required")
    fields: list[str] = []
    for field in fields_raw if isinstance(fields_raw, (list, tuple)) else [fields_raw]:
        name = normalize_ws(str(field)).lower()
        if name not in _FIELD_VALUES:
            raise RuleValidationError(
                f"{where}.fields: unknown FieldName {field!r}; "
                f"must be one of hijenny.schemas.FieldName"
            )
        if name not in fields:
            fields.append(name)
    min_documents = item.get("min_documents", 1)
    min_records = item.get("min_records", 1)
    for key, value in (("min_documents", min_documents), ("min_records", min_records)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuleValidationError(f"{where}.{key}: expected an integer >= 0")
    acceptable_raw = item.get("acceptable_texts")
    if acceptable_raw is not None and not isinstance(acceptable_raw, (list, tuple)):
        raise RuleValidationError(f"{where}.acceptable_texts: expected a list of strings")
    acceptable = tuple(str(t) for t in (acceptable_raw or []) if str(t).strip())
    return Requirement(
        id=req_id,
        label=label,
        fields=tuple(fields),
        min_documents=int(min_documents),
        min_records=int(min_records),
        acceptable_texts=acceptable,
    )


def _canonical_owner(value: Any, where: str) -> str:
    raw = _require_str(value, where)
    canonical = _OWNER_BY_LOWER.get(normalize_ws(raw).lower())
    if canonical is None:
        raise RuleValidationError(
            f"{where}: unknown owner {value!r}; must be one of "
            f"{sorted(o.value for o in Owner)}"
        )
    return canonical


def _canonical_priority(value: Any, where: str) -> str:
    raw = _require_str(value, where).lower()
    if raw not in _PRIORITY_VALUES:
        raise RuleValidationError(
            f"{where}: unknown priority {value!r}; must be one of {sorted(_PRIORITY_VALUES)}"
        )
    return raw


def _parse_rule(
    raw: Any,
    *,
    path: Path,
    source: str,
    pack: Mapping[str, Any],
) -> Rule:
    """Validate one rule entry and inherit pack-level defaults."""
    entry = _require_mapping(raw, f"{source}")
    unknown = set(entry) - _RULE_KEYS
    if unknown:
        raise RuleValidationError(f"{source}: unknown rule key(s) {sorted(unknown)}")
    rule_id = _require_str(entry.get("rule_id"), f"{source}.rule_id")
    if not _RULE_ID_RE.fullmatch(rule_id):
        raise RuleValidationError(f"{source}.rule_id: invalid id {rule_id!r}")
    where = f"{path.name}:{rule_id}"

    applies_raw = entry.get("applies_to")
    if applies_raw is None:
        raise RuleValidationError(f"{where}.applies_to: required (may be an empty mapping)")
    applies = _require_mapping(applies_raw, f"{where}.applies_to")
    unknown_applies = set(applies) - _APPLIES_TO_KEYS
    if unknown_applies:
        raise RuleValidationError(f"{where}.applies_to: unknown key(s) {sorted(unknown_applies)}")

    def inherited(key: str) -> Any:
        # Explicit key in applies_to wins, even when it is an empty list (any).
        return applies[key] if key in applies else pack.get(key)

    applies_to: dict[str, tuple[str, ...] | tuple[int, ...]] = {
        "therapeutic_area": _norm_text_list(inherited("therapeutic_area"), f"{where}.therapeutic_area"),
        "phase": _norm_phases(inherited("phase"), f"{where}.phase"),
        "modality": _norm_text_list(inherited("modality"), f"{where}.modality"),
    }

    evidence_raw = entry.get("required_evidence")
    if not isinstance(evidence_raw, (list, tuple)) or not evidence_raw:
        raise RuleValidationError(f"{where}.required_evidence: expected a non-empty list")
    requirements = tuple(
        _parse_requirement(item, f"{where}.required_evidence[{idx}]")
        for idx, item in enumerate(evidence_raw)
    )
    req_ids = [r.id for r in requirements]
    if len(req_ids) != len(set(req_ids)):
        raise RuleValidationError(f"{where}.required_evidence: duplicate requirement ids")

    template = _require_str(entry.get("question_template"), f"{where}.question_template", min_length=10)

    documents_raw = entry.get("required_documents", [])
    if documents_raw is not None and not isinstance(documents_raw, (list, tuple)):
        raise RuleValidationError(f"{where}.required_documents: expected a list of strings")
    documents = tuple(
        str(d).strip() for d in (documents_raw or []) if str(d).strip()
    )

    checks_raw = entry.get("checks", list(DEFAULT_CHECKS))
    if checks_raw is not None and not isinstance(checks_raw, (list, tuple)):
        raise RuleValidationError(f"{where}.checks: expected a list of check ids")
    checks: list[str] = []
    for check_id in checks_raw or []:
        if check_id not in CHECKS:
            raise RuleValidationError(
                f"{where}.checks: unknown check id {check_id!r}; known ids: {sorted(CHECKS)}"
            )
        if check_id not in checks:
            checks.append(check_id)

    notes = entry.get("notes", "")
    if notes is not None and not isinstance(notes, str):
        raise RuleValidationError(f"{where}.notes: expected a string")

    return Rule(
        rule_id=rule_id,
        name=_require_str(entry.get("name"), f"{where}.name"),
        version=_require_str(entry.get("version"), f"{where}.version"),
        applies_to=applies_to,
        dimension=_require_str(entry.get("dimension"), f"{where}.dimension"),
        required_evidence=requirements,
        question_template=template,
        priority=_canonical_priority(entry.get("priority"), f"{where}.priority"),
        owner=_canonical_owner(entry.get("owner"), f"{where}.owner"),
        required_documents=documents,
        notes=notes or "",
        checks=tuple(checks),
        pack_id=str(pack.get("pack_id") or path.stem),
        pack_name=str(pack.get("name") or ""),
        pack_version=str(pack.get("version") or "0.0.0"),
        source=source,
    )


def _parse_pack(raw: Any, path: Path, root: Path) -> list[Rule]:
    source = str(path.relative_to(root)) if path.is_relative_to(root) else path.name
    if raw is None:
        raise RuleValidationError(f"{source}: empty YAML file")
    if isinstance(raw, list):
        pack: Mapping[str, Any] = {}
        entries = raw
        root_schema_version: Any = None
    elif isinstance(raw, Mapping):
        unknown = set(raw) - {"pack", "rules", "schema_version"}
        root_schema_version = raw.get("schema_version")
        if unknown:
            raise RuleValidationError(f"{source}: unknown top-level key(s) {sorted(unknown)}")
        pack = _require_mapping(raw.get("pack") or {}, f"{source}.pack")
        unknown_pack = set(pack) - _TOP_LEVEL_PACK_KEYS
        if unknown_pack:
            raise RuleValidationError(f"{source}.pack: unknown key(s) {sorted(unknown_pack)}")
        entries = raw.get("rules")
    else:
        raise RuleValidationError(f"{source}: expected a mapping or a list of rules")

    declared_versions = [
        version
        for version in (root_schema_version, pack.get("schema_version") if pack else None)
        if version is not None
    ]
    for schema_version in declared_versions:
        if str(schema_version) != RULES_SCHEMA_VERSION:
            raise RuleValidationError(
                f"{source}: unsupported schema_version {schema_version!r}; "
                f"expected {RULES_SCHEMA_VERSION!r}"
            )
    if not isinstance(entries, (list, tuple)) or not entries:
        raise RuleValidationError(f"{source}.rules: expected a non-empty list of rules")
    return [
        _parse_rule(entry, path=path, source=f"{source}[{idx}]", pack=pack)
        for idx, entry in enumerate(entries)
    ]


def load_rules(rules_dir: str | Path | None = None) -> list[Rule]:
    """Load and validate every YAML rule pack under *rules_dir*.

    Files are processed in sorted relative-path order so that the returned rule
    order (and therefore coverage/question ordering) is deterministic.  A
    malformed pack raises :class:`RuleValidationError` naming the file.
    """
    root = Path(rules_dir) if rules_dir is not None else Path(config.RULES_DIR)
    if not root.exists():
        return []
    if not root.is_dir():
        raise RuleValidationError(f"{root}: rules directory is not a directory")

    files = sorted(
        (p for p in root.rglob("*") if p.suffix.lower() in {".yaml", ".yml"} and p.is_file()),
        key=lambda p: str(p.relative_to(root)),
    )
    rules: list[Rule] = []
    seen: dict[str, str] = {}
    for path in files:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:  # pragma: no cover - message asserted in tests
            raise RuleValidationError(f"{path.name}: invalid YAML: {exc}") from exc
        except OSError as exc:  # pragma: no cover - filesystem failure
            raise RuleValidationError(f"{path.name}: cannot read pack: {exc}") from exc
        for rule in _parse_pack(raw, path, root):
            if rule.rule_id in seen:
                raise RuleValidationError(
                    f"{rule.source}: duplicate rule_id {rule.rule_id!r} "
                    f"(already defined in {seen[rule.rule_id]})"
                )
            seen[rule.rule_id] = rule.source
            rules.append(rule)
    return rules


# ---------------------------------------------------------------------------
# Applicability / selection
# ---------------------------------------------------------------------------


def infer_therapeutic_area(*texts: str | None) -> str | None:
    """Keyword hint for a therapeutic area from free-text indication fields."""
    blob = " ".join(normalize_ws(t).lower() for t in texts if t)
    if not blob:
        return None
    for area, keywords in _AREA_KEYWORDS.items():
        if any(keyword in blob for keyword in keywords):
            return area
    return None


def normalize_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise a context mapping for :func:`select_rules` / :func:`applicability`."""
    raw = dict(context or {})
    area = raw.get("therapeutic_area")
    if not area:
        area = infer_therapeutic_area(raw.get("indication"), raw.get("asset_indication"))
    modality = raw.get("modality")
    raw_phase = raw.get("phase")
    phase: int | None = None
    if raw_phase is not None and str(raw_phase).strip():
        try:
            phase = _norm_phase_value(raw_phase, "context.phase")
        except RuleValidationError:
            phase = None
    return {
        "therapeutic_area": normalize_ws(str(area)).lower() if area else None,
        "phase": phase,
        "modality": normalize_ws(str(modality)).lower() if modality else None,
    }


def _match_text(rule_values: Sequence[str], context_value: str | None) -> bool | None:
    """True/False when the context is known; None when it is unknown."""
    if not rule_values:
        return True
    if not context_value:
        return None
    return any(
        value == context_value or value in context_value or context_value in value
        for value in rule_values
    )


def _match_phase(rule_phases: Sequence[int], context_phase: int | None) -> bool | None:
    if not rule_phases:
        return True
    if context_phase is None:
        return None
    return context_phase in rule_phases


def applicability(rule: Rule, context: Mapping[str, Any] | None) -> str:
    """Three-valued applicability: ``applicable`` / ``not_applicable`` / ``uncertain``.

    Unknown context dimensions never exclude a rule (a conservative package may
    still contain Phase 1 content); they make applicability ``uncertain``.
    """
    ctx = normalize_context(context)
    checks = (
        _match_text(rule.therapeutic_areas, ctx["therapeutic_area"]),
        _match_phase(rule.phases, ctx["phase"]),
        _match_text(rule.modalities, ctx["modality"]),
    )
    if any(value is False for value in checks):
        return "not_applicable"
    if any(value is None for value in checks):
        return "uncertain"
    return "applicable"


def select_rules(
    rules: Iterable[Rule], context: Mapping[str, Any] | None
) -> list[Rule]:
    """Return rules that may apply to *context*, preserving input order.

    A rule is selected when no known context dimension contradicts it; unknown
    dimensions are treated permissively (see :func:`applicability`).  Callers
    that need strict filtering should inspect :func:`applicability`.
    """
    return [rule for rule in rules if applicability(rule, context) != "not_applicable"]


def rule_index(rules: Iterable[Rule]) -> dict[str, Rule]:
    """Convenience: rule_id -> Rule."""
    return {rule.rule_id: rule for rule in rules}


# ---------------------------------------------------------------------------
# Template rendering (kept data-driven; never eval)
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def format_question_template(template: str, values: Mapping[str, Any]) -> str:
    """Fill ``{name}`` placeholders without raising on missing/unknown keys."""
    def replace(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        if value is None:
            return match.group(0)
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        return str(value)

    try:
        return _PLACEHOLDER_RE.sub(replace, template)
    except Exception:  # pragma: no cover - regex substitution cannot fail
        return template
