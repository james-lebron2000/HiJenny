"""Export a reviewed public-evidence snapshot as an editable research report."""
import hashlib
from io import BytesIO
from typing import Annotated, Literal
from uuid import UUID

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, model_validator

router = APIRouter()
Text = Annotated[str, Field(max_length=16000)]
Decision = Literal["纳入", "排除", "待复核"]


class Passage(BaseModel):
    label: Text
    text: str = Field(max_length=500000)


class Record(BaseModel):
    id: str = Field(pattern=r"^([0-9]{1,12}|NCT[0-9]{8})$")
    title: Text
    date: Text = ""
    decision: Decision
    reason: Text = ""
    notices: list[Text] = Field(default_factory=list, max_length=200)
    postedResult: bool = False
    passages: list[Passage] = Field(max_length=100)


class Step(BaseModel):
    source: Literal["pubmed", "trials"]
    query: Text
    retrieved: Text = ""
    total: int | None = Field(default=None, ge=0)
    error: Text = ""
    records: list[Record] = Field(max_length=100)


class Conflict(BaseModel):
    left: str = Field(pattern=r"^([0-9]{1,12}|NCT[0-9]{8})$")
    right: str = Field(pattern=r"^([0-9]{1,12}|NCT[0-9]{8})$")
    status: Literal["待核对", "口径差异可解释", "仍有分歧"]
    note: str = Field(min_length=1, max_length=2000)
    reviewer: str = Field(min_length=1, max_length=120)


class Brief(BaseModel):
    id: UUID
    question: Text
    created: Text
    note: Text
    steps: list[Step] = Field(min_length=1, max_length=2)

    conflicts: list[Conflict] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def check_conflict_sources(self):
        ids = {r.id for step in self.steps for r in step.records}
        for c in self.conflicts:
            if c.left == c.right or not {c.left, c.right} <= ids:
                raise ValueError("每项分歧须关联本版底稿中两条不同记录")
        return self


def clean(value):
    return "".join(c for c in value if c in "\t\n\r" or
                   32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or ord(c) >= 0x10000)


def source_link(paragraph, record):
    if record.id.startswith("NCT"):
        url = f"https://clinicaltrials.gov/study/{record.id}" + ("?tab=results" if record.postedResult else "")
    else:
        url = f"https://pubmed.ncbi.nlm.nih.gov/{record.id}/"
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True))
    run = paragraph.add_run(url)
    run.underline = True
    link.append(run._r)
    paragraph._p.append(link)


@router.post("/api/research/brief.docx")
def export_brief(brief: Brief):
    doc = Document()
    snapshot = hashlib.sha256(brief.model_dump_json().encode()).hexdigest()[:16]
    doc.add_heading("研究证据报告 · 供复核", 0)
    doc.add_paragraph(clean(brief.question))
    doc.add_paragraph(f"底稿 {brief.id}  ·  内容指纹 {snapshot}\n创建时间 {clean(brief.created)}")
    doc.add_heading("审核意见、冲突与未解决问题", 1)
    doc.add_paragraph(clean(brief.note) or "尚未填写审核意见")
    doc.add_heading("检索覆盖", 1)
    records = []
    for step in brief.steps:
        name = "PubMed" if step.source == "pubmed" else "ClinicalTrials.gov"
        status = (f"数据库命中 {step.total}；取回 {len(step.records)}" if step.total is not None
                  else clean(step.error) or "未完成检索")
        doc.add_paragraph(f"{name} · {clean(step.query)}\n{status} · 检索时间 {clean(step.retrieved) or '未提供'}")
        records.extend(step.records)
    counts = [f"{status} {sum(r.decision == status for r in records)}" for status in ("纳入", "排除", "待复核")]
    doc.add_paragraph("取回记录筛选结果：" + "，".join(counts))
    if brief.conflicts:
        indexed = {r.id: r for r in records}
        doc.add_heading("来源分歧复核记录", 1)
        for c in brief.conflicts:
            doc.add_paragraph(f"{c.status} · {clean(c.reviewer)}\n{clean(c.note)}")
            for key in (c.left, c.right):
                r = indexed[key]
                p = doc.add_paragraph(f"[{key}] {clean(r.title)} · {r.decision}\n")
                source_link(p, r)
    doc.add_heading("纳入证据及来源片段", 1)
    if not any(r.decision == "纳入" for r in records):
        doc.add_paragraph("当前底稿尚无纳入记录。")
    for r in records:
        if r.decision != "纳入":
            continue
        doc.add_heading(clean(r.title), 2)
        doc.add_paragraph(f"{r.id} · 来源日期 {clean(r.date) or '未提供'}")
        source_link(doc.add_paragraph(), r)
        for notice in r.notices:
            doc.add_paragraph("出版状态 · " + clean(notice))
        doc.add_paragraph(f"纳入理由：{clean(r.reason) or '未填写'}")
        if not r.passages:
            doc.add_paragraph("该记录缺少来源片段。")
        for p in r.passages:
            doc.add_paragraph().add_run(clean(p.label) or "来源片段").bold = True
            doc.add_paragraph(clean(p.text))
    doc.add_heading("逐条筛选记录与原始出处", 1)
    for r in records:
        entry = doc.add_paragraph(f"[{r.id}] {clean(r.title)}\n{r.decision} · {clean(r.reason) or '未填写理由'}\n")
        source_link(entry, r)
        for notice in r.notices:
            doc.add_paragraph("出版状态 · " + clean(notice))
    stream = BytesIO()
    doc.save(stream)
    return Response(stream.getvalue(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Cache-Control": "no-store", "Content-Disposition":
                             f'attachment; filename="HiJenny-brief-{brief.id}-{snapshot}.docx"'})
