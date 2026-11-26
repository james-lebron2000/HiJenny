"""Registry-backed opportunity screens and traceable handoff to a diligence project."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text

from hijenny import store

router = APIRouter()
REGISTRY = "https://clinicaltrials.gov/api/v2/studies"


class Criteria(BaseModel):
    condition: str = Field(default="", max_length=200)
    intervention: str = Field(default="", max_length=200)
    sponsor: str = Field(default="", max_length=200)
    country: str = Field(default="", max_length=120)

    @field_validator("country")
    @classmethod
    def country_name(cls, value):
        value = value.strip()
        if any(not (c.isalpha() or c in " -'’.,()") for c in value):
            raise ValueError("请填写登记库使用的国家或地区名称")
        return value

    phase: Literal["", "EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4"] = ""

    @field_validator("condition", "intervention")
    @classmethod
    def trim_term(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def require_search_term(self):
        if not self.condition and not self.intervention:
            raise ValueError("请填写适应症或药物／干预名称")
        return self


class RegistryScreen(BaseModel):
    criteria: Criteria
    cursor: str = Field(default="", max_length=4000)


async def fetch_screen(client, request):
    criteria = request.criteria
    params = {"countTotal": "true", "pageSize": 20,
              "fields": "NCTId,BriefTitle,LeadSponsorName,Condition,Phase,OverallStatus,InterventionName,LastUpdatePostDate,LocationCountry"}
    if criteria.condition:
        params["query.cond"] = criteria.condition
    if criteria.intervention:
        params["query.intr"] = criteria.intervention
    if criteria.sponsor.strip():
        params["query.lead"] = criteria.sponsor.strip()
    filters = []
    if criteria.phase:
        filters.append(f"AREA[Phase]{criteria.phase}")
    if criteria.country:
        filters.append(f'AREA[LocationCountry]"{criteria.country}"')
    if filters:
        params["filter.advanced"] = " AND ".join(filters)
    if request.cursor:
        params["pageToken"] = request.cursor
    response = await client.get(REGISTRY, params=params)
    response.raise_for_status()
    data = response.json()
    rows = []
    for study in data["studies"]:
        protocol = study["protocolSection"]
        ident = protocol["identificationModule"]
        status = protocol.get("statusModule", {})
        arms = protocol.get("armsInterventionsModule", {})
        rows.append({
            "id": ident["nctId"], "title": ident["briefTitle"],
            "sponsor": protocol.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", ""),
            "countries": sorted({loc["country"] for loc in protocol.get("contactsLocationsModule", {}).get("locations", [])
                                 if loc.get("country")}),
            "conditions": protocol.get("conditionsModule", {}).get("conditions", []),
            "phases": protocol.get("designModule", {}).get("phases", []),
            "interventions": [item["name"] for item in arms.get("interventions", []) if item.get("name")],
            "status": status.get("overallStatus", ""),
            "updated": status.get("lastUpdatePostDateStruct", {}).get("date", ""),
        })
    return {"items": rows, "total": data.get("totalCount") if request.cursor else data["totalCount"], "cursor": data.get("nextPageToken", ""),
            "retrieved": datetime.now(timezone.utc).isoformat()}


@router.post("/api/discovery/screen")
async def screen(request: RegistryScreen):
    try:
        async with httpx.AsyncClient(timeout=20, transport=httpx.AsyncHTTPTransport(retries=1)) as client:
            return await fetch_screen(client, request)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "暂时无法读取试验登记；已保存的筛选快照仍可打开。") from exc


class Opportunity(BaseModel):
    snapshot_id: UUID
    retrieved: datetime
    criteria: Criteria
    nct: str = Field(pattern=r"^NCT[0-9]{8}$")
    countries: list[str] = Field(default_factory=list, max_length=300)
    trial_title: str = Field(min_length=1, max_length=3000)
    sponsor: str = Field(default="", max_length=500)
    updated: str = Field(default="", max_length=20)
    asset: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=3000)
    phase: str = Field(default="", max_length=40)

    @field_validator("asset", "rationale")
    @classmethod
    def require_text(cls, value):
        if not value.strip():
            raise ValueError("请填写资产及入选依据")
        return value.strip()


@router.post("/api/discovery/projects")
def start_project(request: Opportunity):
    record = request.model_dump(mode="json")
    record["url"] = f"https://clinicaltrials.gov/study/{request.nct}"
    # Each screened trial can contain several assets. A double submission for
    # one asset must reopen the same project even from two tabs or after a timeout.
    key = [str(request.snapshot_id), request.nct, request.asset.casefold()]
    with store.session_scope() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        for existing in store.list_projects(session, "ORG_DEFAULT"):
            if existing["meta"].get("opportunity_key") == key:
                return {"project": existing, "created": False}
        project = store.create_project(session, name=f"{request.asset} · 机会评估"[:200],
                                       asset_name=request.asset, indication=request.criteria.condition,
                                       phase=request.phase, meta={"opportunity_key": key, "opportunity": record})
    return {"project": project, "created": True}
