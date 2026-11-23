"""Evaluation metrics for AssetDD AI (eval-dev owned).

Design rules
------------
* **Pure functions**: no database, no file I/O, no network, no global state.
  Inputs are plain dicts shaped like store rows / ground-truth facts; outputs
  are JSON-serialisable dicts.  The benchmark harness (``eval.benchmark``) is
  responsible for loading rows/pages from the store and writing reports.
* **One-to-one matching**: every metric that compares predictions to a curated
  ground truth makes a deterministic greedy bipartite match.  Unmatched
  predictions count as false positives; unmatched expected items are false
  negatives.
* **Honest denominators**: ``extraction_field_accuracy`` scores only facts the
  corpus authors curated.  That makes recall meaningful but precision
  *conservative* (legitimate evidence outside the curated fact set is scored
  as FP).  Scorecards must repeat this caveat; see ``LIMITATIONS``.

Terminology
-----------
expected / ground truth
    ``{"field_name", "document" | "document_type", "page", "value_num" | "value_text", ...}``
    for facts; ``{"topic_id", "finding_type", "field_name" / keywords, "documents"}``
    for finding/question topics.
predicted
    Evidence rows from ``store.get_evidence`` or finding/question rows from
    ``store.list_findings`` / ``store.list_questions`` (plain dicts).
"""
from __future__ import annotations

import math
import random
import re
from typing import Any, Iterable, Mapping, Sequence

from assetdd.schemas import normalize_date, normalize_ws

METRIC_VERSION = "eval-1.0.0"
DEFAULT_NUMERIC_TOLERANCE = 0.01
DEFAULT_PAGE_SLACK = 0
DEFAULT_Z = 1.959963984540054  # 95% two-sided
DEFAULT_RATIONALE_MIN_LEN = 40

LIMITATIONS: list[str] = [
    "Synthetic corpus: all scores are measured on a hand-built, synthetic "
    "due-diligence package. They demonstrate that the pipeline works on this "
    "corpus; they are NOT evidence of real-client performance or of accuracy "
    "on genuine pharma documents.",
    "Scoring is field/topic-level, not document-level: extraction is scored "
    "against a curated subset of facts (fields x document x page), so recall "
    "of uncurated facts is not measured and extraction precision is "
    "conservative (legitimate evidence outside the curated set counts as FP).",
    "Finding matching is tolerant to wording by design (finding_type + "
    "field/rule + document pair + keywords). A 'match' means the pipeline "
    "raised the adjudicated topic somewhere; it does not grade the quality of "
    "the explanation, severity, or recommended action.",
    "Citation accuracy checks only verbatim quote containment on the cited "
    "page. It does not prove the quote is the best evidence, nor that the "
    "finding's interpretation is correct.",
    "Question metrics are proxies (grounding, owner, requested documents, "
    "dedupe, expected-topic coverage), not a human usefulness rating.",
    "Wilcoxon/Wilson intervals below are statistical intervals on corpus "
    "proportions; with a small curated corpus they are wide and should not be "
    "read as production confidence.",
    "Runtime/cost numbers are from a single local run on one machine and are "
    "not a throughput benchmark.",
]

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-_./]*")


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _ratio(k: int, n: int) -> float | None:
    return (k / n) if n else None


def wilson_ci(successes: int, trials: int, z: float = DEFAULT_Z) -> dict:
    """Wilson score interval for a binomial proportion (no scipy needed).

    ``trials == 0`` returns ``rate/low/high = None`` rather than inventing 0.
    """
    k = max(0, int(successes))
    n = int(trials)
    if n <= 0:
        return {"successes": k, "trials": 0, "rate": None, "low": None, "high": None}
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return {
        "successes": k,
        "trials": n,
        "rate": _round(p),
        "low": _round(max(0.0, centre - margin)),
        "high": _round(min(1.0, centre + margin)),
        "z": _round(z),
        "method": "wilson",
    }


def bootstrap_f1_ci(
    tp: int, fp: int, fn: int, n_boot: int = 2000, seed: int = 0
) -> dict | None:
    """Percentile bootstrap CI for F1, seeded (deterministic).

    Treats (tp, fp, fn) as counts from a multinomial draw over the observed
    units and resamples the confusion counts. Deterministic for a fixed seed.
    """
    total = int(tp) + int(fp) + int(fn)
    if total <= 0:
        return None
    counts = [int(tp), int(fp), int(fn)]
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(int(n_boot)):
        sample = rng.choices((0, 1, 2), weights=counts, k=total)
        s_tp = sample.count(0)
        s_fp = sample.count(1)
        s_fn = sample.count(2)
        p = s_tp / (s_tp + s_fp) if (s_tp + s_fp) else 0.0
        r = s_tp / (s_tp + s_fn) if (s_tp + s_fn) else 0.0
        values.append((2 * p * r / (p + r)) if (p + r) else 0.0)
    values.sort()
    lo = values[int(0.025 * (len(values) - 1))]
    hi = values[int(0.975 * (len(values) - 1))]
    return {
        "low": _round(lo),
        "high": _round(hi),
        "method": "bootstrap_percentile",
        "n_boot": int(n_boot),
        "seed": int(seed),
    }


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "precision": _round(precision),
        "recall": _round(recall),
        "f1": _round(f1),
        "precision_ci": wilson_ci(tp, tp + fp),
        "recall_ci": wilson_ci(tp, tp + fn),
        "f1_ci": bootstrap_f1_ci(tp, fp, fn),
    }


def _norm_text(value: Any) -> str:
    return normalize_ws("" if value is None else str(value)).lower()


def _to_float(value_num: Any, value_text: Any) -> float | None:
    if value_num is not None:
        try:
            return float(value_num)
        except (TypeError, ValueError):
            pass
    if value_text is None:
        return None
    m = _NUM_RE.search(str(value_text))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _norm_doc_token(value: Any) -> str:
    token = _norm_text(value)
    if not token:
        return ""
    token = token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return token


