"""Opt-in retrieval of public records. No project data enters these requests."""
import asyncio
import re
from datetime import datetime, timezone
from typing import Literal
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=800)
    source: Literal["pubmed", "trials", "guidelines"]
    article_type: Literal["", "Clinical Trial", "Randomized Controlled Trial", "Systematic Review", "Meta-Analysis", "Review"] = ""
    year_from: int | None = Field(default=None, ge=1000, le=9999)
    year_to: int | None = Field(default=None, ge=1000, le=9999)


def effective_search(request, query):
    if request.source == "trials" and (request.article_type or request.year_from or request.year_to):
        raise HTTPException(422, "文献类型和发表年份仅适用于 PubMed。")
    if request.year_from and request.year_to and request.year_from > request.year_to:
        raise HTTPException(422, "起始年份应早于或等于结束年份。")
    filters = []
    if request.source == "guidelines":
        if request.article_type:
            raise HTTPException(422, "指南检索已限定文献类型。")
        filters.append("(Guideline[pt] OR Practice Guideline[pt])")
    elif request.article_type:
        filters.append(f'"{request.article_type}"[pt]')
    if request.year_from or request.year_to:
        filters.append(f"{request.year_from or 1000}:{request.year_to or 9999}[dp]")
    return " AND ".join([f"({query})", *filters]) if filters else query


def publication_notices(citation, publication_types):
    relations = {
        "RetractionIn": "本文已有撤稿通知", "RetractionOf": "本记录是撤稿通知，所涉原文",
        "ErratumIn": "本文已有更正通知", "ErratumFor": "本记录是更正通知，所涉原文",
        "ExpressionOfConcernIn": "本文已有编辑关注声明", "ExpressionOfConcernFor": "本记录是编辑关注声明，所涉原文",
        "CorrectedandRepublishedIn": "本文已有更正重刊版", "CorrectedandRepublishedFrom": "本记录是更正重刊版，所涉原文",
        "RetractedandRepublishedIn": "本文已撤回并重刊", "RetractedandRepublishedFrom": "本记录是撤回后的重刊版，所涉原文",
    }
    notices = []
    for row in citation.findall("CommentsCorrectionsList/CommentsCorrections"):
        label = relations.get(row.get("RefType"))
        if not label:
            continue
        pmid = row.findtext("PMID", "").strip()
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if re.fullmatch(r"[0-9]+", pmid) else ""
        notices.append(" · ".join(filter(None, (label, row.findtext("RefSource"), url))))
    types = {"Retracted Publication": "已撤稿文献", "Retraction of Publication": "撤稿通知",
             "Published Erratum": "更正通知", "Expression of Concern": "编辑关注声明"}
    return list(dict.fromkeys([f"PubMed 文献类型 · {types[t]}" for t in publication_types if t in types] + notices))


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
        publication_types = [node.text for node in article.findall("PublicationTypeList/PublicationType") if node.text]
        items.append({"notices": publication_notices(citation, publication_types), "publication_types": publication_types, "authors": authors, "id": pmid, "title": "".join(title.itertext()) if title is not None else pmid,
                      "date": " ".join(date.split()), "detail": article.findtext("Journal/Title", ""),
                      "passages": passages, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"})
    return int(search["count"]), items


async def guidelines(client, query):
    return await pubmed(client, effective_search(SearchRequest(query=query, source="guidelines"), query))


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
    term = effective_search(request, query)
    retrieve = trials if request.source == "trials" else pubmed
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                total, items = await retrieve(client, term)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                await asyncio.sleep(0.4)
                total, items = await retrieve(client, term)
    except (httpx.HTTPError, ElementTree.ParseError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "暂时无法读取所选数据库，请稍后重试。") from exc
    return {"query": query, "effective_query": term, "source": request.source, "total": total, "items": items}


def registry_result_snapshot(record, retrieved):
    """Keep registry-specific groups, denominators, precision and assessment periods intact."""
    protocol = record["protocolSection"]
    ident = protocol["identificationModule"]
    status = protocol.get("statusModule", {})
    results = record.get("resultsSection") or {}
    return {
        "id": ident["nctId"], "title": ident["briefTitle"], "retrieved": retrieved,
        "updated": status.get("lastUpdatePostDateStruct", {}).get("date"),
        "first_posted": status.get("resultsFirstPostDateStruct", {}),
        "has_results": record.get("hasResults", bool(results)),
        "outcomes": results.get("outcomeMeasuresModule", {}).get("outcomeMeasures", []),
        "safety": results.get("adverseEventsModule") or {},
    }


@router.get("/api/research/trials/{nct_id}/results")
async def posted_results(nct_id: str):
    if not re.fullmatch(r"NCT[0-9]{8}", nct_id, re.IGNORECASE):
        raise HTTPException(422, "请输入有效的 NCT 登记编号。")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id.upper()}"
            try:
                response = await client.get(url)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                await asyncio.sleep(0.4)
                response = await client.get(url)
            if response.status_code == 404:
                raise HTTPException(404, "登记库中未找到此试验。")
            response.raise_for_status()
            return registry_result_snapshot(response.json(), datetime.now(timezone.utc).isoformat())
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "无法读取登记结果，请稍后重试。") from exc
