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


def test_sap_uses_selected_design_and_records_missing_statistical_assumptions():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    data = dict(id=str(uuid4()), question="研究设计讨论", options=[{"endpoint": "候选甲"}, {"endpoint": "候选乙"}],
                references=[], choice="B", sap={"missing": "统计负责人填写的处理规则"})
    result = client.post("/api/clinical/sap.docx", json=data)
    assert result.status_code == 200
    text = "\n".join(p.text for p in Document(BytesIO(result.content)).paragraphs)
    for expected in (data["id"], "候选乙", "统计负责人填写的处理规则", "待统计人员补充", "待复核"):
        assert expected in text
    assert "候选甲" not in text
    data["choice"] = ""
    assert client.post("/api/clinical/sap.docx", json=data).status_code == 422


def test_protocol_keeps_the_selected_operational_plan_and_same_version_sap():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    data = dict(id=str(uuid4()), question="演练", choice="B", options=[{}, {"endpoint": "乙终点"}],
                references=[], protocol={"A": {"visits": "甲访视"}, "B": {"visits": "乙访视"}},
                sap={"sets": "本版分析集"})
    response = client.post("/api/clinical/protocol.docx", json=data)
    assert response.status_code == 200
    body = "\n".join(p.text for p in Document(BytesIO(response.content)).paragraphs)
    assert all(x in body for x in [data["id"], "乙终点", "乙访视", "本版分析集", "待研究团队补充"])
    assert "甲访视" not in body


def test_csr_preserves_source_precision_zero_and_missing_values():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    raw = '类别,分析集,组别,指标,评价时间,报告值,单位,分母,出处\n疗效,演练,A,模拟终点,周末,0.00,%,0,"表1,行2"'
    report = dict(source="源表v1", cutoff="2025-01-01", table=raw)
    data = dict(id=str(uuid4()), question="演练", choice="A", options=[{}, {}], references=[], csr={"A": report})
    res = client.post("/api/clinical/csr.docx", json=data)
    assert res.status_code == 200
    doc = Document(BytesIO(res.content))
    cells = [c.text for c in doc.tables[0].rows[1].cells]
    assert cells[2] == "报告值：0.00\n单位：%\n分母：0"
    assert cells[3] == "出处：表1,行2"
    assert "未提供该类统计结果" in [p.text for p in doc.paragraphs]
    report["source"] = ""
    assert client.post("/api/clinical/csr.docx", json=data).status_code == 422
    report.update(source="源表v1", table=raw.rsplit(",", 2)[0])
    assert client.post("/api/clinical/csr.docx", json=data).status_code == 422


def test_ib_requires_sources_for_authored_sections_and_keeps_evidence_gaps():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    section = {"summary": "合成资料摘要", "source": "示例文档 V2 第三页"}
    data = dict(id=str(uuid4()), question="示例研究", options=[{}, {}], references=[],
                ib=dict(product="演练产品", cutoff="2025-01-31", sections={"clinical": section}))
    result = client.post("/api/clinical/ib.docx", json=data)
    assert result.status_code == 200
    paragraphs = [p.text for p in Document(BytesIO(result.content)).paragraphs]
    assert paragraphs.count("尚缺本章摘要，待补证") == 5
    assert section["summary"] in paragraphs and any(section["source"] in p for p in paragraphs)
    assert any(data["id"] in p and "2025-01-31" in p for p in paragraphs)
    section["source"] = " "
    assert client.post("/api/clinical/ib.docx", json=data).status_code == 422


def test_revision_export_uses_direct_parent_and_selected_candidate():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    parent = str(uuid4())
    data = dict(id=str(uuid4()), parent=parent, choice="B", question="复核", references=[],
                options=[{}, {"population": "新版人群"}], previous_design={
                    "id": parent, "options": [{"exclude": "甲原排除条款"}, {"include": "乙原入选条款"}]})
    for endpoint in ("synopsis", "sap"):
        response = client.post(f"/api/clinical/{endpoint}.docx", json=data)
        assert response.status_code == 200
        text = "\n".join(p.text for p in Document(BytesIO(response.content)).paragraphs)
        assert "修订前\n乙原入选条款\n修订后\n（空白）" in text
        assert "修订前\n（空白）\n修订后\n新版人群" in text
        assert ("甲原排除条款" in text) == (endpoint == "synopsis")
        assert parent in text
    data["previous_design"]["id"] = str(uuid4())
    assert client.post("/api/clinical/synopsis.docx", json=data).status_code == 422


def test_parent_review_response_is_separate_from_current_review_decisions():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    parent, version = str(uuid4()), str(uuid4())
    review = dict(id=str(uuid4()), version=parent, role="统计", decision="需修改",
                  reviewer="评审甲", created="2026-11-26T12:00:00Z", note="明确分析集")
    response = dict(review=review, reply="已在 B 方案说明")
    data = dict(id=version, parent=parent, question="复核", references=[], options=[{}, {}], responses=[response])
    result = client.post("/api/clinical/synopsis.docx", json=data)
    assert result.status_code == 200
    text = "\n".join(p.text for p in Document(BytesIO(result.content)).paragraphs)
    assert parent in text and review["id"] in text and review["note"] in text and response["reply"] in text
    assert "统计复核\n待复核" in text
    data["responses"].append(response)
    assert client.post("/api/clinical/synopsis.docx", json=data).status_code == 422
    data["responses"].pop()
    review["version"] = version
    assert client.post("/api/clinical/synopsis.docx", json=data).status_code == 422


def test_handoff_rejects_broken_lineage_and_unmatched_review_responses():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    first = dict(id=str(uuid4()), created="2026-11-27T01:00:00Z", question="测试", references=[],
                 changes=[], options=[{}, {}])
    review = dict(id=str(uuid4()), version=first["id"], reviewer="甲", role="临床", note="补充解释",
                  decision="需修改", created="2026-11-27T01:01:00Z")
    child = dict(first, id=str(uuid4()), parent=first["id"], responses=[dict(review=review, reply="已修订")])
    packet = dict(format="hijenny-clinical-v1", versions=[first, child], reviews=[review])
    check = lambda: client.post("/api/clinical/handoff/validate", json=packet).status_code
    assert check() == 204
    packet["reviews"] = []
    assert check() == 422
    packet["reviews"] = [review]
    packet["versions"].reverse()
    assert check() == 422


def test_parallel_handoff_requires_connected_sibling_evidence():
    from hijenny.api.clinical import ClinicalHandoff
    import pytest
    from pydantic import ValidationError
    base = dict(id=str(uuid4()), created="2026-11-27T01:00:00Z", question="评审", references=[], changes=[], options=[{}, {}])
    left = dict(base, id=str(uuid4()), parent=base["id"])
    right = dict(left, id=str(uuid4()))
    merged = dict(left, id=str(uuid4()), parent=left["id"], adoptions=[
        dict(field="试验字段", source_version=right["id"], base_version=base["id"], edited=True)])
    packet = dict(format="hijenny-clinical-v2", versions=[base, left, right, merged], reviews=[])
    ClinicalHandoff.model_validate(packet)
    for versions in ([base, left, merged], [base, left, dict(right, parent=left["id"]), merged],
                     [base, left, right, dict(right, id=str(uuid4())), merged]):
        with pytest.raises(ValidationError):
            ClinicalHandoff.model_validate(dict(packet, versions=versions))
