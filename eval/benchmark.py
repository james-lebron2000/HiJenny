#!/usr/bin/env python3
"""AssetDD AI benchmark harness (eval-dev owned).

Runs the real pipeline (``assetdd.pipeline.run_pipeline``) on a document package,
then scores the persisted store against adjudicated ground truth:

    python3 -m eval.benchmark --package data/sample_package --fresh --out /tmp/assetdd_eval
    python3 -m eval.benchmark --project prj_xxx --package data/sample_package
    python3 -m eval.benchmark --lifecycle --out /tmp/assetdd_eval
    python3 -m eval.benchmark --clean-control --out /tmp/assetdd_eval
    python3 -m eval.benchmark --adversarial --out /tmp/assetdd_eval

Outputs ``scorecard.json`` + ``scorecard.md`` in the ``--out`` directory
(default ``eval/reports``, git-ignored) with tables, per-field accuracy,
confusion counts, Wilson/bootstrap intervals, runtime, an explicit limitations
section, and a "what real-data evaluation would need" section.

HONESTY RULE: this harness scores a synthetic corpus.  A high score proves the
pipeline works on that corpus; it is **not** real-client performance and must
never be reported as such.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from corpus import adversarial
from corpus.adjudication import CLEAN_CONTROL_EXPECTATIONS
from corpus.clean_control import build_clean_package
from corpus.ground_truth import load_ground_truth, package_files, verify_ground_truth_facts
from eval import metrics

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PACKAGE = REPO_ROOT / "data" / "sample_package"
DEFAULT_PACKAGE_V2 = REPO_ROOT / "data" / "sample_package_v2"
DEFAULT_OUT = REPO_ROOT / "eval" / "reports"

SCORECARD_JSON = "scorecard.json"
SCORECARD_MD = "scorecard.md"

_STAGE_MODULES: tuple[tuple[str, str], ...] = (
    ("INGEST", "assetdd.ingestion"),
    ("EXTRACT", "assetdd.extraction"),
    ("VALIDATE", "assetdd.validation"),
    ("CONSISTENCY", "assetdd.agents.consistency"),
    ("GAP", "assetdd.agents.gap"),
    ("QUESTIONS", "assetdd.agents.questions"),
    ("REPORT", "assetdd.reporting"),
)

_INACTIVE_FINDING_STATUSES = {"resolved", "dismissed"}

_HONESTY_BANNER = (
    "**Synthetic corpus — not real-world validation.** Every number below is "
    "measured on a hand-built, synthetic due-diligence package (or a generated "
    "control). It shows whether the pipeline behaves as designed on this corpus. "
    "It is **not** evidence of real-client performance, real-document accuracy, "
    "or regulatory sufficiency."
)


# ---------------------------------------------------------------------------
# Environment / imports (assetdd imports happen lazily so the harness can set a
# temporary data dir first and never touch data/assetdd.db)
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _setup_environment(work_dir: Path) -> None:
    """Force all AssetDD runtime output into the temp work dir."""
    data_dir = work_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ASSETDD_DATA_DIR"] = str(data_dir)
    os.environ.pop("ASSETDD_PACKAGE_DIR", None)


def _store_and_pipeline():
    from assetdd import pipeline, store  # noqa: PLC0415 - lazy on purpose

    return store, pipeline


def _import_ingestion():
    try:
        import assetdd.ingestion as ingestion  # noqa: PLC0415

        return ingestion
    except Exception:  # noqa: BLE001 - module may still be under construction
        return None


def _missing_stage_modules() -> list[str]:
    missing: list[str] = []
    for stage, module in _STAGE_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{stage}: {module} ({type(exc).__name__}: {exc})")
    return missing


# ---------------------------------------------------------------------------
# Pipeline execution helpers
# ---------------------------------------------------------------------------


def _create_project(name: str, asset_name: str, package_dir: Path):
    store, _ = _store_and_pipeline()
    with store.session_scope() as session:
        return store.create_project(
            session,
            name=name,
            asset_name=asset_name,
            meta={"package_dir": str(package_dir), "eval_harness": True},
        )


def _pre_ingest(project_id: str, package_dir: Path) -> dict | None:
    """Register/parse a package via the ingestion helper when available.

    The INGEST stage resolves the package directory from project meta, so this
    is a convenience that makes discovery explicit; it is idempotent.
    """
    ingestion = _import_ingestion()
    if ingestion is None or not hasattr(ingestion, "ingest_directory"):
        return None
    store, _ = _store_and_pipeline()
    with store.session_scope() as session:
        return ingestion.ingest_directory(session, project_id, str(package_dir))


def _run_pipeline(project_id: str, package_dir: Path, *, force: bool) -> tuple[dict, float]:
    store, pipeline = _store_and_pipeline()
    start = time.perf_counter()
    _pre_ingest(project_id, package_dir)
    run = pipeline.run_pipeline(project_id, force=force)
    return run, time.perf_counter() - start


def _snapshot(project_id: str) -> dict:
    """Read everything the metrics need from the store in one pass."""
    store, _ = _store_and_pipeline()
    with store.session_scope() as session:
        documents = store.list_documents(session, project_id)
        evidence = store.get_evidence(session, project_id, limit=50_000)
        findings = store.list_findings(session, project_id)
        questions = store.list_questions(session, project_id)
        coverage = store.list_coverage(session, project_id)
        stats = store.project_stats(session, project_id)
        runs = store.list_runs(session, project_id, limit=10)
        page_texts = {
            document["document_id"]: store.get_pages_text(session, document["document_id"])
            for document in documents
        }
    return {
        "documents": documents,
        "latest_documents": [d for d in documents if d.get("is_latest")],
        "evidence": evidence,
        "findings": findings,
        "questions": questions,
        "coverage": coverage,
        "stats": stats,
        "runs": runs,
        "page_texts": page_texts,
    }


def _enrich_findings(
    findings: list[dict], evidence_by_id: dict[str, dict]
) -> list[dict]:
    """Attach evidence-derived documents/fields so topic matching has signals."""
    enriched: list[dict] = []
    for finding in findings:
        documents: set[str] = set()
        fields: set[str] = set()
        for evidence_id in finding.get("evidence_ids") or []:
            evidence = evidence_by_id.get(evidence_id)
            if not evidence:
                continue
            for key in ("filename", "doc_type", "document_id"):
                if evidence.get(key):
                    documents.add(str(evidence[key]))
            if evidence.get("field_name"):
                fields.add(str(evidence["field_name"]))
        detail = finding.get("detail") or {}
        if isinstance(detail, dict):
            for item in detail.get("documents") or []:
                if isinstance(item, str):
                    documents.add(item)
                elif isinstance(item, dict):
                    for key in ("filename", "document_type", "doc_type", "document_id"):
                        if item.get(key):
                            documents.add(str(item[key]))
        row = dict(finding)
        row["_documents"] = sorted(documents)
        if fields:
            row["_evidence_fields"] = sorted(fields)
        enriched.append(row)
    return enriched


def _doc_map(snapshot: dict) -> dict[str, dict]:
    return {document["document_id"]: document for document in snapshot["documents"]}


def _run_summary_run(run: dict | None) -> dict:
    if not run:
        return {}
    stats = run.get("stats") or {}
    return {
        "run_id": run.get("run_id"),
        "state": run.get("state"),
        "status": run.get("status"),
        "error": run.get("error"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "stage_stats": {
            key: value for key, value in stats.items() if str(key).startswith("stage_")
        },
    }


# ---------------------------------------------------------------------------
# Scorecard assembly
# ---------------------------------------------------------------------------


def _build_scorecard(
    *,
    mode: str,
    package_name: str,
    package_dir: Path | None,
    ground_truth: dict,
    snapshot: dict,
    runtime_seconds: float,
    project_id: str | None,
    run: dict | None,
    numeric_tolerance: float,
    extra_limitations: list[str] | None = None,
) -> dict:
    evidence = snapshot["evidence"]
    findings = _enrich_findings(snapshot["findings"], {e["evidence_id"]: e for e in evidence})
    questions = snapshot["questions"]
    doc_map = _doc_map(snapshot)
    evidence_by_id = {e["evidence_id"]: e for e in evidence}

    # --- extraction -------------------------------------------------------
    # Only latest document versions are in the extraction scoring scope: after a
    # v2 upload the superseded v1 documents remain in the store but are not part
    # of the v2 analysis.  Package eval_notes can further ignore out-of-scope
    # documents/fields and collapse duplicate predictions.
    facts = ground_truth.get("facts") or []
    latest_doc_ids = {d["document_id"] for d in snapshot["latest_documents"]}
    non_latest_predictions = sum(1 for e in evidence if e.get("document_id") not in latest_doc_ids)
    if facts:
        prepared = metrics.prepare_extraction_predictions(
            [e for e in evidence if e.get("document_id") in latest_doc_ids],
            ignore_documents=ground_truth.get("ignore_documents"),
            ignore_fields=ground_truth.get("ignore_fields"),
            collapse_duplicates=bool(ground_truth.get("collapse_duplicates")),
        )
        prediction_scope = prepared["scope"]
        prediction_scope["latest_documents_only"] = True
        prediction_scope["non_latest_document_predictions"] = non_latest_predictions
        scoring_predictions = prepared["predictions"]
        extraction = {
            "scored": True,
            "ground_truth_facts": len(facts),
            "prediction_scope": prediction_scope,
            "field_accuracy": metrics.extraction_field_accuracy(
                scoring_predictions,
                facts,
                doc_map=doc_map,
                numeric_tolerance=numeric_tolerance,
            ),
            "prf": metrics.extraction_prf(
                scoring_predictions,
                facts,
                doc_map=doc_map,
                numeric_tolerance=numeric_tolerance,
            ),
        }
    else:
        extraction = {
            "scored": False,
            "ground_truth_facts": 0,
            "reason": (
                "No fact-level ground truth available for this package "
                f"(ground_truth_source={ground_truth.get('source')}). "
                "Extraction is not scored rather than scored against invented pages."
            ),
            "predictions": len(evidence),
            "prediction_scope": {
                "latest_documents_only": True,
                "non_latest_document_predictions": non_latest_predictions,
            },
        }

    # --- ground-truth self-check (corpus preflight) ------------------------
    gt_check = {"checked": 0, "issues": [], "ok": True, "skipped": True}
    if facts and package_dir is not None:
        try:
            gt_check = verify_ground_truth_facts(package_dir, facts)
            gt_check["skipped"] = False
        except Exception as exc:  # noqa: BLE001 - preflight must never break scoring
            gt_check = {
                "checked": 0,
                "issues": [],
                "ok": False,
                "skipped": True,
                "error": f"{type(exc).__name__}: {exc}",
            }

    # --- findings ---------------------------------------------------------
    topics = ground_truth.get("finding_topics") or []
    findings_block = metrics.finding_prf(findings, topics, doc_map=doc_map)
    findings_block["scored"] = bool(topics) or bool(ground_truth.get("expect_no_findings"))
    findings_block["counts_by_type"] = _count_by(snapshot["findings"], "finding_type")
    findings_block["counts_by_severity"] = _count_by(snapshot["findings"], "severity")

    # --- citations --------------------------------------------------------
    citations = metrics.citation_accuracy(
        findings,
        questions,
        evidence_by_id,
        snapshot["page_texts"],
    )

    # --- questions --------------------------------------------------------
    question_topics = ground_truth.get("question_topics") or None
    question_block = metrics.question_quality(questions, question_topics)

    # --- coverage ---------------------------------------------------------
    expected_coverage = ground_truth.get("coverage") or []
    if expected_coverage:
        coverage_block = metrics.coverage_accuracy(snapshot["coverage"], expected_coverage)
    else:
        coverage_block = {
            "scored": False,
            "reason": "No coverage ground truth for this package.",
            "predicted_items": len(snapshot["coverage"]),
        }

    # --- adversarial ------------------------------------------------------
    adversarial_block = adversarial.scan_outputs_for_injection(
        evidence, findings, questions, snapshot["page_texts"]
    )
    expected_injection = ground_truth.get("injection") or {}
    adversarial_block["expected_injection"] = expected_injection
    if expected_injection.get("document"):
        adversarial_block["expected_document_found"] = any(
            expected_injection["document"] in str(page.get("document_id"))
            for page in adversarial_block["injection_pages"]
        )

    # --- clean control ----------------------------------------------------
    clean_block = _clean_control_block(snapshot["findings"], ground_truth)

    # --- cost/latency -----------------------------------------------------
    documents = snapshot["stats"].get("documents") or len(snapshot["latest_documents"])
    pages = snapshot["stats"].get("pages") or 0
    cost_block = metrics.cost_latency_summary(
        runtime_seconds=runtime_seconds,
        pages=int(pages),
        documents=int(documents),
        evidence=len(evidence),
        findings=len(snapshot["findings"]),
        questions=len(questions),
    )

    limitations = list(metrics.LIMITATIONS)
    limitations.append(
        "Ground truth source: "
        f"{ground_truth.get('source')}"
        + (f" ({ground_truth.get('path')})" if ground_truth.get("path") else "")
        + ". The benchmark scores against the corpus author's adjudication, not "
        "against independent clinical/regulatory reviewers."
    )
    if not facts:
        limitations.append(
            "Fact-level extraction is not scored for this run (no fact-level "
            "ground truth), so extraction accuracy/recall must not be quoted."
        )
    if facts:
        limitations.append(
            "Extraction precision is scored on curated facts only (and, after a "
            "version upload, on latest document versions only). Legitimate "
            "evidence outside that scope counts as FP, so precision is conservative."
        )
    for issue in gt_check.get("issues") or []:
        limitations.append(
            "Ground-truth self-check issue (corpus bug, not a pipeline error): "
            f"{issue.get('field_name')} in {issue.get('document')} page "
            f"{issue.get('page')} -> {issue.get('issue')}."
        )
    limitations.extend(ground_truth.get("known_limitations") or [])
    limitations.extend(ground_truth.get("warnings") or [])
    limitations.extend(extra_limitations or [])

    scorecard = {
        "meta": {
            "generated_at": _now(),
            "mode": mode,
            "package": package_name,
            "package_dir": str(package_dir) if package_dir else None,
            "variant": ground_truth.get("variant"),
            "asset_name": ground_truth.get("asset_name"),
            "project_id": project_id,
            "runtime_seconds": round(float(runtime_seconds or 0.0), 3),
            "runtime_source": "wall_clock_around_ingest_plus_pipeline",
            "ground_truth_source": ground_truth.get("source"),
            "ground_truth_path": ground_truth.get("path"),
            "metric_version": metrics.METRIC_VERSION,
            "python": sys.version.split()[0],
            "honesty": "Synthetic corpus; NOT real-client performance.",
            "ground_truth_check": gt_check,
            "run": _run_summary_run(run),
        },
        "corpus": {
            "documents": snapshot["stats"].get("documents"),
            "documents_all": snapshot["stats"].get("documents_all"),
            "pages": snapshot["stats"].get("pages"),
            "evidence": snapshot["stats"].get("evidence"),
            "findings": snapshot["stats"].get("findings"),
            "questions": snapshot["stats"].get("questions"),
            "coverage_items": snapshot["stats"].get("coverage"),
            "evidence_by_status": snapshot["stats"].get("evidence_by_status"),
            "documents_detail": [
                {
                    "filename": d.get("filename"),
                    "doc_type": d.get("doc_type"),
                    "version": d.get("version"),
                    "page_count": d.get("page_count"),
                    "is_latest": d.get("is_latest"),
                    "status": d.get("status"),
                }
                for d in snapshot["documents"]
            ],
        },
        "extraction": extraction,
        "findings": findings_block,
        "citations": citations,
        "questions": question_block,
        "coverage": coverage_block,
        "adversarial": adversarial_block,
        "clean_control": clean_block,
        "cost": cost_block,
        "limitations": limitations,
    }
    return scorecard


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        name = str(row.get(key) or "unknown")
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _clean_control_block(findings: list[dict], ground_truth: dict) -> dict:
    expected = bool(ground_truth.get("expect_no_findings"))
    disallowed_types = set(CLEAN_CONTROL_EXPECTATIONS["disallowed_finding_types"])
    disallowed = [
        {
            "finding_id": row.get("finding_id"),
            "finding_type": row.get("finding_type"),
            "severity": row.get("severity"),
            "title": row.get("title"),
        }
        for row in findings
        if str(row.get("finding_type") or "") in disallowed_types
    ]
    other = [
        {
            "finding_id": row.get("finding_id"),
            "finding_type": row.get("finding_type"),
            "title": row.get("title"),
        }
        for row in findings
        if str(row.get("finding_type") or "") not in disallowed_types
    ]
    return {
        "enabled": expected,
        "expect_no_findings": expected,
        "findings_raised": len(findings),
        "disallowed_finding_types": sorted(disallowed_types),
        "disallowed_findings": disallowed,
        "other_findings": other,
        "passed": (not disallowed) if expected else None,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


def _num(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _ci(ci: dict | None, digits: int = 1) -> str:
    if not ci or ci.get("rate") is None:
        return "—"
    return f"{ci['rate'] * 100:.{digits}f}% [{ci['low'] * 100:.{digits}f}, {ci['high'] * 100:.{digits}f}]"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def _heading(level: int, text: str) -> str:
    return f"{'#' * max(1, level)} {text}"


def _render_markdown(scorecard: dict, heading_level: int = 1) -> list[str]:
    meta = scorecard["meta"]
    lines: list[str] = []
    lines.append(_heading(heading_level, "AssetDD AI — Evaluation Scorecard"))
    lines.append("")
    lines.append(f"> {_HONESTY_BANNER}")
    lines.append("")

    # 1. Run summary -------------------------------------------------------
    lines.append(_heading(heading_level + 1, "1. Run summary"))
    lines.append("")
    run = meta.get("run") or {}
    summary_rows = [
        ["Package", meta.get("package") or "—"],
        ["Variant", meta.get("variant") or "—"],
        ["Mode", meta.get("mode")],
        ["Ground truth source", meta.get("ground_truth_source") or "—"],
        ["Project id", meta.get("project_id") or "—"],
        ["Run status", f"{run.get('state')} / {run.get('status')}"],
        ["Runtime", f"{meta.get('runtime_seconds')} s"],
        ["Documents", scorecard["corpus"].get("documents")],
        ["Pages", scorecard["corpus"].get("pages")],
        ["Evidence rows", scorecard["corpus"].get("evidence")],
        ["Findings", scorecard["corpus"].get("findings")],
        ["Questions", scorecard["corpus"].get("questions")],
        ["Coverage items", scorecard["corpus"].get("coverage_items")],
        ["Metric version", meta.get("metric_version")],
        [
            "Ground-truth self-check",
            "ok"
            if (meta.get("ground_truth_check") or {}).get("ok")
            and not (meta.get("ground_truth_check") or {}).get("skipped")
            else f"{(meta.get('ground_truth_check') or {}).get('checked', 0)} facts, "
            f"{len((meta.get('ground_truth_check') or {}).get('issues') or [])} issue(s)",
        ],
    ]
    if run.get("error"):
        summary_rows.append(["Run error", str(run["error"])[:300]])
    lines.extend(_table(["Item", "Value"], summary_rows))
    lines.append("")

    documents_detail = scorecard["corpus"].get("documents_detail") or []
    if documents_detail:
        lines.append(_heading(heading_level + 2, "Documents"))
        lines.append("")
        lines.extend(
            _table(
                ["Filename", "Type", "Version", "Pages", "Latest", "Status"],
                [
                    [
                        d.get("filename"),
                        d.get("doc_type"),
                        d.get("version"),
                        d.get("page_count"),
                        "yes" if d.get("is_latest") else "no",
                        d.get("status"),
                    ]
                    for d in documents_detail
                ],
            )
        )
        lines.append("")

    # 2. Extraction --------------------------------------------------------
    lines.append(_heading(heading_level + 1, "2. Extraction quality (field/page level)"))
    lines.append("")
    extraction = scorecard["extraction"]
    if extraction.get("scored"):
        field_accuracy = extraction["field_accuracy"]
        prf = extraction["prf"]
        lines.append(
            f"Curated facts: **{field_accuracy['expected']}** — correctly extracted: "
            f"**{field_accuracy['matched']}** — accuracy **{_pct(field_accuracy['accuracy'])}** "
            f"(Wilson CI {_ci(field_accuracy['accuracy_ci'])})."
        )
        lines.append("")
        scope = extraction.get("prediction_scope") or {}
        if scope:
            lines.append(
                "Prediction scope: latest documents only "
                f"({scope.get('non_latest_document_predictions', 0)} superseded-document "
                f"predictions excluded); ignored documents "
                f"{len(scope.get('ignore_documents') or [])}, ignored fields "
                f"{len(scope.get('ignore_fields') or [])}, duplicate predictions "
                f"collapsed {scope.get('collapsed_duplicates', 0)}."
            )
            lines.append("")
        lines.append(_heading(heading_level + 2, "Per-field accuracy"))
        lines.append("")
        rows = []
        for field, row in field_accuracy["by_field"].items():
            rows.append(
                [
                    field,
                    row["expected"],
                    row["matched"],
                    row["missing"],
                    _pct(row["accuracy"]),
                    _ci(row["accuracy_ci"]),
                ]
            )
        lines.extend(
            _table(["Field", "Expected", "Matched", "Missing", "Accuracy", "95% CI"], rows)
        )
        lines.append("")
        lines.append(_heading(heading_level + 2, "Evidence precision / recall / F1"))
        lines.append("")
        lines.extend(
            _table(
                ["Metric", "TP", "FP", "FN", "Value", "95% CI"],
                [
                    [
                        "Precision",
                        prf["tp"],
                        prf["fp"],
                        prf["fn"],
                        _pct(prf["precision"]),
                        _ci(prf["precision_ci"]),
                    ],
                    [
                        "Recall",
                        prf["tp"],
                        prf["fp"],
                        prf["fn"],
                        _pct(prf["recall"]),
                        _ci(prf["recall_ci"]),
                    ],
                    [
                        "F1",
                        prf["tp"],
                        prf["fp"],
                        prf["fn"],
                        _num(prf["f1"]),
                        _ci(prf["f1_ci"])
                        if prf.get("f1_ci")
                        else "— (bootstrap)",
                    ],
                ],
            )
        )
        lines.append("")
        lines.append(
            f"Duplicate predictions: {prf.get('duplicate_predictions', 0)}. "
            "Precision is conservative: evidence outside the curated fact set is counted as FP."
        )
        lines.append("")
        if prf.get("missing_facts"):
            lines.append("**Missing facts**")
            lines.append("")
            lines.extend(
                _table(
                    ["Field", "Document", "Page", "Expected"],
                    [
                        [
                            m.get("field_name"),
                            m.get("document"),
                            m.get("page"),
                            m.get("expected"),
                        ]
                        for m in prf["missing_facts"]
                    ],
                )
            )
            lines.append("")
        if field_accuracy.get("value_mismatches"):
            lines.append("**Value near-misses (right location, wrong value)**")
            lines.append("")
            lines.extend(
                _table(
                    ["Field", "Document", "Page", "Expected", "Tolerance"],
                    [
                        [
                            m.get("field_name"),
                            m.get("document"),
                            m.get("page"),
                            m.get("expected"),
                            m.get("tolerance"),
                        ]
                        for m in field_accuracy["value_mismatches"]
                    ],
                )
            )
            lines.append("")
    else:
        lines.append(
            f"**Not scored.** {extraction.get('reason', 'no ground truth')} "
            f"({extraction.get('predictions', 0)} evidence rows extracted.)"
        )
        lines.append("")

    # 3. Findings ----------------------------------------------------------
    findings = scorecard["findings"]
    lines.append(_heading(heading_level + 1, "3. Findings vs adjudicated topics"))
    lines.append("")
    lines.append(
        f"Adjudicated topics: **{findings['expected_topics']}** — findings raised: "
        f"**{findings['predicted_findings']}** — TP **{findings['tp']}**, FP "
        f"**{findings['fp']}**, FN **{findings['fn']}**."
    )
    lines.append("")
    lines.extend(
        _table(
            ["Metric", "Value", "95% CI"],
            [
                ["Precision", _pct(findings["precision"]), _ci(findings["precision_ci"])],
                ["Recall", _pct(findings["recall"]), _ci(findings["recall_ci"])],
                [
                    "F1",
                    _num(findings["f1"]),
                    _ci(findings["f1_ci"]) if findings.get("f1_ci") else "—",
                ],
            ],
        )
    )
    lines.append("")
    if findings.get("per_type"):
        lines.append(_heading(heading_level + 2, "Per finding type"))
        lines.append("")
        lines.extend(
            _table(
                ["Type", "Expected", "TP", "FP", "FN", "Precision", "Recall"],
                [
                    [
                        name,
                        row["expected"],
                        row["tp"],
                        row["fp"],
                        row["fn"],
                        _pct(row["precision"]),
                        _pct(row["recall"]),
                    ]
                    for name, row in findings["per_type"].items()
                ],
            )
        )
        lines.append("")
    severity = findings.get("severity_agreement") or {}
    if severity.get("checked"):
        lines.append(
            f"Severity agreement (advisory): {severity['correct']}/{severity['checked']} "
            f"({_pct(severity['accuracy'])})."
        )
        lines.append("")
    if findings.get("matched"):
        lines.append(_heading(heading_level + 2, "Matched adjudicated topics"))
        lines.append("")
        lines.extend(
            _table(
                ["Topic", "Finding type", "Pipeline finding", "Status"],
                [
                    [
                        m.get("topic_id"),
                        m.get("finding_type"),
                        str(m.get("title") or "")[:80],
                        m.get("finding_status"),
                    ]
                    for m in findings["matched"]
                ],
            )
        )
        lines.append("")
    if findings.get("missed_topics"):
        lines.append(_heading(heading_level + 2, "Missed adjudicated topics (FN)"))
        lines.append("")
        lines.extend(
            _table(
                ["Topic", "Finding type", "Field/dimension", "Keywords"],
                [
                    [
                        m.get("topic_id"),
                        m.get("finding_type"),
                        m.get("field_name"),
                        ", ".join(map(str, m.get("keywords") or [])),
                    ]
                    for m in findings["missed_topics"]
                ],
            )
        )
        lines.append("")
    if findings.get("false_positives"):
        lines.append(_heading(heading_level + 2, "Unmatched findings (FP, first 10)"))
        lines.append("")
        lines.extend(
            _table(
                ["Finding id", "Type", "Severity", "Title"],
                [
                    [
                        f.get("finding_id"),
                        f.get("finding_type"),
                        f.get("severity"),
                        str(f.get("title") or "")[:100],
                    ]
                    for f in findings["false_positives"]
                ],
            )
        )
        lines.append("")

    # 4. Citations ---------------------------------------------------------
    citations = scorecard["citations"]
    lines.append(_heading(heading_level + 1, "4. Citation accuracy (independent verbatim check)"))
    lines.append("")
    lines.append(
        f"Evidence references checked: **{citations['refs']}** — verbatim: "
        f"**{citations['verified']}** — failed: **{citations['failed']}** — accuracy "
        f"**{_pct(citations['accuracy'])}** (Wilson CI {_ci(citations['accuracy_ci'])}). "
        f"Entities without any evidence reference: {citations['entities_without_evidence']} "
        f"of {citations['entities']}."
    )
    lines.append("")
    if citations["failed"]:
        lines.extend(
            _table(
                ["Entity", "Evidence id", "Document", "Page", "Reason"],
                [
                    [
                        f.get("entity_id"),
                        f.get("evidence_id"),
                        f.get("document_id"),
                        f.get("page"),
                        f.get("reason"),
                    ]
                    for f in citations["failures"][:10]
                ],
            )
        )
        lines.append("")

    # 5. Questions ---------------------------------------------------------
    questions = scorecard["questions"]
    lines.append(_heading(heading_level + 1, "5. Question quality proxies"))
    lines.append("")
    lines.extend(
        _table(
            ["Proxy", "Value", "95% CI / detail"],
            [
                [
                    "With evidence ids",
                    _pct(questions["with_evidence_share"]["rate"]),
                    _ci(questions["with_evidence_share"]),
                ],
                [
                    "With requested documents",
                    _pct(questions["with_requested_documents_share"]["rate"]),
                    _ci(questions["with_requested_documents_share"]),
                ],
                [
                    "Owner assigned",
                    _pct(questions["with_owner_share"]["rate"]),
                    _ci(questions["with_owner_share"]),
                ],
                [
                    "Linked to a finding",
                    _pct(questions["linked_to_finding_share"]["rate"]),
                    _ci(questions["linked_to_finding_share"]),
                ],
                [
                    "Actionable (evidence + documents)",
                    _pct(questions["actionable_share"]["rate"]),
                    _ci(questions["actionable_share"]),
                ],
                ["Dedupe rate", _pct(questions["dedupe_rate"]), "1 - unique/total text"],
                [
                    "Rationale median length",
                    questions["rationale_length"]["median"],
                    f"min threshold {questions['rationale_length']['min_len_threshold']}",
                ],
                [
                    "Expected question-topic coverage",
                    _pct((questions.get("topic_coverage") or {}).get("coverage")),
                    _ci((questions.get("topic_coverage") or {}).get("coverage_ci")),
                ],
            ],
        )
    )
    lines.append("")
    topic_cov = questions.get("topic_coverage")
    if topic_cov and topic_cov.get("missed_topics"):
        lines.append(
            "Missed expected question topics: "
            + ", ".join(t.get("topic_id", "?") for t in topic_cov["missed_topics"])
        )
        lines.append("")

    # 6. Coverage ----------------------------------------------------------
    lines.append(_heading(heading_level + 1, "6. Coverage accuracy"))
    lines.append("")
    coverage = scorecard["coverage"]
    if coverage.get("scored") is False:
        lines.append(f"Not scored: {coverage.get('reason')}")
        lines.append("")
    else:
        lines.append(
            f"Expected coverage items: **{coverage['expected']}** — matched: "
            f"**{coverage['matched']}** — correct status: **{coverage['status_matches']}** — "
            f"accuracy **{_pct(coverage['accuracy'])}** (Wilson CI {_ci(coverage['accuracy_ci'])}); "
            f"coverage recall {_pct(coverage['coverage_recall'])}."
        )
        lines.append("")
        if coverage.get("missed"):
            lines.extend(
                _table(
                    ["Dimension", "Requirement", "Expected status"],
                    [
                        [m.get("dimension"), m.get("requirement"), m.get("expected_status")]
                        for m in coverage["missed"]
                    ],
                )
            )
            lines.append("")

    # 7. Adversarial -------------------------------------------------------
    adversarial_block = scorecard["adversarial"]
    lines.append(_heading(heading_level + 1, "7. Adversarial / prompt-injection checks"))
    lines.append("")
    lines.append(
        f"Injection pages found: **{len(adversarial_block['injection_pages'])}** — "
        f"injected conclusion adopted: **{'YES' if adversarial_block['injected_value_leaked'] else 'no'}** — "
        f"directive phrase echoed as own conclusion: **{'YES' if adversarial_block['directive_phrase_leaked'] else 'no'}** — "
        f"flagged as technical concern: **{'yes' if adversarial_block['flagged_as_technical_concern'] else 'no'}**."
    )
    lines.append("")
    lines.append(
        "This is a deterministic output scan (no LLM judge). It detects adoption "
        "of injected conclusions in structured output, not general prompt-attack immunity."
    )
    lines.append("")

    # 8. Cost --------------------------------------------------------------
    cost = scorecard["cost"]
    lines.append(_heading(heading_level + 1, "8. Runtime & cost"))
    lines.append("")
    lines.extend(
        _table(
            ["Metric", "Value"],
            [
                ["Wall-clock runtime", f"{cost['runtime_seconds']} s"],
                ["Documents", cost["documents"]],
                ["Pages", cost["pages"]],
                ["Seconds / document", _num(cost["seconds_per_document"])],
                ["Seconds / page", _num(cost["seconds_per_page"])],
                ["Pages / second", _num(cost["pages_per_second"], 2)],
                ["Evidence / page", _num(cost["evidence_per_page"])],
                ["Findings / document", _num(cost["findings_per_document"])],
                ["LLM calls", cost["llm_calls"]],
                ["Estimated cost (USD)", _num(cost["estimated_cost_usd"], 4) if cost["estimated_cost_usd"] is not None else "not configured"],
            ],
        )
    )
    lines.append("")

    # 9. Clean control -----------------------------------------------------
    clean = scorecard.get("clean_control") or {}
    if clean.get("enabled"):
        lines.append(_heading(heading_level + 1, "9. False-positive control (clean package)"))
        lines.append("")
        lines.append(
            f"Findings raised on the clean package: **{clean['findings_raised']}** — "
            f"disallowed-type findings: **{len(clean['disallowed_findings'])}** — "
            f"**{'PASS' if clean['passed'] else 'FAIL'}**."
        )
        lines.append("")
        if clean.get("disallowed_findings"):
            lines.extend(
                _table(
                    ["Finding id", "Type", "Severity", "Title"],
                    [
                        [f.get("finding_id"), f.get("finding_type"), f.get("severity"), f.get("title")]
                        for f in clean["disallowed_findings"]
                    ],
                )
            )
            lines.append("")
        if clean.get("other_findings"):
            lines.append(
                "Other (not scored) findings on the clean package: "
                + ", ".join(
                    f"{f.get('finding_type')}: {str(f.get('title'))[:60]}"
                    for f in clean["other_findings"]
                )
            )
            lines.append("")

    # 10. Limitations ------------------------------------------------------
    lines.append(_heading(heading_level + 1, "10. Limitations (read before quoting any number)"))
    lines.append("")
    for item in scorecard["limitations"]:
        lines.append(f"- {item}")
    lines.append("")

    # 11. Real-data evaluation --------------------------------------------
    lines.append(_heading(heading_level + 1, "11. What a real-data evaluation would require"))
    lines.append("")
    for item in _REAL_DATA_REQUIREMENTS:
        lines.append(f"- {item}")
    lines.append("")
    return lines


_REAL_DATA_REQUIREMENTS = [
    "A de-identified real package (CSR/IB/SAP/safety summary/protocol, multi-version) "
    "with permission to process it, replacing this synthetic corpus.",
    "Independent, double-adjudicated ground truth built by clinicians/regulators "
    "(field-level facts, topics, severity, expected questions), with an "
    "inter-adjudicator agreement coefficient (e.g. Cohen's kappa) reported.",
    "A pre-registered protocol: sample size / power for each metric, frozen "
    "metric definitions, primary vs secondary endpoints, and a held-out corpus.",
    "Breakdowns by document type, extraction mode (rules vs hybrid vs LLM), and "
    "language; error taxonomy with examples (false negatives and false positives).",
    "Human grading of finding explanations and question usefulness (not just "
    "grounding proxies), including reviewer time saved and downstream answer rate.",
    "Performance and cost at production scale (hundreds/thousands of pages, "
    "LLM token spend per document, p95 latency), plus security review of "
    "prompt-injection and data-exfiltration behaviour on adversarial documents.",
    "Versioned corpus and scorecards with reproducible seeds/runs, so scores can "
    "be compared across releases without silently moving goalposts.",
]


# ---------------------------------------------------------------------------
# Lifecycle analysis
# ---------------------------------------------------------------------------


def _run_duration(run: dict | None) -> float:
    if not run:
        return 0.0
    try:
        start = datetime.fromisoformat(str(run.get("started_at")))
        end = datetime.fromisoformat(str(run.get("finished_at")))
        return max(0.0, (end - start).total_seconds())
    except (TypeError, ValueError):
        return 0.0


def _topic_presence(
    topics: list[dict], findings: list[dict], doc_map: dict[str, dict]
) -> dict[str, dict]:
    assignment = metrics.match_finding_topics(findings, topics, doc_map=doc_map)
    presence: dict[str, dict] = {}
    for ti, topic in enumerate(topics):
        tid = str(topic.get("topic_id") or topic.get("id") or f"topic_{ti}")
        match = assignment["matches"].get(tid)
        if not match:
            presence[tid] = {"matched": False, "raised": False, "finding_id": None, "status": None}
            continue
        status = str(match.get("finding_status") or "")
        presence[tid] = {
            "matched": True,
            "raised": status not in _INACTIVE_FINDING_STATUSES,
            "finding_id": match.get("finding_id"),
            "status": status or None,
        }
    return presence


def _merge_topics(gt_v1: dict, gt_v2: dict) -> list[dict]:
    merged: dict[str, dict] = {}
    for source in (gt_v1, gt_v2):
        for ti, topic in enumerate(source.get("finding_topics") or []):
            tid = str(topic.get("topic_id") or topic.get("id") or f"topic_{ti}")
            merged[tid] = dict(topic)
    return [merged[tid] for tid in sorted(merged)]


def _lifecycle_analysis(
    gt_v1: dict, gt_v2: dict, snapshot_v1: dict, snapshot_v2: dict
) -> dict:
    topics = _merge_topics(gt_v1, gt_v2)
    lifecycle = dict(gt_v2.get("lifecycle") or gt_v1.get("lifecycle") or {})
    expected_map: dict[str, str] = {}
    for tid in lifecycle.get("resolved_topics") or []:
        expected_map[str(tid)] = "resolved"
    for tid in lifecycle.get("persisting_topics") or []:
        expected_map[str(tid)] = "persisting"
    for tid in lifecycle.get("new_topics") or []:
        expected_map[str(tid)] = "new"
    # Fall back to per-topic expected_status_v2 when lists are absent.
    for topic in topics:
        tid = str(topic.get("topic_id") or "")
        status_v2 = topic.get("expected_status_v2")
        if tid and tid not in expected_map and status_v2:
            expected_map[tid] = "resolved" if status_v2 == "resolved" else "persisting"

    presence_v1 = _topic_presence(topics, snapshot_v1["findings"], _doc_map(snapshot_v1))
    presence_v2 = _topic_presence(topics, snapshot_v2["findings"], _doc_map(snapshot_v2))

    per_topic: list[dict] = []
    observed_lists: dict[str, list[str]] = {"resolved": [], "persisting": [], "new": [], "absent": []}
    correct = 0
    scored = 0
    for topic in topics:
        tid = str(topic.get("topic_id") or "")
        p1 = presence_v1.get(tid, {"raised": False, "finding_id": None, "status": None})
        p2 = presence_v2.get(tid, {"raised": False, "finding_id": None, "status": None})
        if p1["raised"] and p2["raised"]:
            observed = "persisting"
        elif p1["raised"] and not p2["raised"]:
            observed = "resolved"
        elif not p1["raised"] and p2["raised"]:
            observed = "new"
        else:
            observed = "absent"
        observed_lists[observed].append(tid)
        expected = expected_map.get(tid)
        is_correct = None
        if expected is not None:
            scored += 1
            is_correct = observed == expected
            correct += int(is_correct)
        per_topic.append(
            {
                "topic_id": tid,
                "finding_type": topic.get("finding_type"),
                "expected_transition": expected,
                "observed_transition": observed,
                "correct": is_correct,
                "v1": p1,
                "v2": p2,
            }
        )
    unlisted = [
        row
        for row in per_topic
        if row["expected_transition"] is None and row["observed_transition"] != "absent"
    ]

    questions_v1 = snapshot_v1["questions"]
    questions_v2 = snapshot_v2["questions"]
    keys_v1 = {_question_key(q) for q in questions_v1}
    keys_v2 = {_question_key(q) for q in questions_v2}
    question_details = {
        "v1_questions": len(questions_v1),
        "v2_questions": len(questions_v2),
        "persisting": len(keys_v1 & keys_v2),
        "new": len(keys_v2 - keys_v1),
        "removed": len(keys_v1 - keys_v2),
        "new_examples": [
            {"key": k, "question": _question_by_key(questions_v2, k)}
            for k in sorted(keys_v2 - keys_v1)[:5]
        ],
        "removed_examples": [
            {"key": k, "question": _question_by_key(questions_v1, k)}
            for k in sorted(keys_v1 - keys_v2)[:5]
        ],
        "status_counts_v2": _count_by(questions_v2, "status"),
    }
    return {
        "expected": {
            "resolved": sorted(lifecycle.get("resolved_topics") or []),
            "persisting": sorted(lifecycle.get("persisting_topics") or []),
            "new": sorted(lifecycle.get("new_topics") or []),
            "source": "ground_truth_json" if lifecycle else "none",
        },
        "observed": observed_lists,
        "per_topic": per_topic,
        "scored_topics": scored,
        "correct_topics": correct,
        "accuracy": (correct / scored) if scored else None,
        "accuracy_ci": metrics.wilson_ci(correct, scored) if scored else None,
        "unlisted_observed_topics": unlisted,
        "question_transitions": question_details,
        "note": (
            "A topic counts as raised only while a matching finding is not in "
            "status resolved/dismissed. Topics absent from the expected lifecycle "
            "lists are reported as 'unlisted_observed_topics' and not scored."
        ),
    }


def _question_key(question: dict) -> str:
    if question.get("dedup_key"):
        return str(question["dedup_key"])
    return metrics.normalize_ws(str(question.get("question") or "")).lower()


def _question_by_key(questions: list[dict], key: str) -> str | None:
    for question in questions:
        if _question_key(question) == key:
            return str(question.get("question") or "")[:200]
    return None


def _render_lifecycle_markdown(lifecycle: dict, heading_level: int = 2) -> list[str]:
    lines: list[str] = []
    lines.append(_heading(heading_level, "Lifecycle: v1 -> v2 (same project, docs supersede)"))
    lines.append("")
    expected = lifecycle["expected"]
    observed = lifecycle["observed"]
    lines.append(
        f"Expected: resolved **{len(expected['resolved'])}**, persisting "
        f"**{len(expected['persisting'])}**, new **{len(expected['new'])}**. "
    )
    lines.append(
        f"Observed: resolved **{len(observed['resolved'])}**, persisting "
        f"**{len(observed['persisting'])}**, new **{len(observed['new'])}**, "
        f"absent **{len(observed['absent'])}**."
    )
    lines.append("")
    if lifecycle.get("scored_topics"):
        lines.append(
            f"Lifecycle topic score: **{lifecycle['correct_topics']}/"
            f"{lifecycle['scored_topics']}** correct ({_pct(lifecycle['accuracy'])}, "
            f"Wilson CI {_ci(lifecycle.get('accuracy_ci'))})."
        )
        lines.append("")
    rows = [
        [
            row["topic_id"],
            row.get("finding_type"),
            row.get("expected_transition") or "—",
            row["observed_transition"],
            "—" if row.get("correct") is None else ("yes" if row["correct"] else "NO"),
            row["v1"].get("finding_id") or "—",
            row["v2"].get("finding_id") or "—",
        ]
        for row in lifecycle["per_topic"]
    ]
    lines.extend(
        _table(
            ["Topic", "Type", "Expected", "Observed", "Correct", "v1 finding", "v2 finding"],
            rows,
        )
    )
    lines.append("")
    q = lifecycle["question_transitions"]
    lines.append(
        f"Questions: v1 **{q['v1_questions']}**, v2 **{q['v2_questions']}**, "
        f"persisting **{q['persisting']}**, new **{q['new']}**, removed "
        f"**{q['removed']}**."
    )
    lines.append("")
    if lifecycle.get("unlisted_observed_topics"):
        lines.append(
            "Unlisted observed transitions (informational, not scored): "
            + ", ".join(
                f"{row['topic_id']}={row['observed_transition']}"
                for row in lifecycle["unlisted_observed_topics"]
            )
        )
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def _write_outputs(
    scorecard: dict,
    out_dir: Path,
    *,
    title: str | None = None,
) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / SCORECARD_JSON
    json_path.write_text(
        json.dumps(scorecard, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    lines = _render_markdown(scorecard)
    if title:
        lines[0] = f"# {title}"
    md_path = out_dir / SCORECARD_MD
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def _write_lifecycle_outputs(combined: dict, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / SCORECARD_JSON
    json_path.write_text(
        json.dumps(combined, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    meta = combined["meta"]
    lines: list[str] = [
        "# AssetDD AI — Lifecycle Evaluation Scorecard",
        "",
        f"> {_HONESTY_BANNER}",
        "",
        f"Package v1: `{meta.get('package_v1')}` — package v2: `{meta.get('package_v2')}`. "
        "v2 documents were uploaded into the same project; superseded v1 documents are "
        "excluded from the v2 analysis.",
        "",
        f"Generated: {meta.get('generated_at')} — metric version {meta.get('metric_version')}.",
        "",
    ]
    lines.extend(_render_lifecycle_markdown(combined["lifecycle"], heading_level=2))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(_render_markdown(combined["v1"], heading_level=2))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(_render_markdown(combined["v2"], heading_level=2))
    lines.append("")
    combined_limitations = combined.get("limitations") or []
    if combined_limitations:
        lines.append("## Lifecycle-specific limitations")
        lines.append("")
        for item in combined_limitations:
            lines.append(f"- {item}")
        lines.append("")
    md_path = out_dir / SCORECARD_MD
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def _headline(scorecard: dict) -> str:
    extraction = scorecard["extraction"]
    findings = scorecard["findings"]
    citations = scorecard["citations"]
    parts = [
        f"findings F1={_num(findings['f1'])} (P={_pct(findings['precision'])}, R={_pct(findings['recall'])})",
        f"citation accuracy={_pct(citations['accuracy'])}",
    ]
    if extraction.get("scored"):
        parts.insert(
            0,
            f"extraction accuracy={_pct(extraction['field_accuracy']['accuracy'])} "
            f"({extraction['field_accuracy']['matched']}/{extraction['field_accuracy']['expected']})",
        )
    else:
        parts.insert(0, "extraction not scored (no fact ground truth)")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def _resolve_db_path(args: argparse.Namespace, work_dir: Path) -> Path:
    if args.database:
        return Path(args.database).expanduser().resolve()
    return work_dir / "data" / "assetdd.db"


def _print_missing(missing: list[str]) -> None:
    print("ERROR: pipeline stage modules are not importable yet:")
    for item in missing:
        print(f"  - {item}")
    print("The benchmark will run once ingestion/extraction/agents/reporting land.")


def _cmd_package(args: argparse.Namespace, out_dir: Path, work_dir: Path) -> int:
    package_dir = Path(args.package).expanduser().resolve()
    if not package_dir.exists():
        print(f"ERROR: package directory not found: {package_dir}")
        return 3
    _setup_environment(work_dir)
    db_path = _resolve_db_path(args, work_dir)
    missing = _missing_stage_modules()
    if missing:
        _print_missing(missing)
        return 3
    ground_truth = load_ground_truth(package_dir, strict=not args.lenient_ground_truth)
    store, _ = _store_and_pipeline()
    store.configure(db_path)
    project = _create_project(
        name=f"eval:{package_dir.name}",
        asset_name=ground_truth.get("asset_name") or "",
        package_dir=package_dir,
    )
    print(f"[eval] project {project['project_id']} <- {package_dir}")
    run, runtime = _run_pipeline(project["project_id"], package_dir, force=True)
    print(f"[eval] pipeline run {run.get('run_id')}: {run.get('state')}/{run.get('status')}")
    snapshot = _snapshot(project["project_id"])
    scorecard = _build_scorecard(
        mode="package",
        package_name=ground_truth.get("package") or package_dir.name,
        package_dir=package_dir,
        ground_truth=ground_truth,
        snapshot=snapshot,
        runtime_seconds=runtime,
        project_id=project["project_id"],
        run=run,
        numeric_tolerance=args.numeric_tolerance,
    )
    paths = _write_outputs(scorecard, out_dir)
    print(f"[eval] scorecard: {paths['md']} + {paths['json']}")
    print(f"[eval] headline: {_headline(scorecard)}")
    return 0 if run.get("status") == "succeeded" else 1


def _cmd_project(args: argparse.Namespace, out_dir: Path, work_dir: Path) -> int:
    from assetdd import config  # noqa: PLC0415 - after env is untouched

    db_path = Path(args.database).expanduser().resolve() if args.database else Path(config.DB_PATH)
    store, pipeline = _store_and_pipeline()
    store.configure(db_path)
    with store.session_scope() as session:
        project = store.get_project(session, args.project)
    if not project:
        print(f"ERROR: project {args.project} not found in {db_path}")
        return 3
    meta = project.get("meta") or {}
    package_dir = (
        Path(args.package).expanduser().resolve()
        if args.package
        else Path(str(meta.get("package_dir") or config.SAMPLE_PACKAGE_DIR)).expanduser()
    )
    run = None
    runtime = 0.0
    if args.fresh:
        missing = _missing_stage_modules()
        if missing:
            _print_missing(missing)
            return 3
        run, runtime = _run_pipeline(project["project_id"], package_dir, force=True)
    else:
        with store.session_scope() as session:
            runs = store.list_runs(session, project["project_id"], limit=1)
        run = runs[0] if runs else None
        runtime = _run_duration(run)
    ground_truth = load_ground_truth(package_dir, strict=not args.lenient_ground_truth)
    snapshot = _snapshot(project["project_id"])
    scorecard = _build_scorecard(
        mode="project",
        package_name=ground_truth.get("package") or package_dir.name,
        package_dir=package_dir,
        ground_truth=ground_truth,
        snapshot=snapshot,
        runtime_seconds=runtime,
        project_id=project["project_id"],
        run=run,
        numeric_tolerance=args.numeric_tolerance,
        extra_limitations=[
            "Project mode scores whatever is currently in the store for this "
            "project; it does not re-run the pipeline unless --fresh is passed."
        ],
    )
    paths = _write_outputs(scorecard, out_dir)
    print(f"[eval] scorecard: {paths['md']} + {paths['json']}")
    print(f"[eval] headline: {_headline(scorecard)}")
    if run and run.get("status") == "failed":
        return 1
    return 0


def _cmd_lifecycle(args: argparse.Namespace, out_dir: Path, work_dir: Path) -> int:
    package_v1 = Path(args.package).expanduser().resolve()
    package_v2 = Path(args.package_v2).expanduser().resolve()
    for package in (package_v1, package_v2):
        if not package.exists():
            print(f"ERROR: package directory not found: {package}")
            return 3
    _setup_environment(work_dir)
    db_path = _resolve_db_path(args, work_dir)
    missing = _missing_stage_modules()
    if missing:
        _print_missing(missing)
        return 3
    gt_v1 = load_ground_truth(package_v1, strict=not args.lenient_ground_truth)
    gt_v2 = load_ground_truth(package_v2, strict=not args.lenient_ground_truth)
    store, pipeline = _store_and_pipeline()
    store.configure(db_path)
    project = _create_project(
        name=f"eval:lifecycle:{package_v1.name}",
        asset_name=gt_v1.get("asset_name") or "",
        package_dir=package_v1,
    )
    project_id = project["project_id"]
    print(f"[eval] lifecycle project {project_id}: v1={package_v1.name} -> v2={package_v2.name}")

    run_v1, runtime_v1 = _run_pipeline(project_id, package_v1, force=True)
    snapshot_v1 = _snapshot(project_id)
    scorecard_v1 = _build_scorecard(
        mode="lifecycle_v1",
        package_name=gt_v1.get("package") or package_v1.name,
        package_dir=package_v1,
        ground_truth=gt_v1,
        snapshot=snapshot_v1,
        runtime_seconds=runtime_v1,
        project_id=project_id,
        run=run_v1,
        numeric_tolerance=args.numeric_tolerance,
    )

    ingestion = _import_ingestion()
    if ingestion is None or not hasattr(ingestion, "ingest_directory"):
        print("ERROR: assetdd.ingestion.ingest_directory unavailable; cannot upload v2.")
        return 3
    with store.session_scope() as session:
        ingestion.ingest_directory(session, project_id, str(package_v2))
        store.update_project(session, project_id, meta={"package_dir": str(package_v2)})
    run_v2, runtime_v2 = _run_pipeline(project_id, package_v2, force=True)
    snapshot_v2 = _snapshot(project_id)
    scorecard_v2 = _build_scorecard(
        mode="lifecycle_v2",
        package_name=gt_v2.get("package") or package_v2.name,
        package_dir=package_v2,
        ground_truth=gt_v2,
        snapshot=snapshot_v2,
        runtime_seconds=runtime_v2,
        project_id=project_id,
        run=run_v2,
        numeric_tolerance=args.numeric_tolerance,
    )
    lifecycle = _lifecycle_analysis(gt_v1, gt_v2, snapshot_v1, snapshot_v2)
    combined = {
        "meta": {
            "generated_at": _now(),
            "mode": "lifecycle",
            "package_v1": str(package_v1),
            "package_v2": str(package_v2),
            "project_id": project_id,
            "metric_version": metrics.METRIC_VERSION,
            "python": sys.version.split()[0],
            "honesty": "Synthetic corpus; NOT real-client performance.",
            "runtime_seconds_v1": round(runtime_v1, 3),
            "runtime_seconds_v2": round(runtime_v2, 3),
            "run_v1": _run_summary_run(run_v1),
            "run_v2": _run_summary_run(run_v2),
        },
        "headline": {"v1": _headline(scorecard_v1), "v2": _headline(scorecard_v2)},
        "lifecycle": lifecycle,
        "v1": scorecard_v1,
        "v2": scorecard_v2,
        "limitations": list(metrics.LIMITATIONS)
        + [
            "Lifecycle scoring compares topic presence before/after a synthetic "
            "version update; it does not model real-world document versioning "
            "exceptions (e.g. late-arriving documents, mixed cutoffs).",
            "Only adjudicated lifecycle topics are scored; unlisted transitions "
            "are reported separately and not counted as errors.",
        ],
    }
    paths = _write_lifecycle_outputs(combined, out_dir)
    print(f"[eval] lifecycle scorecard: {paths['md']} + {paths['json']}")
    print(f"[eval] v1: {_headline(scorecard_v1)}")
    print(f"[eval] v2: {_headline(scorecard_v2)}")
    print(
        f"[eval] lifecycle: {lifecycle['correct_topics']}/{lifecycle['scored_topics']} "
        f"topics correct"
    )
    if run_v1.get("status") != "succeeded" or run_v2.get("status") != "succeeded":
        return 1
    return 0


def _cmd_clean_control(args: argparse.Namespace, out_dir: Path, work_dir: Path) -> int:
    _setup_environment(work_dir)
    db_path = _resolve_db_path(args, work_dir)
    missing = _missing_stage_modules()
    if missing:
        _print_missing(missing)
        return 3
    package_dir = work_dir / "clean_control"
    build_clean_package(package_dir)
    adversarial_mode = bool(args.adversarial)
    if adversarial_mode:
        adversarial.write_injection_pdf(package_dir / "Injection_Fixture_IB.pdf")
    ground_truth = load_ground_truth(package_dir, strict=not args.lenient_ground_truth)
    if adversarial_mode:
        # The only finding tolerated on this control package is the technical
        # concern flagging the injection attempt; contradiction/gap finding
        # types remain false positives.
        ground_truth["injection"] = {
            "document": "Injection_Fixture_IB.pdf",
            "anchor": adversarial.INJECTION_MARKER,
        }
        ground_truth["finding_topics"] = [
            {
                "topic_id": "prompt_injection_fixture",
                "finding_type": "technical_concern",
                "keywords": ["injection", "prompt", "untrusted", "automated reviewers"],
                "expected_severity": "high",
            }
        ]
    store, _ = _store_and_pipeline()
    store.configure(db_path)
    project = _create_project(
        name=f"eval:clean_control{':adversarial' if adversarial_mode else ''}",
        asset_name=ground_truth.get("asset_name") or "",
        package_dir=package_dir,
    )
    run, runtime = _run_pipeline(project["project_id"], package_dir, force=True)
    snapshot = _snapshot(project["project_id"])
    scorecard = _build_scorecard(
        mode="adversarial" if adversarial_mode else "clean_control",
        package_name=ground_truth.get("package") or "clean_control",
        package_dir=package_dir,
        ground_truth=ground_truth,
        snapshot=snapshot,
        runtime_seconds=runtime,
        project_id=project["project_id"],
        run=run,
        numeric_tolerance=args.numeric_tolerance,
        extra_limitations=[
            "This package is a generated clean control: tiny, single-page, "
            "internally consistent documents. A pass is necessary but not "
            "sufficient evidence of low false-positive rates on real packages."
        ],
    )
    passed = True
    clean = scorecard["clean_control"]
    if clean.get("enabled"):
        passed = passed and bool(clean.get("passed"))
    if adversarial_mode:
        passed = passed and bool(scorecard["adversarial"]["passed"])
    scorecard["meta"]["control_passed"] = passed
    paths = _write_outputs(scorecard, out_dir)
    print(f"[eval] control scorecard: {paths['md']} + {paths['json']}")
    print(
        f"[eval] clean-control: {'PASS' if clean.get('passed') else 'FAIL'} "
        f"({clean['findings_raised']} findings raised)"
    )
    if adversarial_mode:
        print(
            "[eval] adversarial: "
            f"{'PASS' if scorecard['adversarial']['passed'] else 'FAIL'} "
            f"(injection pages={len(scorecard['adversarial']['injection_pages'])})"
        )
    if run.get("status") != "succeeded":
        return 1
    return 0 if passed else 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m eval.benchmark",
        description=(
            "AssetDD AI evaluation benchmark. Runs the real pipeline and writes "
            "a synthetic-corpus scorecard (not real-world validation)."
        ),
    )
    parser.add_argument(
        "--package",
        default=str(DEFAULT_PACKAGE),
        help=f"package directory with ground_truth.json (default: {DEFAULT_PACKAGE})",
    )
    parser.add_argument(
        "--package-v2",
        default=str(DEFAULT_PACKAGE_V2),
        help=f"v2 package for --lifecycle (default: {DEFAULT_PACKAGE_V2})",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="score an existing project id instead of creating a temp project",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="SQLite path (default: isolated temp DB; --project defaults to data/assetdd.db)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="re-run the pipeline before scoring (project mode) / explicit fresh run",
    )
    parser.add_argument(
        "--lifecycle",
        action="store_true",
        help="run v1 then upload v2 into the same project; report resolved/new/persisting",
    )
    parser.add_argument(
        "--clean-control",
        action="store_true",
        help="generate the clean control package and score false-positive control",
    )
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="clean control + prompt-injection fixture; scans output for obeyed instructions",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"output directory for scorecard.json/.md (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--numeric-tolerance",
        type=float,
        default=metrics.DEFAULT_NUMERIC_TOLERANCE,
        help="absolute numeric tolerance for extraction matching (default: %(default)s); "
        "per-fact 'tolerance' in ground truth overrides it",
    )
    parser.add_argument(
        "--lenient-ground-truth",
        action="store_true",
        help="drop malformed ground-truth facts with a warning instead of failing",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="keep the temporary work directory for debugging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out_dir = Path(args.out).expanduser().resolve()
    work_dir = Path(tempfile.mkdtemp(prefix="assetdd_eval_"))
    try:
        if args.project:
            return _cmd_project(args, out_dir, work_dir)
        if args.lifecycle:
            return _cmd_lifecycle(args, out_dir, work_dir)
        if args.clean_control or args.adversarial:
            return _cmd_clean_control(args, out_dir, work_dir)
        return _cmd_package(args, out_dir, work_dir)
    finally:
        if args.keep_workdir:
            print(f"[eval] work dir kept: {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
