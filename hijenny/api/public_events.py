import json
import re
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from hijenny.asset_names import verified_asset
from hijenny.config import REPO_ROOT
from hijenny.public_events import connect
from hijenny.registry_report import outcome_text, safety_text

router = APIRouter()
OBSERVED_DAY = "date(json_extract(payload, '$.observed'), '+8 hours')"
KINDS = {"baseline": "首次收录", "change": "字段变化", "reviewed": "原文核实"}


def event_filters(as_of: date | None = None, asset: str = "", region: str = "", since: date | None = None,
                  discovered_from: date | None = None, discovered_to: date | None = None,
                  observed_after: datetime | None = None, disclosed_to: date | None = None, source: Literal["NMPA", "SEC", "PubMed", "ClinicalTrials.gov", "Drugs@FDA", "EMA", "jRCT", "FDA Orange Book"] | None = None,
                  document_type: Literal["Label", "Letter", "Review", "Summary Review", "Medication Guide"] | None = None,
                  source_record: str = "", notice_title: str = "", notice_id: str = "", trial: str = "", application: str = "", event_id: str | None = None, publication_status: bool = False, study_interruption: bool = False,
                  kind: Literal["baseline", "change", "reviewed"] | None = None):
    if discovered_from and discovered_to and discovered_from > discovered_to:
        raise HTTPException(422, "发现起始日期须早于或等于结束日期。")
    if since and disclosed_to and since > disclosed_to:
        raise HTTPException(422, "披露起始日期须早于或等于结束日期。")
    asset = asset.strip()
    match = ["instr(lower(asset), lower(?))>0", "instr(lower(json_extract(payload, '$.subject')), lower(?))>0"]
    args = [asset, asset]
    mapping = verified_asset(asset)
    if mapping:
        match.append("instr(lower(json_extract(payload, '$.asset')), ?) > 0")
        args.append(mapping["name"])
    sql = " WHERE (" + " OR ".join(match) + ") AND instr(lower(coalesce(region,'')), lower(?))>0 AND date>=?"
    args.extend([region.strip(), since.isoformat() if since else ""])
    if document_type:
        sql += """ AND json_extract(payload, '$.source')='Drugs@FDA' AND EXISTS (
                   SELECT 1 FROM json_each(payload, '$.documents') AS doc
                   WHERE json_extract(doc.value, '$.type')=?)"""
        args.append(document_type)
    source_record = source_record.strip()
    if source_record:
        sql += " AND json_extract(payload, '$.record')=?"
        args.append(source_record)
    notice_id = notice_id.strip()
    if notice_id:
        if not re.fullmatch(r"[0-9]+", notice_id):
            raise HTTPException(422, "请输入数字公告编号。")
        sql += """ AND json_extract(payload, '$.source')='NMPA' AND json_extract(payload, '$.record')=?"""
        args.append(notice_id)
    notice_title = notice_title.strip()
    if notice_title:
        sql += """ AND json_extract(payload, '$.category')='nmpa-index'
                   AND instr(lower(json_extract(payload, '$.value')), lower(?))>0"""
        args.append(notice_title)
    application = application.strip().upper()
    if application:
        if not re.fullmatch(r"(?:NDA|ANDA|BLA)[0-9]{6}", application):
            raise HTTPException(422, "请输入 NDA、ANDA 或 BLA 加六位数字。")
        sql += """ AND json_extract(payload, '$.source') IN ('Drugs@FDA','FDA Orange Book')
                   AND instr(json_extract(payload, '$.record'), ?) = 1"""
        args.append(application + "/")
    trial = trial.strip().upper()
    link_query = ""
    link_args = []
    if trial:
        if re.fullmatch(r"NCT[0-9]{8}", trial):
            link_query = "SELECT ? AS matched_nct"
            link_args = [trial]
        elif re.fullmatch(r"JRCT[0-9]{10}", trial):
            trial = "jRCT" + trial[4:]
            link_query = """SELECT json_extract(payload, '$.trial') AS matched_nct FROM events
                            WHERE json_extract(payload, '$.source')='jRCT' AND json_extract(payload, '$.record')=?
                            AND json_extract(payload, '$.field')='日本登记所列关联试验'
                            AND json_extract(payload, '$.value')=json_extract(payload, '$.trial')"""
            link_args = [trial]
            for cutoff, column in [(as_of, "date"), (as_of, OBSERVED_DAY), (disclosed_to, "date"),
                                   (discovered_to, OBSERVED_DAY)]:
                if cutoff:
                    link_query += f" AND {column}<=?"
                    link_args.append(cutoff.isoformat())
            link_query += " ORDER BY julianday(json_extract(payload, '$.observed')) DESC, rowid DESC LIMIT 1"
        else:
            raise HTTPException(422, "请输入 NCT 加八位数字或 jRCT 加十位数字。")
        sql += """ AND ((json_extract(payload, '$.source')='ClinicalTrials.gov' AND json_extract(payload, '$.record')=matched_nct)
                     OR (json_extract(payload, '$.source') IN ('PubMed','jRCT') AND json_extract(payload, '$.trial')=matched_nct)
                     OR (json_extract(payload, '$.source')='Drugs@FDA' AND EXISTS (
                         SELECT 1 FROM json_each(payload, '$.label_trials') AS citation
                         WHERE json_extract(citation.value, '$.id')=matched_nct)))"""
    if disclosed_to:
        sql += " AND date<=?"
        args.append(disclosed_to.isoformat())
    if source:
        sql += " AND json_extract(payload, '$.source')=?"
        args.append(source)
    if publication_status:
        sql += " AND json_extract(payload, '$.category')='publication-status'"
    if study_interruption:
        sql += " AND json_extract(payload, '$.category')='study-interruption' AND json_extract(payload, '$.value') IS NOT NULL"
    if event_id:
        sql += " AND id=?"
        args.append(event_id)
    if observed_after is not None:
        if observed_after.tzinfo is None:
            raise HTTPException(422, "观察时间须包含时区。")
        sql += " AND julianday(json_extract(payload, '$.observed'))>julianday(?)"
        args.append(observed_after.isoformat())
    if discovered_from:
        sql += f" AND {OBSERVED_DAY}>=?"
        args.append(discovered_from.isoformat())
    if discovered_to:
        sql += f" AND {OBSERVED_DAY}<=?"
        args.append(discovered_to.isoformat())
    if kind:
        sql += " AND json_extract(payload, '$.kind')=?"
        args.append(kind)
    order = (" ORDER BY julianday(json_extract(payload, '$.observed')) DESC, date DESC, id"
             if source_record or discovered_from or discovered_to else " ORDER BY date DESC, id")
    labels = [f"{label}：{value}" for label, value in (
        ("范围", "试验暂停、终止或撤回" if study_interruption else None), ("NMPA 栏目标题含", notice_title), ("NMPA 公告编号", notice_id), ("试验登记号", trial), ("FDA 申请号", application), ("当前归档含 FDA 文件类型", document_type), ("来源", source), ("来源记录编号", source_record), ("披露截止日", disclosed_to), ("范围", "论文出版状态" if publication_status else None), ("动态编号", event_id), ("资产或公司", asset), ("地区", region), ("披露起始日", since), ("发现起始日", discovered_from),
        ("发现结束日", discovered_to), ("仅含首次观察晚于", observed_after.isoformat() if observed_after else None), ("记录类型", KINDS.get(kind))) if value]
    table = "events"
    if as_of:
        # Select the last observation per source field before applying display filters.
        table = f"""(SELECT *, row_number() OVER (
                       PARTITION BY json_extract(payload, '$.source'), json_extract(payload, '$.record'),
                                    json_extract(payload, '$.field')
                       ORDER BY julianday(json_extract(payload, '$.observed')) DESC, rowid DESC
                     ) AS position FROM events WHERE date<=? AND {OBSERVED_DAY}<=?)"""
        sql += " AND position=1"
        link_args = [as_of.isoformat(), as_of.isoformat(), *link_args]
        labels.insert(0, f"历史截面（截至台北日期）：{as_of}，每个来源字段仅列当时最后一次已收录的值。")
    if link_query:
        table += f" CROSS JOIN ({link_query}) AS registration"
    if mapping:
        labels.append(f"核对药名：{mapping['name']} · 名称来源：{mapping['source']}")
    return table, sql, [*link_args, *args], order, labels, mapping


