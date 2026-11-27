"""Single-asset paid pilot intake. Receipts are returned only to the submitter."""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from hijenny import config, exports, store
from fastapi import Response
from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor
from .clinical import word_download

router = APIRouter()


SampleVersion = Literal["asd101-v1"]


class PilotSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    sample_version: SampleVersion


class PilotEvent(PilotSession):
    event: Literal["pilot_open", "sample_open", "source_open"]


class HomeEvidenceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    sample_version: Literal["asset-home-v1"]
    event: Literal["home_open", "search_submitted", "evidence_shown", "brief_requested", "quote_link_clicked"]


@router.post("/api/pilot/events", status_code=204)
def track_engagement(payload: PilotEvent | HomeEvidenceEvent):
    with store.session_scope() as session:
        store.record_pilot_engagement(session, str(payload.session_id), payload.sample_version, payload.event)


class AssetContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    asset: str = Field(min_length=1, max_length=400)
    as_of: date


class PilotRequest(BaseModel):
    asset_context: AssetContext | None = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    analytics: PilotSession | None = None
    request_key: UUID
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")
    stage: Literal["临床前", "I 期", "II 期", "III 期", "上市后", "待确认"]
    pages: Literal["50 页以内", "51–200 页", "201–500 页", "500 页以上", "待确认"]
    meeting: date
    deployment: Literal["HiJenny 托管试点", "客户专属环境", "待讨论"]


@router.post("/api/pilot/inquiries")
def submit(request: PilotRequest):
    fields = request.model_dump(mode="json", exclude={"request_key", "analytics"})
    try:
        with store.session_scope() as session:
            result = store.submit_pilot_inquiry(session, str(request.request_key), fields)
            if request.analytics:
                store.record_pilot_engagement(session, str(request.analytics.session_id),
                                              request.analytics.sample_version, "inquiry_accepted")
                if request.asset_context:
                    store.record_pilot_engagement(session, str(request.analytics.session_id),
                                                  "asset-home-v1", "scoped_inquiry_accepted")
            return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

@router.get("/api/pilot/sample-report")
def sample_report(session_id: UUID | None = None, sample_version: SampleVersion = "asd101-v1"):
    if session_id:
        with store.session_scope() as session:
            store.record_pilot_engagement(session, str(session_id), sample_version, "report_requested")
    return FileResponse(config.REPO_ROOT / "docs/sample_output/HiJenny_Report.md",
                        media_type="text/markdown", filename="HiJenny-Synthetic-Diligence-Report.md")


@router.get("/api/pilot/sources/{source}")
def pilot_source(source: Literal["csr", "forecast"]):
    filenames = {"csr": "Clinical_Study_Report_v1.0.pdf", "forecast": "Commercial_Forecast_v1.pptx"}
    name = filenames[source]
    # Only the committed synthetic package belongs in this unauthenticated demonstration.
    return FileResponse(config.REPO_ROOT / "data/sample_package" / name, filename=name)

