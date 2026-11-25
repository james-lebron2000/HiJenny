"""Single-asset paid pilot intake. Receipts are returned only to the submitter."""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from hijenny import config, store

router = APIRouter()


class PilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_key: UUID
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")
    stage: Literal["临床前", "I 期", "II 期", "III 期", "上市后", "待确认"]
    pages: Literal["50 页以内", "51–200 页", "201–500 页", "500 页以上", "待确认"]
    meeting: date
    deployment: Literal["HiJenny 托管试点", "客户专属环境", "待讨论"]


@router.post("/api/pilot/inquiries")
def submit(request: PilotRequest):
    fields = request.model_dump(mode="json", exclude={"request_key"})
    try:
        with store.session_scope() as session:
            return store.submit_pilot_inquiry(session, str(request.request_key), fields)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

@router.get("/api/pilot/sample-report")
def sample_report():
    return FileResponse(config.REPO_ROOT / "docs/sample_output/HiJenny_Report.md",
                        media_type="text/markdown", filename="HiJenny-Synthetic-Diligence-Report.md")


@router.get("/api/pilot/sources/{source}")
def pilot_source(source: Literal["csr", "forecast"]):
    filenames = {"csr": "Clinical_Study_Report_v1.0.pdf", "forecast": "Commercial_Forecast_v1.pptx"}
    name = filenames[source]
    # Only the committed synthetic package belongs in this unauthenticated demonstration.
    return FileResponse(config.REPO_ROOT / "data/sample_package" / name, filename=name)
