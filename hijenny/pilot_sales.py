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
