from io import BytesIO
from uuid import uuid4

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hijenny.api.study import router


def test_proposal_export_keeps_kind_specific_design_and_saved_evidence():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    data = dict(id=str(uuid4()), created="2026-11-26T12:00:00Z", kind="IIT",
                values={"question": "演练问题", "recruitment": "演练中心", "index": "RWE索引日期"},
                checks=[{"status": "部分可用", "note": "缺字段"}] * 5, meetingId=str(uuid4()),
                refs=[dict(id="123", title="演练来源", label="摘要", quote="确认版片段")],
                bias={"risk": "选择偏倚"}, gaps=["待统计复核"], changes=["控制措施"])
    result = client.post("/api/medical/study.docx", json=data)
    assert result.status_code == 200
    doc = Document(BytesIO(result.content))
    text = "\n".join(p.text for p in doc.paragraphs)
    for value in ["演练中心", "选择偏倚", "待评估", "确认版片段", data["meetingId"], data["id"], "待统计复核"]:
        assert value in text
    assert "RWE索引日期" not in text
    assert doc.tables[0].rows[1].cells[2].text == "缺字段"
    data.pop("bias")
    data["kind"] = "RWE"
    legacy = client.post("/api/medical/study.docx", json=data)
    assert legacy.status_code == 200
    assert "RWE索引日期" in [p.text for p in Document(BytesIO(legacy.content)).paragraphs]
    data["conflicts"] = [dict(left="123", right="NCT00000001", status="仍有分歧",
                              reviewer="底稿审阅甲", note="人群不同", plan="核对亚组定义")]
    payload = client.post("/api/medical/study.docx", json=data)
    assert payload.status_code == 200
    text = "\n".join(p.text for p in Document(BytesIO(payload.content)).paragraphs)
    assert all(value in text for value in ("核对亚组定义", "底稿审阅甲", "NCT00000001", "人群不同"))
    data["conflicts"][0]["left"] = "999"
    assert client.post("/api/medical/study.docx", json=data).status_code == 422
