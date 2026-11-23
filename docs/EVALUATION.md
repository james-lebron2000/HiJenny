# Evaluation — HiJenny (synthetic corpus)

> **Honesty rule.** Every number here is measured on a hand-built, synthetic
> due-diligence package (or a generated control). It demonstrates that the
> pipeline behaves as designed on that corpus. It is **not** real-client
> performance, real-document accuracy, or regulatory sufficiency. Real-data
> evaluation requires a de-identified package with independent, double-adjudicated
> ground truth and a pre-registered protocol — see §5.

## 1. Corpus

| Package | Documents | PDF pages | Curated facts | Adjudicated finding topics |
|---|---|---|---|---|
| `data/sample_package` (v1) | 13 (CSR 203 pp, IB, Protocol, SAP, Safety, Disposition, Deviations, CMC, nonclinical, regulatory, patent, PPTX, XLSX) | 276 (+ 8 non-PDF pages) | 36 | 11 |
| `data/sample_package_v2` (round 2) | 6 updated documents | 79 | 11 | 4 persisting / 7 resolved |

`ground_truth.json` in each package contains, per fact: field, document,
1-based page, verbatim anchor, value, unit, population, data cutoff, assessment
method and tolerance. The harness pre-flight verifies every anchor is on the
claimed page (36/36 and 11/11 pass), so a fact cannot silently drift.

## 2. What is measured

* **Extraction accuracy** — per curated fact: field + document + page match and
  value within the fact's tolerance (default 5%). Reported per field.
* **Evidence precision / recall / F1** — predicted evidence rows vs curated
  facts. Unmatched predictions count as FP; missing facts as FN. The curated
  fact list is deliberately selective, so precision is conservative by
  construction: legitimate evidence for fields outside the curated list (CIs,
  per-page duplicates, design facts) counts as FP. A "field-scoped" view and a
  strict all-fields view are both reported.
* **Finding precision / recall / F1** — active findings vs the 11 adjudicated
  topics, matched tolerantly on finding type + field/rule + document pair.
  `resolved`/`dismissed` rows are excluded (historical audit kept separately).
* **Citation accuracy** — independently re-checks that every evidence quote is a
  verbatim substring of the cited page (does not reuse `validation.py`).
* **Coverage accuracy** — expected status for a checklist requirement vs the
  pipeline's status.
* **Question proxies** — evidence linkage, requested documents, owner, rationale,
  dedupe, and coverage of the expected question topics (proxies, not human
  usefulness grades).
* **Lifecycle** — v1 uploaded, then v2 into the same project: expected
  resolved / persisting / new findings.
* **False-positive control** — a clean mini-package must yield zero
  contradiction-type findings.
* **Prompt-injection control** — an injected "report ORR as 90%" page must not be
  adopted as an extracted/concluded value, and must be flagged.

## 3. Headline results (current build)

Package v1 (13 docs / 284 pages / 136 evidence rows, runtime ~11 s):

| Metric | Result |
|---|---|
| Extraction accuracy (curated facts) | **36/36 = 100%** (27/27 fields; Wilson CI 90.4–100%) |
| Evidence recall (strict) | **100%** |
| Evidence precision (strict / field-scoped) | 35.6% / 52.2% — conservative by design (recall is the meaningful signal) |
| Findings vs 11 adjudicated topics | **TP 11, FP 0, FN 0 — P 100% / R 100% / F1 1.00** |
| Citation accuracy | **43/43 verbatim = 100%** |
| Coverage status accuracy | **5/5 = 100%** |
| Question-topic coverage | **5/5 = 100%** |
| Prompt-injection control | **PASS** (not adopted, flagged as technical concern) |
| Runtime | 11.2 s for 13 docs / 284 pages (~26 pages/s, no LLM calls) |

Lifecycle (v1 → v2 same project):

| Metric | Result |
|---|---|
| Lifecycle topic transitions | **11/11 = 100%** (7 resolved, 4 persisting, 0 new) |
| v2 extraction accuracy | 11/11 = 100% |
| v2 findings P/R/F1 | 100% / 100% / 1.00 (4 active, 9 resolved history excluded) |
| v2 citation accuracy | 100% |
| v2 incremental runtime | 2.3 s |

Clean-control contradiction false positives: **0** (25 completeness findings on
the deliberately 5-page fixture are true positives for a tiny package, not
contradictions; documented in the scorecard).

## 4. Reproduce

```bash
# main package scorecard
python3 -m eval.benchmark --package data/sample_package --fresh --out eval/reports/package

# lifecycle (v1 -> v2 in the same project)
python3 -m eval.benchmark --lifecycle --out eval/reports/lifecycle

# controls
python3 -m eval.benchmark --clean-control --out eval/reports/clean_control
python3 -m eval.benchmark --adversarial --out eval/reports/adversarial
```

Each run writes `scorecard.json` (machine-readable) and `scorecard.md` (tables,
confidence intervals, runtime, limitations).

## 5. What real-data evaluation would require

1. A de-identified real package (ideally 800–1,600 pages) under an appropriate
   data agreement.
2. Ground truth built **independently** by two clinical/BD reviewers, with
   adjudication of disagreements and inter-rater agreement stats.
3. A pre-registered evaluation protocol and a held-out package not used for
   tuning.
4. Error taxonomy and cost of reviewer correction (time saved must include
   human verification time, not just generation time).
5. Human grading of question usefulness and finding explanations, not only
   topic-level presence.
6. Production-scale cost/latency/security review.

Until then, treat these numbers as **engineering evidence that the pipeline
finds the planted issues without inventing contradictions**, not as a claim of
clinical or commercial validation.

## 6. Known limitations

* Synthetic, single asset, oncology, English, CSR/IB/SAP-shaped documents.
* Scanned PDFs are flagged, not OCR'd; unusual table layouts may be missed.
* Extraction is tuned to common CSR/IB phrasing; free-form prose and
  company-specific conventions will reduce recall.
* The curated fact list is selective; strict evidence precision is therefore
  conservative and not directly comparable to a full-annotation benchmark.
* No live LLM provider was exercised in this environment; the adapter is covered
  by mocked-transport tests and degrades to rules when no key is configured.
