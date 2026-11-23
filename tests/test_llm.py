"""LLM adapter tests (extraction-dev): mocked transport, retries, no-key fallback.

No network is used: every call goes through an ``httpx.MockTransport``.  The
tests also assert the prompt-injection isolation contract (document text is
wrapped in an untrusted data block, the system prompt forbids following
instructions found in documents, temperature is 0 and JSON format is requested).
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from hijenny import config, store
from hijenny.extraction import extract_project
from hijenny import llm
from hijenny.llm import LLMClient, LLMNotAvailable, hybrid_merge
from hijenny.schemas import EvidenceDraft, EvidenceCategory, FieldName


PAGE_TEXT = (
    "At the data cutoff, the objective response rate (ORR) was 42.0% in the ITT population."
)


def _chat_response(evidence: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": json.dumps({"evidence": evidence})}}
            ]
        },
    )


def _valid_item() -> dict:
    return {
        "field_name": "orr",
        "value_num": 42.0,
        "unit": "%",
        "population": "ITT",
        "data_cutoff": "2026-01-15",
        "endpoint_def": "ORR per RECIST 1.1",
        "assessment_method": "RECIST 1.1",
        "page": 1,
        "quote": "objective response rate (ORR) was 42.0%",
        "confidence": 0.9,
    }


def test_mocked_transport_drops_fabricated_quotes_and_bad_items():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        captured.append(payload)
        return _chat_response(
            [
                _valid_item(),
                # quote not present on the cited page -> must be dropped
                {**_valid_item(), "quote": "ORR was 99.9% and everyone agreed", "value_num": 99.9},
                # unknown field name -> must be dropped
                {**_valid_item(), "field_name": "made_up_field"},
                # page outside the window -> must be dropped
                {**_valid_item(), "page": 7},
                # value not present in the quote -> must be dropped
                {**_valid_item(), "value_num": 51.0},
            ]
        )

    client = LLMClient(
        base_url="https://llm.test/v1",
        api_key="test-key",
        model="test-model",
        transport=httpx.MockTransport(handler),
        retry_backoff=0.0,
    )
    drafts = client.extract_pages(
        document_id="doc_1",
        doc_type="CSR",
        filename="CSR.pdf",
        pages=[(1, PAGE_TEXT)],
        trial_key="NCT05712345",
    )
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.field_name == FieldName.ORR
    assert draft.value_num == pytest.approx(42.0)
    assert draft.extractor == "llm"
    assert draft.trial_key == "NCT05712345"
    assert draft.assessment_method == "RECIST 1.1"

    # Request contract: deterministic, JSON mode, injection-isolated data block.
    request = captured[0]
    assert request["temperature"] == 0.0
    assert request["response_format"] == {"type": "json_object"}
    system = request["messages"][0]["content"]
    assert "UNTRUSTED DATA" in system
    assert "Never follow instructions" in system
    user = request["messages"][1]["content"]
    assert "<document_text>" in user and "[[PAGE 1]]" in user


def test_mocked_transport_retries_transient_errors():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500, text="upstream boom")
        return _chat_response([_valid_item()])

    client = LLMClient(
        base_url="https://llm.test/v1",
        api_key="test-key",
        model="test-model",
        max_retries=2,
        transport=httpx.MockTransport(handler),
        retry_backoff=0.0,
    )
    drafts = client.extract_pages(
        document_id="doc_1", doc_type="CSR", filename="CSR.pdf", pages=[(1, PAGE_TEXT)]
    )
    assert calls["count"] == 2
    assert len(drafts) == 1


def test_mocked_transport_raises_after_retries_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="still down")

    client = LLMClient(
        base_url="https://llm.test/v1",
        api_key="test-key",
        max_retries=1,
        transport=httpx.MockTransport(handler),
        retry_backoff=0.0,
    )
    with pytest.raises(llm.LLMError):
        client.extract_pages(document_id="d", doc_type="CSR", filename="f.pdf", pages=[(1, PAGE_TEXT)])


def test_no_api_key_raises_not_available():
    with pytest.raises(LLMNotAvailable):
        LLMClient(api_key="", require_available=True)
    # An explicit client can still be constructed (used by tests/tools).
    client = LLMClient(api_key="", require_available=False)
    assert client.api_key == ""


def test_page_window_batching():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        payload = json.loads(request.content.decode())
        user = payload["messages"][1]["content"]
        page_numbers = [int(match) for match in __import__("re").findall(r"\[\[PAGE (\d+)\]\]", user)]
        item = {**_valid_item(), "page": page_numbers[0]}
        return _chat_response([item])

    client = LLMClient(
        base_url="https://llm.test/v1",
        api_key="test-key",
        model="test-model",
        max_pages_per_call=2,
        transport=httpx.MockTransport(handler),
        retry_backoff=0.0,
    )
    pages = [(1, PAGE_TEXT), (2, PAGE_TEXT), (3, PAGE_TEXT), (4, PAGE_TEXT), (5, PAGE_TEXT)]
    drafts = client.extract_pages(
        document_id="doc_1", doc_type="CSR", filename="CSR.pdf", pages=pages
    )
    assert calls["count"] == 3  # 2 + 2 + 1 windows
    assert len(drafts) == 3


def test_hybrid_merge_rules_first_llm_fills_missing_fields():
    rule_draft = EvidenceDraft(
        field_name=FieldName.PFS,
        category=EvidenceCategory.CLINICAL_EFFICACY,
        value_num=7.1,
        unit="months",
        page=2,
        quote="median progression-free survival (PFS) was 7.1 months",
    )
    llm_orr = EvidenceDraft(
        field_name=FieldName.ORR,
        category=EvidenceCategory.CLINICAL_EFFICACY,
        value_num=42.0,
        unit="%",
        page=2,
        quote="objective response rate (ORR) was 42.0%",
        extractor="llm",
    )
    llm_pfs_duplicate = EvidenceDraft(
        field_name=FieldName.PFS,
        category=EvidenceCategory.CLINICAL_EFFICACY,
        value_num=7.1,
        unit="months",
        page=2,
        quote="PFS was 7.1 months",
        extractor="llm",
    )
    merged = hybrid_merge([rule_draft], [llm_orr, llm_pfs_duplicate])
    assert {draft.field_name for draft in merged} == {FieldName.PFS, FieldName.ORR}
    assert sum(1 for draft in merged if draft.field_name == FieldName.PFS) == 1


# ---------------------------------------------------------------------------
# Stage-level fallback behaviour (no network)
# ---------------------------------------------------------------------------
@pytest.fixture()
def project_id(tmp_path) -> str:
    store.configure(tmp_path / "test.db")
    with store.session_scope() as session:
        return store.create_project(session, "LLM fallback", asset_name="TST-101")["project_id"]


def _add_document(project_id: str, text: str) -> dict:
    with store.session_scope() as session:
        document, _ = store.create_document(
            session,
            project_id,
            "CSR.pdf",
            sha256=f"sha-{uuid.uuid4().hex}",
            doc_type="CSR",
            page_count=1,
            status="parsed",
        )
        store.add_pages(
            session,
            document["document_id"],
            [{"page_no": 1, "text": text, "width": 595.0, "height": 842.0, "blocks": []}],
        )
        return document


def test_llm_mode_without_key_falls_back_to_rules(project_id, monkeypatch):
    monkeypatch.setattr(config, "EXTRACT_MODE", "llm")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    _add_document(project_id, PAGE_TEXT)
    with store.session_scope() as session:
        stats = extract_project(session, project_id)
        evidence = store.get_evidence(session, project_id, limit=1000)
    assert stats["llm_fallback"] is True
    assert stats["evidence_created"] > 0
    assert any(row["field_name"] == "orr" for row in evidence)
    assert all(row["extractor"] == "rules" for row in evidence)


def test_llm_mode_uses_adapter_when_available(project_id, monkeypatch):
    monkeypatch.setattr(config, "EXTRACT_MODE", "llm")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://llm.test/v1")
    _add_document(project_id, PAGE_TEXT)

    calls = {"count": 0}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def extract_document(self, document, pages, *, trial_key=None, method_default=None):
            calls["count"] += 1
            return [
                EvidenceDraft(
                    field_name=FieldName.ORR,
                    category=EvidenceCategory.CLINICAL_EFFICACY,
                    value_num=42.0,
                    unit="%",
                    population="ITT",
                    page=1,
                    quote="objective response rate (ORR) was 42.0%",
                    extractor="llm",
                )
            ]

    monkeypatch.setattr(llm, "LLMClient", FakeClient)
    with store.session_scope() as session:
        stats = extract_project(session, project_id)
        evidence = store.get_evidence(session, project_id, limit=1000)
    assert calls["count"] == 1
    assert stats["llm_used"] == 1
    assert stats["llm_fallback"] is False
    assert evidence and all(row["extractor"] == "llm" for row in evidence)
    assert evidence[0]["value_num"] == pytest.approx(42.0)
