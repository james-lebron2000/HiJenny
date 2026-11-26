import json
from contextlib import closing
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from hijenny.public_events import connect

router = APIRouter()
OBSERVED_DAY = "date(json_extract(payload, '$.observed'), '+8 hours')"
KINDS = {"baseline": "首次收录", "change": "字段变化", "reviewed": "原文核实"}


def event_filters(as_of: date | None = None, asset: str = "", region: str = "", since: date | None = None,
                  discovered_from: date | None = None, discovered_to: date | None = None,
                  disclosed_to: date | None = None, source: Literal["SEC", "PubMed", "ClinicalTrials.gov", "Drugs@FDA"] | None = None,
                  event_id: str | None = None, publication_status: bool = False, study_interruption: bool = False,
                  kind: Literal["baseline", "change", "reviewed"] | None = None):
    if discovered_from and discovered_to and discovered_from > discovered_to:
        raise HTTPException(422, "发现起始日期须早于或等于结束日期。")
    sql = """ WHERE (instr(lower(asset), lower(?))>0
                       OR instr(lower(json_extract(payload, '$.subject')), lower(?))>0)
                    AND instr(lower(coalesce(region,'')), lower(?))>0 AND date>=?"""
    args = [asset.strip(), asset.strip(), region.strip(), since.isoformat() if since else ""]
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
        ("范围", "试验暂停、终止或撤回" if study_interruption else None), ("来源", source), ("披露截止日", disclosed_to), ("范围", "论文出版状态" if publication_status else None), ("动态编号", event_id), ("资产或公司", asset), ("地区", region), ("披露起始日", since), ("发现起始日", discovered_from),
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
        args = [as_of.isoformat(), as_of.isoformat(), *args]
        labels.insert(0, f"历史截面（截至台北日期）：{as_of}，每个来源字段仅列当时最后一次已收录的值。")
    return table, sql, args, order, labels


@router.get("/api/public-events")
def public_events(filters=Depends(event_filters), offset: int = Query(0, ge=0),
                  limit: int = Query(50, ge=1, le=100)):
    table, sql, args, order, _ = filters
    with closing(connect()) as db:
        db.execute("BEGIN")
        count = db.execute(f"SELECT count(*) FROM {table}" + sql, args).fetchone()[0]
        rows = db.execute(f"SELECT payload FROM {table}" + sql + order + " LIMIT ? OFFSET ?", (*args, limit, offset))
        return {"total": count, "items": [json.loads(row[0]) for row in rows]}


def display(value):
    if isinstance(value, dict):
        return " · ".join(str(v) for v in (value.get("date"), value.get("type")) if v)
    return "未披露" if value is None else str(value)


@router.get("/api/public-events/report.txt", response_class=PlainTextResponse)
def report(filters=Depends(event_filters)):
    table, sql, args, order, labels = filters
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
                kind = "配套文件更新"
            elif row["kind"] == "change":
                value = display(row["previous"]) + " → " + value
            lines.extend(["", f'{count}. {row["asset"] or row["subject"]} · {row["field"]}',
                          f'{kind} · {value}', f'{row.get("date_label", "来源披露")} {row["source_date"]} · 首次发现 {row["observed"]}'])
            if row.get("effective"):
                lines.append("事件发生或生效 " + row["effective"])
            if row.get("region"):
                lines.append("地区 " + row["region"])
            lines.append(f'{row["source"]} · {row["record"]} · {row["url"]}')
            for doc in row.get("documents", []):
                lines.append(f'{doc["type"]} · {doc["url"]}')
    lines.append(f"\n共 {count} 条")
    return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": 'attachment; filename="HiJenny-public-events.txt"'})
