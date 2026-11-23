# AssetDD AI — Multi-Agent Engineering Contract (v1, Lead-owned)

Read this before writing code. It is the interface contract between the Lead and
the module owners. **Do not edit frozen files** (`assetdd/schemas.py`,
`assetdd/db.py`, `assetdd/store.py`, `assetdd/interfaces.py`,
`assetdd/pipeline.py`, `assetdd/config.py`). If a contract change is genuinely
required, message the Lead first; do not edit unilaterally.

## 0. What we are building

An evidence-first pharma BD due-diligence pipeline:

```
upload package -> parse (page + bbox) -> extract Evidence -> verify quotes
-> consistency findings -> gap analysis -> seller questions -> report/xlsx
```

Hierarchy: **Document -> Page -> Evidence -> Finding -> Question**. Every claim
must trace to page/quote. Offline default mode must work with **no API key**
(deterministic rule extractor). LLM mode is an optional adapter.

## 1. Environment

- Python 3.11 at `python3` (system interpreter). Installed: fastapi, uvicorn,
  pydantic v2, sqlalchemy 2, PyMuPDF (`fitz`), openpyxl, python-docx,
  python-pptx, reportlab, pandas, numpy, pyyaml, pytest, httpx.
- **Do not add new dependencies.** No `pip install`.
- Run tests: `python3 -m pytest tests/test_<yours>.py -q`
- SQLite DB path defaults to `data/assetdd.db`. **Tests must isolate themselves**:
  `store.configure(tmp_path / "test.db")` (or `ASSETDD_DB_PATH` env) before use.
  Never write to the shared `data/assetdd.db` during tests.
- No network calls at import time. LLM calls only when `config.llm_available()`.

## 2. Frozen contracts (read, don't edit)

| File | What it gives you |
|---|---|
| `assetdd/schemas.py` | Enums (`DocType`, `FieldName`, `EvidenceCategory`, `FindingType`, `Severity`, `Priority`, `CoverageStatus`, `Owner`, `RunState`...), `EvidenceDraft`, `FindingDraft`, `QuestionDraft`, `CoverageDraft`, normalisation helpers (`normalize_population`, `normalize_method`, `normalize_date`, `normalize_trial`), `comparable_key`, `new_id`, `utcnow`. |
| `assetdd/db.py` | SQLAlchemy tables (do not query them directly). |
| `assetdd/store.py` | **All persistence goes through here.** `session_scope()`, `save_evidence_batch`, `get_evidence`, `save_findings`, `save_questions`, `replace_coverage`, `list_*`, `project_stats`, review-event logging, etc. Returns plain dicts. |
| `assetdd/interfaces.py` | `ParsedDocument` / `ParsedPage` / `ParsedTable` / `TextBlock`, `quote_from_page`. |
| `assetdd/pipeline.py` | State machine. Loads these handlers lazily: |
| `assetdd/config.py` | Paths, LLM settings, `llm_available()`, `ensure_dirs()`. |

### Stage handler contract (called by `pipeline.py`)

```python
def handler(session, project_id: str, force: bool = False) -> dict:  # stats
```

| Stage | Module (owner) | Must return stats with at least |
|---|---|---|
| INGEST | `assetdd/ingestion.py` `ingest_project` (parser-dev) | `documents_parsed`, `documents_failed`, `pages_parsed` |
| EXTRACT | `assetdd/extraction/__init__.py` `extract_project` (extraction-dev) | `documents_extracted`, `evidence_created`, `assets`, `trials` |
| VALIDATE | `assetdd/validation.py` `validate_project` (extraction-dev) | `checked`, `verified`, `failed`, `flagged` |
| CONSISTENCY | `assetdd/agents/consistency.py` `run` (analysis-dev) | `findings_created`, `findings_updated`, `comparisons` |
| GAP | `assetdd/agents/gap.py` `run` (analysis-dev) | `coverage_items`, `gaps`, `findings_created` |
| QUESTIONS | `assetdd/agents/questions.py` `run` (analysis-dev) | `questions_created`, `questions_updated`, `merged` |
| REPORT | `assetdd/reporting.py` `generate_project_outputs` (app-dev) | `report_path`, `questions_xlsx` |

`force=True` means recompute from scratch (delete/ignore prior derived rows for
that document/project). Without `force`, be idempotent: re-running must not
duplicate evidence, findings or questions (store upserts by fingerprint/dedup_key
already; respect it).

