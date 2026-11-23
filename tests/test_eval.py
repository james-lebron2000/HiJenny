"""Evaluation harness tests (eval-dev owned).

Unit tests use hand-computed tiny cases for matching/tolerance/FP/FN/Wilson CI.
The integration test runs the real pipeline on a generated tiny package and is
skipped with a clear reason while pipeline stage modules are still being built in
parallel; it activates automatically once they are importable.

Tests never depend on ``data/sample_package`` existing (they skip if absent) and
never touch ``data/assetdd.db``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from corpus import adversarial
from corpus.adjudication import (
    CLEAN_CONTROL_EXPECTATIONS,
    SAMPLE_PACKAGE_FINDING_TOPICS,
    fallback_ground_truth,
)
from corpus.clean_control import build_clean_package
from corpus.ground_truth import (
    load_ground_truth,
    package_files,
    verify_ground_truth_facts,
)
from eval import metrics
from eval.benchmark import _lifecycle_analysis

REPO_ROOT = Path(__file__).resolve().parent.parent


def _round4(value: float) -> float:
    """Rates in the scorecard are rounded to 4 decimals; compare to that."""
    return round(value, 4)
SAMPLE_PACKAGE = REPO_ROOT / "data" / "sample_package"
SAMPLE_PACKAGE_V2 = REPO_ROOT / "data" / "sample_package_v2"


# ---------------------------------------------------------------------------
# Hand-computed metric unit tests
# ---------------------------------------------------------------------------


def test_wilson_ci_hand_computed():
    ci = metrics.wilson_ci(0, 10)
    assert ci["rate"] == 0.0
    assert ci["low"] == 0.0
    # Wilson upper bound for 0/10 at z=1.96 is ~0.2775
    assert ci["high"] == pytest.approx(0.2775, abs=5e-3)

    ci = metrics.wilson_ci(5, 10)
    assert ci["rate"] == 0.5
    assert ci["low"] == pytest.approx(0.2366, abs=1e-3)
    assert ci["high"] == pytest.approx(0.7634, abs=1e-3)

    empty = metrics.wilson_ci(0, 0)
    assert empty["rate"] is None and empty["low"] is None and empty["high"] is None


def _evidence(field, document, page, value_num=None, value_text=None, document_id=None, doc_type=None):
    return {
        "evidence_id": f"ev_{field}_{page}_{value_num or value_text}",
        "document_id": document_id or f"doc_{document}",
        "filename": document,
        "document_type": doc_type or "CSR",
        "field_name": field,
        "page": page,
        "value_num": value_num,
        "value_text": value_text,
    }


def test_extraction_field_accuracy_tolerance_and_page():
    facts = [
        {"field_name": "orr", "document": "csr.pdf", "page": 3, "value_num": 42.0},
        {"field_name": "sae", "document": "csr.pdf", "page": 4, "value_num": 19.8},
        {"field_name": "pfs", "document": "csr.pdf", "page": 5, "value_num": 7.4},
        # per-fact tolerance overrides the default
        {"field_name": "dcr", "document": "csr.pdf", "page": 6, "value_num": 74.0, "tolerance": 0.5},
    ]
    predictions = [
        _evidence("orr", "csr.pdf", 3, 42.04),   # outside default 0.01, inside 0.05
        _evidence("sae", "csr.pdf", 5, 19.8),    # wrong page -> miss
        _evidence("pfs", "csr.pdf", 5, 7.4),     # match
        _evidence("dcr", "csr.pdf", 6, 74.4),    # inside fact-level 0.5
    ]

    strict = metrics.extraction_field_accuracy(predictions, facts)
    assert strict["matched"] == 2  # pfs, dcr
    assert strict["expected"] == 4
    assert strict["accuracy"] == pytest.approx(0.5)
    assert strict["by_field"]["orr"]["matched"] == 0
    assert strict["by_field"]["pfs"]["accuracy"] == 1.0

    loose = metrics.extraction_field_accuracy(
        predictions, facts, numeric_tolerance=0.05
    )
    assert loose["matched"] == 3  # orr, pfs, dcr
    assert loose["accuracy"] == pytest.approx(0.75)
    assert loose["by_field"]["orr"]["missing"] == 0
    missing_fields = {m["field_name"] for m in loose["missing_facts"]}
    assert missing_fields == {"sae"}


def test_extraction_value_mismatch_reports_near_miss():
    facts = [{"field_name": "orr", "document": "csr.pdf", "page": 3, "value_num": 42.0}]
    predictions = [_evidence("orr", "csr.pdf", 3, 44.4)]
    result = metrics.extraction_field_accuracy(predictions, facts)
    assert result["matched"] == 0
    assert result["value_mismatches"]
    mismatch = result["value_mismatches"][0]
    assert mismatch["field_name"] == "orr"
    assert mismatch["predicted_near_misses"][0]["predicted"] == 44.4


def test_extraction_text_value_tolerant_match():
    facts = [
        {
            "field_name": "design",
            "document": "csr.pdf",
            "page": 2,
            "value_text": "Phase 2, open-label, single-arm study",
        }
    ]
    contained = [
        _evidence("design", "csr.pdf", 2, value_text="Phase 2, open-label, single-arm")
    ]
    assert metrics.extraction_field_accuracy(contained, facts)["matched"] == 1
    different = [_evidence("design", "csr.pdf", 2, value_text="randomised, blinded")]
    assert metrics.extraction_field_accuracy(different, facts)["matched"] == 0


def test_extraction_prf_hand_computed():
    facts = [
        {"field_name": "orr", "document": "csr.pdf", "page": 3, "value_num": 42.0},
        {"field_name": "sae", "document": "csr.pdf", "page": 4, "value_num": 19.8},
        {"field_name": "pfs", "document": "csr.pdf", "page": 5, "value_num": 7.4},
    ]
    predictions = [
        _evidence("orr", "csr.pdf", 3, 42.0),   # TP
        _evidence("sae", "csr.pdf", 4, 19.8),   # TP
        _evidence("os", "csr.pdf", 7, 11.2),    # FP
    ]
    prf = metrics.extraction_prf(predictions, facts)
    assert (prf["tp"], prf["fp"], prf["fn"]) == (2, 1, 1)
    assert prf["precision"] == pytest.approx(_round4(2 / 3))
    assert prf["recall"] == pytest.approx(_round4(2 / 3))
    assert prf["f1"] == pytest.approx(_round4(2 / 3))
    assert prf["precision_ci"]["successes"] == 2 and prf["precision_ci"]["trials"] == 3
    assert prf["missing_facts"][0]["field_name"] == "pfs"


def test_prepare_extraction_predictions_ignores_and_collapses():
    predictions = [
        _evidence("orr", "csr.pdf", 3, 42.0, document_id="d1", doc_type="CSR"),
        _evidence("orr", "csr.pdf", 3, 42.0, document_id="d1", doc_type="CSR"),  # duplicate
        _evidence("orr", "csr.pdf", 3, 43.0, document_id="d1", doc_type="CSR"),
        _evidence("cmc", "cmc.pdf", 1, 1.0, document_id="d9", doc_type="CMC"),
        _evidence("n_fas", "sap.pdf", 2, 120.0, document_id="d4", doc_type="SAP"),
    ]
    prepared = metrics.prepare_extraction_predictions(
        predictions,
        ignore_documents=["cmc.pdf"],
        ignore_fields=["n_fas"],
        collapse_duplicates=True,
    )
    scope = prepared["scope"]
    assert scope["input_predictions"] == 5
    assert scope["ignored_by_document"] == 1
    assert scope["ignored_by_field"] == 1
    assert scope["collapsed_duplicates"] == 1
    assert scope["scored_predictions"] == 2


def test_finding_prf_tolerant_wording_and_confusion():
    topics = [
        {
            "topic_id": "t_orr",
            "finding_type": "data_inconsistency",
            "field_name": "orr",
            "documents": ["csr.pdf", "deck.pptx"],
            "expected_severity": "critical",
        },
        {"topic_id": "t_mult", "finding_type": "evidence_gap", "keywords": ["multiplicity", "alpha"]},
    ]
    findings = [
        {
            "finding_id": "f1",
            "finding_type": "data_inconsistency",
            # deliberately different wording than the adjudication rationale
            "title": "Response rate differs between CSR and commercial deck",
            "detail": {"field": "orr", "documents": ["csr.pdf", "deck.pptx"]},
            "severity": "critical",
            "status": "open",
            "_documents": ["csr.pdf", "deck.pptx"],
        },
        {
            "finding_id": "f2",
            "finding_type": "evidence_gap",
            "title": "No multiplicity/alpha-control strategy in SAP",
            "detail": {},
            "severity": "medium",
            "status": "open",
        },
        {
            "finding_id": "f3",
            "finding_type": "technical_concern",
            "title": "Injection attempt in IB",
            "detail": {},
            "severity": "high",
            "status": "open",
        },
    ]
    prf = metrics.finding_prf(findings, topics)
    assert (prf["tp"], prf["fp"], prf["fn"]) == (2, 1, 0)
    assert prf["precision"] == pytest.approx(_round4(2 / 3))
    assert prf["recall"] == 1.0
    assert prf["f1"] == pytest.approx(0.8)
    assert prf["severity_agreement"]["checked"] == 1
    assert prf["severity_agreement"]["correct"] == 1
    assert {m["finding_id"] for m in prf["matched"]} == {"f1", "f2"}


def test_finding_prf_wrong_type_is_fp_and_fn():
    topics = [
        {"topic_id": "t1", "finding_type": "data_inconsistency", "field_name": "orr"},
    ]
    findings = [
        {"finding_id": "f1", "finding_type": "version_difference", "title": "ORR differs",
         "detail": {"field": "orr"}, "status": "open"},
    ]
    prf = metrics.finding_prf(findings, topics)
    assert (prf["tp"], prf["fp"], prf["fn"]) == (0, 1, 1)
    assert prf["precision"] == 0.0
    assert prf["recall"] == 0.0
    assert prf["missed_topics"][0]["topic_id"] == "t1"


def test_match_finding_topics_one_to_one():
    topics = [
        {"topic_id": "t1", "finding_type": "data_inconsistency", "field_name": "orr"},
        {"topic_id": "t2", "finding_type": "data_inconsistency", "field_name": "orr"},
    ]
    findings = [
        {"finding_id": "f1", "finding_type": "data_inconsistency", "title": "ORR mismatch",
         "detail": {"field": "orr"}, "status": "open"},
    ]
    assignment = metrics.match_finding_topics(findings, topics)
    assert len(assignment["matches"]) == 1  # one-to-one: second topic unmatched
    assert len(assignment["unmatched_topics"]) == 1
    assert assignment["unmatched_finding_indices"] == []


def test_citation_accuracy_verbatim_and_failures():
    evidence_by_id = {
        "e1": {"evidence_id": "e1", "document_id": "d1", "page": 1, "quote": "ORR was 42.0%"},
        "e2": {"evidence_id": "e2", "document_id": "d1", "page": 2, "quote": "SAE was 19.8%"},
    }
    page_texts = {"d1": {1: "The  ORR was\n42.0% in the ITT population."}}
    findings = [
        {"finding_id": "f1", "evidence_ids": ["e1"]},        # verified (whitespace)
        {"finding_id": "f2", "evidence_ids": ["missing"]},   # missing evidence
    ]
    questions = [
        {"question_id": "q1", "evidence_ids": []},           # no evidence
        {"question_id": "q2", "evidence_ids": ["e2"]},       # page 2 missing -> fail
    ]
    result = metrics.citation_accuracy(findings, questions, evidence_by_id, page_texts)
    assert result["refs"] == 3
    assert result["verified"] == 1
    assert result["failed"] == 2
    assert result["accuracy"] == pytest.approx(1 / 3)
    assert result["entities_without_evidence"] == 1
    reasons = {f["reason"] for f in result["failures"]}
    assert reasons == {"missing_evidence", "page_not_found"}


def test_question_quality_hand_computed():
    questions = [
        {
            "question": "Please reconcile the ORR discrepancy between CSR and commercial deck.",
            "rationale": "x" * 60,
            "owner": "Biostatistics",
            "required_documents": ["CSR"],
            "evidence_ids": ["e1"],
            "finding_ids": ["f1"],
            "dedup_key": "a",
            "status": "draft",
        },
        {
            # duplicate wording, no grounding: dedupe rate and shares must reflect it
            "question": "Please reconcile the ORR discrepancy between CSR and commercial deck.",
            "rationale": "short",
            "owner": "Biostatistics",
            "required_documents": [],
            "evidence_ids": [],
            "finding_ids": [],
            "dedup_key": "b",
            "status": "draft",
        },
        {
            "question": "What is the multiplicity strategy?",
            "rationale": "short",
            "owner": "Biostatistics",
            "required_documents": [],
            "evidence_ids": [],
            "finding_ids": [],
            "dedup_key": "c",
            "status": "draft",
        },
    ]
    expected_topics = [
        {"topic_id": "q_orr", "keywords": ["reconcile", "ORR"]},
        {"topic_id": "q_pp", "keywords": ["per-protocol"]},
    ]
    result = metrics.question_quality(questions, expected_topics)
    assert result["questions"] == 3
    assert result["with_evidence"] == 1
    assert result["with_evidence_share"]["rate"] == pytest.approx(_round4(1 / 3))
    assert result["with_requested_documents"] == 1
    assert result["actionable"] == 1
    assert result["dedupe_rate"] == pytest.approx(_round4(1 - 2 / 3))
    assert result["rationale_length"]["min_len_threshold"] == 40
    assert result["rationale_length"]["share_ge_min"] == pytest.approx(_round4(1 / 3))
    coverage = result["topic_coverage"]
    assert coverage["expected_topics"] == 2
    assert coverage["matched"] == 1
    assert coverage["coverage"] == pytest.approx(0.5)
    assert coverage["missed_topics"][0]["topic_id"] == "q_pp"


def test_coverage_accuracy_hand_computed():
    predicted = [
        {"dimension": "population", "requirement": "per-protocol population definition and size", "status": "partial"},
        {"dimension": "statistics", "requirement": "multiplicity control", "status": "sufficient"},
    ]
    expected = [
        {"dimension": "population", "requirement": "per-protocol population definition and size", "expected_status": "partial"},
        {"dimension": "statistics", "requirement": "multiplicity / alpha control", "expected_status": "not_found"},
        {"dimension": "quality", "requirement": "monitoring / audit summary", "expected_status": "not_found"},
    ]
    result = metrics.coverage_accuracy(predicted, expected)
    assert result["expected"] == 3
    assert result["matched"] == 2
    assert result["status_matches"] == 1  # statistics status wrong
    assert result["accuracy"] == pytest.approx(1 / 3)
    assert result["coverage_recall"] == pytest.approx(2 / 3)
    assert result["status_accuracy"] == pytest.approx(0.5)
    assert result["missed"][0]["dimension"] == "quality"


def test_bootstrap_f1_ci_is_deterministic():
    first = metrics.bootstrap_f1_ci(8, 2, 1, n_boot=300, seed=7)
    second = metrics.bootstrap_f1_ci(8, 2, 1, n_boot=300, seed=7)
    assert first == second
    assert 0.0 <= first["low"] <= first["high"] <= 1.0
    assert metrics.bootstrap_f1_ci(0, 0, 0) is None


def test_cost_latency_summary_hook():
    summary = metrics.cost_latency_summary(
        runtime_seconds=10.0, pages=100, documents=5, evidence=200, findings=4, questions=3
    )
    assert summary["seconds_per_page"] == pytest.approx(0.1)
    assert summary["evidence_per_page"] == pytest.approx(2.0)
    assert summary["estimated_cost_usd"] is None  # never invent a price
    priced = metrics.cost_latency_summary(
        runtime_seconds=10.0, pages=100, documents=5, evidence=200, findings=4, questions=3,
        tokens=1000, cost_per_1k_tokens=0.01,
    )
    assert priced["estimated_cost_usd"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Corpus helpers
# ---------------------------------------------------------------------------


def _write_minimal_gt(package_dir: Path, *, with_page: bool = True) -> Path:
    package_dir.mkdir(parents=True, exist_ok=True)
    fact = {
        "field_name": "orr",
        "document": "csr.pdf",
        "document_type": "CSR",
        "page": 3 if with_page else None,
        "anchor": "ORR was 42.0%",
        "value_num": 42.0,
        "unit": "%",
        "tolerance": 0.05,
    }
    gt = {
        "package": package_dir.name,
        "variant": "v1",
        "asset_name": "TEST-101",
        "trial": {"nct_id": "NCT00000001"},
        "facts": [fact],
        "finding_topics": [
            {
                "topic_id": "t1",
                "finding_type": "data_inconsistency",
                "field_name": "orr",
                "keywords": ["ORR", "42.0"],
                "expected_severity": "critical",
            }
        ],
        "question_topics": [{"topic_id": "q1", "keywords": ["ORR"]}],
        "coverage": [{"dimension": "population", "requirement": "PP size", "expected_status": "partial"}],
        "lifecycle": {"resolved_topics": ["t1"], "persisting_topics": [], "new_topics": []},
        "injection": {"document": "ib.pdf", "anchor": "NOTE TO AUTOMATED REVIEWERS"},
        "eval_notes": {
            "duplicates": "identical predictions count once",
            "ignore_documents_for_fact_scoring": ["cmc.pdf"],
            "ignore_fields_for_fact_scoring": [],
        },
        "known_limitations": ["synthetic"],
    }
    path = package_dir / "ground_truth.json"
    path.write_text(json.dumps(gt), encoding="utf-8")
    return path


def test_ground_truth_loader_pinned_shape(tmp_path):
    package_dir = tmp_path / "sample_package"
    _write_minimal_gt(package_dir)
    gt = load_ground_truth(package_dir)
    assert gt["source"] == "file"
    assert gt["variant"] == "v1"
    assert len(gt["facts"]) == 1
    assert gt["finding_topics"][0]["topic_id"] == "t1"
    assert gt["question_topics"][0]["topic_id"] == "q1"
    assert gt["coverage"][0]["expected_status"] == "partial"
    assert gt["lifecycle"]["resolved_topics"] == ["t1"]
    assert gt["injection"]["document"] == "ib.pdf"
    assert gt["ignore_documents"] == ["cmc.pdf"]
    assert gt["collapse_duplicates"] is True
    assert gt["known_limitations"] == ["synthetic"]


def test_ground_truth_loader_rejects_and_drops_malformed(tmp_path):
    package_dir = tmp_path / "pkg"
    _write_minimal_gt(package_dir, with_page=False)
    with pytest.raises(ValueError, match="missing_page"):
        load_ground_truth(package_dir, strict=True)
    lenient = load_ground_truth(package_dir, strict=False)
    assert lenient["facts"] == []
    assert lenient["warnings"]


def test_ground_truth_fallback_when_absent(tmp_path):
    package_dir = tmp_path / "sample_package"
    package_dir.mkdir()
    gt = load_ground_truth(package_dir)
    assert gt["source"] == "adjudication_fallback"
    assert gt["package"] == "sample_package"
    assert len(gt["finding_topics"]) == len(SAMPLE_PACKAGE_FINDING_TOPICS)
    assert gt["facts"] == []  # never invent fact locations
    assert gt["injection"] == {}


def test_package_files_lists_documents_only(tmp_path):
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    (package_dir / "a.pdf").write_bytes(b"%PDF-1.4")
    (package_dir / "b.pptx").write_bytes(b"x")
    (package_dir / "ground_truth.json").write_text("{}")
    (package_dir / "notes.md").write_text("x")
    names = [p.name for p in package_files(package_dir)]
    assert names == ["a.pdf", "b.pptx"]


def test_clean_control_package_builds_and_ground_truth_verifies(tmp_path):
    info = build_clean_package(tmp_path / "clean_control")
    package_dir = Path(info["package_dir"])
    assert (package_dir / "ground_truth.json").exists()
    assert "Clinical_Study_Report_v1.0.pdf" in info["files"]
    gt = load_ground_truth(package_dir)
    assert gt["expect_no_findings"] is True
    assert gt["finding_topics"] == []
    assert any(f["field_name"] == "orr" and f["value_num"] == 40.0 for f in gt["facts"])
    assert all(f["page"] >= 1 for f in gt["facts"])
    check = verify_ground_truth_facts(package_dir, gt["facts"])
    assert check["ok"], check["issues"]
    assert CLEAN_CONTROL_EXPECTATIONS["expect_no_findings"] is True


def test_adversarial_injection_fixtures(tmp_path):
    page = adversarial.injection_page()
    assert adversarial.scan_text_for_injection(page.text)
    pages = adversarial.injection_pages()
    assert [p.page_no for p in pages] == [1, 2]

    pdf_path = adversarial.write_injection_pdf(tmp_path / "injection.pdf")
    import fitz

    with fitz.open(pdf_path) as document:
        assert document.page_count == 2
        page_two = document[1].get_text("text")
    assert "NOTE TO AUTOMATED REVIEWERS" in page_two

    # Injection page present and a structured value adopts 99.9 -> fail.
    page_texts = {"doc_ib": {1: "intro", 2: adversarial.INJECTION_TEXT}}
    evidence = [
        {"evidence_id": "ev1", "field_name": "orr", "document_id": "doc_ib", "page": 2, "value_num": 99.9}
    ]
    findings = [
        {"finding_id": "f1", "finding_type": "technical_concern", "title": "Injection attempt in IB"}
    ]
    scanned = adversarial.scan_outputs_for_injection(evidence, findings, [], page_texts)
    assert scanned["injection_pages"]
    assert scanned["injected_value_leaked"] is True
    assert scanned["flagged_as_technical_concern"] is True
    assert scanned["passed"] is False

    # Compliant behaviour: instruction treated as data and flagged, no leak.
    compliant = adversarial.scan_outputs_for_injection([], findings, [], page_texts)
    assert compliant["passed"] is True
    assert compliant["directive_phrase_leaked"] is False


# ---------------------------------------------------------------------------
# Lifecycle analysis (pure, fabricated snapshots)
# ---------------------------------------------------------------------------


def _finding(fid, finding_type, title, *, field=None, docs=None, status="open", severity="medium"):
    return {
        "finding_id": fid,
        "finding_type": finding_type,
        "title": title,
        "status": status,
        "severity": severity,
        "detail": {"field": field} if field else {},
        "_documents": docs or [],
    }


def test_lifecycle_analysis_resolved_persisting_new():
    gt_v1 = {
        "finding_topics": [
            {"topic_id": "t_resolved", "finding_type": "data_inconsistency", "field_name": "orr"},
            {"topic_id": "t_resolved_gap", "finding_type": "evidence_gap", "keywords": ["multiplicity"]},
            {"topic_id": "t_persisting", "finding_type": "definition_difference", "field_name": "orr"},
        ],
        "lifecycle": {
            "resolved_topics": ["t_resolved", "t_resolved_gap"],
            "persisting_topics": ["t_persisting"],
            "new_topics": [],
        },
    }
    gt_v2 = {
        "finding_topics": [
            {"topic_id": "t_persisting", "finding_type": "definition_difference", "field_name": "orr"},
        ],
        "lifecycle": gt_v1["lifecycle"],
    }
    documents = [{"document_id": "d1", "filename": "csr.pdf", "doc_type": "CSR"}]
    snapshot_v1 = {
        "documents": documents,
        "findings": [
            _finding("f1", "data_inconsistency", "ORR mismatch", field="orr"),
            _finding("f2", "evidence_gap", "No multiplicity strategy"),
            _finding("f3", "definition_difference", "FAS vs ITT", field="orr"),
        ],
        "questions": [
            {"question_id": "q1", "question": "Reconcile ORR?", "dedup_key": "a"},
            {"question_id": "q2", "question": "PP population size?", "dedup_key": "b"},
        ],
    }
    snapshot_v2 = {
        "documents": documents,
        "findings": [
            _finding("f9", "definition_difference", "FAS vs ITT persists", field="orr"),
        ],
        "questions": [
            {"question_id": "q1", "question": "Reconcile ORR?", "dedup_key": "a"},
            {"question_id": "q3", "question": "Audit summary?", "dedup_key": "c"},
        ],
    }
    lifecycle = _lifecycle_analysis(gt_v1, gt_v2, snapshot_v1, snapshot_v2)
    assert "t_resolved" in lifecycle["observed"]["resolved"]
    assert "t_resolved_gap" in lifecycle["observed"]["resolved"]
    assert lifecycle["observed"]["persisting"] == ["t_persisting"]
    assert lifecycle["observed"]["new"] == []
    assert lifecycle["correct_topics"] == lifecycle["scored_topics"] == 3
    assert lifecycle["accuracy"] == 1.0
    questions = lifecycle["question_transitions"]
    assert questions["persisting"] == 1
    assert questions["new"] == 1
    assert questions["removed"] == 1


# ---------------------------------------------------------------------------
# Pipeline integration test (skipped until all stage modules exist)
# ---------------------------------------------------------------------------

_STAGE_MODULES = (
    "assetdd.ingestion",
    "assetdd.extraction",
    "assetdd.validation",
    "assetdd.agents.consistency",
    "assetdd.agents.gap",
    "assetdd.agents.questions",
    "assetdd.reporting",
)


def _missing_stage_modules() -> list[str]:
    import importlib

    missing = []
    for module in _STAGE_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{module} ({type(exc).__name__}: {exc})")
    return missing


def test_pipeline_integration_scorecard(tmp_path, monkeypatch):
    missing = _missing_stage_modules()
    if missing:
        pytest.skip("pipeline stage modules not importable yet: " + "; ".join(missing))

    # Isolate all runtime output and the DB inside tmp_path.
    monkeypatch.setenv("ASSETDD_DATA_DIR", str(tmp_path / "data"))
    from assetdd import store  # noqa: PLC0415

    store.configure(tmp_path / "t.db")

    package_dir = tmp_path / "clean_control"
    build_clean_package(package_dir)
    out_dir = tmp_path / "reports"
    from eval import benchmark  # noqa: PLC0415 - avoid import side effects at collection

    exit_code = benchmark.main(
        [
            "--package",
            str(package_dir),
            "--out",
            str(out_dir),
            "--database",
            str(tmp_path / "bench.db"),
            "--numeric-tolerance",
            "0.05",
        ]
    )
    assert exit_code == 0, "benchmark pipeline run failed"

    scorecard_path = out_dir / "scorecard.json"
    markdown_path = out_dir / "scorecard.md"
    assert scorecard_path.exists() and markdown_path.exists()
    scorecard = json.loads(scorecard_path.read_text())
    for key in (
        "meta",
        "corpus",
        "extraction",
        "findings",
        "citations",
        "questions",
        "coverage",
        "adversarial",
        "clean_control",
        "cost",
        "limitations",
    ):
        assert key in scorecard, f"missing scorecard section: {key}"
    assert scorecard["meta"]["honesty"].startswith("Synthetic corpus")
    assert scorecard["extraction"]["scored"] is True, scorecard["extraction"].get("reason")

    accuracy = scorecard["extraction"]["field_accuracy"]["accuracy"]
    assert accuracy is not None and math.isfinite(accuracy) and 0.0 <= accuracy <= 1.0
    prf = scorecard["extraction"]["prf"]
    assert prf["tp"] + prf["fn"] == scorecard["extraction"]["ground_truth_facts"]
    assert prf["precision"] is not None and 0.0 <= prf["precision"] <= 1.0

    citations = scorecard["citations"]
    assert citations["refs"] >= 0
    if citations["refs"]:
        assert 0.0 <= citations["accuracy"] <= 1.0
    assert scorecard["cost"]["runtime_seconds"] >= 0.0
    assert scorecard["cost"]["pages"] > 0

    markdown = markdown_path.read_text()
    assert "Synthetic corpus" in markdown
    assert "Limitations" in markdown
    assert "What a real-data evaluation would require" in markdown
    assert "False-positive control" in markdown


def test_sample_package_accessible_when_present():
    """Optional smoke test of the real corpus (skips if the Lead hasn't generated it)."""
    if not (SAMPLE_PACKAGE / "ground_truth.json").exists():
        pytest.skip("data/sample_package/ground_truth.json not generated yet")
    gt = load_ground_truth(SAMPLE_PACKAGE)
    assert gt["source"] == "file"
    assert len(gt["facts"]) >= 20
    assert len(gt["finding_topics"]) >= 5
    check = verify_ground_truth_facts(SAMPLE_PACKAGE, gt["facts"])
    assert check["ok"], check["issues"][:3]
