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
from .clinical import word_download

router = APIRouter()


SampleVersion = Literal["asd101-v1"]


class PilotSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    sample_version: SampleVersion


class PilotEvent(PilotSession):
    event: Literal["pilot_open", "sample_open", "source_open"]


@router.post("/api/pilot/events", status_code=204)
def track_engagement(payload: PilotEvent):
    with store.session_scope() as session:
        store.record_pilot_engagement(session, str(payload.session_id), payload.sample_version, payload.event)


class PilotRequest(BaseModel):
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


@router.get("/api/pilot/decision-brief.docx")
def sample_decision_brief():
    doc = Document()
    doc.add_heading("ASD-101 · 首轮引进评审", 0)
    doc.add_paragraph("内部决策简报 · 合成样例 · 材料版本 v1")
    doc.add_heading("本次会议需决定什么", 1)
    doc.add_paragraph("卖方补证前，是否保留两种疗效假设，并由医学与统计负责人核对哪组应答人数可用于交易评估。")
    doc.add_heading("两份材料如何描述客观缓解率", 1)
    table = doc.add_table(rows=0, cols=3)
    table.style = "Table Grid"
    for row in [("合成材料出处", "人数与原报值", "按一位小数复算"),
                ("研究报告 v1.0，第 10 页，表 14.2.1", "53/126 · 42.0%", "42.1%"),
                ("商业预测 v1，第 3 页", "61/126 · 48.4%", "48.4%")]:
        for cell, value in zip(table.add_row().cells, row):
            cell.text = value
    doc.add_heading("估值假设与待补证事项", 1)
    doc.add_paragraph("商业预测多列 8 名应答者。审阅人应追索两份签核表，核对人群、评估者、截止日和舍入规则；现有材料无法确定差异原因。")
    doc.add_heading("负责人及下一步", 1)
    doc.add_paragraph("BD 将问题单交给卖方；统计负责人复算，医学负责人核对终点评估。收到答复后重审估值输入，并保留本次会议的原始依据。")
    return word_download(doc, "HiJenny-Synthetic-Decision-Brief.docx")