def _doc_tokens(record: Mapping[str, Any], doc_map: Mapping[str, Mapping[str, Any]] | None = None) -> set[str]:
    """Document identifiers a record can be matched on (filename / type / id)."""
    tokens: set[str] = set()

    def add(value: Any) -> None:
        tok = _norm_doc_token(value)
        if not tok:
            return
        tokens.add(tok)
        stem = re.sub(r"\.(pdf|docx?|pptx?|xlsx?|txt)$", "", tok)
        if stem:
            tokens.add(stem)

    for key in ("document", "filename", "document_type", "doc_type", "document_id"):
        add(record.get(key))
    docs = record.get("documents") or record.get("_documents") or []
    if isinstance(docs, (str, Mapping)):
        docs = [docs]
    for doc in docs:
        if isinstance(doc, Mapping):
            for key in ("document", "filename", "document_type", "doc_type", "document_id"):
                add(doc.get(key))
        else:
            add(doc)
    doc_id = record.get("document_id")
    if doc_map and doc_id and doc_id in doc_map:
        row = doc_map[doc_id]
        for key in ("filename", "doc_type", "document_type", "document_id"):
            add(row.get(key))
    return tokens


def _docs_match(
    expected: Mapping[str, Any],
    predicted: Mapping[str, Any],
    doc_map: Mapping[str, Mapping[str, Any]] | None = None,
) -> bool:
    exp_tokens = _doc_tokens(expected)
    pred_tokens = _doc_tokens(predicted, doc_map)
    if not exp_tokens or not pred_tokens:
        # No comparable document metadata on one side: do not manufacture a
        # mismatch. (Reported in the scorecard limitations.)
        return True
    return bool(exp_tokens & pred_tokens)


def _page_match(expected_page: Any, predicted_page: Any, slack: int = DEFAULT_PAGE_SLACK) -> bool:
    if expected_page is None:
        return True
    try:
        exp = int(expected_page)
        got = int(predicted_page)
    except (TypeError, ValueError):
        return False
    return abs(exp - got) <= max(0, int(slack))


def _tolerance_for(field: str, default: float, overrides: Mapping[str, float] | None) -> float:
    if overrides:
        if field in overrides:
            return float(overrides[field])
        low = field.lower()
        if low in overrides:
            return float(overrides[low])
    return float(default)


def _value_matches(
    expected: Mapping[str, Any],
    predicted: Mapping[str, Any],
    field: str,
    tolerance: float,
) -> tuple[bool, dict]:
    exp_num = expected.get("value_num")
    pred_num = predicted.get("value_num")
    exp_text = expected.get("value_text")
    pred_text = predicted.get("value_text")
    exp_float = _to_float(exp_num, exp_text)
    pred_float = _to_float(pred_num, pred_text)
    detail: dict[str, Any] = {
        "expected": exp_float if exp_float is not None else exp_text,
        "predicted": pred_float if pred_float is not None else pred_text,
    }
    if exp_float is not None and pred_float is not None:
        delta = abs(exp_float - pred_float)
        detail["delta"] = _round(delta)
        detail["tolerance"] = tolerance
        return delta <= tolerance, detail
    # Non-numeric (or one-sided numeric): compare normalised text / dates.
    exp_norm = _norm_text(exp_text if exp_text is not None else exp_num)
    pred_norm = _norm_text(pred_text if pred_text is not None else pred_num)
    exp_date = normalize_date(exp_text if exp_text is not None else exp_num)
    pred_date = normalize_date(pred_text if pred_text is not None else pred_num)
    if exp_date and pred_date:
        detail["expected_date"] = exp_date
        detail["predicted_date"] = pred_date
        return exp_date == pred_date, detail
    if exp_norm and exp_norm == pred_norm:
        return True, detail
    # Tolerant free-text matching: the fact's value_text is often a short phrase
    # taken from a longer extracted sentence (or vice versa); document + page +
    # field are already matched, so containment / high token recall is safe and
    # avoids punishing wording differences in e.g. study design text.
    if len(exp_norm) >= 8 and len(pred_norm) >= 8:
        if exp_norm in pred_norm or pred_norm in exp_norm:
            detail["text_match"] = "containment"
            return True, detail
        exp_tokens = set(_WORD_RE.findall(exp_norm))
        pred_tokens = set(_WORD_RE.findall(pred_norm))
        if exp_tokens and len(exp_tokens & pred_tokens) / len(exp_tokens) >= 0.8:
            detail["text_match"] = "token_recall>=0.8"
            return True, detail
    return False, detail


