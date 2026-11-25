from io import BytesIO
from uuid import uuid4

from docx import Document
from hijenny.api.brief import Brief, export_brief


def test_word_report_records_screening_scope_and_preserves_version_identity():
    record = dict(id="123", title="Paper", decision="纳入", reason="匹配人群",
                  passages=[{"label": "", "text": "纳入来源原文"}])
    excluded = dict(id="345", title="Other paper", decision="排除", reason="重复队列",
                    passages=[{"label": "摘要", "text": "排除来源原文"}])
    brief = Brief(id=uuid4(), question="What is reported?", created="2026-11-26", note="待核对",
                  steps=[dict(source="pubmed", query="example", total=100, records=[record, excluded]),
                         dict(source="trials", query="example", error="上游暂时不可用", records=[])])
    original = export_brief(brief)
    doc = Document(BytesIO(original.body))
    body = "\n".join(p.text for p in doc.paragraphs)
    for value in ["纳入来源原文", "重复队列", "待核对", "上游暂时不可用", "纳入 1，排除 1，待复核 0", "数据库命中 100"]:
        assert value in body
    assert "排除来源原文" not in body
    links = {rel.target_ref for rel in doc.part.rels.values() if rel.is_external}
    assert links == {"https://pubmed.ncbi.nlm.nih.gov/123/", "https://pubmed.ncbi.nlm.nih.gov/345/"}
    brief.note = "更新后的复核意见"
    assert export_brief(brief).headers["content-disposition"] != original.headers["content-disposition"]
