"""Opt-in retrieval of public records. No project data enters these requests."""
import asyncio
import re
from typing import Literal

from xml.etree import ElementTree

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=400)
    source: Literal["pubmed", "trials"]


async def pubmed(client, query):
    result = await client.get(f"{NCBI}/esearch.fcgi", params={
        "db": "pubmed", "term": query, "retmax": 10, "retmode": "json",
        "sort": "relevance",
    })
    result.raise_for_status()
    search = result.json()["esearchresult"]
    ids = search["idlist"]
    if not ids:
        return int(search["count"]), []
    summary = await client.get(f"{NCBI}/efetch.fcgi", params={
        "db": "pubmed", "id": ",".join(ids), "retmode": "xml",
    })
    summary.raise_for_status()
    root = ElementTree.fromstring(summary.content)
    items = []
    for citation in root.findall("./PubmedArticle/MedlineCitation"):
        pmid = citation.findtext("PMID")
        article = citation.find("Article")
        if not pmid or article is None:
            continue
        title = article.find("ArticleTitle")
        pubdate = article.find("Journal/JournalIssue/PubDate")
        date = " ".join(pubdate.itertext()).strip() if pubdate is not None else ""
        passages = [{"label": node.get("Label") or "Abstract", "text": "".join(node.itertext()).strip()}
                    for node in article.findall("Abstract/AbstractText")]
        authors = []
        for author in article.findall("AuthorList/Author"):
            surname = author.findtext("LastName")
            if surname:
                name = " ".join(filter(None, (author.findtext("ForeName") or author.findtext("Initials"), surname)))
                authors.append(name)
        items.append({"authors": authors, "id": pmid, "title": "".join(title.itertext()) if title is not None else pmid,
                      "date": " ".join(date.split()), "detail": article.findtext("Journal/Title", ""),
                      "passages": passages, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"})
    return int(search["count"]), items


async def trials(client, query):
    base_url = "https://clinicaltrials.gov/api/v2/studies"
    if re.fullmatch(r"NCT[0-9]{8}", query, re.IGNORECASE):
        response = await client.get(f"{base_url}/{query.upper()}")
        if response.status_code == 404:
            return 0, []
        response.raise_for_status()
        data = {"totalCount": 1, "studies": [response.json()]}
    else:
        response = await client.get(base_url, params={
            "query.term": query, "pageSize": 10, "countTotal": "true",
        })
        response.raise_for_status()
        data = response.json()
    items = []
    for record in data["studies"]:
        protocol = record["protocolSection"]
        ident = protocol["identificationModule"]
        status = protocol["statusModule"]
        nct = ident["nctId"]
        passages = []
        brief = protocol.get("descriptionModule", {}).get("briefSummary")
        if brief:
            passages.append({"label": "登记摘要", "text": brief})
        for outcome in protocol.get("outcomesModule", {}).get("primaryOutcomes", []):
            passages.append({"label": "登记主要终点", "text": " · ".join(
                str(outcome[key]) for key in ("measure", "timeFrame") if outcome.get(key))})
        design = protocol.get("designModule", {})
        eligibility = protocol.get("eligibilityModule", {})
        design_info = design.get("designInfo", {})
        comparison = {
            "适应症": protocol.get("conditionsModule", {}).get("conditions", []),
            "阶段": design.get("phases", []),
            "研究类型": design.get("studyType"),
            "分配方式": design_info.get("allocation"),
            "盲法": design_info.get("maskingInfo", {}).get("masking"),
            "入组人数": design.get("enrollmentInfo", {}).get("count"),
            "入组数类型": design.get("enrollmentInfo", {}).get("type"),
            "年龄下限": eligibility.get("minimumAge"),
            "年龄上限": eligibility.get("maximumAge"),
            "登记主要终点": [p["text"] for p in passages if p["label"] == "登记主要终点"],
        }
        milestones = {}
        for key, label in (("primaryCompletionDateStruct", "主要完成日期"), ("completionDateStruct", "研究完成日期")):
            info = status.get(key) or {}
            milestones[label] = {"date": info.get("date"), "type": info.get("type")}
        items.append({"milestones": milestones, "comparison": comparison, "passages": passages, "id": nct, "title": ident["briefTitle"],
                      "date": status.get("lastUpdatePostDateStruct", {}).get("date", ""),
                      "detail": status["overallStatus"], "url": f"https://clinicaltrials.gov/study/{nct}"})
    return data["totalCount"], items


@router.post("/api/research/search")
async def search_public_records(request: SearchRequest):
    query = request.query.strip()
    if not query:
        raise HTTPException(422, "请输入公开的药物名、适应症或检索式。")
    retrieve = pubmed if request.source == "pubmed" else trials
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                total, items = await retrieve(client, query)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                await asyncio.sleep(0.4)
                total, items = await retrieve(client, query)
    except (httpx.HTTPError, ElementTree.ParseError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "暂时无法读取所选数据库，请稍后重试。") from exc
    return {"query": query, "source": request.source, "total": total, "items": items}