def _match_facts(
    expected_facts: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    doc_map: Mapping[str, Mapping[str, Any]] | None = None,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    field_tolerances: Mapping[str, float] | None = None,
    page_slack: int = DEFAULT_PAGE_SLACK,
) -> dict:
    """Shared one-to-one matcher for extraction metrics."""
    used: set[int] = set()
    matches: list[dict] = []
    missed: list[dict] = []
    value_mismatches: list[dict] = []

    for i, fact in enumerate(expected_facts):
        field = str(fact.get("field_name") or fact.get("field") or "").strip().lower()
        # Per-fact tolerance wins over field/default tolerances (ground truth may
        # carry e.g. {"tolerance": 0.05} for a rounded percentage).
        if fact.get("tolerance") is not None:
            try:
                tolerance = float(fact["tolerance"])
            except (TypeError, ValueError):
                tolerance = _tolerance_for(field, numeric_tolerance, field_tolerances)
        else:
            tolerance = _tolerance_for(field, numeric_tolerance, field_tolerances)
        candidates: list[tuple[float, int, dict]] = []
        near_misses: list[dict] = []
        for j, pred in enumerate(predictions):
            if j in used:
                continue
            pred_field = str(pred.get("field_name") or pred.get("field") or "").strip().lower()
            if field and pred_field != field:
                continue
            if not _page_match(fact.get("page"), pred.get("page"), page_slack):
                continue
            if not _docs_match(fact, pred, doc_map):
                continue
            ok, detail = _value_matches(fact, pred, field, tolerance)
            if ok:
                exp_float = detail.get("expected")
                pred_float = detail.get("predicted")
                score = (
                    abs(exp_float - pred_float)
                    if isinstance(exp_float, (int, float)) and isinstance(pred_float, (int, float))
                    else 0.0
                )
                candidates.append((score, j, detail))
            else:
                near_misses.append({**detail, "predicted_page": pred.get("page"), "predicted_index": j})
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            _, j, detail = candidates[0]
            used.add(j)
            pred = predictions[j]
            matches.append(
                {
                    "expected_index": i,
                    "predicted_index": j,
                    "field_name": field,
                    "document": pred.get("filename") or pred.get("document_type"),
                    "page": pred.get("page"),
                    "value": detail,
                }
            )
        else:
            missed.append(
                {
                    "index": i,
                    "field_name": field,
                    "document": fact.get("document") or fact.get("document_type") or fact.get("filename"),
                    "page": fact.get("page"),
                    "expected": fact.get("value_num") if fact.get("value_num") is not None else fact.get("value_text"),
                }
            )
            if near_misses:
                value_mismatches.append(
                    {
                        "field_name": field,
                        "document": fact.get("document") or fact.get("document_type"),
                        "page": fact.get("page"),
                        "expected": fact.get("value_num")
                        if fact.get("value_num") is not None
                        else fact.get("value_text"),
                        "tolerance": _tolerance_for(field, numeric_tolerance, field_tolerances),
                        "predicted_near_misses": near_misses[:5],
                    }
                )
    unmatched = [j for j in range(len(predictions)) if j not in used]
    return {
        "matches": matches,
        "missed": missed,
        "unmatched_predictions": unmatched,
        "value_mismatches": value_mismatches,
    }


# ---------------------------------------------------------------------------
# Prediction preparation (honours package eval_notes)
# ---------------------------------------------------------------------------


def _value_key(prediction: Mapping[str, Any]) -> tuple:
    numeric = _to_float(prediction.get("value_num"), prediction.get("value_text"))
    if numeric is not None:
        return ("num", _round(numeric, 6))
    return ("text", _norm_text(prediction.get("value_text")))


def prepare_extraction_predictions(
    predictions: Sequence[Mapping[str, Any]],
    *,
    ignore_documents: Iterable[str] | None = None,
    ignore_fields: Iterable[str] | None = None,
    collapse_duplicates: bool = False,
) -> dict:
    """Apply the package's ``eval_notes`` before extraction scoring.

    * ``ignore_documents``: evidence from documents that are deliberately outside
      the fact-scoring scope (e.g. CMC/nonclinical/patent summaries) is dropped,
      so it is neither TP nor FP.
    * ``ignore_fields``: fields declared out of scope.
    * ``collapse_duplicates``: identical (field, document, value) predictions are
      counted once and extra copies are *not* false positives (Lead eval notes).
      Without it, strict one-to-one matching counts duplicates as FP.

    Returns ``{"predictions": [...], "scope": {...}}``; the input is not mutated.
    """
    ignore_docs = {_norm_doc_token(doc) for doc in (ignore_documents or []) if doc}
    ignore_fields_norm = {_norm_text(field) for field in (ignore_fields or []) if field}
    kept: list[Mapping[str, Any]] = []
    ignored_by_document = 0
    ignored_by_field = 0
    collapsed = 0
    seen: set[tuple] = set()
    for prediction in predictions:
        field = _norm_text(prediction.get("field_name"))
        if field in ignore_fields_norm:
            ignored_by_field += 1
            continue
        doc_tokens = _doc_tokens(prediction)
        if ignore_docs and doc_tokens & ignore_docs:
            ignored_by_document += 1
            continue
        if collapse_duplicates:
            document = str(
                prediction.get("document_id")
                or prediction.get("filename")
                or prediction.get("document_type")
                or ""
            )
            key = (field, _norm_doc_token(document), _value_key(prediction))
            if key in seen:
                collapsed += 1
                continue
            seen.add(key)
        kept.append(prediction)
    return {
        "predictions": kept,
        "scope": {
            "input_predictions": len(predictions),
            "scored_predictions": len(kept),
            "ignore_documents": sorted(ignore_documents or []),
            "ignore_fields": sorted(ignore_fields or []),
            "ignored_by_document": ignored_by_document,
            "ignored_by_field": ignored_by_field,
            "collapse_duplicates": bool(collapse_duplicates),
            "collapsed_duplicates": collapsed,
        },
    }


# ---------------------------------------------------------------------------
# 1. Extraction field accuracy
# ---------------------------------------------------------------------------


def extraction_field_accuracy(
    predictions: Sequence[Mapping[str, Any]],
    expected_facts: Sequence[Mapping[str, Any]],
    doc_map: Mapping[str, Mapping[str, Any]] | None = None,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    field_tolerances: Mapping[str, float] | None = None,
    page_slack: int = DEFAULT_PAGE_SLACK,
) -> dict:
    """Share of curated facts whose value was extracted at the right field,
    document and page within tolerance.

    Returns overall accuracy (+ Wilson CI), a per-field table, the missing
    facts, and value near-misses (right location, wrong number).
    """
    matched = _match_facts(
        expected_facts,
        predictions,
        doc_map=doc_map,
        numeric_tolerance=numeric_tolerance,
        field_tolerances=field_tolerances,
        page_slack=page_slack,
    )
    by_field: dict[str, dict] = {}
    for fact in expected_facts:
        field = str(fact.get("field_name") or fact.get("field") or "").strip().lower()
        row = by_field.setdefault(field, {"expected": 0, "matched": 0})
        row["expected"] += 1
    for match in matched["matches"]:
        by_field.setdefault(match["field_name"], {"expected": 0, "matched": 0})["matched"] += 1
    field_table: dict[str, dict] = {}
    for field in sorted(by_field):
        row = by_field[field]
        field_table[field] = {
            "expected": row["expected"],
            "matched": row["matched"],
            "missing": row["expected"] - row["matched"],
            "accuracy": _ratio(row["matched"], row["expected"]),
            "accuracy_ci": wilson_ci(row["matched"], row["expected"]),
        }
    n_expected = len(expected_facts)
    n_matched = len(matched["matches"])
    return {
        "expected": n_expected,
        "matched": n_matched,
        "missing": n_expected - n_matched,
        "predictions": len(predictions),
        "accuracy": _ratio(n_matched, n_expected),
        "accuracy_ci": wilson_ci(n_matched, n_expected),
        "by_field": field_table,
        "missing_facts": matched["missed"],
        "value_mismatches": matched["value_mismatches"],
        "config": {
            "numeric_tolerance": numeric_tolerance,
            "field_tolerances": dict(field_tolerances or {}),
            "page_slack": page_slack,
        },
    }


