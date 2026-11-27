"""Produce an editable protocol synopsis from a clinician's saved design decisions."""
import csv
import hashlib
from datetime import date, datetime
from io import BytesIO, StringIO
from typing import Annotated, Literal
from uuid import UUID

from docx import Document
from fastapi import APIRouter, HTTPException, Request, Response
from .brief import clean
from pydantic import BaseModel, Field, ValidationError, model_validator

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


class ReviewedComment(DesignReview):
    id: UUID


class ReviewResponse(BaseModel):
    review: ReviewedComment
    reply: str = Field(min_length=1, max_length=6000)


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


class StudyReport(BaseModel):
    cutoff: date | None = None
    source: Text = ""
    table: str = Field(default="", max_length=120000)
    conduct: Text = ""
    interpretation: Text = ""


IBSection = Literal["product", "nonclinical", "kinetics", "clinical", "safety", "guidance"]
IB_TITLES = {
    "product": "研究用产品概况与制剂信息", "nonclinical": "非临床研究摘要",
    "kinetics": "人体药代与药效资料", "clinical": "既往人体研究与临床经验",
    "safety": "安全性资料与已识别风险", "guidance": "供研究者参考的资料与待解问题",
}


class SourcedSection(BaseModel):
    summary: Text = ""
    source: Text = ""


class InvestigatorBrochure(BaseModel):
    product: Text = ""
    cutoff: date | None = None
    sections: dict[IBSection, SourcedSection] = Field(default_factory=dict)


class PreviousDesign(BaseModel):
    id: UUID
    options: list[DesignOption] = Field(min_length=2, max_length=2)


class Synopsis(BaseModel):
    parent: UUID | None = None
    previous_design: PreviousDesign | None = None
    id: UUID
    question: str = Field(min_length=1, max_length=400)
    options: list[DesignOption] = Field(min_length=2, max_length=2)
    choice: Literal["", "A", "B"] = ""
    rationale: Text = ""
    pending: Text = ""
    references: list[TrialReference] = Field(max_length=4)
    ib: InvestigatorBrochure = Field(default_factory=InvestigatorBrochure)
    csr: dict[Literal["A", "B"], StudyReport] = Field(default_factory=dict)
    sap: StatisticalPlan = Field(default_factory=StatisticalPlan)
    protocol: dict[Literal["A", "B"], ProtocolSections] = Field(default_factory=dict)
    reviews: list[DesignReview] = Field(default_factory=list)
    responses: list[ReviewResponse] = Field(default_factory=list)
    changes: list[Text] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def reviews_belong_to_this_version(self):
        if self.previous_design and (self.previous_design.id != self.parent or self.parent == self.id):
            raise ValueError("对照设计与上一版编号不符")
        if any(item.review.version != self.parent or self.parent == self.id for item in self.responses):
            raise ValueError("修订回应必须对应直接上一版的复核意见")
        if len({item.review.id for item in self.responses}) != len(self.responses):
            raise ValueError("同一复核意见存在重复回应")
        if any(review.version != self.id for review in self.reviews):
            raise ValueError("复核记录与方案版本不符")
        return self


class HandoffExcerpt(BaseModel):
    label: str
    text: str
    event: str | None = None


class HandoffReference(TrialReference):
    archive_cutoff: date | None = None
    excerpts: list[HandoffExcerpt] = Field(default_factory=list)


class HandoffVersion(Synopsis):
    created: datetime
    references: list[HandoffReference] = Field(max_length=4)
    changes: list[Text] = Field(max_length=64)


class ClinicalHandoff(BaseModel):
    format: Literal["hijenny-clinical-v1"]
    versions: list[HandoffVersion] = Field(min_length=1)
    reviews: list[ReviewedComment]

    @model_validator(mode="after")
    def verify_lineage(self):
        expected_parent = None
        seen = set()
        reviews = {r.id: r for r in self.reviews}
        if len(reviews) != len(self.reviews):
            raise ValueError("重复的复核意见编号")
        for version in self.versions:
            if version.id in seen or version.parent != expected_parent:
                raise ValueError("设计版本链不完整")
            if any(reviews.get(item.review.id) != item.review for item in version.responses):
                raise ValueError("修订回应缺少匹配的原始复核意见")
            seen.add(version.id)
            expected_parent = version.id
        if any(r.version not in seen for r in self.reviews):
            raise ValueError("复核意见所属版本不在交接包内")
        return self


@router.post("/api/clinical/handoff/validate", status_code=204)
async def validate_handoff(request: Request):
    try:
        ClinicalHandoff.model_validate_json(await request.body(), strict=True)
    except ValidationError as exc:
        raise HTTPException(422, "交接包的版本、字段或复核意见不匹配") from exc
    return Response(status_code=204)


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

    if plan.parent:
        document.add_heading("候选设计修订前后原文", 1)
        paragraph(f"上一版 {plan.parent} · 本版 {plan.id}")
        if plan.previous_design:
            differences = 0
            for name, previous, current in zip(("A", "B"), plan.previous_design.options, plan.options):
                if isinstance(plan, SelectedDesign) and plan.choice != name:
                    continue
                for field, label in FIELDS.items():
                    before, after = getattr(previous, field), getattr(current, field)
                    if before != after:
                        differences += 1
                        document.add_heading(f"设计 {name} · {label}", 2)
                        paragraph("修订前")
                        paragraph(before or "（空白）")
                        paragraph("修订后")
                        paragraph(after or "（空白）")
            if not differences:
                paragraph("本文件所列候选设计字段均与上一版相同。")
        else:
            paragraph("本次导出缺少上一版，无法核对原文。")
    if plan.responses:
        document.add_heading("本版对上版复核意见的回应", 1)
        for item in plan.responses:
            review = item.review
            paragraph(f"{review.role} · {review.reviewer} · {review.decision} · {review.created.isoformat()}")
            paragraph(f"来源版本 {review.version} · 意见编号 {review.id}")
            paragraph(review.note)
            paragraph("修订回应")
            paragraph(item.reply)
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