@router.get("/api/pilot/seller-reply.xlsx")
def sample_seller_reply():
    questions = [
        {"question_id": "ASD-101-Q1", "question": "请核对 CSR 第 10 页的 53/126 与商业预测第 3 页的 61/126，并说明两组应答人数差异的来源。",
         "required_documents": ["两项数字的签核汇总表、分析人群、截止日和评估方式"]},
        {"question_id": "ASD-101-Q2", "question": "请说明 CSR 中 42.0% 的计算依据，包括原始分子、分母及舍入规则。",
         "required_documents": ["表 14.2.1 的统计输出及计算说明"]},
    ]
    return Response(exports.seller_reply_workbook(questions, sample=True),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="HiJenny-Synthetic-Seller-Reply.xlsx"'})


BRIEF_COPY = {
    "zh": {
        "title": "ASD-101 首轮引进评审", "subtitle": "内部决策简报 · 合成样例 · 材料版本 v1",
        "decision": ("本次会议需决定什么", "卖方补证前，是否保留两种疗效假设，并由医学与统计负责人核对哪组应答人数可用于交易评估。"),
        "evidence": "两份材料如何描述客观缓解率", "headers": ("合成材料出处", "人数与原报值", "按一位小数复算"),
        "sources": ("研究报告 v1.0，第 10 页，表 14.2.1", "商业预测 v1，第 3 页"),
        "gap": ("估值假设与待补证事项", "商业预测多列 8 名应答者。请索取两份签核表，核对分析人群、评估者、截止日和舍入规则。现有材料无法确定差异原因。"),
        "next": ("负责人及下一步", "BD 将问题单交给卖方，统计负责人复算，医学负责人核对终点评估。收到答复后重审估值输入，并保留本次会议的原始依据。"),
    },
    "en": {
        "title": "ASD-101 licensing review", "subtitle": "Internal decision brief · Synthetic example · Source package v1",
        "decision": ("Decision for the review meeting", "Decide whether to retain both response-rate assumptions pending the seller’s reply. Medical and statistics leads should verify which responder count can support the valuation."),
        "evidence": "Objective response rate in the source documents", "headers": ("Source document", "Responders and reported rate", "Recalculated to one decimal"),
        "sources": ("Clinical study report v1.0, page 10, Table 14.2.1", "Commercial forecast v1, slide 3"),
        "gap": ("Evidence needed for valuation", "The forecast counts eight additional responders. Request both signed tables and reconcile the analysis populations, assessors, cutoff dates and rounding rules. The available documents do not establish the cause."),
        "next": ("Owners and follow-up", "BD sends the questions to the seller. Statistics recalculates the rates and the medical lead checks the endpoint assessments. Revisit the valuation inputs after the reply and retain the sources used for this review."),
    },
    "ja": {
        "title": "ASD-101 導入審査", "subtitle": "社内審査資料 · 架空の事例 · 資料版 v1",
        "decision": ("審査会での判断事項", "導出側から回答を得るまで、奏効率について二通りの前提を残すかを判断する。メディカルと統計の担当者は、どちらの奏効例数を資産評価に使用できるかを確認する。"),
        "evidence": "原資料に記載された客観的奏効率", "headers": ("出典", "奏効例数と記載値", "小数第1位までの再計算値"),
        "sources": ("治験総括報告書 v1.0、10ページ、表14.2.1", "売上予測資料 v1、スライド3"),
        "gap": ("資産評価のために確認する資料", "売上予測資料では奏効例数が8例多い。署名済みの集計表を双方について入手し、解析対象集団、評価者、データカットオフ日、丸め方を照合する。現時点の資料だけでは差の理由を特定できない。"),
        "next": ("担当者と次の対応", "事業開発担当者が導出側へ質問票を送付する。統計担当者が奏効率を再計算し、メディカル担当者が評価方法を確認する。回答後に資産評価の前提を見直し、今回の判断に用いた資料を保存する。"),
    },
}


@router.get("/api/pilot/decision-brief.docx")
def sample_decision_brief(lang: Literal["zh", "en", "ja"] = "zh"):
    copy = BRIEF_COPY[lang]
    doc = Document()
    doc.add_heading(copy["title"], 0)
    doc.add_paragraph(copy["subtitle"])
    doc.add_heading(copy["decision"][0], 1)
    doc.add_paragraph(copy["decision"][1])
    doc.add_heading(copy["evidence"], 1)
    table = doc.add_table(rows=0, cols=3)
    table.style = "Table Grid"
    rows = [copy["headers"], (copy["sources"][0], "53/126 · 42.0%", "42.1%"),
            (copy["sources"][1], "61/126 · 48.4%", "48.4%")]
    for row in rows:
        for cell, value in zip(table.add_row().cells, row):
            cell.text = value
    for key in ("gap", "next"):
        doc.add_heading(copy[key][0], 1)
        doc.add_paragraph(copy[key][1])
    font = {"zh": "PingFang SC", "en": "Arial", "ja": "Hiragino Sans"}[lang]
    title_style = doc.styles["Title"].element.find(qn("w:pPr"))
    border = title_style.find(qn("w:pBdr"))
    if border is not None:
        title_style.remove(border)
    paragraphs = list(doc.paragraphs) + [p for row in table.rows for cell in row.cells for p in cell.paragraphs]
    for paragraph in paragraphs:
        for run in paragraph.runs:
            run.font.name = font
            run.font.color.rgb = RGBColor(0, 0, 0)
            run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), font)
    for cell in table.rows[0].cells:
        cell.paragraphs[0].runs[0].bold = True
    suffix = "" if lang == "zh" else "-" + lang
    return word_download(doc, f"HiJenny-Synthetic-Decision-Brief{suffix}.docx")