@router.get("/api/public-events/collection")
def latest_collection():
    with closing(connect()) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT payload FROM collection_receipt WHERE id=1").fetchone()
        check = db.execute("SELECT payload FROM verification WHERE id=1").fetchone()
        # For the newest dated disclosure, retain the first archived observation of that date.
        samples = db.execute(f"""
            WITH dated AS (
                SELECT id, date, json_extract(payload, '$.source') AS source,
                       json_extract(payload, '$.observed') AS observed, {OBSERVED_DAY} AS observed_day
                FROM events WHERE length(date)=10 AND date(date, '+0 days')=date
            ), ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY source ORDER BY date DESC, julianday(observed), id
                ) AS position FROM dated WHERE observed_day IS NOT NULL
            )
            SELECT source, id, date, observed_day,
                   CAST(julianday(observed_day)-julianday(date) AS INTEGER)
            FROM ranked WHERE position=1
        """)
        timing = {source: dict(event_id=event_id, source_date=source_date, observed_day=day, days=days)
                  for source, event_id, source_date, day, days in samples}
    return {"receipt": json.loads(row[0]) if row else None,
            "verification": json.loads(check[0]) if check else None, "source_timing": timing}


def source_counts(db, table, sql, args):
    rows = db.execute(f"""SELECT json_extract(payload, '$.source') AS source, count(*) AS events,
                          count(DISTINCT json_extract(payload, '$.record')) AS records
                          FROM {table}""" + sql + " GROUP BY source ORDER BY events DESC, source", args)
    return [{"source": source, "events": events, "records": records} for source, events, records in rows]