# ---------------------------------------------------------------------------
# 2. Extraction precision / recall / F1
# ---------------------------------------------------------------------------


def extraction_prf(
    predictions: Sequence[Mapping[str, Any]],
    expected_facts: Sequence[Mapping[str, Any]],
    doc_map: Mapping[str, Mapping[str, Any]] | None = None,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    field_tolerances: Mapping[str, float] | None = None,
    page_slack: int = DEFAULT_PAGE_SLACK,
    example_limit: int = 10,
) -> dict:
    """Evidence-level precision / recall / F1 versus curated facts.

    TP = expected fact matched by an evidence row; FP = evidence row not
    matched (including duplicates of a matched row); FN = expected fact not
    found. Also reports a per-field breakdown and duplicate-prediction count.
    """
    matched = _match_facts(
        expected_facts,
        predictions,
        doc_map=doc_map,
        numeric_tolerance=numeric_tolerance,
        field_tolerances=field_tolerances,
        page_slack=page_slack,
    )
    tp = len(matched["matches"])
    fn = len(matched["missed"])
    unmatched = matched["unmatched_predictions"]
    fp = len(unmatched)

    # Per-field accounting (single-pass; unmatched predictions are attributed
    # to their own field).
    field_stats: dict[str, dict] = {}

    def bucket(field: str) -> dict:
        return field_stats.setdefault(field, {"tp": 0, "fp": 0, "fn": 0})

    for match in matched["matches"]:
        bucket(match["field_name"])["tp"] += 1
    for miss in matched["missed"]:
        bucket(miss["field_name"])["fn"] += 1
    for j in unmatched:
        field = str(predictions[j].get("field_name") or predictions[j].get("field") or "").lower()
        bucket(field)["fp"] += 1
    per_field = {}
    for field in sorted(field_stats):
        row = field_stats[field]
        prf = _prf(row["tp"], row["fp"], row["fn"])
        per_field[field] = {
            "tp": prf["tp"],
            "fp": prf["fp"],
            "fn": prf["fn"],
            "precision": prf["precision"],
            "recall": prf["recall"],
            "f1": prf["f1"],
        }

    # Count exact duplicates among predictions (same field/doc/page/value).
    seen: set[tuple] = set()
    duplicates = 0
    for pred in predictions:
        key = (
            str(pred.get("field_name") or ""),
            str(pred.get("document_id") or pred.get("filename") or ""),
            str(pred.get("page")),
            _round(_to_float(pred.get("value_num"), pred.get("value_text")) or 0.0, 6),
        )
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)

    result = _prf(tp, fp, fn)
    result.update(
        {
            "expected": len(expected_facts),
            "predictions": len(predictions),
            "per_field": per_field,
            "duplicate_predictions": duplicates,
            "missing_facts": matched["missed"][:example_limit],
            "false_positive_examples": [
                {
                    "field_name": predictions[j].get("field_name"),
                    "document": predictions[j].get("filename") or predictions[j].get("document_type"),
                    "page": predictions[j].get("page"),
                    "value": predictions[j].get("value_num")
                    if predictions[j].get("value_num") is not None
                    else predictions[j].get("value_text"),
                }
                for j in unmatched[:example_limit]
            ],
            "config": {
                "numeric_tolerance": numeric_tolerance,
                "field_tolerances": dict(field_tolerances or {}),
                "page_slack": page_slack,
            },
        }
    )
    return result


# ---------------------------------------------------------------------------
# 3. Finding precision / recall / F1 vs adjudicated topics
# ---------------------------------------------------------------------------


def _keyword_hits(keywords: Iterable[str], text: str, fields: set[str]) -> list[str]:
    hits: list[str] = []
    for raw in keywords or []:
        kw = _norm_text(raw)
        if not kw:
            continue
        if kw in text or kw in fields:
            hits.append(str(raw))
            continue
        # token-level tolerance (e.g. "ORR" vs "overall response rate")
        toks = [t for t in _WORD_RE.findall(kw) if len(t) >= 3]
        if toks and all(t in text for t in toks):
            hits.append(str(raw))
    return hits


