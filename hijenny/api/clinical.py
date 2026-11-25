"""Produce an editable protocol synopsis from a clinician's saved design decisions."""
from datetime import datetime
from io import BytesIO
from typing import Annotated, Literal
from uuid import UUID

from docx import Document
from fastapi import APIRouter, Response
from .brief import clean
from pydantic import BaseModel, Field, model_validator

router = APIRouter()
Text = Annotated[str, Field(max_length=6000)]
FIELDS = {
    "population": "目标人群", "include": "入选标准", "exclude": "排除标准",
    "control": "对照与分配", "endpoint": "主要终点与评价时间", "feasibility": "可行性与限制",
}


class DesignOption(BaseModel):
    population: Text = ""
    include: Text = ""
    exclude: Text = ""
    control: Text = ""
    endpoint: Text = ""
    feasibility: Text = ""


class TrialReference(BaseModel):
    id: str = Field(pattern=r"^NCT[0-9]{8}$")
    title: Text
    date: Text = ""
    snapshot: str = Field(default="", max_length=500_000)


class DesignReview(BaseModel):
    version: UUID
    role: Literal["临床", "统计"]
    reviewer: str = Field(min_length=1, max_length=120)
    decision: Literal["需修改", "同意当前设计", "需补充材料"]
    note: str = Field(min_length=1, max_length=6000)
    created: datetime


class StatisticalPlan(BaseModel):
    estimand: Text = ""
    sets: Text = ""
    method: Text = ""
    sampleSize: Text = ""
    multiplicity: Text = ""
    missing: Text = ""
    sensitivity: Text = ""
    interim: Text = ""
    outputs: Text = ""


class ProtocolSections(BaseModel):
    objectives: Text = ""
    organisation: Text = ""
    intervention: Text = ""
    visits: Text = ""
    discontinuation: Text = ""
    safety: Text = ""
    data: Text = ""
    ethics: Text = ""


class Synopsis(BaseModel):
    id: UUID
    question: str = Field(min_length=1, max_length=400)
    options: list[DesignOption] = Field(min_length=2, max_length=2)
    choice: Literal["", "A", "B"] = ""
    rationale: Text = ""
    pending: Text = ""
    references: list[TrialReference] = Field(max_length=4)
    sap: StatisticalPlan = Field(default_factory=StatisticalPlan)
    protocol: dict[Literal["A", "B"], ProtocolSections] = Field(default_factory=dict)
    reviews: list[DesignReview] = Field(default_factory=list)
    changes: list[Text] = Field(default_factory=list, max_length=48)

    @model_validator(mode="after")
    def reviews_belong_to_this_version(self):
        if any(review.version != self.id for review in self.reviews):
            raise ValueError("复核记录与方案版本不符")
        return self


@router.post("/api/clinical/synopsis.docx")
def export_synopsis(plan: Synopsis):
    document = Document()

    def paragraph(value):
        document.add_paragraph(clean(value) or "待填写")

    document.add_heading("临床研究方案摘要 · 评审草稿", 0)
    paragraph(plan.question)
    paragraph(f"版本 {plan.id}")
    for name, option in zip(("A", "B"), plan.options):
        document.add_heading(f"候选设计 {name}", 1)
        for key, label in FIELDS.items():
            document.add_heading(label, 2)
            paragraph(getattr(option, key))
    document.add_heading("团队设计选择与依据", 1)
    paragraph(f"倾向方案 {plan.choice}" if plan.choice else "尚未选择方案")
    paragraph(plan.rationale)
    document.add_heading("待临床与统计复核", 1)
    paragraph(plan.pending)
    for item in plan.changes:
        paragraph(item)
    append_review_and_sources(document, plan)
    return word_download(document, "HiJenny-Protocol-Synopsis.docx")


