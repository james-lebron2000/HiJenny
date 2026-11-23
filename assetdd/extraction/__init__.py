"""EXTRACT stage handler (evidence extraction orchestration).

Pipeline contract: ``extract_project(session, project_id, force=False) -> dict``.
It loads the latest parsed documents, runs the deterministic rule extractor and
the table-first extractor (plus the optional LLM adapter per
``config.EXTRACT_MODE``), resolves asset/trial identity and persists evidence
through ``store.save_evidence_batch`` only.

Idempotency: without ``force`` documents that already have evidence are skipped;
with ``force`` prior evidence for the document is deleted first.  Evidence rows
are deduplicated by the store's fingerprint/upsert semantics.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.orm import Session

from assetdd import config, llm, store
from assetdd.extraction import asset_agent
from assetdd.extraction.asset_agent import DocumentView
from assetdd.extraction.common import (
    DocumentContext,
    DraftCollector,
    build_document_context,
    normalize_ws,
)
from assetdd.extraction.rules_extractor import RulesExtractor
from assetdd.extraction.table_extractor import TableExtractor
from assetdd.schemas import EvidenceDraft, normalize_trial

LLM_MODES = {"llm", "hybrid"}


def extract_document(context: DocumentContext) -> list[EvidenceDraft]:
    """Run the deterministic extractors for one document context (no DB writes)."""
    drafts = DraftCollector()
    for draft in RulesExtractor(context).extract():
        drafts.add(draft)
    for draft in TableExtractor(context).extract():
        drafts.add(draft)
    return drafts.drafts()


def _dim_score(draft: EvidenceDraft) -> int:
    return sum(
        1
        for value in (
            draft.population,
            draft.arm,
            draft.data_cutoff,
            draft.endpoint_def,
            draft.assessment_method,
        )
        if value
    )


def _dedupe_same_page(drafts: Sequence[EvidenceDraft]) -> list[EvidenceDraft]:
    """One row per (field, page, value): keep the richest provenance variant.

    The synopsis page can surface the same fact twice (prose sentence and a
    flattened table row); the variant with more non-null dimensions wins.
    Distinct arms are never collapsed.
    """
    groups: dict[tuple, list[EvidenceDraft]] = {}
    order: list[tuple] = []
    for draft in drafts:
        number = None if draft.value_num is None else round(float(draft.value_num), 6)
        key = (
            str(draft.field_name),
            draft.page,
            number,
            normalize_ws(draft.value_text or "").lower(),
        )
        if key not in groups:
            groups[key] = [draft]
            order.append(key)
        else:
            groups[key].append(draft)
    out: list[EvidenceDraft] = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            out.append(group[0])
            continue
        arms = {draft.arm for draft in group if draft.arm}
        if len(arms) > 1:
            out.extend(group)
            continue
        out.append(max(group, key=lambda draft: (_dim_score(draft), draft.extractor == "table", draft.confidence)))
    return out


def _combine(*draft_lists: Sequence[EvidenceDraft]) -> list[EvidenceDraft]:
    collector = DraftCollector()
    for drafts in draft_lists:
        for draft in drafts:
            collector.add(draft)
    return collector.drafts()


def extract_project(session: Session, project_id: str, force: bool = False) -> dict:
    """EXTRACT stage: documents -> evidence rows (+ assets/trials)."""
    stats: dict = {
        "documents_extracted": 0,
        "documents_skipped": 0,
        "documents_without_pages": 0,
        "evidence_created": 0,
        "evidence_skipped": 0,
        "evidence_deleted": 0,
        "drafts_dropped_quote": 0,
        "injection_sentences_skipped": 0,
        "injection_flags_added": 0,
        "assets": 0,
        "trials": 0,
        "extract_mode": llm.llm_mode(),
        "llm_used": 0,
        "llm_fallback": False,
        "llm_errors": 0,
    }
    documents = store.list_documents(session, project_id, latest_only=True)
    if not documents:
        return stats

    views: list[DocumentView] = []
    for document in documents:
        document_id = document["document_id"]
        pages = store.get_pages(session, document_id)
        if not pages or not any(str(page.get("text") or "").strip() for page in pages):
            stats["documents_without_pages"] += 1
            stats["documents_skipped"] += 1
            continue
        if not force:
            existing = store.get_evidence(session, project_id, document_id=document_id, limit=1)
            if existing:
                stats["documents_skipped"] += 1
                continue
        views.append(DocumentView(document=document, pages=pages))
    if not views:
        stats["assets"] = len(store.list_assets(session, project_id))
        stats["trials"] = len(store.list_trials(session, project_id))
        return stats

    resolution = asset_agent.resolve_assets(session, project_id, views)
    asset_id = resolution.asset["asset_id"] if resolution.asset else None
    stats["asset_name"] = resolution.asset_name
    stats["project_trial_key"] = resolution.project_trial_key

    mode = llm.llm_mode()
    llm_client: llm.LLMClient | None = None
    if mode in LLM_MODES:
        if config.llm_available():
            try:
                llm_client = llm.LLMClient(require_available=False)
            except llm.LLMError:  # pragma: no cover - defensive
                stats["llm_fallback"] = True
        else:
            stats["llm_fallback"] = True

    drafts_by_trial: dict[str, list[tuple[dict, EvidenceDraft]]] = {}
    for view in views:
        document = view.document
        document_id = document["document_id"]
        if force:
            stats["evidence_deleted"] += store.delete_evidence_for_document(session, document_id)

        context = build_document_context(
            document,
            view.pages,
            project_trial_key=resolution.project_trial_key,
            project_study_id=resolution.project_study_id,
        )
        rule_extractor = RulesExtractor(context)
        rule_drafts = rule_extractor.extract()
        table_drafts = TableExtractor(context).extract()
        drafts = _combine(rule_drafts, table_drafts)

        if llm_client is not None:
            try:
                llm_drafts = llm_client.extract_document(
                    document,
                    view.pages,
                    trial_key=context.trial_key,
                    method_default=context.method_default,
                )
                stats["llm_used"] += 1
                if mode == "llm" and llm_drafts:
                    drafts = _combine(llm_drafts)
                else:
                    drafts = llm.hybrid_merge(drafts, llm_drafts)
            except llm.LLMError:
                stats["llm_errors"] += 1
                stats["llm_fallback"] = True

        drafts = _dedupe_same_page(drafts)
        page_norm = {page.page_no: page.norm for page in context.pages}
        valid: list[EvidenceDraft] = []
        for draft in drafts:
            cited = page_norm.get(draft.page)
            if cited is None:
                stats["drafts_dropped_quote"] += 1
                continue
            quote_norm = normalize_ws(draft.quote)
            if not quote_norm or quote_norm.lower() not in cited.lower():
                stats["drafts_dropped_quote"] += 1
                continue
            valid.append(draft)

        groups: dict[str | None, list[EvidenceDraft]] = {}
        for draft in valid:
            key = normalize_trial(draft.trial_key) or context.trial_key
            groups.setdefault(key, []).append(draft)

        for trial_key, group_drafts in groups.items():
            trial = asset_agent.ensure_trial(session, project_id, trial_key, resolution, asset_id)
            trial_id = trial["trial_id"] if trial else None
            created, skipped = store.save_evidence_batch(
                session,
                project_id,
                document,
                group_drafts,
                asset_id=asset_id,
                trial_id=trial_id,
            )
            stats["evidence_created"] += len(created)
            stats["evidence_skipped"] += skipped
            if trial_key:
                drafts_by_trial.setdefault(trial_key, []).extend((document, draft) for draft in group_drafts)

        stats["documents_extracted"] += 1
        stats["injection_sentences_skipped"] += rule_extractor.skipped_injected_segments
        if rule_extractor.injection_flags:
            added = _merge_injection_flags(session, document, rule_extractor.injection_flags)
            stats["injection_flags_added"] += added

    asset_agent.update_trials_from_evidence(session, project_id, drafts_by_trial, resolution, asset_id)

    stats["assets"] = len(store.list_assets(session, project_id))
    stats["trials"] = len(store.list_trials(session, project_id))
    stats["evidence_total"] = len(store.get_evidence(session, project_id, limit=100000))
    return stats


def _merge_injection_flags(session: Session, document: dict, flags: Sequence[dict]) -> int:
    """Merge extraction-detected injection flags into the document meta (deduped)."""
    document_id = document["document_id"]
    current = store.get_document(session, document_id) or document
    meta = dict(current.get("meta") or {})
    existing = list(meta.get("injection_flags") or [])
    seen = {(int(item.get("page") or 0), str(item.get("pattern") or "")) for item in existing if isinstance(item, dict)}
    added = 0
    for flag in flags:
        key = (int(flag.get("page") or 0), str(flag.get("pattern") or ""))
        if key in seen:
            continue
        seen.add(key)
        existing.append(
            {
                "page": key[0],
                "pattern": key[1],
                "excerpt": str(flag.get("excerpt") or "")[:400],
                "source": "extraction",
            }
        )
        added += 1
    if added:
        meta["injection_flags"] = existing
        store.update_document(session, document_id, meta=meta)
    return added


__all__ = ["extract_document", "extract_project"]
