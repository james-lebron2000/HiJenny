import hashlib
import json
from contextlib import closing
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from hijenny import public_events

spec = spec_from_file_location("collector", Path(__file__).resolve().parents[1] / "tools/collect_public_events.py")
collector = module_from_spec(spec)
spec.loader.exec_module(collector)


def test_retry_change_reversion_and_sql_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(public_events, "DATA_DIR", tmp_path)
    row = dict(record="NCT05899608", field="登记状态", source="ClinicalTrials.gov", value="A",
               source_date="2026-11-01", asset="demo", subject="demo", region=None,
               url="https://clinicaltrials.gov/study/NCT05899608")
    history = []
    for value in ("A", "A", "B", "A"):
        history.extend(collector.changes([dict(row, value=value)], history, "2026-11-02T00:00:00Z"))
    assert [entry["previous"] for entry in history] == [None, "A", "B"]
    assert not list(collector.changes([dict(row, value="B", source_date="2026-10-21")], history, "later"))
    content = "\n".join(json.dumps(entry) for entry in history).encode()
    sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    with closing(public_events.connect()) as db, db:
        for _ in range(2):
            public_events.import_month(db, "2026-11.jsonl", sha, content)
        assert db.execute("select count(*) from events").fetchone()[0] == 3
        with pytest.raises(ValueError, match="digest"):
            public_events.import_month(db, "2026-11.jsonl", sha, content + b" ")