def document_changes(row):
    if row["source"] != "Drugs@FDA" or "previous_documents" not in row or "documents" not in row:
        return {}
    current = {(d["type"], d["url"]) for d in row["documents"]}
    previous = {(d["type"], d["url"]) for d in row["previous_documents"]}
    return {key: [{"type": kind, "url": url} for kind, url in sorted(diff)]
            for key, diff in (("added", current - previous), ("removed", previous - current)) if diff}


@router.get("/api/public-events/asset-evidence")
def asset_evidence(asset: str, as_of: date | None = None):
    asset = asset.strip()
    if not asset:
        raise HTTPException(422, "请输入药物名。")
    cutoff = as_of or datetime.now(timezone(timedelta(hours=8))).date()
    table, sql, args, order, _, mapping = event_filters(as_of=cutoff, asset=asset, source="ClinicalTrials.gov")
    fields = {"登记状态": "status", "登记研究阶段": "phase", "登记研究疾病": "conditions",
              "牵头申办方": "sponsor", "试验中心所在国家或地区": "countries", "登记年龄组": "ages",
              "方案登记主要终点": "planned_endpoints"}
    placeholders = ",".join("?" for _ in fields)
    sql += f" AND (json_extract(payload, '$.field') IN ({placeholders}) OR json_extract(payload, '$.category')='primary-outcomes')"
    args.extend(fields)
    trials, outcomes = {}, []
    with closing(connect()) as db:
        for (payload,) in db.execute("SELECT payload FROM " + table + sql + order, args):
            row = json.loads(payload)
            entry = {key: row[key] for key in ("id", "record", "asset", "source_date")}
            if row.get("category") == "primary-outcomes":
                entry["endpoints"] = [m["title"] for m in (row["value"] or {}).get("primary_outcomes", [])]
                if entry["endpoints"]:
                    outcomes.append(entry)
            else:
                trial = trials.setdefault(row["record"], {})
                trial[fields[row["field"]]] = row["value"]
                if row["field"] == "登记状态":
                    trial.update(entry)
    name = mapping["name"] if mapping else asset.casefold()
    catalog = json.loads((REPO_ROOT / "data/public/guidance.json").read_text(encoding="utf-8"))
    guidance = [g for g in catalog if g["asset"] == name and
                max(g["published"], g["version_date"], g["observed"]) <= cutoff.isoformat()]
    return {"as_of": cutoff.isoformat(), "trials": [r for r in trials.values() if "id" in r], "outcomes": outcomes, "guidance": guidance}


@router.get("/api/public-events/count")
def public_event_count(filters=Depends(event_filters)):
    table, sql, args, _, _, _ = filters
    with closing(connect()) as db:
        count = db.execute(f"SELECT count(*) FROM {table}" + sql, args).fetchone()[0]
    return {"total": count}


@router.get("/api/public-events")
def public_events(filters=Depends(event_filters), offset: int = Query(0, ge=0),
                  limit: int = Query(50, ge=1, le=100)):
    table, sql, args, order, _, mapping = filters
    with closing(connect()) as db:
        db.execute("BEGIN")
        counts = source_counts(db, table, sql, args)
        count = sum(row["events"] for row in counts)
        rows = db.execute(f"SELECT payload FROM {table}" + sql + order + " LIMIT ? OFFSET ?", (*args, limit, offset))
        items = [json.loads(row[0]) for row in rows]
        differences = {row["id"]: diff for row in items if (diff := document_changes(row))}
        return {"total": count, "source_counts": counts, "asset_match": mapping,
                "document_changes": differences, "items": items}


