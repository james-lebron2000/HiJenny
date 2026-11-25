"""Produce an editable protocol synopsis from a clinician's saved design decisions."""
from datetime import datetime
from io import BytesIO
from typing import Annotated, Literal
from uuid import UUID

from docx import Document
from fastapi import APIRouter, Response
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


class Synopsis(BaseModel):
    id: UUID
    question: str = Field(min_length=1, max_length=400)
    options: list[DesignOption] = Field(min_length=2, max_length=2)
    choice: Literal["", "A", "B"] = ""
    rationale: Text = ""
    pending: Text = ""
    references: list[TrialReference] = Field(max_length=4)
    reviews: list[DesignReview] = Field(default_factory=list)
    changes: list[Text] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def reviews_belong_to_this_version(self):
        if any(review.version != self.id for review in self.reviews):
            raise ValueError("复核记录与方案版本不符")
        return self


@router.post("/api/clinical/synopsis.docx")
def export_synopsis(plan: Synopsis):
    document = Document()

    def paragraph(value):
        # Word XML accepts tabs/newlines but rejects control characters in pasted source text.
        document.add_paragraph("".join(c for c in (value or "待填写") if c in "\t\n\r" or
                                       32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or ord(c) >= 0x10000))

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
    stream = BytesIO()
    document.save(stream)
    return Response(stream.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="HiJenny-Protocol-Synopsis.docx"'})
