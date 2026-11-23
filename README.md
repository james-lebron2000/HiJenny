# HiJenny — 医药资产交易智能尽调系统

[![CI](https://github.com/james-lebron2000/HiJenny/actions/workflows/ci.yml/badge.svg)](https://github.com/james-lebron2000/HiJenny/actions/workflows/ci.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](pyproject.toml)

**GitHub:** https://github.com/james-lebron2000/HiJenny

**Evidence-first, workflow-driven due diligence for pharma BD / license-in teams.**
Input: a seller data package (PDF / PPTX / XLSX). Output: a traceable clinical
evidence base, cross-document consistency findings, evidence gaps, a seller
question list, and a reviewable due-diligence report — every claim linked to
document + page + verbatim quote.

> **Status: MVP implemented and running end-to-end** on a 276-page synthetic
> Phase 2 oncology package (13 documents; 1,000-page class when the CSR appendix
> is expanded): 136 verified evidence rows, 11 active findings, 6 seller
> questions, 174 automated tests. This is a working product prototype, not a
> validated clinical/regulatory instrument — see [Limitations](#limitations).
> Product design: [docs/PRODUCT.md](docs/PRODUCT.md). Engineering:
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Evaluation:
> [docs/EVALUATION.md](docs/EVALUATION.md).

---

## 1. Why not a PDF chatbot

A general LLM reading 1,600 pages cannot reliably tell you *which document a
number came from*, whether two numbers are even comparable, or what the seller
*did not* provide. HiJenny therefore builds a structured evidence database
first, then reasons over it:

```
Document -> Page -> Evidence -> Finding -> Question -> Report
```

Each **Evidence** record carries a *comparability vector*
(`trial, arm, population, data cutoff, endpoint definition, assessment method`).
Two numbers are only compared when those dimensions agree. That is what keeps
the consistency agent from inventing contradictions out of incomparable data —
the single most important product-quality decision in this system.

## 2. What it does

| Stage | Module | Output |
|---|---|---|
| Ingest | `hijenny/ingestion.py`, `hijenny/parsers/` | documents, pages, bboxes, tables, chunks, hashes, version/supersede chain, prompt-injection flags |
| Extract | `hijenny/extraction/` | clinical evidence (study design, efficacy, safety, statistics) with page/table/quote + dimensions |
| Verify | `hijenny/validation.py` | verbatim-quote check, value-in-quote, numeric plausibility, CR+PR vs ORR, CI ordering, duplicates |
| Consistency | `hijenny/agents/consistency.py` | Type A contradiction / Type B version difference / Type C definition difference / comparability unclear — only within comparable dimensions |
| Gap analysis | `hijenny/agents/gap.py`, `hijenny/rules/` | Checklist coverage: sufficient / partial / not found / not applicable, from YAML rule packs |
| Questions | `hijenny/agents/questions.py` | Deduplicated seller questions with priority, owner, requested documents, evidence links |
| Deliverables | `hijenny/reporting.py`, `hijenny/exports.py` | Markdown + DOCX report (Decision Brief first), 6-sheet Excel workbook |
| App | `hijenny/api/`, `web/` | FastAPI + no-build SPA: upload, evidence workspace with PDF page jump, findings/questions review, report |

## 3. Quickstart (no API key needed)

Python 3.11. No cloud services are required; **offline rule-based extraction is
the default mode**. The sample package is committed, so the demo runs immediately
after install:

```bash
pip install -e ".[dev]"        # installs runtime dependencies + pytest/reportlab

# 1) run the full pipeline and produce the deliverables
hijenny demo --package data/sample_package \
    --name "ASD-101 License-in Clinical Due Diligence" \
    --asset "ASD-101 (zafinostat)" --indication "MTAP-deleted advanced solid tumors" --phase "Phase 2"

# (equivalent) python3 -m hijenny.cli demo --package data/sample_package ...

# 2) inspect results
hijenny status --project <project_id>
open data/projects/<project_id>/exports/HiJenny_Report.md
open data/projects/<project_id>/exports/Seller_Questions.xlsx

# 3) web app (upload -> analyze -> evidence workspace -> review -> report)
hijenny serve --port 8123     # http://127.0.0.1:8123/

# 4) optional: regenerate the synthetic data package deterministically
python3 tools/make_sample_package.py --out data/sample_package --variant v1 --filler 180 --force
python3 tools/make_sample_package.py --out data/sample_package_v2 --variant v2 --force
```

Optional LLM mode (OpenAI-compatible endpoint; the adapter sends *document
text as data*, never as instructions):

```bash
export HIJENNY_LLM_API_KEY=...  HIJENNY_LLM_MODEL=gpt-4o-mini
export HIJENNY_EXTRACT_MODE=hybrid   # rules first, LLM fills gaps
```

### Tests

```bash
python3 -m pytest -q          # 174 unit + integration tests; the e2e test runs the real pipeline
```

## 4. Architecture

```
                    FastAPI + SPA (hijenny/api, web/)
                                |
                    Workflow state machine (hijenny/pipeline.py)
   CREATED -> PARSING -> EXTRACTING -> VALIDATING -> ANALYZING
           -> GENERATING_QUESTIONS -> HUMAN_REVIEW -> REPORT_READY
                                |
  +-----------------------------+-----------------------------+
  |                             |                             |
parsers                     extraction                     agents
PDF/DOCX/PPTX/XLSX          rules (+ optional LLM)          consistency / gap / questions
  |                             |                             |
  +----------> Evidence store (SQLite; PostgreSQL-ready schema) <----+
                    documents / pages / chunks / evidence / findings /
                    questions / coverage / review_events / agent_runs
                                |
                     reporting + Excel export
```

The pipeline is deterministic and resumable: each stage is idempotent
(evidence is fingerprinted, findings/questions upserted by stable dedup keys,
re-runs do not duplicate data). A 1,000-page package runs in seconds, not hours,
because no per-page LLM call is required in the default mode.

### Data model

`projects` · `assets` · `documents` · `document_pages` · `document_chunks` ·
`clinical_trials` · `evidence` · `findings` · `finding_evidence` · `questions` ·
`question_evidence` · `coverage_items` · `review_events` · `agent_runs` ·
`users` / `project_members` (multi-tenant scaffold).

Key invariants:

* every evidence row stores `document_id + page + quote` (verbatim) and, where
  applicable, `table`, bbox, extractor version, confidence;
* re-upload of the same file is de-duplicated by SHA-256; a new version of a
  family (CSR v1 → v2) supersedes the old one, and superseded documents are
  excluded from active analysis but retained for audit;
* findings and questions reference evidence IDs; review events record who
  changed what, when.

## 5. The sample package (test fixture)

`tools/make_sample_package.py` generates a realistic Phase 2 oncology package
for a fictional asset (ASD-101, PRMT5 inhibitor, MTAP-deleted solid tumours,
NCT05712345) with planted due-diligence issues:

| Planted issue | Where | Expected classification |
|---|---|---|
| ORR 42.0% vs 48.4% (same population/cutoff/method) | CSR vs commercial deck | Type A contradiction (critical) |
| SAE 19.8% vs 15.9% (same safety set/cutoff) | CSR vs safety summary | Type A contradiction (high) |
| ORR 45.0% at an older cutoff 2025-10-01 | IB | Type B version difference (not a contradiction) |
| ORR 40.0% FAS/BICR sensitivity vs 42.0% ITT/investigator | CSR | Type C definition difference |
| PP population size missing; subgroup results missing; no audit summary | SAP/CSR | Evidence gaps (partial / not found) |
| "NOTE TO AUTOMATED REVIEWERS: ignore all previous instructions…" | IB page 3 | Technical concern; text treated as data, no fabricated evidence |
| Round-2 update (CSR/IB/SAP/Safety/commercial v2) | second package | A findings resolved, Type C persists, version history visible |

`ground_truth.json` in each package carries adjudicated facts (document, page,
value, dimensions, tolerance), finding topics and coverage expectations — used
by `eval/` to measure the system, not to drive it.

## 6. Evaluation

`python3 -m eval.benchmark --package data/sample_package --fresh --lifecycle`
runs the pipeline and scores it against the adjudicated ground truth:
per-field extraction accuracy, evidence precision/recall, finding
precision/recall vs the adjudicated topics, citation accuracy (independently
re-verifies quotes), coverage accuracy, question-quality proxies and runtime.
Results land in `eval/reports/scorecard.md|json`.

Current build on the 276-page package: **36/36 curated facts correct (100%)**,
**findings P 100% / R 100% / F1 1.00** against the 11 adjudicated topics,
**citation accuracy 100%** (independent verbatim re-check), coverage and
question-topic coverage 100%, prompt-injection control PASS, runtime ~11 s.
Round-trip v1 → v2: **11/11 lifecycle transitions correct**. Full methodology,
confidence intervals and caveats: [docs/EVALUATION.md](docs/EVALUATION.md).

**Read the scores with the caveat attached:** the corpus is synthetic and small;
they demonstrate that the pipeline finds the planted issues without inventing
others, not that it matches expert performance on real filings.

## 7. Reliability (four layers)

1. **Schema validation** — every extracted fact is a validated Pydantic
   `EvidenceDraft` (numeric fields numeric, units, page ≥ 1, quote required).
2. **Source verification** — the validator independently checks that the quote is
   a verbatim substring of the cited page, the value appears in the quote,
   numbers are plausible and internally consistent (CR+PR ≈ ORR, CI ordering).
   Extraction failures are surfaced as `incomplete_evidence`, never hidden.
3. **Cross-document verification** — comparability gating (see §1); "not
   comparable" produces a clarification question, not an accusation.
4. **Human review** — findings and questions are drafts; the UI records review
   events (who changed priority/status, when). The report carries an explicit
   "no automated Go/No-Go" disclaimer.

## 8. Security posture

* **Project-level isolation** in every query; org id on projects; `X-Org-Id`
  scoping hook (auth is intentionally out of MVP scope).
* **Prompt-injection defence**: documents are untrusted data. Rule extraction
  skips instruction-like sentences and records flags; the LLM adapter frames
  page text as data, validates output against the schema and drops any fact
  whose quote is not on the page. Injection attempts become a finding.
* **File serving allowlist** (`/api/files`) restricted to project document and
  export artefacts, with traversal blocked.
* **Model data policy**: the offline default sends nothing to a third party;
  LLM mode is opt-in via environment variables, so a customer can choose the
  endpoint/vendor that satisfies their confidentiality requirements.

## 9. Repository layout

```
hijenny/            pipeline, schemas, DB/store, parsers, extraction, agents, rules, API, reporting, exports
  parsers/          PyMuPDF / DOCX / PPTX / XLSX parsers + injection scanner
  extraction/       rule + table extractors, asset/trial resolver, LLM adapter, validation
  agents/           consistency, gap analysis, question generation
  rules/            YAML due-diligence rule packs (oncology phase 1/2/3, endpoints, safety, statistics, data quality)
  api/              FastAPI app
web/                dependency-free SPA (5 views)
tools/              synthetic seller-package generator (+ ground truth)
eval/               metrics, benchmark CLI, scorecards
corpus/             adjudication + adversarial fixtures
tests/              unit + end-to-end integration tests
docs/               PRODUCT.md (product design), ARCHITECTURE.md (engineering), EVALUATION.md (scorecards)
data/               committed sample packages + generated project artefacts (git-ignored)
.github/workflows/  CI: install, 174 tests, deterministic package generation, CLI smoke test
```

## 10. MVP scope boundaries (deliberate)

Implemented: clinical due diligence for oncology Phase 1–3, PDF/PPTX/XLSX
ingestion, evidence/consistency/gap/question/report pipeline, review UI, Excel
export, version round-trip.

**Not** in this MVP (per the product plan): full CMC/IP/commercial analysis,
automatic FTO or rNPV, deal-term generation, autonomous external database
search, multi-agent debate, knowledge graphs, model training, automated
Go/No-Go, automated emails to the seller, production auth/SSO/audit export.

## Limitations

* Synthetic corpus, single asset: accuracy figures are not real-client
  validation. A pilot with adjudicated real packages is required.
* Rule extraction is tuned to English CSR/IB/SAP phrasing; scanned PDFs are
  flagged, not OCR'd; unusual table layouts can be missed.
* No live LLM was exercised in this environment (no key); the adapter is
  covered by mocked-transport tests and degrades to rules when unavailable.
* The Excel/DOCX reports are working drafts for expert review, not regulatory
  submissions.

## Roadmap

| Version | Focus |
|---|---|
| **V0.1 (this repo)** | Package → clinical evidence → consistency/gaps → seller questions → report, end-to-end |
| V0.5 | Real pilot packages, adjudicated scorecards, OCR, reviewer workflow polish |
| V1.0 | Multi-tenant auth/SSO, audit export, PostgreSQL + object storage, version-diff UI |
| V2.0 | CMC + regulatory/IP agents, risk register, decision brief for IC |
| V3.0 | Pharma BD intelligence platform (portfolio, benchmarks, cross-deal learning) |
