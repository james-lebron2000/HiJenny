"""Editable slides for a browser-confirmed medical meeting brief."""
from io import BytesIO
from typing import Annotated, Literal
from unicodedata import east_asian_width
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, model_validator

from hijenny.api.brief import Conflict
from pptx import Presentation
from pptx.util import Inches, Pt

router = APIRouter()
Text = Annotated[str, Field(max_length=8000)]
Required = Annotated[str, Field(min_length=1, max_length=8000)]


class Reference(BaseModel):
    id: str = Field(pattern=r"^(NCT[0-9]{8}|[0-9]+)$", max_length=24)
    title: Required
    date: Text | None = None
    retrieved: Text | None = None
    label: Text
    notices: list[Text] = Field(default_factory=list, max_length=200)
    quote: str = Field(min_length=1, max_length=64000)


class Meeting(BaseModel):
    id: UUID
    briefId: UUID
    confirmed: Literal[True]
    created: Required
    reviewer: Required
    expert: Text = ""
    question: Required
    answer: Required
    discussion: Text = ""
    action: Text = ""
    owner: Text = ""
    due: Text = ""
    refs: list[Reference] = Field(min_length=1, max_length=100)

    conflicts: list[Conflict] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_conflict_links(self):
        cited = {r.id for r in self.refs}
        for c in self.conflicts:
            if c.left == c.right or not cited.intersection((c.left, c.right)):
                raise ValueError("分歧须包含两条不同来源，其中至少一条为会议引文")
        return self


def reference_url(id):
    return ("https://clinicaltrials.gov/study/" + id if id.startswith("NCT")
            else f"https://pubmed.ncbi.nlm.nih.gov/{id}/")


def lines_for_slide(value):
    for paragraph in value.expandtabs(4).splitlines() or [""]:
        line, width = "", 0
        for char in paragraph:
            if ord(char) < 32:
                continue
            size = 2 if east_asian_width(char) in "WFA" else 1
            if width + size > 66:
                yield line
                line, width = "", 0
            line += char
            width += size
        yield line


@router.post("/api/medical/meeting.pptx")
def export_meeting(meeting: Meeting):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.33), Inches(7.5)

    def section(title, values, links=()):
        lines = [line for value in values for line in lines_for_slide(value)]
        for start in range(0, len(lines), 10):
            slide = deck.slides.add_slide(deck.slide_layouts[6])

            def box(top, height):
                return slide.shapes.add_textbox(Inches(.7), Inches(top), Inches(11.9), Inches(height)).text_frame

            heading = box(.3, .7).paragraphs[0]
            heading.text = title + ("（续）" if start else "")
            heading.font.size = Pt(29)
            frame = box(1.2, 5.4)
            for i, line in enumerate(lines[start:start + 10]):
                p = frame.add_paragraph() if i else frame.paragraphs[0]
                p.text = line
                p.font.size = Pt(20)
                p.line_spacing = Pt(35)
            footer = box(6.75, .5).paragraphs[0]
            footer.font.size = Pt(12)
            footer.text = f"HiJenny · 版本 {meeting.id} · {len(deck.slides)}"
            for label, link in links:
                run = footer.add_run()
                run.text = "    " + label
                run.hyperlink.address = link

    section("专家会 · 医学问题", [meeting.question, "讨论对象：" + (meeting.expert or "待补充"),
                           "底稿版本：" + str(meeting.briefId), "用户填写的复核人：" + meeting.reviewer,
                           "确认时间：" + meeting.created])
    section("已确认的答复", [meeting.answer])
    section("请专家讨论", [meeting.discussion or "待补充"])
    for index, ref in enumerate(meeting.refs, 1):
        section(f"支持引文 {index} · {ref.id}", [ref.title, "来源日期：" + (ref.date or "未提供"),
                "检索时间：" + (ref.retrieved or "未提供"), *ref.notices, ref.label, ref.quote], [("查看原始记录", reference_url(ref.id))])
    for index, c in enumerate(meeting.conflicts, 1):
        section(f"引文涉及的证据分歧 {index}", [c.status, "底稿复核人：" + c.reviewer, c.note,
                "比较来源：" + c.left + " / " + c.right],
                [(id, reference_url(id)) for id in (c.left, c.right)])
    section("会后行动", [meeting.action or "待补充", "负责人：" + (meeting.owner or "待指定"),
                     "预计完成日期：" + (meeting.due or "待确定")])
    stream = BytesIO()
    deck.save(stream)
    return Response(stream.getvalue(), media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    headers={"Content-Disposition": f'attachment; filename="HiJenny-meeting-{meeting.id}.pptx"',
                             "Cache-Control": "no-store"})