def _finding_fields(finding: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
        elif isinstance(value, Mapping):
            for item in value.keys():
                add(item)
        elif value is not None:
            values.add(_norm_text(value))

    for key in ("field_name", "field", "fields", "field_names", "dimension", "rule_id", "rule"):
        add(finding.get(key))
    detail = finding.get("detail") or {}
    if isinstance(detail, Mapping):
        for key in ("field", "field_name", "fields", "field_names", "dimension", "rule_id", "rule"):
            add(detail.get(key))
        add(detail.get("values"))
    return {v for v in values if v}


def _finding_text(finding: Mapping[str, Any]) -> str:
    parts = [
        finding.get("title"),
        finding.get("explanation"),
        finding.get("recommended_action"),
        finding.get("domain"),
    ]
    for key in ("title", "explanation", "recommended_action", "domain"):
        detail = finding.get("detail") or {}
        if isinstance(detail, Mapping) and detail.get(key):
            parts.append(detail.get(key))
    return _norm_text(" ".join(str(p) for p in parts if p))


def _topic_field_tokens(topic: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
        elif value is not None:
            values.add(_norm_text(value))

    for key in ("field_name", "field", "fields", "field_names", "dimension"):
        add(topic.get(key))
    return {v for v in values if v}


def _topic_document_tokens(topic: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("document", "filename", "document_type", "doc_type", "documents", "document_types"):
        value = topic.get(key)
        if value is None:
            continue
        for item in value if isinstance(value, (list, tuple, set)) else [value]:
            tok = _norm_doc_token(item)
            if tok:
                tokens.add(tok)
                tokens.add(re.sub(r"\.(pdf|docx?|pptx?|xlsx?|txt)$", "", tok))
    return tokens


def _topic_matches_finding(
    topic: Mapping[str, Any],
    finding: Mapping[str, Any],
    doc_map: Mapping[str, Mapping[str, Any]] | None = None,
    keyword_min_matches: int = 1,
) -> tuple[bool, dict]:
    """Tolerant subject match: finding_type + field/rule/dimension + document
    pair, with a keyword fallback so wording differences do not hide a match.

    ``topic.finding_type`` is required when set.  A topic with structured
    identifiers (field/dimension/rule) matches when one of them is present in
    the finding *or* a topic keyword appears in the finding text.  A
    keyword-only topic needs ``keyword_min_matches`` keyword hits.
    """
    expected_type = str(topic.get("finding_type") or "").strip()
    finding_type = str(finding.get("finding_type") or "").strip()
    if expected_type and expected_type != finding_type:
        return False, {"reason": "finding_type_mismatch", "expected": expected_type, "got": finding_type}

    finding_fields = _finding_fields(finding)
    topic_fields = _topic_field_tokens(topic)
    structured = sorted(topic_fields & finding_fields)
    reasons: dict[str, Any] = {"finding_type": finding_type}

    text = _finding_text(finding)
    keywords = [str(k) for k in (topic.get("keywords") or [])]
    hits = _keyword_hits(keywords, text, finding_fields)
    if hits:
        reasons["keyword_hits"] = hits

    if structured:
        reasons["field_match"] = structured
    elif topic_fields and any(tok in text for tok in topic_fields):
        structured = sorted(tok for tok in topic_fields if tok in text)
        reasons["field_in_text"] = structured

    # Document-pair check: only enforced when both sides expose documents.
    topic_docs = _topic_document_tokens(topic)
    finding_docs = _doc_tokens(finding, doc_map)
    docs_ok = True
    if topic_docs and finding_docs:
        overlap = sorted(topic_docs & finding_docs)
        docs_ok = bool(overlap)
        reasons["document_overlap"] = overlap
        if not docs_ok:
            reasons["topic_documents"] = sorted(topic_docs)
            reasons["finding_documents"] = sorted(finding_docs)
    elif topic_docs and not finding_docs:
        # A gap finding often cites no evidence; wording is the only signal.
        docs_ok = True
        reasons["documents_unverifiable"] = True

    if not docs_ok:
        return False, {**reasons, "reason": "document_mismatch"}

    if topic_fields:
        matched = bool(structured) or bool(hits)
        if not matched:
            return False, {**reasons, "reason": "subject_mismatch"}
        return True, reasons

    if len(hits) >= max(1, int(keyword_min_matches)):
        return True, {**reasons, "reason": "keyword_match"}
    return False, {**reasons, "reason": "no_keyword_match", "keywords": keywords}


def _topic_id(topic: Mapping[str, Any], index: int) -> str:
    return str(topic.get("topic_id") or topic.get("id") or f"topic_{index}")


def match_finding_topics(
    predicted_findings: Sequence[Mapping[str, Any]],
    topics: Sequence[Mapping[str, Any]],
    doc_map: Mapping[str, Mapping[str, Any]] | None = None,
    keyword_min_matches: int = 1,
) -> dict:
    """One-to-one greedy assignment of adjudicated topics -> findings.

    Returns ``{"matches": {topic_id: match}, "unmatched_topics": [...],
    "unmatched_finding_indices": [...]}``.  Public because the lifecycle
    benchmark reuses it to decide whether a topic is still raised.
    """
    used: set[int] = set()
    matches: dict[str, dict] = {}
    unmatched_topics: list[dict] = []
    for ti, topic in enumerate(topics):
        tid = _topic_id(topic, ti)
        found = False
        for fi, finding in enumerate(predicted_findings):
            if fi in used:
                continue
            ok, reasons = _topic_matches_finding(topic, finding, doc_map, keyword_min_matches)
            if ok:
                used.add(fi)
                matches[tid] = {
                    "topic_id": tid,
                    "finding_index": fi,
                    "finding_id": finding.get("finding_id"),
                    "finding_type": finding.get("finding_type"),
                    "finding_status": finding.get("status"),
                    "severity": finding.get("severity"),
                    "title": finding.get("title"),
                    "match_reasons": reasons,
                }
                found = True
                break
        if not found:
            unmatched_topics.append(topic)
    return {
        "matches": matches,
        "unmatched_topics": unmatched_topics,
        "unmatched_finding_indices": [i for i in range(len(predicted_findings)) if i not in used],
    }


def finding_prf(
    predicted_findings: Sequence[Mapping[str, Any]],
    expected_topics: Sequence[Mapping[str, Any]],
    doc_map: Mapping[str, Mapping[str, Any]] | None = None,
    keyword_min_matches: int = 1,
    example_limit: int = 10,
) -> dict:
    """Finding-level precision/recall/F1 against adjudicated topics.

    Matching is one-to-one and tolerant to wording (structured field/rule/
    dimension + document pair first, keywords as fallback). Unmatched findings
    are FP; expected topics never raised are FN. For matched topics it also
    reports severity agreement when the ground truth declares an expected
    severity (advisory metric, not part of precision/recall).
    """
    assignment = match_finding_topics(
        predicted_findings, expected_topics, doc_map, keyword_min_matches
    )
    matches = list(assignment["matches"].values())
    unmatched = assignment["unmatched_finding_indices"]
    matched_topic_ids = set(assignment["matches"])
    tp = len(matches)
    fn = len(expected_topics) - tp
    fp = len(unmatched)
    result = _prf(tp, fp, fn)

    by_type: dict[str, dict] = {}

    def bucket(name: str) -> dict:
        return by_type.setdefault(name, {"expected": 0, "tp": 0, "fp": 0, "fn": 0})

    for ti, topic in enumerate(expected_topics):
        bucket(str(topic.get("finding_type") or "unspecified"))["expected"] += 1
        if _topic_id(topic, ti) not in matched_topic_ids:
            bucket(str(topic.get("finding_type") or "unspecified"))["fn"] += 1
    for match in matches:
        bucket(str(match.get("finding_type") or "unspecified"))["tp"] += 1
    for fi in unmatched:
        name = str(predicted_findings[fi].get("finding_type") or "unspecified")
        bucket(name)["fp"] += 1
    per_type = {}
    for name in sorted(by_type):
        row = by_type[name]
        per_type[name] = {
            "expected": row["expected"],
            "tp": row["tp"],
            "fp": row["fp"],
            "fn": row["fn"],
            "precision": _ratio(row["tp"], row["tp"] + row["fp"]),
            "recall": _ratio(row["tp"], row["tp"] + row["fn"]),
        }

    # Advisory severity agreement on matched topics.
    severity_checked = 0
    severity_ok = 0
    severity_details: list[dict] = []
    for ti, topic in enumerate(expected_topics):
        tid = _topic_id(topic, ti)
        expected_severity = _norm_text(topic.get("expected_severity"))
        match = assignment["matches"].get(tid)
        if not expected_severity or not match:
            continue
        severity_checked += 1
        got = _norm_text(match.get("severity"))
        ok = got == expected_severity
        severity_ok += int(ok)
        if not ok:
            severity_details.append(
                {"topic_id": tid, "expected": expected_severity, "got": got or None}
            )

    result.update(
        {
            "expected_topics": len(expected_topics),
            "predicted_findings": len(predicted_findings),
            "matched": matches,
            "missed_topics": [
                {
                    "topic_id": _topic_id(topic, ti),
                    "finding_type": topic.get("finding_type"),
                    "field_name": topic.get("field_name") or topic.get("dimension"),
                    "keywords": topic.get("keywords"),
                }
                for ti, topic in enumerate(expected_topics)
                if _topic_id(topic, ti) not in matched_topic_ids
            ],
            "false_positives": [
                {
                    "finding_id": predicted_findings[fi].get("finding_id"),
                    "finding_type": predicted_findings[fi].get("finding_type"),
                    "severity": predicted_findings[fi].get("severity"),
                    "title": predicted_findings[fi].get("title"),
                }
                for fi in unmatched[:example_limit]
            ],
            "per_type": per_type,
            "severity_agreement": {
                "checked": severity_checked,
                "correct": severity_ok,
                "accuracy": _ratio(severity_ok, severity_checked),
                "mismatches": severity_details,
            },
            "config": {"keyword_min_matches": keyword_min_matches},
        }
    )
    return result


# ---------------------------------------------------------------------------
# 4. Citation accuracy (independent of assetdd.validation)
# ---------------------------------------------------------------------------


def _page_text(
    page_texts: Mapping[str, Mapping[int, str]] | Mapping[tuple[str, int], str],
    document_id: str,
    page: int,
) -> str | None:
    if not page_texts:
        return None
    inner = page_texts.get(document_id)  # type: ignore[arg-type]
    if isinstance(inner, Mapping):
        value = inner.get(page)
        return value if isinstance(value, str) else None
    value = page_texts.get((document_id, page))  # type: ignore[arg-type]
    return value if isinstance(value, str) else None


def citation_accuracy(
    findings: Sequence[Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    page_texts: Mapping[str, Mapping[int, str]] | Mapping[tuple[str, int], str],
    failure_limit: int = 20,
) -> dict:
    """Check every finding/question evidence quote is verbatim on its cited page.

    Independent of ``assetdd.validation``: it re-derives page text from the
    store and compares whitespace-normalised quotes.  A referenced evidence id
    that does not exist, a missing page, or a non-verbatim quote is a failure.
    """
    failures: list[dict] = []
    verified = 0
    entities = 0
    entities_without_evidence = 0
    entity_failures = 0
    by_entity: dict[str, dict] = {
        "findings": {"entities": 0, "without_evidence": 0, "refs": 0, "verified": 0, "failed": 0},
        "questions": {"entities": 0, "without_evidence": 0, "refs": 0, "verified": 0, "failed": 0},
    }

    def check(entity_type: str, entity: Mapping[str, Any]) -> None:
        nonlocal verified, entities, entities_without_evidence, entity_failures
        entities += 1
        bucket = by_entity[entity_type]
        bucket["entities"] += 1
        refs = [r for r in (entity.get("evidence_ids") or []) if r]
        if not refs:
            entities_without_evidence += 1
            bucket["without_evidence"] += 1
            return
        entity_failed = False
        for evidence_id in refs:
            bucket["refs"] += 1
            evidence = evidence_by_id.get(evidence_id)
            if not evidence:
                entity_failed = True
                bucket["failed"] += 1
                failures.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": entity.get("finding_id") or entity.get("question_id"),
                        "evidence_id": evidence_id,
                        "reason": "missing_evidence",
                    }
                )
                continue
            document_id = str(evidence.get("document_id") or "")
            page = evidence.get("page")
            quote = evidence.get("quote") or ""
            text = _page_text(page_texts, document_id, int(page)) if page is not None else None
            if text is None:
                entity_failed = True
                bucket["failed"] += 1
                failures.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": entity.get("finding_id") or entity.get("question_id"),
                        "evidence_id": evidence_id,
                        "document_id": document_id,
                        "page": page,
                        "reason": "page_not_found",
                    }
                )
            elif not quote.strip() or normalize_ws(quote) not in normalize_ws(text):
                entity_failed = True
                bucket["failed"] += 1
                failures.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": entity.get("finding_id") or entity.get("question_id"),
                        "evidence_id": evidence_id,
                        "document_id": document_id,
                        "page": page,
                        "reason": "quote_not_verbatim",
                        "quote": quote[:200],
                    }
                )
            else:
                verified += 1
                bucket["verified"] += 1
        if entity_failed:
            entity_failures += 1

    for finding in findings:
        check("findings", finding)
    for question in questions:
        check("questions", question)

    total_refs = verified + len(failures)
    return {
        "refs": total_refs,
        "verified": verified,
        "failed": len(failures),
        "accuracy": _ratio(verified, total_refs),
        "accuracy_ci": wilson_ci(verified, total_refs),
        "entities": entities,
        "entities_without_evidence": entities_without_evidence,
        "entities_with_failed_citation": entity_failures,
        "entity_accuracy": _ratio(entities - entity_failures, entities),
        "by_entity": by_entity,
        "failures": failures[:failure_limit],
        "failure_reasons": _count_reasons(failures),
    }


