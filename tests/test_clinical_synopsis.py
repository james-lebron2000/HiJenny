from io import BytesIO
from uuid import uuid4

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hijenny.api.clinical import router


def test_protocol_synopsis_retains_choices_sources_and_explicit_gaps():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    data = dict(id=str(uuid4()), question="例示研究问题", options=[{}, {"population": "待讨论人群"}],
                choice="B", rationale="示例理由", changes=["复核分析集"], references=[
                    dict(id="NCT00115739", title="登记标题", snapshot="来源\x00片段")])
    response = client.post("/api/clinical/synopsis.docx", json=data)
    assert response.status_code == 200
    text = "\n".join(p.text for p in Document(BytesIO(response.content)).paragraphs)
    for expected in (data["id"], "倾向方案 B", "示例理由", "待填写", "来源片段", "复核分析集",
                     "https://clinicaltrials.gov/study/NCT00115739"):
        assert expected in text
    data["options"] = [{}]
    assert client.post("/api/clinical/synopsis.docx", json=data).status_code == 422


def test_review_export_requires_the_matching_version():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    version = str(uuid4())
    review = dict(version=version, role="统计", reviewer="演练统计师", decision="需修改",
                  note="澄清主要分析集", created="2026-11-26T12:30:00Z")
    data = dict(id=version, question="演练", options=[{}, {}], references=[], reviews=[review])
    response = client.post("/api/clinical/synopsis.docx", json=data)
    assert response.status_code == 200
    paragraphs = [p.text for p in Document(BytesIO(response.content)).paragraphs]
    assert paragraphs[paragraphs.index("临床复核") + 1] == "待复核"
    assert review["note"] in paragraphs
    data["id"] = str(uuid4())
    assert client.post("/api/clinical/synopsis.docx", json=data).status_code == 422