def test_review_digest_uses_taipei_discovery_day_and_exports_all_matches(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hijenny.api.public_events import router

    monkeypatch.setattr(public_events, "DATA_DIR", tmp_path)
    with closing(public_events.connect()) as db, db:
        for i, observed in enumerate(["2026-11-26T15:59:59Z", "2026-11-26T16:00:00Z", "2026-11-27T15:59:59Z"]):
            row = dict(id=str(i), asset="review asset", subject="company", region="中东", record="123",
                       source="SEC", field="扩区", value="新地区", previous=None, kind="reviewed", observed=observed,
                       source_date="2024-06-03", url="https://www.sec.gov/Archives/demo")
            db.execute("insert into events values (?,?,?,?,?)",
                       (row["id"], row["asset"], row["region"], row["source_date"], json.dumps(row)))
        future = dict(row, id="future", source_date="2026-11-28")
        db.execute("insert into events values (?,?,?,?,?)",
                   (future["id"], future["asset"], future["region"], future["source_date"], json.dumps(future)))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    for cutoff, ids in [("2026-11-25", []), ("2026-11-26", ["0"]), ("2026-11-27", ["2"]), ("2026-11-28", ["future"])]:
        snapshot = client.get("/api/public-events", params={"as_of": cutoff}).json()
        assert [r["id"] for r in snapshot["items"]] == ids
        assert snapshot["total"] == len(ids)
    snapshot_report = client.get("/api/public-events/report.txt", params={"as_of": "2026-11-26"})
    assert "历史截面" in snapshot_report.text and "共 1 条" in snapshot_report.text
    params = dict(disclosed_to="2026-11-27", discovered_from="2026-11-27", discovered_to="2026-11-27", kind="reviewed")
    result = client.get("/api/public-events", params=dict(params, limit=1)).json()
    assert result["total"] == 2 and result["items"][0]["id"] == "2"
    report = client.get("/api/public-events/report.txt", params=params)
    assert report.text.count(row["url"]) == 2 and "共 2 条" in report.text
    assert "15:59:59Z" in report.text and "2026-11-26T15:59:59Z" not in report.text
    assert client.get("/api/public-events", params=dict(params, discovered_to="2026-11-26")).status_code == 422


def test_pubmed_link_requires_exact_registry_and_preserves_index_date():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
        <ArticleTitle>Design of <i>study</i></ArticleTitle><DataBankList><DataBank>
        <DataBankName>ClinicalTrials.gov</DataBankName><AccessionNumberList>
        <AccessionNumber>NCT05499390</AccessionNumber></AccessionNumberList></DataBank></DataBankList>
        </Article></MedlineCitation><PubmedData><History><PubMedPubDate PubStatus="pubmed">
        <Year>2025</Year><Month>3</Month><Day>7</Day></PubMedPubDate></History></PubmedData></PubmedArticle></PubmedArticleSet>"""
    trial = {"id": "NCT05499390", "asset": "demo"}
    root = collector.ElementTree.fromstring(xml)
    record, baseline = collector.publication_links(trial, root)
    assert baseline["value"] == "本次元数据未见出版提醒"
    correction = xml.replace("</MedlineCitation>", """<DateRevised><Year>2026</Year><Month>4</Month><Day>1</Day>
        </DateRevised><CommentsCorrectionsList><CommentsCorrections RefType="ErratumIn"><PMID>456</PMID>
        </CommentsCorrections></CommentsCorrectionsList></MedlineCitation>""")
    _, revised = collector.publication_links(trial, collector.ElementTree.fromstring(correction))
    history = list(collector.changes([baseline], [], "2025-03-10"))
    event, = collector.changes([revised], history, "2026-04-02")
    assert event["kind"] == "change" and event["previous"] == baseline["value"]
    assert event["source_date"] == "2026-04-01" and "本文已有更正通知" in event["value"]
    assert "https://pubmed.ncbi.nlm.nih.gov/456/" in event["value"]
    assert not list(collector.changes([revised], history+[event], "2026-04-03"))
    assert (record["record"], record["trial"], record["source_date"], record["value"]) == (
        "123", trial["id"], "2025-03-07", "Design of study")
    assert not list(collector.publication_links(dict(trial, id="NCT00000001"), root))
    assert not list(collector.publication_links(trial, collector.ElementTree.fromstring(xml.replace("ClinicalTrials.gov", "Other"))))


def test_study_stop_reason_revisions_and_resumption(monkeypatch):
    trial = {"id": "NCT07038915", "asset": "demo"}
    status = {"lastUpdatePostDateStruct": {"date": "2026-01-01"}}
    monkeypatch.setattr(collector, "fetch", lambda _: {"protocolSection": {
        "identificationModule": {"nctId": trial["id"]}, "statusModule": status}})
    history = []
    for overall, reason in [("RECRUITING", ""), ("SUSPENDED", ""), ("SUSPENDED", "review"),
                            ("RECRUITING", ""), ("WITHDRAWN", "0 participants")]:
        status.update(overallStatus=overall, whyStopped=reason)
        history.extend(collector.changes(collector.trial_records(trial), history, "2026-01-01"))
    notes = [e for e in history if e.get("category") == "study-interruption"]
    assert [e["value"] for e in notes] == [
        "暂停 · 登记记录未提供原因", "暂停 · review", None, "入组前撤回 · 0 participants"]
    assert notes[2]["previous"] == notes[1]["value"]


def test_fda_approval_actions_keep_submission_identity(monkeypatch):
    target = {"id": "BLA761234", "asset": "demo"}
    original = dict(submission_type="ORIG", submission_number="1", submission_status="AP",
                    submission_status_date="20220318", submission_class_code_description="Type 1")
    supplement = dict(original, submission_type="SUPPL", submission_class_code_description="Labeling")
    response = {"application_number": target["id"], "sponsor_name": "example",
                "submissions": [original, supplement, dict(supplement, submission_status="TA")]}
    monkeypatch.setattr(collector, "fetch", lambda _: {"results": [response]})
    rows = list(collector.fda_records(target))
    assert [r["record"] for r in rows] == ["BLA761234/ORIG/1", "BLA761234/SUPPL/1"]
    assert rows[1]["field"] == "补充申请获批" and rows[1]["value"] == "Labeling"
    assert rows[0]["effective"] == "2022-03-18"
    with pytest.raises(ValueError, match="Unexpected FDA"):
        list(collector.fda_records(dict(target, id="BLA999999")))

    history = list(collector.changes(rows, [], "2026-11-26"))
    url = "www.accessdata.fda.gov/drugsatfda_docs/label/test.pdf#page=23"
    original["application_docs"] = [{"type": "Label", "url": "http://" + url},
                                    {"type": "Label", "url": "https://" + url}]
    revised, = collector.changes(collector.fda_records(target), history, "2026-11-27")
    assert revised["documents"] == [{"type": "Label", "url": "https://" + url}]
    assert revised["previous_documents"] == [] and revised["previous_date"] == revised["source_date"]
    assert not list(collector.changes(collector.fda_records(target), history + [revised], "2026-11-28"))
