"""Server-local assignment and work queue for accepted pilot inquiries."""
from sqlalchemy import ForeignKey, String, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Mapped, mapped_column

from hijenny import store
from hijenny.db import Base, PilotInquiry


class PilotAssignment(Base):
    __tablename__ = "pilot_assignments"

    receipt: Mapped[str] = mapped_column(ForeignKey("pilot_inquiries.receipt"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(120))
    due: Mapped[str] = mapped_column(String(10))
    action: Mapped[str] = mapped_column(String(300))


def assign(session, receipt, owner, due, action):
    owner, action = owner.strip(), action.strip()
    if not 1 <= len(owner) <= 120 or not 1 <= len(action) <= 300:
        raise ValueError("请填写负责人（1–120 字）及下一步（1–300 字）")
    if session.get(PilotInquiry, receipt) is None:
        raise ValueError("受理编号不存在")
    values = dict(owner=owner, due=due.isoformat(), action=action)
    session.execute(insert(PilotAssignment).values(receipt=receipt, **values)
                    .on_conflict_do_update(index_elements=["receipt"], set_=values))


def queue(session, owner=None, due_by=None, waiting_quote=False, include_finished=False):
    assignments = {item.receipt: item for item in session.scalars(select(PilotAssignment))}
    rows = []
    for inquiry in store.list_pilot_inquiries(session):
        stage = inquiry["progress"][-1]["stage"] if inquiry["progress"] else "新受理"
        assignment = assignments.get(inquiry["receipt"])
        if stage in ("已回款", "已关闭") and not include_finished:
            continue
        if waiting_quote and stage not in ("新受理", "已联系"):
            continue
        if owner and (not assignment or assignment.owner != owner):
            continue
        if due_by and (not assignment or assignment.due > due_by.isoformat()):
            continue
        rows.append(dict(receipt=inquiry["receipt"], sales_stage=stage, meeting=inquiry["meeting"],
                         owner=assignment.owner if assignment else None, due=assignment.due if assignment else None,
                         next_action=assignment.action if assignment else None))
    return sorted(rows, key=lambda r: (r["due"] or "9999-12-31", r["meeting"], r["receipt"]))


def quote_draft(session, receipt, *, currency, amount, scope, delivery, tax, valid_until):
    """Prepare a printable fixed-fee proposal using the operator's commercial terms."""
    from datetime import date
    from decimal import Decimal
    from html import escape
    import re
    from uuid import uuid4

    inquiry = session.get(PilotInquiry, receipt)
    if inquiry is None:
        raise ValueError("受理编号不存在")
    if currency not in ("CNY", "USD", "EUR", "JPY"):
        raise ValueError("不支持的币种")
    digits = 0 if currency == "JPY" else 2
    pattern = r"[0-9]{1,12}" + (r"(?:\.[0-9]{1,2})?" if digits else "")
    if not re.fullmatch(pattern, amount) or Decimal(amount) <= 0:
        raise ValueError("请提供正数总价，精度须符合所选币种")
    if valid_until < date.today() or not all(x.strip() for x in (scope, delivery, tax)):
        raise ValueError("请填写交付范围、时间、税费口径及有效的截止日期")
    quote_id = "Q-" + uuid4().hex[:16].upper()

    def table(rows):
        return "<table>" + "".join(f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
                                   for label, value in rows) + "</table>"

    intake = table([("询价受理编号", receipt), ("联系邮箱", inquiry.email), ("资产阶段", inquiry.stage),
                    ("资料规模", inquiry.pages), ("计划会议日期", inquiry.meeting), ("部署需求", inquiry.deployment)])
    terms = table([("拟交付成果与范围", scope.strip()), ("拟交付时间", delivery.strip()),
                   ("固定总价", f"{currency} {Decimal(amount):,.{digits}f}"), ("税费口径", tax.strip()),
                   ("报价有效至", valid_until.isoformat())])
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>HiJenny 试点报价草案 {quote_id}</title>
<style>body{{max-width:44rem;margin:3rem auto;padding:0 1rem;font:16px/1.6 sans-serif;color:#172936}}
h1{{font-size:1.8rem}}h2{{font-size:1.25rem}}table{{width:100%;border-collapse:collapse}}
td,th{{padding:.5rem;text-align:left;border-bottom:1px solid #ccd5da;white-space:pre-wrap;overflow-wrap:anywhere}}
th{{width:30%}}tr{{break-inside:avoid}}@media print{{body{{margin:0}}}}</style>
<h1>HiJenny 单资产尽调试点报价草案</h1><p>{quote_id} · 编制日期 {date.today().isoformat()}</p>
<h2>客户询价范围</h2>{intake}<h2>拟议交付与费用</h2>{terms}
<p>请核对资料规模、部署需求、交付成果及费用。双方确认后安排试点。</p></html>"""
