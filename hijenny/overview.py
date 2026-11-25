"""Asset discovery + evaluation overview (Lead-owned).

Produces the aggregate object used by the interactive dashboard
(``GET /api/projects/{id}/overview``) and by report/Excel appendices.

Design notes
------------
* Only **active documents** (``is_latest``) contribute evidence, matching the
  consistency/gap agents.
* Evaluation scores are transparent, rule-based *diligence-readiness* scores:
  evidence completeness, verification, consistency, coverage and data maturity.
  They are not investment ratings and never produce a Go/No-Go recommendation.
* Every key fact carries document + page + verbatim quote + evidence id so the
  UI can deep-link to the source.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from statistics import mean
from typing import Any, Iterable

from hijenny import store
from hijenny.schemas import FieldName

# ---------------------------------------------------------------------------
# Reference definitions
# ---------------------------------------------------------------------------

FACT_DEFS: list[tuple[str, str, str, str]] = [
    # field, zh label, en label, value kind
    (FieldName.ORR, "客观缓解率 ORR", "Objective response rate", "percent"),
    (FieldName.DCR, "疾病控制率 DCR", "Disease control rate", "percent"),
    (FieldName.PFS, "中位无进展生存期 PFS", "Median PFS", "months"),
    (FieldName.OS, "中位总生存期 OS", "Median OS", "months"),
    (FieldName.DOR, "中位缓解持续时间 DoR", "Median DoR", "months"),
    (FieldName.ANY_AE, "任意级别 AE", "Any-grade AE", "percent"),
    (FieldName.GRADE3_AE, "≥3 级 AE", "Grade ≥3 AE", "percent"),
    (FieldName.SAE, "严重不良事件 SAE", "Serious AEs", "percent"),
    (FieldName.TREATMENT_RELATED_DEATH, "治疗相关死亡", "Treatment-related death", "percent"),
    (FieldName.DISCONTINUATION, "因 AE 停药", "Discontinuation due to AE", "percent"),
    (FieldName.SAMPLE_SIZE, "样本量", "Sample size", "patients"),
    (FieldName.N_ITT, "ITT 人群", "ITT population", "patients"),
    (FieldName.FOLLOW_UP, "中位随访", "Median follow-up", "months"),
]

FIELD_LABEL = {f: (zh, en, kind) for f, zh, en, kind in FACT_DEFS}

DOC_PRIORITY = {
    "CSR": 0,
    "SAP": 1,
    "IB": 2,
    "SAFETY": 3,
    "PROTOCOL": 4,
    "DISPOSITION": 5,
    "COMMERCIAL": 6,
    "OTHER": 9,
}

EXPECTED_DOCS: list[tuple[str, str, str]] = [
    ("CSR", "临床研究报告", "Clinical study report"),
    ("PROTOCOL", "临床试验方案", "Protocol"),
    ("IB", "研究者手册", "Investigator's brochure"),
    ("SAP", "统计分析计划", "Statistical analysis plan"),
    ("SAFETY", "安全性汇总", "Safety summary"),
    ("CMC", "CMC / 药学", "CMC / manufacturing"),
    ("PRECLINICAL", "临床前", "Nonclinical"),
    ("REGULATORY", "监管沟通", "Regulatory"),
    ("PATENT", "专利", "Patents"),
    ("COMMERCIAL", "商业预测", "Commercial"),
]

SEVERITY_WEIGHT = {"critical": 3.0, "high": 2.0, "medium": 1.0, "low": 0.4, "info": 0.0}
CONSISTENCY_PENALTY = {"critical": 25.0, "high": 15.0, "medium": 8.0, "low": 3.0}
RISK_TYPES = {
    "data_inconsistency",
    "version_difference",
    "definition_difference",
    "comparability_unclear",
}
ACTIVE_STATUSES = {"open", "confirmed", "needs_review"}


def _verified_factor(status: str | None) -> float:
    return {"verified": 1.0, "pending": 0.6, "unverifiable": 0.2}.get(str(status or ""), 0.0)


def _fmt_value(ev: dict, kind: str) -> str:
    value = ev.get("value_num")
    if value is None:
        text = (ev.get("value_text") or "").strip()
        return text or "—"
    if kind == "percent":
        return f"{value:.1f}%"
    if kind == "months":
        return f"{value:.1f} months"
    if kind == "patients":
        return f"{int(value)} patients"
    return f"{value:g}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value).strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _months_between(then: date | None, now: date) -> float | None:
    if then is None:
        return None
    return max(0.0, (now - then).days / 30.44)


def _pick_fact(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None

    def rank(ev: dict) -> tuple:
        status = str(ev.get("verification_status") or "")
        verified = 0 if status == "verified" else 1
        doc = DOC_PRIORITY.get(str(ev.get("document_type") or ""), 9)
        confidence = -float(ev.get("confidence") or 0.0)
        quote = str(ev.get("quote") or "").lower()
        # Prefer the overall population; do not promote subgroup/MTAP-specific values.
        subgroup = 1 if any(
            token in quote for token in ("subgroup", "mtap-deleted", "by line of therapy", "patients with mtap")
        ) else 0
        # Known analysis population beats "not stated".
        population = 0 if str(ev.get("population") or "").upper() in {"ITT", "FAS", "SAFETY", "PP"} else 1
        # Prefer a concise narrative quote over a long synopsis table dump.
        quote_len = len(str(ev.get("quote") or ""))
        return (verified, doc, subgroup, population, confidence, quote_len)

    return sorted(candidates, key=rank)[0]


def _evidence_for(evidence: list[dict], field: str) -> list[dict]:
    return [e for e in evidence if str(e.get("field_name")) == str(field)]


def _best_field(evidence: list[dict], field: str) -> dict | None:
    return _pick_fact(_evidence_for(evidence, field))


def _score_fields(evidence: list[dict], weights: dict[str, float]) -> tuple[float, list[str]]:
    score = 0.0
    missing: list[str] = []
    for field, weight in weights.items():
        ev = _best_field(evidence, field)
        if ev is None:
            missing.append(field)
            continue
        score += weight * 100.0 * _verified_factor(ev.get("verification_status"))
    return round(min(100.0, max(0.0, score)), 1), missing


def _doc_inventory(docs: list[dict]) -> dict:
    by_type: dict[str, dict] = {}
    for doc in docs:
        if not doc.get("is_latest", True):
            continue
        key = str(doc.get("doc_type") or "OTHER")
        entry = by_type.setdefault(
            key, {"doc_type": key, "documents": 0, "pages": 0, "filenames": []}
        )
        entry["documents"] += 1
        entry["pages"] += int(doc.get("page_count") or 0)
        entry["filenames"].append(doc.get("filename") or "")
    present = set(by_type)
    expected = [
        {
            "doc_type": dtype,
            "label_zh": zh,
            "label_en": en,
            "present": dtype in present,
            "documents": by_type.get(dtype, {}).get("documents", 0),
        }
        for dtype, zh, en in EXPECTED_DOCS
    ]
    return {
        "by_type": sorted(by_type.values(), key=lambda x: (-x["pages"], x["doc_type"])),
        "expected": expected,
        "present_count": sum(1 for row in expected if row["present"]),
        "expected_count": len(expected),
        "total_documents": len([d for d in docs if d.get("is_latest", True)]),
        "total_pages": sum(int(d.get("page_count") or 0) for d in docs if d.get("is_latest", True)),
    }


def _document_highlights(session, project_id: str, docs: list[dict]) -> list[dict]:
    """Short previews of non-clinical documents (explicitly not extracted evidence)."""
    wanted = ["CMC", "PRECLINICAL", "REGULATORY", "PATENT", "COMMERCIAL"]
    highlights: list[dict] = []
    for doc in docs:
        if not doc.get("is_latest", True) or str(doc.get("doc_type")) not in wanted:
            continue
        page = store.get_page(session, doc["document_id"], 1)
        if not page:
            continue
        lines = [
            line.strip()
            for line in str(page.get("text") or "").splitlines()
            if line.strip()
            and "CONFIDENTIAL" not in line.upper()
            and not re.match(r"(?i)^page\s+\d+", line.strip())
        ]
        if not lines:
            continue
        excerpt = " ".join(lines[:2])[:260]
        highlights.append(
            {
                "doc_type": doc.get("doc_type"),
                "filename": doc.get("filename"),
                "page": 1,
                "excerpt": excerpt,
                "evidence_status": "unstructured_preview",
            }
        )
    return highlights


def _profile_attributes(assets: list[dict], trials: list[dict], evidence: list[dict]) -> list[dict]:
    asset = assets[0] if assets else {}
    trial = trials[0] if trials else {}
    field_map = {
        "name": (FieldName.ASSET_NAME, asset.get("name")),
        "target": (FieldName.TARGET, asset.get("target")),
        "mechanism": (FieldName.MECHANISM, asset.get("mechanism")),
        "modality": (FieldName.MODALITY, asset.get("modality")),
        "indication": (FieldName.INDICATION, asset.get("indication")),
        "phase": (FieldName.PHASE, asset.get("phase")),
        "nct": (FieldName.NCT_ID, trial.get("nct_id")),
        "sample_size": (FieldName.SAMPLE_SIZE, trial.get("sample_size")),
        "cutoff": (FieldName.DATA_CUTOFF, trial.get("data_cutoff")),
    }
    labels = {
        "name": ("资产名称", "Asset"),
        "target": ("靶点", "Target"),
        "mechanism": ("作用机制", "Mechanism"),
        "modality": ("分子类型", "Modality"),
        "indication": ("适应症", "Indication"),
        "phase": ("临床阶段", "Phase"),
        "nct": ("试验编号", "Trial / NCT"),
        "sample_size": ("样本量", "Sample size"),
        "cutoff": ("数据截止日", "Data cutoff"),
    }
    rows: list[dict] = []
    for key, (field, fallback) in field_map.items():
        ev = _best_field(evidence, field)
        value = ev.get("value_text") if ev and ev.get("value_text") else (ev.get("value_num") if ev else None)
        if value in (None, ""):
            value = fallback
        rows.append(
            {
                "key": key,
                "label_zh": labels[key][0],
                "label_en": labels[key][1],
                "value": value,
                "source": "evidence" if ev else ("asset_registry" if fallback not in (None, "") else "missing"),
                "evidence_id": ev.get("evidence_id") if ev else None,
                "document_id": ev.get("document_id") if ev else None,
                "document": ev.get("filename") if ev else (trial.get("nct_id") and "clinical_trials"),
                "page": ev.get("page") if ev else None,
                "quote": ev.get("quote") if ev else None,
                "verification_status": ev.get("verification_status") if ev else None,
            }
        )
    return rows


def _risk_summary(findings: list[dict]) -> dict:
    active = [f for f in findings if str(f.get("status")) in ACTIVE_STATUSES]
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in active:
        sev = str(f.get("severity") or "low")
        counts[sev] = counts.get(sev, 0) + 1
    weighted = sum(SEVERITY_WEIGHT.get(str(f.get("severity")), 1.0) for f in active)
    if weighted >= 6:
        level = "high"
        label = "高风险暴露"
    elif weighted >= 3:
        level = "medium_high"
        label = "中高风险"
    elif weighted >= 1.5:
        level = "medium"
        label = "中等风险"
    else:
        level = "low"
        label = "低风险"
    return {
        "active_findings": len(active),
        "by_severity": counts,
        "weighted_score": round(weighted, 2),
        "level": level,
        "label_zh": label,
    }


def _consistency_summary(findings: list[dict]) -> dict:
    classes = {"A": 0, "B": 0, "C": 0, "U": 0}
    for f in findings:
        if str(f.get("finding_type")) not in RISK_TYPES:
            continue
        detail = f.get("detail") or {}
        cls = str(detail.get("class") or "").upper()
        if cls in classes:
            classes[cls] += 1
    return classes


def _evaluate(
    evidence: list[dict],
    findings: list[dict],
    coverage: list[dict],
    docs: list[dict],
    attributes: list[dict],
    trials: list[dict],
    now: date,
) -> dict:
    # 1) profile completeness
    required = [a for a in attributes if a["key"] in {"name", "target", "mechanism", "modality",
                                                      "indication", "phase", "nct", "sample_size", "cutoff"}]
    present = [a for a in required if a.get("value") not in (None, "", "—")]
    profile_score = round(len(present) / max(1, len(required)) * 100, 1)

    # 2) efficacy: ORR / PFS / OS / DCR with verification weighting
    efficacy_score, eff_missing = _score_fields(
        evidence, {"orr": 0.35, "pfs": 0.25, "os": 0.25, "dcr": 0.15}
    )
    # 3) safety
    safety_score, safety_missing = _score_fields(
        evidence,
        {"any_ae": 0.20, "grade3_ae": 0.25, "sae": 0.25,
         "treatment_related_death": 0.15, "discontinuation": 0.15},
    )
    # 4) evidence quality
    verified = [e for e in evidence if e.get("verification_status") == "verified"]
    quality_score = round(len(verified) / max(1, len(evidence)) * 100, 1)

    # 5) consistency: severity-weighted penalty (A stronger than B/C/U via severity)
    active = [f for f in findings if str(f.get("status")) in ACTIVE_STATUSES]
    consistency_findings = [f for f in active if str(f.get("finding_type")) in RISK_TYPES]
    penalty = sum(
        CONSISTENCY_PENALTY.get(str(f.get("severity")), 5.0) for f in consistency_findings
    )
    consistency_score = round(max(0.0, 100.0 - penalty), 1)

    # 6) coverage
    applicable = [c for c in coverage if str(c.get("status")) != "not_applicable"]
    if applicable:
        weights = {"sufficient": 1.0, "partial": 0.5, "not_found": 0.0, "not_reviewed": 0.25}
        coverage_score = round(
            sum(weights.get(str(c.get("status")), 0.0) for c in applicable) / len(applicable) * 100, 1
        )
    else:
        coverage_score = 0.0

    # 7) data maturity: cutoff recency + follow-up
    cutoff_ev = _best_field(evidence, FieldName.DATA_CUTOFF)
    follow_ev = _best_field(evidence, FieldName.FOLLOW_UP)
    cutoff_date = _parse_date((cutoff_ev or {}).get("value_text") or (cutoff_ev or {}).get("value_num"))
    cutoff_age = _months_between(cutoff_date, now)
    if cutoff_age is None:
        cutoff_score = 30.0
    elif cutoff_age <= 6:
        cutoff_score = 100.0
    elif cutoff_age <= 12:
        cutoff_score = 85.0
    elif cutoff_age <= 18:
        cutoff_score = 65.0
    elif cutoff_age <= 24:
        cutoff_score = 45.0
    else:
        cutoff_score = 25.0
    follow_months = (follow_ev or {}).get("value_num")
    if follow_months is None:
        follow_score = 30.0
    else:
        follow_months = float(follow_months)
        if follow_months >= 18:
            follow_score = 100.0
        elif follow_months >= 12:
            follow_score = 85.0
        elif follow_months >= 6:
            follow_score = 65.0
        else:
            follow_score = 40.0
    maturity_score = round((cutoff_score + follow_score) / 2, 1)

    # 8) package completeness
    inventory = _doc_inventory(docs)
    package_score = round(inventory["present_count"] / max(1, inventory["expected_count"]) * 100, 1)

    dimensions = [
        {
            "id": "profile",
            "label_zh": "资产档案完整度",
            "label_en": "Asset profile completeness",
            "score": profile_score,
            "weight": 0.10,
            "basis": f"{len(present)}/{len(required)} 个关键字段有值（名称/靶点/机制/类型/适应症/阶段/试验/样本量/cutoff）",
            "missing": [a["label_zh"] for a in required if a.get("value") in (None, "", "—")],
        },
        {
            "id": "efficacy",
            "label_zh": "疗效证据",
            "label_en": "Efficacy evidence",
            "score": efficacy_score,
            "weight": 0.20,
            "basis": "ORR 35% · PFS 25% · OS 25% · DCR 15%，按核验状态加权"
            + (f"；缺失 {', '.join(eff_missing)}" if eff_missing else ""),
            "missing": eff_missing,
        },
        {
            "id": "safety",
            "label_zh": "安全性证据",
            "label_en": "Safety evidence",
            "score": safety_score,
            "weight": 0.20,
            "basis": "AE/≥3 级 AE/SAE/治疗相关死亡/停药，按核验状态加权"
            + (f"；缺失 {', '.join(safety_missing)}" if safety_missing else ""),
            "missing": safety_missing,
        },
        {
            "id": "quality",
            "label_zh": "证据可核验性",
            "label_en": "Evidence verifiability",
            "score": quality_score,
            "weight": 0.15,
            "basis": f"{len(verified)}/{len(evidence)} 条证据通过逐字引用校验",
            "missing": [],
        },
        {
            "id": "consistency",
            "label_zh": "跨文件一致性",
            "label_en": "Cross-document consistency",
            "score": consistency_score,
            "weight": 0.15,
            "basis": (
                f"发现 A/B/C/U 共 {len(consistency_findings)} 项；按严重度扣分 "
                f"（critical -25 / high -15 / medium -8 / low -3）"
            ),
            "missing": [],
        },
        {
            "id": "coverage",
            "label_zh": "尽调检查覆盖",
            "label_en": "Checklist coverage",
            "score": coverage_score,
            "weight": 0.10,
            "basis": f"适用检查项 {len(applicable)} 项，按充分/部分/缺失加权",
            "missing": [],
        },
        {
            "id": "maturity",
            "label_zh": "数据成熟度",
            "label_en": "Data maturity",
            "score": maturity_score,
            "weight": 0.05,
            "basis": f"cutoff {'未知' if cutoff_date is None else cutoff_date.isoformat()}；"
                     f"随访 {follow_months if follow_months is not None else '未知'} 个月",
            "missing": [],
        },
        {
            "id": "package",
            "label_zh": "资料包完整度",
            "label_en": "Package completeness",
            "score": package_score,
            "weight": 0.05,
            "basis": f"{inventory['present_count']}/{inventory['expected_count']} 类关键文档已提供",
            "missing": [row["label_zh"] for row in inventory["expected"] if not row["present"]],
        },
    ]
    overall = round(sum(d["score"] * d["weight"] for d in dimensions), 1)
    critical_contradictions = [
        f for f in active
        if str(f.get("finding_type")) == "data_inconsistency"
        and str(f.get("severity")) == "critical"
    ]
    readiness_cap = 65.0 if len(critical_contradictions) == 1 else (55.0 if len(critical_contradictions) >= 2 else 100.0)
    if overall > readiness_cap:
        overall = readiness_cap
    if overall >= 80:
        level, level_zh = "high", "高"
    elif overall >= 65:
        level, level_zh = "medium_high", "中高"
    elif overall >= 50:
        level, level_zh = "medium", "中"
    else:
        level, level_zh = "low", "低"

    return {
        "overall": {
            "score": overall,
            "level": level,
            "label_zh": level_zh,
            "title_zh": "尽调就绪度",
            "title_en": "Diligence readiness",
            "critical_contradictions": len(critical_contradictions),
            "readiness_cap": readiness_cap if readiness_cap < 100 else None,
            "disclaimer": (
                "规则化汇总，基于证据完整度、可核验性、一致性、覆盖度与数据成熟度；"
                "不构成投资建议，也不自动给出 Go/No-Go 结论。"
            ),
        },
        "dimensions": dimensions,
        "risk": _risk_summary(findings),
        "consistency": _consistency_summary(findings),
        "coverage": {
            "counts": _coverage_counts(coverage),
            "applicable": len(applicable),
        },
        "evidence": {
            "total": len(evidence),
            "verified": len(verified),
            "verified_pct": quality_score,
        },
    }


def _coverage_counts(coverage: list[dict]) -> dict:
    counts = {"sufficient": 0, "partial": 0, "not_found": 0, "not_applicable": 0, "not_reviewed": 0}
    for c in coverage:
        key = str(c.get("status") or "not_reviewed")
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_overview(session, project_id: str) -> dict:
    """Aggregate discovery + evaluation data for the interactive dashboard."""
    project = store.get_project(session, project_id)
    if not project:
        raise ValueError(f"project not found: {project_id}")

    documents = store.list_documents(session, project_id)
    active_ids = {d["document_id"] for d in documents if d.get("is_latest", True)}
    all_evidence = store.get_evidence(session, project_id)
    evidence = [e for e in all_evidence if e.get("document_id") in active_ids]
    findings = store.list_findings(session, project_id)
    questions = store.list_questions(session, project_id)
    coverage = store.list_coverage(session, project_id)
    assets = store.list_assets(session, project_id)
    trials = store.list_trials(session, project_id)
    runs = store.list_runs(session, project_id, limit=3)
    stats = store.project_stats(session, project_id)

    attributes = _profile_attributes(assets, trials, evidence)
    inventory = _doc_inventory(documents)
    highlights = _document_highlights(session, project_id, documents)

    key_facts: list[dict] = []
    for field, label_zh, label_en, kind in FACT_DEFS:
        candidates = _evidence_for(evidence, field)
        chosen = _pick_fact(candidates)
        if chosen is None:
            continue
        key_facts.append(
            {
                "field": str(field),
                "label_zh": label_zh,
                "label_en": label_en,
                "value_display": _fmt_value(chosen, kind),
                "value_num": chosen.get("value_num"),
                "value_text": chosen.get("value_text"),
                "unit": chosen.get("unit"),
                "population": chosen.get("population"),
                "cutoff": chosen.get("data_cutoff"),
                "assessment_method": chosen.get("assessment_method"),
                "document": chosen.get("filename"),
                "document_id": chosen.get("document_id"),
                "document_type": chosen.get("document_type"),
                "page": chosen.get("page"),
                "quote": chosen.get("quote"),
                "evidence_id": chosen.get("evidence_id"),
                "verification_status": chosen.get("verification_status"),
                "alternatives": max(0, len(candidates) - 1),
            }
        )

    active_findings = sorted(
        [f for f in findings if str(f.get("status")) in ACTIVE_STATUSES],
        key=lambda f: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(str(f.get("severity")), 9),
            str(f.get("title")),
        ),
    )
    active_questions = sorted(
        questions,
        key=lambda q: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(q.get("priority")), 9),
            str(q.get("question_id")),
        ),
    )

    now = datetime.now(timezone.utc).date()
    evaluation = _evaluate(evidence, findings, coverage, documents, attributes, trials, now)

    versions = {
        "latest": [
            {"filename": d["filename"], "doc_type": d["doc_type"], "version": d["version"],
             "pages": d["page_count"]}
            for d in documents if d.get("is_latest", True)
        ],
        "superseded": [
            {"filename": d["filename"], "doc_type": d["doc_type"], "version": d["version"],
             "pages": d["page_count"], "superseded_by": d.get("document_id")}
            for d in documents if not d.get("is_latest", True)
        ],
    }

    return {
        "project": project,
        "stats": stats,
        "latest_run": _run_summary(runs[0]) if runs else None,
        "discovery": {
            "assets": assets,
            "trials": trials,
            "attributes": attributes,
            "key_facts": key_facts,
            "documents": inventory,
            "document_highlights": highlights,
        },
        "evaluation": evaluation,
        "findings": [
            {
                "finding_id": f["finding_id"],
                "finding_type": f["finding_type"],
                "severity": f["severity"],
                "status": f["status"],
                "title": f["title"],
                "domain": f.get("domain"),
                "evidence_count": len(f.get("evidence_ids") or []),
            }
            for f in active_findings
        ],
        "questions": [
            {
                "question_id": q["question_id"],
                "priority": q["priority"],
                "owner": q["owner"],
                "question": _truncate(q.get("question") or "", 180),
                "status": q["status"],
                "review_status": q.get("review_status"),
            }
            for q in active_questions
        ],
        "versions": versions,
    }


def _run_summary(run: dict) -> dict:
    return {
        "run_id": run.get("run_id"),
        "state": run.get("state"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
    }


def _truncate(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
