import json
import re
from contextlib import closing
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from hijenny.asset_names import verified_asset
from hijenny.public_events import connect
from hijenny.registry_report import outcome_text, safety_text

router = APIRouter()
OBSERVED_DAY = "date(json_extract(payload, '$.observed'), '+8 hours')"
KINDS = {"baseline": "首次收录", "change": "字段变化", "reviewed": "原文核实"}


def event_filters(as_of: date | None = None, asset: str = "", region: str = "", since: date | None = None,
                  discovered_from: date | None = None, discovered_to: date | None = None,
                  disclosed_to: date | None = None, source: Literal["NMPA", "SEC", "PubMed", "ClinicalTrials.gov", "Drugs@FDA", "EMA", "jRCT", "FDA Orange Book"] | None = None,
                  notice_title: str = "", notice_id: str = "", trial: str = "", application: str = "", event_id: str | None = None, publication_status: bool = False, study_interruption: bool = False,
                  kind: Literal["baseline", "change", "reviewed"] | None = None):
    if discovered_from and discovered_to and discovered_from > discovered_to:
        raise HTTPException(422, "发现起始日期须早于或等于结束日期。")
    asset = asset.strip()
    match = ["instr(lower(asset), lower(?))>0", "instr(lower(json_extract(payload, '$.subject')), lower(?))>0"]
    args = [asset, asset]
    mapping = verified_asset(asset)
    if mapping:
        match.append("instr(lower(json_extract(payload, '$.asset')), ?) > 0")
        args.append(mapping["name"])
    sql = " WHERE (" + " OR ".join(match) + ") AND instr(lower(coalesce(region,'')), lower(?))>0 AND date>=?"
    args.extend([region.strip(), since.isoformat() if since else ""])
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
             if discovered_from or discovered_to else " ORDER BY date DESC, id")
    labels = [f"{label}：{value}" for label, value in (
        ("范围", "试验暂停、终止或撤回" if study_interruption else None), ("NMPA 栏目标题含", notice_title), ("NMPA 公告编号", notice_id), ("试验登记号", trial), ("FDA 申请号", application), ("来源", source), ("披露截止日", disclosed_to), ("范围", "论文出版状态" if publication_status else None), ("动态编号", event_id), ("资产或公司", asset), ("地区", region), ("披露起始日", since), ("发现起始日", discovered_from),
        ("发现结束日", discovered_to), ("记录类型", KINDS.get(kind))) if value]
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
        row = db.execute("SELECT payload FROM collection_receipt WHERE id=1").fetchone()
    return {"receipt": json.loads(row[0]) if row else None}


@router.get("/api/public-events")
def public_events(filters=Depends(event_filters), offset: int = Query(0, ge=0),
                  limit: int = Query(50, ge=1, le=100)):
    table, sql, args, order, _, mapping = filters
    with closing(connect()) as db:
        db.execute("BEGIN")
        count = db.execute(f"SELECT count(*) FROM {table}" + sql, args).fetchone()[0]
        rows = db.execute(f"SELECT payload FROM {table}" + sql + order + " LIMIT ? OFFSET ?", (*args, limit, offset))
        return {"total": count, "asset_match": mapping, "items": [json.loads(row[0]) for row in rows]}


def display(value):
    if isinstance(value, dict) and "safety_summary" in value:
        return safety_text(value["safety_summary"])
    if isinstance(value, dict) and "primary_outcomes" in value:
        return "\n\n".join(outcome_text(m) for m in value["primary_outcomes"])
    if isinstance(value, dict):
        return " · ".join(str(v) for v in (value.get("date"), value.get("type")) if v)
    return "未披露" if value is None else str(value)


@router.get("/api/public-events/report.txt", response_class=PlainTextResponse)
def report(filters=Depends(event_filters)):
    table, sql, args, order, labels, _ = filters
    lines = ["HiJenny 公开资产动态", "筛选范围（发现日期按台北时间）", *labels]
    with closing(connect()) as db:
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
            if row["source"] == "jRCT":
                lines.append("登记所列关联试验 · https://clinicaltrials.gov/study/" + row["trial"])
            for trial in row.get("label_trials", []):
                lines.extend([f'标签所列试验 · {trial["name"]} · https://clinicaltrials.gov/study/{trial["id"]}',
                              f'{trial["section"]} · 第 {trial["page"]} 页 · {trial["label"]}#page={trial["page"]}'])
            for doc in row.get("documents", []):
                lines.append(f'{doc["type"]} · {doc["url"]}')
    lines.append(f"\n共 {count} 条")
    return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": 'attachment; filename="HiJenny-public-events.txt"'})
