import json
from contextlib import closing
from datetime import date

from fastapi import APIRouter, Query
from hijenny.public_events import connect

router = APIRouter()


@router.get("/api/public-events")
def public_events(asset: str = "", region: str = "", since: date | None = None,
                  offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    filters = """ WHERE (instr(lower(asset), lower(?))>0
                       OR instr(lower(json_extract(payload, '$.subject')), lower(?))>0)
                    AND instr(lower(coalesce(region,'')), lower(?))>0 AND date>=?"""
    args = (asset.strip(), asset.strip(), region.strip(), since.isoformat() if since else "")
    with closing(connect()) as db:
        count = db.execute("SELECT count(*) FROM events" + filters, args).fetchone()[0]
        rows = db.execute("SELECT payload FROM events" + filters + " ORDER BY date DESC, id LIMIT ? OFFSET ?",
                          (*args, limit, offset))
        return {"total": count, "items": [json.loads(row[0]) for row in rows]}