def display(value):
    if isinstance(value, dict) and "safety_summary" in value:
        return safety_text(value["safety_summary"])
    if isinstance(value, dict) and "primary_outcomes" in value:
        return "\n\n".join(outcome_text(m) for m in value["primary_outcomes"])
    if isinstance(value, dict):
        return " · ".join(str(v) for v in (value.get("date"), value.get("type")) if v)
    return "未披露" if value is None else str(value)


@router.get("/api/public-events/report.txt", response_class=PlainTextResponse)
def report(request: Request, filters=Depends(event_filters)):
    table, sql, args, order, labels, _ = filters
    lines = ["HiJenny 公开资产动态", "筛选范围（发现日期按台北时间）", *labels]
    with closing(connect()) as db:
        db.execute("BEGIN")
        receipt_row = db.execute("SELECT payload FROM collection_receipt WHERE id=1").fetchone()
        if receipt_row:
            receipt = json.loads(receipt_row[0])
            lines.append("导出时数据库所载最近一次采集回执 · " + receipt["finished_utc"])
            run_id = str(receipt.get("run_id", ""))
            if re.fullmatch(r"[0-9]+", run_id):
                lines.append("采集记录 · https://github.com/james-lebron2000/HiJenny/actions/runs/" + run_id)
        else:
            lines.append("导出时数据库尚无采集回执")
        archive_page = str(request.base_url) + "#/public-events?"
        counts = source_counts(db, table, sql, args)
        if counts:
            lines.extend(["", "当前筛选的来源分布（编号在各来源内去重）"])
            lines.extend(f"{r['source']} · {r['events']} 条归档 · {r['records']} 个来源编号" for r in counts)
        rows = db.execute(f"SELECT payload FROM {table}" + sql + order, args)
        count = 0
        for count, (payload,) in enumerate(rows, 1):
            row = json.loads(payload)
            value = display(row["value"])
            kind = KINDS[row["kind"]]
            if (row["kind"] == "change" and row["source"] == "Drugs@FDA" and
                    row["previous"] == row["value"] and row.get("previous_date") == row["source_date"]):
                kind = "标签试验关联更新" if row.get("label_trials_changed") else "配套文件更新"
            elif row["kind"] == "change" and row.get("category") in ("primary-outcomes", "safety-summary"):
                value = "本次归档\n" + value + "\n\n上次归档\n" + display(row["previous"])
            elif row["kind"] == "change":
                value = display(row["previous"]) + " → " + value
            lines.extend(["", f'{count}. {row["asset"] or row["subject"]} · {row["field"]}',
                          f'{kind} · {value}', f'{row.get("date_label", "来源披露")} {row["source_date"]} · 首次发现 {row["observed"]}'])
            if row.get("category") == "publication-mention":
                lines.append("题名或摘要提及该登记号，结果归属待原文复核。")
            if row.get("effective"):
                lines.append("事件发生或生效 " + row["effective"])
            if row.get("region"):
                lines.append("地区 " + row["region"])
            lines.append(f'{row["source"]} · {row["record"]} · {row["url"]}')
            lines.append("HiJenny 单条归档 · " + archive_page + urlencode({"event": row["id"]}))
            if row["source"] == "FDA Orange Book":
                lines.append("专利和独占期分别归档。这些日期不能单独确定仿制药上市时间。")
            if row["source"] == "jRCT":
                lines.append("登记所列关联试验 · https://clinicaltrials.gov/study/" + row["trial"])
            for trial in row.get("label_trials", []):
                lines.extend([f'标签所列试验 · {trial["name"]} · https://clinicaltrials.gov/study/{trial["id"]}',
                              f'{trial["section"]} · 第 {trial["page"]} 页 · {trial["label"]}#page={trial["page"]}'])
            diff = document_changes(row)
            for doc in row.get("documents", []):
                prefix = "本次新增归档链接 · " if doc in diff.get("added", []) else ""
                lines.append(f'{prefix}{doc["type"]} · {doc["url"]}')
            for doc in diff.get("removed", []):
                lines.append(f'仅上次归档列出 · {doc["type"]} · {doc["url"]}')
    lines.append(f"\n共 {count} 条")
    return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": 'attachment; filename="HiJenny-public-events.txt"'})
