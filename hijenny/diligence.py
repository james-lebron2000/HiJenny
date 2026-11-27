"""Question-level follow-up, stored in the project's existing audit trail."""
from typing import Annotated, Literal, Union

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from hijenny import store

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: Text
    revision: int = Field(ge=0)


class Comparison(Entry):
    kind: Literal["comparison"]
    asset: Text
    population: Text
    line: Text
    method: Text
    followup: Text
    endpoint: Text
    value: Text
    document_id: Text
    locator: Text
    limits: Text


class Request(Entry):
    kind: Literal["request"]
    note: Text


class Reply(Entry):
    kind: Literal["reply", "experiment_result"]
    document_id: Text
    locator: Text
    note: Text


class Review(Entry):
    kind: Literal["review"]
    decision: Literal["documents", "experiment", "clarify"]
    note: Text


class Publish(Entry):
    kind: Literal["publish"]
    note: Text


Action = Annotated[Union[Comparison, Request, Reply, Review, Publish], Field(discriminator="kind")]
DECISIONS = {"documents": "资料复核即可，无需新增实验", "experiment": "需要实验验证", "clarify": "继续补资料"}


def histories(session, project_id):
    result = {}
    for event in store.list_review_events(session, project_id, limit=-1, entity_type="bd_followup"):
        result.setdefault(event["entity_id"], []).append(event)
    for events in result.values():
        events.sort(key=lambda e: e["after"]["revision"])
    return result


def followup_stage(question, events):
    if question.get("review_status") == "rejected" or question.get("status") == "dismissed":
        return "rejected"
    if question.get("status") == "closed":
        return "closed"
    last = next((e for e in reversed(events) if e["action"] != "comparison"), None)
    if not last:
        return {"sent": "reply", "answered": "record"}.get(question.get("status"), "request")
    kind = last["action"]
    if kind == "request":
        return "reply"
    if kind in ("reply", "experiment_result") or (kind == "publish" and events[-1] is not last):
        return "review"
    review = next(e for e in reversed(events) if e["action"] == "review")
    decision = review["after"]["decision"]
    return {"experiment": "experiment", "clarify": "clarify"}.get(decision, "done" if kind == "publish" else "publish")


def question_stages(questions, history):
    return {q["question_id"]: followup_stage(q, history.get(q["question_id"], [])) for q in questions}


def allowed(events, question=None):
    last = next((e for e in reversed(events) if e["action"] != "comparison"), None)
    choices = ["comparison", "request"]
    if not last:
        if question and question["status"] in ("sent", "answered"):
            choices.append("reply")
        return choices
    kind = last["action"]
    if kind in ("request", "reply", "experiment_result"):
        choices.append("reply")
    if kind in ("reply", "experiment_result"):
        choices.append("review")
    if kind == "review":
        decision = last["after"]["decision"]
        if decision == "experiment":
            choices.append("experiment_result")
        if decision == "clarify":
            choices.append("reply")
        choices.append("publish")
    if kind == "publish":
        if events[-1]["action"] == "comparison":
            choices.append("review")
        review = next(e for e in reversed(events) if e["action"] == "review")
        choices.extend({"documents": [], "experiment": ["experiment_result"], "clarify": ["reply"]}[review["after"]["decision"]])
    return choices


def append(session, project_id, question_id, action):
    question = store.get_question(session, question_id)
    if not question or question["project_id"] != project_id:
        raise HTTPException(404, "Question not found in project")
    events = histories(session, project_id).get(question_id, [])
    if action.revision != len(events):
        raise HTTPException(409, "记录已更新，请刷新后重试")
    if action.kind not in allowed(events, question):
        raise HTTPException(409, "请先完成前一项复核步骤")
    payload = action.model_dump(exclude={"actor", "kind"})
    if "document_id" in payload:
        doc = store.get_document(session, payload["document_id"])
        if not doc or doc["project_id"] != project_id:
            raise HTTPException(422, "请选择本项目的来源文件")
        payload["filename"] = doc["filename"]
    payload["revision"] += 1
    if action.kind in ("request", "reply"):
        status = "sent" if action.kind == "request" else "answered"
        store.update_question(session, question_id, actor=action.actor, status=status)
    return store.log_review_event(session, project_id, "bd_followup", question_id,
                                  action.actor, action.kind, after=payload)


def brief_updates(questions, history):
    updates = []
    for question in questions:
        events = history.get(question["question_id"], [])
        published = [i for i, e in enumerate(events) if e["action"] == "publish"]
        if not published:
            continue
        i = published[-1]
        decision = events[i]
        review = next(e for e in reversed(events[:i]) if e["action"] == "review")
        source = next(e for e in reversed(events[:i]) if e["action"] in ("reply", "experiment_result"))
        detail = source["after"]
        comparisons = [e["after"] for e in events[:i] if e["action"] == "comparison"]
        updates.append([
            ("问题", question["question"]),
            *[("可比资产", f'{r["asset"]} · {r["endpoint"]} {r["value"]} · {r["limits"]} · {r["filename"]} / {r["locator"]}') for r in comparisons],
            ("补充材料", f'{detail["filename"]} · {detail["locator"]} · {detail["note"]}'),
            ("验证判断", DECISIONS[review["after"]["decision"]] + " · " + review["after"]["note"]),
            ("BD 结论", decision["after"]["note"]),
            ("确认人", decision["actor"] + " · " + decision["ts"]),
            ("后续状态", "有新记录，等待重新确认" if i != len(events)-1 else "已更新简报"),
        ])
    return updates
