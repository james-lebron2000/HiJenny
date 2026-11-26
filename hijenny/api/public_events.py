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


def event_filters(asset: str = "", region: str = "", since: date | None = None,
                  discovered_from: date | None = None, discovered_to: date | None = None,
                  event_id: str | None = None,
                  kind: Literal["baseline", "change", "reviewed"] | None = None):
    if discovered_from and discovered_to and discovered_from > discovered_to:
        raise HTTPException(422, "发现起始日期须早于或等于结束日期。")
    sql = """ WHERE (instr(lower(asset), lower(?))>0
                       OR instr(lower(json_extract(payload, '$.subject')), lower(?))>0)
                    AND instr(lower(coalesce(region,'')), lower(?))>0 AND date>=?"""
    args = [asset.strip(), asset.strip(), region.strip(), since.isoformat() if since else ""]
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
        ("动态编号", event_id), ("资产或公司", asset), ("地区", region), ("披露起始日", since), ("发现起始日", discovered_from),
        ("发现结束日", discovered_to), ("记录类型", KINDS.get(kind))) if value]
    return sql, args, order, labels


@router.get("/api/public-events")
def public_events(filters=Depends(event_filters), offset: int = Query(0, ge=0),
                  limit: int = Query(50, ge=1, le=100)):
    sql, args, order, _ = filters
    with closing(connect()) as db:
        db.execute("BEGIN")
        count = db.execute("SELECT count(*) FROM events" + sql, args).fetchone()[0]
        rows = db.execute("SELECT payload FROM events" + sql + order + " LIMIT ? OFFSET ?", (*args, limit, offset))
        return {"total": count, "items": [json.loads(row[0]) for row in rows]}


def display(value):
    if isinstance(value, dict):
        return " · ".join(str(v) for v in (value.get("date"), value.get("type")) if v)
    return "未披露" if value is None else str(value)


@router.get("/api/public-events/report.txt", response_class=PlainTextResponse)
def report(filters=Depends(event_filters)):
    sql, args, order, labels = filters
    lines = ["HiJenny 公开资产动态", "筛选范围（发现日期按台北时间）", *labels]
    with closing(connect()) as db:
        rows = db.execute("SELECT payload FROM events" + sql + order, args)
        count = 0
        for count, (payload,) in enumerate(rows, 1):
            row = json.loads(payload)
            value = display(row["value"])
            if row["kind"] == "change":
                value = display(row["previous"]) + " → " + value
            lines.extend(["", f'{count}. {row["asset"] or row["subject"]} · {row["field"]}',
                          f'{KINDS[row["kind"]]} · {value}', f'来源披露 {row["source_date"]} · 首次发现 {row["observed"]}'])
            if row.get("effective"):
                lines.append("事件发生或生效 " + row["effective"])
            if row.get("region"):
                lines.append("地区 " + row["region"])
            lines.append(f'{row["source"]} · {row["record"]} · {row["url"]}')
    lines.append(f"\n共 {count} 条")
    return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": 'attachment; filename="HiJenny-public-events.txt"'})
