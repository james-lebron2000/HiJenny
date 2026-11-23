"""Load and validate package ground truth (eval-dev owned).

The Lead-owned generator writes ``ground_truth.json`` next to the package files.
This loader accepts the Lead-pinned shape and is tolerant about section naming
(``facts`` / ``expected_facts`` ...) but strict about the fields that make
scoring sound:

* every fact needs ``field_name``, a matchable document key (``document`` or
  ``document_type``), a 1-based ``page`` and a value (``value_num`` or
  ``value_text``);
* ``page`` may be omitted only when the caller passes ``strict=False`` (then the
  fact is dropped with a warning rather than scored on a wildcard page).

When no ground-truth file exists the loader falls back to
``corpus.adjudication`` (topic-level expectations only; fact-level extraction
stays scored as "not_scored" because inventing page locations would be
dishonest).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from corpus.adjudication import fallback_ground_truth

GT_FILENAME = "ground_truth.json"

_LIST_KEYS = {
    "facts": ("facts", "expected_facts", "extraction", "extracted_facts"),
    "finding_topics": (
        "finding_topics",
        "findings",
        "expected_finding_topics",
        "expected_findings",
    ),
    "question_topics": (
        "question_topics",
        "questions",
        "expected_question_topics",
        "expected_questions",
    ),
    "coverage": ("coverage", "expected_coverage", "coverage_expectations"),
}

_PACKAGE_SUFFIXES = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".txt"}


def package_ground_truth_path(package_dir: str | Path) -> Path:
    return Path(package_dir) / GT_FILENAME


def package_files(package_dir: str | Path) -> list[Path]:
    """Supported documents in a package directory (non-recursive, sorted)."""
    directory = Path(package_dir)
    if not directory.exists():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.name != GT_FILENAME
        and p.suffix.lower() in _PACKAGE_SUFFIXES
    )


def _first_list(data: dict, keys: tuple[str, ...]) -> list:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _normalise_fact(fact: dict) -> dict:
    out = dict(fact)
    out.setdefault("value_text", None)
    out.setdefault("value_num", None)
    if out.get("document") is None and fact.get("filename") is not None:
        out["document"] = fact["filename"]
    return out


def _fact_problem(fact: dict) -> str | None:
    if not isinstance(fact, dict):
        return "not_an_object"
    if not fact.get("field_name") and not fact.get("field"):
        return "missing_field_name"
    if not (fact.get("document") or fact.get("document_type") or fact.get("filename")):
        return "missing_document"
    if fact.get("page") is None and fact.get("page_no") is not None:
        fact["page"] = fact["page_no"]
    if fact.get("page") is None:
        return "missing_page"
    if fact.get("value_num") is None and fact.get("value_text") is None:
        return "missing_value"
    return None


def load_ground_truth(
    package_dir: str | Path,
    *,
    strict: bool = True,
) -> dict:
    """Load adjudicated ground truth for a package directory.

    Returns a dict with stable keys: ``package``, ``variant``, ``asset_name``,
    ``trial``, ``facts``, ``finding_topics``, ``question_topics``, ``coverage``,
    ``lifecycle``, ``injection``, ``source`` (``"file"`` or
    ``"adjudication_fallback"``), ``path``, ``warnings`` and ``raw``.
    """
    directory = Path(package_dir)
    gt_path = package_ground_truth_path(directory)
    warnings: list[str] = []
    if not gt_path.exists():
        fallback = fallback_ground_truth(directory.name)
        fallback.update(
            {
                "source": "adjudication_fallback",
                "path": None,
                "warnings": [
                    f"{gt_path} not found; using corpus.adjudication fallback. "
                    "Fact-level extraction metrics will be reported as not_scored."
                ],
                "raw": {},
            }
        )
        return fallback

    try:
        raw = json.loads(gt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read ground truth {gt_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"ground truth {gt_path} must be a JSON object")

    facts: list[dict] = []
    for index, fact in enumerate(_first_list(raw, _LIST_KEYS["facts"])):
        if not isinstance(fact, dict):
            warnings.append(f"fact[{index}] is not an object; dropped")
            continue
        fact = _normalise_fact(fact)
        problem = _fact_problem(fact)
        if problem:
            message = f"fact[{index}] {problem}; dropped"
            if strict:
                raise ValueError(f"{gt_path}: {message}")
            warnings.append(message)
            continue
        facts.append(fact)

    data = {
        "package": raw.get("package") or directory.name,
        "variant": raw.get("variant")
        or ("v2" if "v2" in directory.name.lower() else "v1"),
        "asset_name": raw.get("asset_name"),
        "trial": raw.get("trial") or {},
        "facts": facts,
        "finding_topics": [
            dict(t) for t in _first_list(raw, _LIST_KEYS["finding_topics"]) if isinstance(t, dict)
        ],
        "question_topics": [
            dict(t) for t in _first_list(raw, _LIST_KEYS["question_topics"]) if isinstance(t, dict)
        ],
        "coverage": [
            dict(c) for c in _first_list(raw, _LIST_KEYS["coverage"]) if isinstance(c, dict)
        ],
        "lifecycle": dict(raw.get("lifecycle") or {}),
        "injection": dict(raw.get("injection") or {}),
        "expect_no_findings": bool(raw.get("expect_no_findings", False)),
        "source": "file",
        "path": str(gt_path),
        "warnings": warnings,
        "raw": raw,
    }
    return data


__all__ = [
    "GT_FILENAME",
    "load_ground_truth",
    "package_files",
    "package_ground_truth_path",
]
