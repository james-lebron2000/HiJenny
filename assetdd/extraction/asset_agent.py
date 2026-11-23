"""Asset / clinical-trial resolution for the EXTRACT stage.

Responsibilities
----------------
1. Identify the asset (drug name/code) from title pages and the project metadata.
2. Derive mechanism/target/modality/indication/phase from the brochure/CSR prose
   (only when explicitly stated; never inferred from the asset name alone).
3. Upsert the asset and clinical trial(s) through ``store.upsert_asset`` /
   ``store.upsert_trial`` and expose the mapping used to attach
   ``asset_id`` / ``trial_id`` / ``trial_key`` to evidence at save time.

Trial identity is the project-level canonical NCT id (a project identity, not a
document claim): documents such as a Safety Summary or a commercial deck do not
repeat the registry number but still belong to the same trial.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from assetdd import store
from assetdd.extraction.common import (
    detect_ncts,
    detect_study_ids,
    normalize_ws,
)
from assetdd.schemas import EvidenceDraft, FieldName, normalize_date, normalize_ws as _nws

_ASSET_CODE_RE = re.compile(r"\b([A-Z]{2,6}-\d{2,5}(?!-\d))\b")
_ASSET_LABEL_RE = re.compile(
    r"\b(?:investigational\s+product|study\s+drug|investigational\s+medicinal\s+product|IMP|"
    r"asset|compound|test\s+article|drug\s+substance)\s*[:=]\s*([A-Za-z0-9][A-Za-z0-9\-]{2,30})",
    re.IGNORECASE,
)
_STUDY_OF_RE = re.compile(r"\b(?:study|trial)\s+of\s+([A-Z][A-Za-z0-9\-]{2,20})\b")
_ALIAS_RE = re.compile(r"\b([A-Z]{2,6}-\d{2,5})\s*\(([a-z][a-z0-9\-]{2,20})\)")

_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:selective|reversible|irreversible|covalent|potent|novel|oral)\b[^.;]{0,60}?\binhibitor\s+of\s+([A-Za-z0-9\-/]{2,20})\b", re.I),
    re.compile(r"\binhibitor\s+of\s+([A-Za-z0-9\-/]{2,20})\b", re.I),
    re.compile(r"\b(?:targets?|targeting|binds?\s+(?:to|with)|against)\s+([A-Za-z0-9\-/]{2,20})\b", re.I),
    re.compile(r"\b(?:anti[-\s])([A-Za-z0-9\-]{2,20})\s+(?:monoclonal\s+)?antibody\b", re.I),
)
_MECHANISM_RE = re.compile(
    r"\b((?:[A-Za-z]+(?:,\s+|\s+)){0,4}inhibitor\s+of\s+[A-Za-z0-9\-/]{2,20})\b", re.I
)
_MECHANISM_LABEL_RE = re.compile(r"\bmechanism\s+of\s+action\s*[:=]\s*([^.;]{3,160})", re.I)

_MODALITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bantibody[-\s]drug\s+conjugate\b|\bADC\b"), "antibody-drug conjugate"),
    (re.compile(r"\bbispecific\b", re.I), "bispecific antibody"),
    (re.compile(r"\bCAR[-\s]?T\b", re.I), "CAR-T cell therapy"),
    (re.compile(r"\bfusion\s+protein\b", re.I), "fusion protein"),
    (re.compile(r"\bmonoclonal\s+antibody\b", re.I), "monoclonal antibody"),
    (re.compile(r"\bsmall\s+molecule\b", re.I), "small molecule"),
    (re.compile(r"\bpeptide\b", re.I), "peptide"),
    (re.compile(r"\bvaccine\b", re.I), "vaccine"),
)

_INDICATION_RE = re.compile(
    r"\b(?:in|for)\s+(?:the\s+)?(?:treatment\s+of\s+)?(?:patients|subjects|adults|participants)?\s*with\s+"
    r"([A-Za-z][A-Za-z0-9 ,\-/'()]{3,70}?)(?=\s+(?:Study|Protocol|Sponsor|Report|Data|Edition|Version|NCT)\b|[.;]|$)",
    re.I,
)
_PHASE_RE = re.compile(r"\bphase\s*(1/2|2/3|1b/2|1b|2a|2b|IV|III|II|I|4|3|2|1)\b", re.I)
_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4"}

_DOC_PRIORITY = {"CSR": 0, "PROTOCOL": 1, "SAP": 2, "IB": 3, "SAFETY": 4}


@dataclass
class DocumentView:
    document: dict
    pages: list[dict]


@dataclass
class AssetResolution:
    asset: dict | None = None
    asset_name: str | None = None
    project_trial_key: str | None = None
    project_study_id: str | None = None
    trials_by_key: dict[str, dict] = field(default_factory=dict)
    doc_trial_key: dict[str, str | None] = field(default_factory=dict)
    asset_meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Asset detection
# ---------------------------------------------------------------------------
def _title_texts(view: DocumentView, max_pages: int = 2) -> list[str]:
    texts: list[str] = []
    for page in view.pages[:max_pages]:
        texts.append(str(page.get("text") or ""))
    return texts


def _detect_asset_name(views: Sequence[DocumentView], project_asset_name: str | None) -> tuple[str | None, str | None]:
    scores: Counter[str] = Counter()
    aliases: dict[str, str] = {}
    for view in views:
        doc_type = str(view.document.get("doc_type") or "")
        if doc_type not in {"IB", "CSR", "PROTOCOL", "SAP", "COMMERCIAL", "SAFETY"}:
            continue
        for text in _title_texts(view):
            norm = normalize_ws(text)
            for match in _ASSET_CODE_RE.finditer(norm):
                scores[match.group(1)] += 2
            for match in _ASSET_LABEL_RE.finditer(norm):
                candidate = match.group(1).strip()
                scores[candidate] += 3
            for match in _STUDY_OF_RE.finditer(norm):
                candidate = match.group(1)
                if re.search(r"\d", candidate):
                    scores[candidate] += 2
            for match in _ALIAS_RE.finditer(norm):
                aliases[match.group(1)] = match.group(2)
    project_name = (project_asset_name or "").strip()
    if project_name:
        base = project_name.split("(")[0].strip()
        if base:
            if "(" in project_name and ")" in project_name:
                alias = project_name.split("(", 1)[1].split(")", 1)[0].strip()
                aliases.setdefault(base, alias)
            return base, aliases.get(base)
    if not scores:
        return None, None
    # Prefer the token that looks most like a drug code (letters + digits), then frequency.
    ordered = sorted(
        scores.items(),
        key=lambda item: (
            0 if re.fullmatch(r"[A-Z]{2,6}-\d{2,5}", item[0]) else 1,
            -item[1],
            item[0],
        ),
    )
    name = ordered[0][0]
    return name, aliases.get(name)


def _detect_target_modality_mechanism(views: Sequence[DocumentView]) -> tuple[str | None, str | None, str | None]:
    target = None
    mechanism = None
    modality = None
    # Prefer the IB / CSR for pharmacology statements.
    ordered = sorted(
        views,
        key=lambda view: _DOC_PRIORITY.get(str(view.document.get("doc_type") or ""), 9),
    )
    for view in ordered:
        for page in view.pages[:8]:
            norm = normalize_ws(str(page.get("text") or ""))
            if not norm:
                continue
            if target is None:
                for pattern in _TARGET_PATTERNS:
                    match = pattern.search(norm)
                    if match:
                        candidate = normalize_ws(match.group(1))
                        if 1 < len(candidate) <= 20 and not candidate.isdigit():
                            target = candidate
                            break
            if mechanism is None:
                label_match = _MECHANISM_LABEL_RE.search(norm)
                if label_match:
                    mechanism = normalize_ws(label_match.group(1))
                else:
                    match = _MECHANISM_RE.search(norm)
                    if match:
                        mechanism = normalize_ws(match.group(1))
                        # Drop verb/modality prefixes that leaked into the phrase.
                        mechanism = re.sub(
                            r"^(?:is|are|was|were)\s+(?:an?\s+)?", "", mechanism, flags=re.I
                        )
                        mechanism = re.sub(
                            r"^(?:oral|intravenous|subcutaneous|potent|novel)[,\s]+",
                            "",
                            mechanism,
                            flags=re.I,
                        )
            if modality is None:
                for pattern, canonical in _MODALITY_PATTERNS:
                    if pattern.search(norm):
                        modality = canonical
                        break
                if modality is None and re.search(r"\binhibitor\b", norm, re.I) and re.search(r"\boral\b", norm, re.I):
                    modality = "oral small molecule"
            if target and mechanism and modality:
                return target, modality, mechanism
    return target, modality, mechanism


def _detect_indication(views: Sequence[DocumentView], project_indication: str | None) -> str | None:
    counts: Counter[str] = Counter()
    for view in views:
        for page in view.pages[:3]:
            norm = normalize_ws(str(page.get("text") or ""))
            for match in _INDICATION_RE.finditer(norm):
                value = normalize_ws(match.group(1)).strip(" .,;:-")
                if 4 <= len(value) <= 90:
                    counts[value] += 1
    if counts:
        return sorted(counts.items(), key=lambda item: (-item[1], -len(item[0])))[0][0]
    return (project_indication or "").strip() or None


def _detect_phase(views: Sequence[DocumentView], project_phase: str | None) -> str | None:
    counts: Counter[str] = Counter()
    for view in views:
        for page in view.pages[:3]:
            norm = normalize_ws(str(page.get("text") or ""))
            match = _PHASE_RE.search(norm)
            if match:
                raw = match.group(1).upper()
                number = _ROMAN.get(raw, raw.lower())
                counts[f"Phase {number}"] += 1
    if counts:
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return (project_phase or "").strip() or None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def resolve_assets(session: Session, project_id: str, views: Sequence[DocumentView]) -> AssetResolution:
    """Resolve asset + trial identities and persist them (idempotent upserts)."""
    project = store.get_project(session, project_id) or {}
    name, alias = _detect_asset_name(views, project.get("asset_name"))

    # Project-level registry identity (deterministic: most frequent NCT).
    nct_counts: Counter[str] = Counter()
    study_id_counts: Counter[str] = Counter()
    nct_source: dict[str, str] = {}
    for view in views:
        doc_id = str(view.document.get("document_id") or "")
        for page in view.pages:
            norm = normalize_ws(str(page.get("text") or ""))
            for nct in detect_ncts(norm):
                nct_counts[nct] += 1
                nct_source.setdefault(nct, doc_id)
            for study_id in detect_study_ids(norm):
                study_id_counts[study_id] += 1
    project_trial_key = (
        sorted(nct_counts.items(), key=lambda item: (-item[1], item[0]))[0][0] if nct_counts else None
    )
    project_study_id = (
        sorted(study_id_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        if study_id_counts
        else None
    )

    asset: dict | None = None
    asset_meta: dict = {}
    if name:
        target, modality, mechanism = _detect_target_modality_mechanism(views)
        indication = _detect_indication(views, project.get("indication"))
        phase = _detect_phase(views, project.get("phase"))
        asset_meta = {
            "alias": alias,
            "name_source": "title_pages",
            "target": target,
            "modality": modality,
            "mechanism": mechanism,
        }
        asset = store.upsert_asset(
            session,
            project_id,
            name,
            modality=modality or "",
            target=target or "",
            mechanism=mechanism or "",
            indication=indication or "",
            phase=phase or "",
            meta=asset_meta,
        )
    asset_id = asset["asset_id"] if asset else None

    trials_by_key: dict[str, dict] = {}
    # Canonical NCT trial(s).
    for nct, _count in nct_counts.most_common():
        trial = store.upsert_trial(
            session,
            project_id,
            nct_id=nct,
            internal_id=project_study_id or "",
            asset_id=asset_id,
            source_document_id=nct_source.get(nct),
            phase=(asset or {}).get("phase") or "",
            indication=(asset or {}).get("indication") or "",
        )
        trials_by_key[nct] = trial
    if not trials_by_key and project_study_id:
        trial = store.upsert_trial(
            session,
            project_id,
            internal_id=project_study_id,
            asset_id=asset_id,
            phase=(asset or {}).get("phase") or "",
            indication=(asset or {}).get("indication") or "",
        )
        trials_by_key[project_study_id] = trial
    # Alias the internal study id to the same trial row for evidence grouping.
    for trial in list(trials_by_key.values()):
        internal_id = trial.get("internal_id")
        if internal_id and internal_id not in trials_by_key:
            trials_by_key[internal_id] = trial

    doc_trial_key: dict[str, str | None] = {}
    for view in views:
        doc_id = str(view.document.get("document_id") or "")
        doc_ncts: list[str] = []
        for page in view.pages:
            doc_ncts.extend(detect_ncts(normalize_ws(str(page.get("text") or ""))))
        if doc_ncts:
            doc_trial_key[doc_id] = Counter(doc_ncts).most_common(1)[0][0]
        else:
            doc_trial_key[doc_id] = project_trial_key

    return AssetResolution(
        asset=asset,
        asset_name=name,
        project_trial_key=project_trial_key,
        project_study_id=project_study_id,
        trials_by_key=trials_by_key,
        doc_trial_key=doc_trial_key,
        asset_meta=asset_meta,
    )


def ensure_trial(
    session: Session,
    project_id: str,
    trial_key: str | None,
    resolution: AssetResolution,
    asset_id: str | None,
) -> dict | None:
    """Return (creating if needed) the trial row for *trial_key*."""
    if not trial_key:
        return None
    existing = resolution.trials_by_key.get(trial_key)
    if existing is not None:
        return existing
    nct_id = trial_key if trial_key.upper().startswith("NCT") else ""
    internal_id = "" if nct_id else trial_key
    if not nct_id and not internal_id:
        return None
    trial = store.upsert_trial(
        session,
        project_id,
        nct_id=nct_id,
        internal_id=internal_id,
        asset_id=asset_id,
    )
    resolution.trials_by_key[trial_key] = trial
    return trial


def update_trials_from_evidence(
    session: Session,
    project_id: str,
    drafts_by_trial: dict[str, list[tuple[dict, EvidenceDraft]]],
    resolution: AssetResolution,
    asset_id: str | None,
) -> None:
    """Fill design-level trial columns from extracted study-design evidence."""
    for trial_key, entries in drafts_by_trial.items():
        if not entries:
            continue
        trial = ensure_trial(session, project_id, trial_key, resolution, asset_id)
        if trial is None:
            continue
        fields = _trial_fields(entries)
        if fields:
            store.upsert_trial(session, project_id, nct_id=trial.get("nct_id") or "", internal_id=trial.get("internal_id") or "", **fields)


def _trial_fields(entries: Iterable[tuple[dict, EvidenceDraft]]) -> dict:
    by_field: dict[FieldName, list[tuple[int, dict, EvidenceDraft]]] = {}
    for doc, draft in entries:
        priority = _DOC_PRIORITY.get(str(doc.get("doc_type") or ""), 9)
        by_field.setdefault(draft.field_name, []).append((priority, doc, draft))
    out: dict = {}

    def best(field_name: FieldName):
        values = by_field.get(field_name)
        if not values:
            return None
        values.sort(key=lambda item: (item[0], item[1].get("filename") or ""))
        return values[0][2]

    phase = best(FieldName.PHASE)
    if phase and phase.value_text:
        out["phase"] = phase.value_text
    indication = best(FieldName.INDICATION)
    if indication and indication.value_text:
        out["indication"] = indication.value_text
    design = best(FieldName.DESIGN)
    if design and design.value_text:
        out["design"] = design.value_text
    sample_size = best(FieldName.SAMPLE_SIZE)
    if sample_size and sample_size.value_num is not None:
        out["sample_size"] = int(sample_size.value_num)
    cutoff = best(FieldName.DATA_CUTOFF)
    if cutoff:
        iso = normalize_date(cutoff.value_text) if cutoff.value_text else None
        if iso:
            out["data_cutoff"] = iso
    primary_candidates = by_field.get(FieldName.PRIMARY_ENDPOINT) or []
    if primary_candidates:
        primary_candidates = sorted(
            primary_candidates,
            key=lambda item: (item[0], 0 if " per " in (item[2].value_text or "") else 1, -(len(item[2].value_text or ""))),
        )
        chosen = primary_candidates[0][2]
        if chosen.value_text:
            out["primary_endpoint"] = chosen.value_text
    comparator = best(FieldName.COMPARATOR)
    if comparator and comparator.value_text:
        out["comparator"] = comparator.value_text
    arms_draft = best(FieldName.ARMS)
    if arms_draft and arms_draft.value_text:
        out["arms"] = [part.strip() for part in re.split(r"\s+(?:vs\.?|versus)\s+", arms_draft.value_text) if part.strip()]
    return out


__all__ = [
    "AssetResolution",
    "DocumentView",
    "ensure_trial",
    "resolve_assets",
    "update_trials_from_evidence",
]