def _count_reasons(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        reason = str(item.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 5. Question quality proxies
# ---------------------------------------------------------------------------


def _question_text(question: Mapping[str, Any]) -> str:
    return _norm_text(f"{question.get('question') or ''} {question.get('rationale') or ''}")


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def question_quality(
    questions: Sequence[Mapping[str, Any]],
    expected_topics: Sequence[Mapping[str, Any]] | None = None,
    rationale_min_len: int = DEFAULT_RATIONALE_MIN_LEN,
) -> dict:
    """Question quality proxies (not a human usefulness rating).

    Reports grounding (evidence ids), requested documents, owner assignment,
    rationale length, dedupe rate, and coverage of adjudicated question topics.
    """
    n = len(questions)
    with_evidence = sum(1 for q in questions if [r for r in (q.get("evidence_ids") or []) if r])
    with_docs = sum(1 for q in questions if [d for d in (q.get("required_documents") or []) if d])
    with_owner = sum(1 for q in questions if str(q.get("owner") or "").strip())
    with_finding = sum(1 for q in questions if [f for f in (q.get("finding_ids") or []) if f])
    actionable = sum(
        1
        for q in questions
        if [r for r in (q.get("evidence_ids") or []) if r]
        and [d for d in (q.get("required_documents") or []) if d]
    )
    rationale_lengths = [len(str(q.get("rationale") or "").strip()) for q in questions]
    long_enough = sum(1 for length in rationale_lengths if length >= rationale_min_len)

    norm_texts = [_norm_text(q.get("question")) for q in questions]
    unique_texts = len({t for t in norm_texts if t})
    dedup_keys = [str(q.get("dedup_key") or "") for q in questions]
    unique_keys = len({k for k in dedup_keys if k})

    topic_results: dict | None = None
    if expected_topics is not None:
        matched: list[dict] = []
        missed: list[dict] = []
        texts = norm_texts
        for ti, topic in enumerate(expected_topics):
            topic_id = topic.get("topic_id") or topic.get("id") or f"question_topic_{ti}"
            keywords = [str(k) for k in (topic.get("keywords") or [])]
            hit_question = None
            for qi, q in enumerate(questions):
                hits = _keyword_hits(keywords, texts[qi], _finding_fields(q))
                if hits or not keywords:
                    hit_question = {"question_index": qi, "keyword_hits": hits}
                    break
            if hit_question:
                matched.append({"topic_id": topic_id, **hit_question})
            else:
                missed.append({"topic_id": topic_id, "keywords": keywords})
        topic_results = {
            "expected_topics": len(expected_topics),
            "matched": len(matched),
            "coverage": _ratio(len(matched), len(expected_topics)),
            "coverage_ci": wilson_ci(len(matched), len(expected_topics)),
            "matched_topics": matched,
            "missed_topics": missed,
        }

    return {
        "questions": n,
        "with_evidence": with_evidence,
        "with_evidence_share": wilson_ci(with_evidence, n),
        "with_requested_documents": with_docs,
        "with_requested_documents_share": wilson_ci(with_docs, n),
        "with_owner": with_owner,
        "with_owner_share": wilson_ci(with_owner, n),
        "linked_to_finding": with_finding,
        "linked_to_finding_share": wilson_ci(with_finding, n),
        "actionable": actionable,
        "actionable_share": wilson_ci(actionable, n),
        "dedupe_rate": _round(1.0 - unique_texts / n) if n else None,
        "dedupe_key_unique_rate": _round(unique_keys / len([k for k in dedup_keys if k]))
        if any(dedup_keys)
        else None,
        "rationale_length": {
            "min": min(rationale_lengths) if rationale_lengths else None,
            "max": max(rationale_lengths) if rationale_lengths else None,
            "mean": _round(sum(rationale_lengths) / n) if n else None,
            "median": _round(_median(rationale_lengths)),
            "share_ge_min": _round(long_enough / n) if n else None,
            "min_len_threshold": rationale_min_len,
        },
        "topic_coverage": topic_results,
    }


# ---------------------------------------------------------------------------
# 6. Coverage accuracy
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "by", "as", "at", "from", "that", "this",
}


def _tokens(text: Any) -> set[str]:
    return {t for t in _WORD_RE.findall(_norm_text(text)) if t not in _STOPWORDS}


def _requirement_overlap(expected: str, predicted: str) -> float:
    """Jaccard-ish recall of expected requirement tokens in the predicted one."""
    exp = _tokens(expected)
    if not exp:
        return 1.0
    got = _tokens(predicted)
    return len(exp & got) / len(exp)


def coverage_accuracy(
    predicted_coverage: Sequence[Mapping[str, Any]],
    expected_coverage: Sequence[Mapping[str, Any]],
    requirement_overlap: float = 0.5,
) -> dict:
    """Accuracy of coverage items: right dimension + requirement + status.

    Expected requirements may be free text; matching uses token recall of the
    expected requirement inside the predicted requirement (default >= 0.5).
    ``accuracy`` counts only items with the correct status; ``coverage_recall``
    counts items found regardless of status, and ``status_accuracy`` is
    correct-status over matched.
    """
    used: set[int] = set()
    matches: list[dict] = []
    for ei, expected in enumerate(expected_coverage):
        exp_dim = _norm_text(expected.get("dimension"))
        exp_req = str(expected.get("requirement") or "")
        exp_status = _norm_text(expected.get("status") or expected.get("expected_status"))
        for pi, predicted in enumerate(predicted_coverage):
            if pi in used:
                continue
            pred_dim = _norm_text(predicted.get("dimension"))
            if exp_dim and pred_dim != exp_dim:
                continue
            pred_req = str(predicted.get("requirement") or "")
            overlap = _requirement_overlap(exp_req, pred_req)
            if exp_req and overlap < requirement_overlap:
                continue
            status_ok = (not exp_status) or _norm_text(predicted.get("status")) == exp_status
            used.add(pi)
            matches.append(
                {
                    "expected_index": ei,
                    "predicted_index": pi,
                    "rule_id": predicted.get("rule_id"),
                    "dimension": predicted.get("dimension"),
                    "requirement_overlap": _round(overlap),
                    "expected_status": exp_status or None,
                    "predicted_status": predicted.get("status"),
                    "status_ok": status_ok,
                }
            )
            break
    matched = len(matches)
    status_ok = sum(1 for m in matches if m["status_ok"])
    missed = [
        {
            "dimension": expected.get("dimension"),
            "requirement": expected.get("requirement"),
            "expected_status": expected.get("status") or expected.get("expected_status"),
        }
        for ei, expected in enumerate(expected_coverage)
        if ei not in {m["expected_index"] for m in matches}
    ]
    extra = [
        {
            "rule_id": predicted_coverage[pi].get("rule_id"),
            "dimension": predicted_coverage[pi].get("dimension"),
            "status": predicted_coverage[pi].get("status"),
        }
        for pi in range(len(predicted_coverage))
        if pi not in used
    ]
    by_dimension: dict[str, dict] = {}
    for expected in expected_coverage:
        dim = _norm_text(expected.get("dimension")) or "unspecified"
        by_dimension.setdefault(dim, {"expected": 0, "matched": 0, "status_ok": 0})["expected"] += 1
    for match in matches:
        dim = match.get("dimension") or "unspecified"
        row = by_dimension.setdefault(_norm_text(dim), {"expected": 0, "matched": 0, "status_ok": 0})
        row["matched"] += 1
        row["status_ok"] += int(match["status_ok"])
    for dim, row in by_dimension.items():
        row["accuracy"] = _ratio(row["status_ok"], row["expected"])
    return {
        "expected": len(expected_coverage),
        "predicted": len(predicted_coverage),
        "matched": matched,
        "status_matches": status_ok,
        "accuracy": _ratio(status_ok, len(expected_coverage)),
        "accuracy_ci": wilson_ci(status_ok, len(expected_coverage)),
        "coverage_recall": _ratio(matched, len(expected_coverage)),
        "coverage_recall_ci": wilson_ci(matched, len(expected_coverage)),
        "status_accuracy": _ratio(status_ok, matched),
        "by_dimension": {k: by_dimension[k] for k in sorted(by_dimension)},
        "missed": missed,
        "extra": extra,
        "config": {"requirement_overlap": requirement_overlap},
    }


# ---------------------------------------------------------------------------
# 7. Cost / latency summary hook
# ---------------------------------------------------------------------------


def cost_latency_summary(
    runtime_seconds: float,
    pages: int,
    documents: int,
    evidence: int,
    findings: int,
    questions: int,
    llm_calls: int = 0,
    tokens: int | None = None,
    cost_per_1k_tokens: float | None = None,
) -> dict:
    """Runtime and cost-per-unit hook (pure; pricing supplied by caller).

    ``estimated_cost_usd`` stays ``None`` unless a token price is supplied, so
    a scorecard never invents a dollar figure.
    """
    runtime = float(runtime_seconds or 0.0)
    pages = int(pages or 0)
    documents = int(documents or 0)
    estimated_cost = None
    if cost_per_1k_tokens is not None and tokens is not None:
        estimated_cost = _round(float(tokens) / 1000.0 * float(cost_per_1k_tokens), 6)
    return {
        "runtime_seconds": _round(runtime, 3),
        "documents": documents,
        "pages": pages,
        "evidence": int(evidence or 0),
        "findings": int(findings or 0),
        "questions": int(questions or 0),
        "seconds_per_document": _round(runtime / documents, 4) if documents else None,
        "seconds_per_page": _round(runtime / pages, 4) if pages else None,
        "pages_per_second": _round(pages / runtime, 2) if runtime > 0 else None,
        "evidence_per_page": _round(int(evidence or 0) / pages, 3) if pages else None,
        "findings_per_document": _round(int(findings or 0) / documents, 3) if documents else None,
        "questions_per_document": _round(int(questions or 0) / documents, 3) if documents else None,
        "llm_calls": int(llm_calls or 0),
        "tokens": tokens,
        "cost_per_1k_tokens": cost_per_1k_tokens,
        "estimated_cost_usd": estimated_cost,
        "cost_model": "tokens x price" if estimated_cost is not None else "not configured",
    }


__all__ = [
    "DEFAULT_NUMERIC_TOLERANCE",
    "DEFAULT_PAGE_SLACK",
    "LIMITATIONS",
    "METRIC_VERSION",
    "bootstrap_f1_ci",
    "citation_accuracy",
    "cost_latency_summary",
    "coverage_accuracy",
    "extraction_field_accuracy",
    "extraction_prf",
    "finding_prf",
    "match_finding_topics",
    "prepare_extraction_predictions",
    "question_quality",
    "wilson_ci",
]