## 3. Evidence rules (product-critical, enforced by LEAD review)

1. `quote` must be a **verbatim** snippet (whitespace-normalised substring) of
   the cited page text. The validator checks this deterministically.
2. `page` is 1-based.
3. Numeric fields must carry `value_num`; `unit` is `%`, `months`, `patients`,
   `ratio`, `mg`, `date`... Set by `EvidenceDraft` validators.
4. Do not merge numbers across different populations / arms / cutoffs. If the
   source does not state a dimension, leave it `None` and let the consistency
   agent treat it as "comparability unclear".
5. Never invent values. If a value is not present, do not emit evidence.
6. Asset/trial linkage: evidence gets `asset_id`/`trial_id`/`trial_key` from
   extraction; trials are upserted via `store.upsert_trial`.

## 4. File ownership (advisory write scopes)

| Owner | Scope |
|---|---|
| **Lead** | `assetdd/{schemas,db,store,interfaces,pipeline,config}.py`, `tools/`, `README.md`, `docs/`, `tests/test_e2e.py`, `pyproject.toml`, integration fixes |
| parser-dev | `assetdd/parsers/`, `assetdd/ingestion.py`, `tests/test_parsers.py`, `tests/test_ingestion.py` |
| extraction-dev | `assetdd/extraction/`, `assetdd/llm.py`, `assetdd/validation.py`, `tests/test_extraction.py`, `tests/test_llm.py` |
| analysis-dev | `assetdd/agents/`, `assetdd/rules.py`, `assetdd/rules/**/*.yaml`, `tests/test_agents.py`, `tests/test_rules.py` |
| app-dev | `assetdd/api/`, `assetdd/reporting.py`, `assetdd/exports.py`, `web/`, `tests/test_api.py`, `tests/test_exports.py` |
| eval-dev | `eval/`, `corpus/`, `tests/test_eval.py` |

Only touch files in your scope. Shared state lives in `data/` (git-ignored).

## 5. Sample package (Lead generates; appears in `data/sample_package/`)

Synthetic but realistic package for a Phase 2 oncology asset (English):
CSR + IB + Protocol + SAP + Safety summary + Disposition + CMC + nonclinical +
regulatory + patent PDFs, plus `Clinical_Data_Listing.xlsx`,
`Commercial_Forecast.pptx`. Deliberately planted issues:

- CSR ORR 42.0% (ITT, cutoff 2026-01-15) vs PPT ORR 48.2% (same dims) → **Type A**
- IB ORR 45.0% (cutoff 2025-10-01) → **Type B** (version/cutoff)
- SAP analysis population FAS/BICR vs CSR ITT/investigator → **Type C**
- SAE 19.8% (CSR) vs 15.9% (Safety summary) → **Type A**
- Missing PP population / missing SAP multiplicity → **gaps**
- One page contains prompt-injection text (must be treated as data, flagged)
- `ground_truth.json` next to the PDFs: expected facts (value + document + page),
  expected finding topics, expected question topics.

`data/sample_package_v2/` is a second-round package (updated CSR/IB/SAP with new
cutoff) for lifecycle/version tests.

## 6. Definition of done for each module

- Code runs end-to-end through `assetdd.pipeline.run_pipeline(project_id)` on the
  sample package without exceptions.
- Your tests pass: `python3 -m pytest tests/test_<yours>.py -q` (all green).
- No hidden TODOs/stubs; no hardcoded answers for the sample corpus (patterns
  and rules must generalise; the corpus exists to test, not to be memorised).
- Return stats dicts as specified; log meaningful counts.
- Report to the Lead with: files created, test command + result, any contract
  friction, and known limitations.

## 7. Cross-module Q&A (do not silently diverge)

- Document classification is heuristic + filename/metadata based
  (`ib`, `csr`, `protocol`, `sap`, `safety`, `disposition`, `cmc`, `patent`,
  `commercial`, `regulatory`, `nonclinical`). Put it in `assetdd/ingestion.py`.
- Chunking is for retrieval only; evidence extraction reads pages directly.
- Findings created by consistency/gap agents must carry `dedup_key`; questions
  must carry `dedup_key` derived from finding/rule (stable across re-runs).
- The `detail` dict of a finding should contain the compared values and dims so
  the UI/report can render a table (`values`, `dims`, `delta`, `documents`).
- All user-visible text in English; UI labels may be bilingual.
