"""Editable study proposals assembled from the selected browser snapshot."""
from datetime import datetime
from uuid import UUID
from typing import Literal

from docx import Document
from fastapi import APIRouter
from pydantic import BaseModel, Field

from .brief import clean
from .clinical import word_download
from .medical import Reference, Text

router = APIRouter()
COMMON = {"question": "研究问题", "population": "目标人群和入选条件", "exposure": "暴露或干预及对照",
          "outcome": "结局定义与测量", "followup": "随访起点、窗口与终止规则", "data": "可用数据与获取条件",
          "confounding": "混杂因素与处理", "missing": "缺失数据处理", "analysis": "分析方法与敏感性分析"}
SPECIFIC = {"RWE": {"index": "索引日期与暴露分组时点", "linkage": "数据关联与结局捕获", "selection": "选择偏倚与时间相关偏倚"},
            "IIT": {"recruitment": "研究中心与招募可行性", "allocation": "分组与分配依据", "monitoring": "研究监测与实施责任"}}
CHECKS = ["人群可识别", "暴露或干预可测量", "结局可捕获", "随访窗口可覆盖", "关键混杂变量可获得"]
BIAS = {"risk": "主要偏倚及发生环节", "impact": "可能的影响方向与依据", "mitigation": "拟采取的控制措施", "residual": "剩余不确定性与验证方式"}


class Check(BaseModel):
    status: Literal["待核实", "可用", "部分可用", "不可用"]
    note: Text = ""


class Study(BaseModel):
    id: UUID
    parent: UUID | None = None
    created: datetime
    kind: Literal["RWE", "IIT"]
    values: dict[str, Text] = Field(max_length=20)
    checks: list[Check] = Field(min_length=5, max_length=5)
    bias: dict[str, Text] = Field(default_factory=dict, max_length=4)
    epi: Text = ""
    stats: Text = ""
    meetingId: UUID | None = None
    refs: list[Reference] = Field(default_factory=list, max_length=100)
    gaps: list[Text] = Field(default_factory=list, max_length=40)
    changes: list[Text] = Field(default_factory=list, max_length=40)


@router.post("/api/medical/study.docx")
def export_study(plan: Study):
    doc = Document()
    title = "真实世界研究方案" if plan.kind == "RWE" else "研究者发起研究概念书"
    doc.add_heading(f"{title} · 评审草稿", 0)
    doc.add_paragraph(f"版本 {plan.id} · {plan.created.isoformat()}\n前一版本：{plan.parent or '首版'}")
    for key, label in (COMMON | SPECIFIC[plan.kind]).items():
        doc.add_heading(label, 2)
        doc.add_paragraph(clean(plan.values.get(key, "")) or "待填写")
    doc.add_heading("偏倚评估", 1)
    for key, label in BIAS.items():
        doc.add_paragraph(label + "：" + (clean(plan.bias.get(key, "")) or "待评估"))
    doc.add_heading("数据可行性", 1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for cell, label in zip(table.rows[0].cells, ["核查项", "状态", "依据或补救措施"]):
        cell.text = label
    for label, check in zip(CHECKS, plan.checks):
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = label, check.status, clean(check.note) or "依据待补充"
    for label, value in [("流行病学复核记录", plan.epi), ("统计复核记录", plan.stats)]:
        doc.add_heading(label, 2)
        doc.add_paragraph(clean(value) or "未记录")
    for label, items, empty in [("仍需解决", plan.gaps, "本版未记录其他缺项"), ("本版修改", plan.changes, "首版或无记录")]:
        doc.add_heading(label, 1)
        for item in items or [empty]:
            doc.add_paragraph(clean(item))
    doc.add_heading("会议来源与引文", 1)
    doc.add_paragraph(f"会议版本：{plan.meetingId or '独立起草'}")
    for ref in plan.refs:
        doc.add_heading(clean(ref.title), 2)
        url = f"https://clinicaltrials.gov/study/{ref.id}" if ref.id.startswith("NCT") else f"https://pubmed.ncbi.nlm.nih.gov/{ref.id}/"
        doc.add_paragraph(f"{url}\n来源日期：{clean(ref.date or '') or '未提供'} · 检索时间：{clean(ref.retrieved or '') or '未提供'}")
        doc.add_paragraph(clean(ref.label))
        doc.add_paragraph(clean(ref.quote))
    return word_download(doc, f"HiJenny-{plan.kind}-{plan.id}.docx")