CSR_COLUMNS = ("类别", "分析集", "组别", "指标", "评价时间", "报告值", "单位", "分母", "出处")
CSR_CATEGORIES = ("受试者处置", "疗效", "安全性")


def csr_rows(raw):
    if not raw.strip():
        return []
    try:
        reader = csv.DictReader(StringIO(raw.lstrip("\ufeff"), newline=""), strict=True)
        if tuple(reader.fieldnames or ()) != CSR_COLUMNS:
            raise ValueError("结果表的表头或列顺序与模板不符")
        rows = []
        for row in reader:
            if len(rows) >= 1000:
                raise ValueError("单张结果表最多 1000 行")
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"第 {reader.line_num} 行列数有误")
            if row["类别"] not in CSR_CATEGORIES or not row["指标"].strip() or not row["出处"].strip():
                raise ValueError(f"第 {reader.line_num} 行请核对类别、指标及出处")
            rows.append(row)
        return rows
    except (csv.Error, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/clinical/csr.docx")
def export_csr(plan: SelectedDesign):
    report = plan.csr.get(plan.choice, StudyReport())
    rows = csr_rows(report.table)
    if rows and not (report.cutoff and report.source.strip()):
        raise HTTPException(422, "有结果行时请填写数据截止日期与源表名称、版本")
    document = Document()
    document.add_heading("临床研究报告（CSR）· 评审草稿", 0)
    document.add_paragraph(clean(plan.question))
    document.add_paragraph(f"设计版本 {plan.id} · 候选设计 {plan.choice}")
    append_selected_design(document, plan)
    document.add_heading("实际实施与偏离方案", 1)
    document.add_paragraph(clean(report.conduct) or "待补充实际实施情况")
    document.add_heading("统计结果来源", 1)
    document.add_paragraph(f"数据截止日期：{report.cutoff or '未提供'}")
    document.add_paragraph(f"源表名称与版本：{clean(report.source) or '未提供'}")
    if report.table:
        fingerprint = hashlib.sha256(report.table.encode("utf-8")).hexdigest()
        document.add_paragraph(f"录入表文本 SHA-256：{fingerprint}")
    for category in CSR_CATEGORIES:
        document.add_heading(category, 1)
        evidence = [r for r in rows if r["类别"] == category]
        if not evidence:
            document.add_paragraph("未提供该类统计结果")
            continue
        table = document.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        for cell, label in zip(table.rows[0].cells, ["分析集与组别", "指标与评价时间", "报告值、单位及分母", "源表定位"]):
            cell.text = label
        for row in evidence:
            cells = table.add_row().cells
            for cell, keys in zip(cells, [("分析集", "组别"), ("指标", "评价时间"), ("报告值", "单位", "分母"), ("出处",)]):
                cell.text = "\n".join(f"{k}：{clean(row[k]) or '未提供'}" for k in keys)
    document.add_heading("结果解释与限制 · 人工填写", 1)
    document.add_paragraph(clean(report.interpretation) or "待专业人员解释")
    append_changes(document, plan)
    append_review_and_sources(document, plan)
    return word_download(document, f"HiJenny-CSR-{plan.id}.docx")

@router.post("/api/clinical/ib.docx")
def export_ib(plan: Synopsis):
    ib = plan.ib
    if not ib.product.strip() or not ib.cutoff:
        raise HTTPException(422, "请填写研究用产品名称及资料截止日期")
    for key, entry in ib.sections.items():
        if entry.summary.strip() and not entry.source.strip():
            raise HTTPException(422, f"请补充「{IB_TITLES[key]}」的来源、版本与定位")
    document = Document()
    document.add_heading("研究者手册（IB）· 评审草稿", 0)
    document.add_paragraph(clean(ib.product))
    document.add_paragraph(f"资料截止日期 {ib.cutoff} · 工作区设计版本 {plan.id}")
    document.add_paragraph(f"关联研究问题：{clean(plan.question)}")
    for key, title in IB_TITLES.items():
        entry = ib.sections.get(key, SourcedSection())
        document.add_heading(title, 1)
        document.add_paragraph(clean(entry.summary.strip()) or "尚缺本章摘要，待补证")
        document.add_paragraph("资料名称、版本及定位：" + (clean(entry.source.strip()) or "待提供"))
    append_changes(document, plan)
    append_review_and_sources(document, plan)
    return word_download(document, f"HiJenny-IB-{plan.id}.docx")