def append_review_and_sources(document, plan):
    def paragraph(value):
        document.add_paragraph(clean(value) or "待填写")

    document.add_heading("本版专业复核记录", 1)
    for role in ("临床", "统计"):
        reviews = [r for r in plan.reviews if r.role == role]
        document.add_heading(f"{role}复核", 2)
        if not reviews:
            paragraph("待复核")
        for review in reviews:
            paragraph(f"{review.reviewer} · {review.created.isoformat()} · {review.decision}")
            paragraph(review.note)
    document.add_heading("公开登记来源快照", 1)
    if not plan.references:
        paragraph("未关联登记来源")
    for ref in plan.references:
        paragraph(ref.title)
        paragraph(f"{ref.id} · 登记更新 {ref.date or '未提供'}")
        paragraph(f"https://clinicaltrials.gov/study/{ref.id}")
        paragraph(ref.snapshot)


def word_download(document, filename):
    stream = BytesIO()
    document.save(stream)
    return Response(stream.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})

class SelectedDesign(Synopsis):
    choice: Literal["A", "B"]


@router.post("/api/clinical/sap.docx")
def export_sap(plan: SelectedDesign):
    document = Document()
    document.add_heading("统计分析计划（SAP）· 评审草稿", 0)
    document.add_paragraph(clean(plan.question))
    document.add_paragraph(f"来源设计版本 {plan.id} · 候选设计 {plan.choice}")
    append_selected_design(document, plan)
    append_statistics(document, plan)
    append_changes(document, plan)
    append_review_and_sources(document, plan)
    return word_download(document, f"HiJenny-SAP-{plan.id}.docx")


def append_selected_design(document, plan):
    selected = plan.options[0 if plan.choice == "A" else 1]
    document.add_heading("来源设计与分析约束", 1)
    for key, label in FIELDS.items():
        document.add_heading(label, 2)
        document.add_paragraph(clean(getattr(selected, key)) or "来源设计未填写")


def append_statistics(document, plan):
    document.add_heading("统计计划 · 本版人工填写", 1)
    sections = {
        "estimand": "估计目标与伴发事件处理", "sets": "分析集与主要分析人群", "method": "主要分析模型、比较与效应量",
        "sampleSize": "样本量依据与参数来源", "multiplicity": "多重性与检验顺序", "missing": "缺失数据处理",
        "sensitivity": "敏感性与亚组分析", "interim": "期中分析与停止规则", "outputs": "预定表图与数据锁定要求",
    }
    for key, label in sections.items():
        document.add_heading(label, 2)
        document.add_paragraph(clean(getattr(plan.sap, key)) or "待统计人员补充")


def append_changes(document, plan):
    document.add_heading("本版变更与待复核事项", 1)
    document.add_paragraph(clean(plan.pending) or "未记录待复核事项")
    for change in plan.changes:
        document.add_paragraph(clean(change))


@router.post("/api/clinical/protocol.docx")
def export_protocol(plan: SelectedDesign):
    document = Document()
    document.add_heading("临床研究方案（Protocol）· 评审草稿", 0)
    document.add_paragraph(clean(plan.question))
    document.add_paragraph(f"来源设计版本 {plan.id} · 候选设计 {plan.choice}")
    append_selected_design(document, plan)
    document.add_heading("方案选择依据", 1)
    document.add_paragraph(clean(plan.rationale) or "待填写")
    document.add_heading("研究实施 · 所选设计的人工填写内容", 1)
    protocol = plan.protocol.get(plan.choice, ProtocolSections())
    sections = {
        "objectives": "研究背景、依据与目标", "organisation": "申办方、研究中心与职责",
        "intervention": "干预实施与合并治疗", "visits": "访视安排、评价窗口与随访",
        "discontinuation": "退出、停药与随访处理", "safety": "安全监测与报告流程",
        "data": "数据采集、质量控制与管理", "ethics": "知情同意、伦理与隐私安排",
    }
    for key, label in sections.items():
        document.add_heading(label, 2)
        document.add_paragraph(clean(getattr(protocol, key)) or "待研究团队补充")
    append_statistics(document, plan)
    append_changes(document, plan)
    append_review_and_sources(document, plan)
    return word_download(document, f"HiJenny-Protocol-{plan.id}.docx")
