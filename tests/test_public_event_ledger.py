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

    unindexed = xml.replace("ClinicalTrials.gov", "Other")
    def abstract_records(text):
        with_text = unindexed.replace("</Article>", f"<Abstract><AbstractText>{text}</AbstractText></Abstract></Article>")
        return list(collector.publication_links(trial, collector.ElementTree.fromstring(with_text)))
    mention, _ = abstract_records("(nct<i>05499390</i>)")
    assert mention["field"] == "论文摘要提及登记号 · NCT05499390" and mention["category"] == "publication-mention"
    assert not abstract_records("XNCT05499390 or NCT054993901")
    titled = unindexed.replace("Design of", "NCT05499390: Design of")
    assert list(collector.publication_links(trial, collector.ElementTree.fromstring(titled)))[0]["field"].startswith("论文题名提及")


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

    trial = dict(id="NCT03470922", label="https://" + url, page=18, name="trial", section="14")
    target["label_trials"] = {"ORIG/1": [trial]}
    linked, = collector.changes(collector.fda_records(target), history + [revised], "2026-11-29")
    assert linked["label_trials_changed"] and linked["label_trials"] == [trial]
    target["label_trials"] = {"SUPPL/1": [trial]}
    with pytest.raises(ValueError, match="Invalid trial citation"):
        list(collector.fda_records(target))


def test_trial_filter_only_accepts_source_specific_links(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hijenny.api.public_events import router

    trial = "NCT03470922"
    monkeypatch.setattr(public_events, "DATA_DIR", tmp_path)
    with closing(public_events.connect()) as db, db:
        for i, fields in enumerate([
            dict(source="ClinicalTrials.gov", record=trial),
            dict(source="PubMed", record="123", trial=trial),
            dict(source="Drugs@FDA", record="BLA/ORIG/1", label_trials=[{"id": trial}]),
            dict(source="SEC", record=trial, trial=trial),
            dict(source="ClinicalTrials.gov", record="NCT00000001"),
        ]):
            payload = dict(id=str(i), field="status", observed="2026-11-27T01:00:00Z", **fields)
            db.execute("insert into events values (?,?,?,?,?)", (str(i), trial, "", "2025-01-01", json.dumps(payload)))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    url = "/api/public-events"
    assert {r["id"] for r in client.get(url, params={"trial": trial.lower()}).json()["items"]} == {"0", "1", "2"}
    assert client.get(url, params={"trial": trial, "as_of": "2026-11-26"}).json()["total"] == 0
    assert client.get(url, params={"trial": "NCT0347"}).status_code == 422

    japan = "jRCT2031230688"
    with closing(public_events.connect()) as db, db:
        link = dict(id="5", source="jRCT", record=japan, trial=trial, value=trial, field="日本登记所列关联试验",
                    observed="2026-11-28T02:00:00Z")
        db.execute("insert into events values (?,?,?,?,?)", ("5", trial, "日本", "2026-11-20", json.dumps(link)))
    assert {r["id"] for r in client.get(url, params={"trial": japan.upper()}).json()["items"]} == {"0","1","2","5"}
    assert client.get(url, params={"trial": japan, "as_of": "2026-11-27"}).json()["total"] == 0
    assert client.get(url, params={"trial": japan, "as_of": "2026-11-28", "source": "PubMed"}).json()["total"] == 1
    assert client.get(url, params={"trial": "jRCT0000000000"}).json()["total"] == 0


def test_primary_result_revisions_keep_denominators_and_prior_snapshot(monkeypatch):
    import copy
    target = {"id": "NCT03470922", "asset": "demo"}
    measures = []
    data = {"protocolSection": {"identificationModule": {"nctId": target["id"]},
                                "statusModule": {"overallStatus": "COMPLETED", "lastUpdatePostDateStruct": {"date": "2026-01-01"}}},
            "resultsSection": {"outcomeMeasuresModule": {"outcomeMeasures": measures}}}
    monkeypatch.setattr(collector, "fetch", lambda _: copy.deepcopy(data))
    history = []
    def check():
        new = list(collector.changes(collector.trial_records(target), history, "2026-01-01"))
        history.extend(new)
        return [r for r in new if r.get("category") == "primary-outcomes"]
    assert not check()
    measures.append({"type": "PRIMARY", "title": "test", "denoms": [{"counts": [{"groupId":"A","value":"0"}]}]})
    first, = check()
    assert not check()
    measures[0]["denoms"][0]["counts"][0]["value"] = "2"
    revised, = check()
    assert revised["previous"] == first["value"] and revised["value"] != first["value"]
    measures[0]["analyses"] = [{"groupIds": ["A", "B"], "pValue": "<0.001"}]
    updated, = check()
    assert updated["previous"] == revised["value"]
    assert updated["value"]["primary_outcomes"][0]["analyses"] == measures[0]["analyses"]
    assert not check()
    measures[0]["type"] = "SECONDARY"
    removed, = check()
    assert removed["value"] is None and removed["previous"] == updated["value"]


def test_archived_safety_keeps_event_group_denominators_and_zero(monkeypatch):
    trial = {"id": "NCT03470922", "asset": "demo"}
    group = {"id": "EG000", "seriousNumAffected": 0, "seriousNumAtRisk": 3}
    module = {"frequencyThreshold": 0, "timeFrame": "30 days", "eventGroups": [group],
              "seriousEvents": [{"term": "separate detail"}]}
    data = {"protocolSection": {"identificationModule": {"nctId": trial["id"]},
                                "statusModule": {"overallStatus": "COMPLETED", "lastUpdatePostDateStruct": {"date": "2026-02-01"}}},
            "resultsSection": {"adverseEventsModule": module}}
    monkeypatch.setattr(collector, "fetch", lambda _: data)
    def safety(history):
        return [r for r in collector.changes(collector.trial_records(trial), history, "2026-02-01")
                if r.get("category") == "safety-summary"]
    first, = safety([])
    assert first["value"]["safety_summary"] == {k: module[k] for k in ("eventGroups","frequencyThreshold","timeFrame")}
    assert not safety([first])
    data["resultsSection"] = {}
    removed, = safety([first])
    assert removed["value"] is None and removed["previous"] == first["value"]


def test_text_report_keeps_nested_scopes_missing_values_and_precision():
    from hijenny.registry_report import outcome_text, safety_text
    block = {"title": "subgroup", "denoms": [{"units": "eyes", "counts": [{"groupId": "A", "value": 0}]}],
             "measurements": [{"groupId": "A", "value": "0.00", "lowerLimit": "0.0"}]}
    measure = {"title": "test", "groups": [{"id": "A", "title": "arm"}],
               "classes": [{"title": "visit", "categories": [block]}]}
    text = outcome_text(measure)
    assert "visit / subgroup | A · arm | 0 | eyes" in text
    assert "visit / subgroup | A · arm | 0.00 | 未提供 | 未提供 | 0.0 | 未提供" in text
    assert "此版归档尚未采集统计分析" in text
    measure["analyses"] = [{"groupIds": ["A"], "pValue": "<0.001"}]
    assert "1 | P 值 | <0.001" in outcome_text(measure)
    assert "严重不良事件 | A | 0 | 未提供" in safety_text({"eventGroups": [{"id": "A", "seriousNumAffected": 0}]})


def test_collection_receipt_partial_failure_and_empty_match(tmp_path, monkeypatch):
    root = tmp_path / "public"
    root.mkdir()
    (root / "watchlist.json").write_text(json.dumps({"publications": [{"id": "found"}, {"id": "none"}, {"id": "failed"}]}))
    def records(target):
        if target["id"] == "failed":
            raise OSError("unavailable")
        if target["id"] == "found":
            for field in ("link", "status"):
                yield dict(source="PubMed", record="123", field=field, source_date="2026-11-26", value=field)
    monkeypatch.setattr(collector, "publication_records", records)
    receipt = tmp_path / "receipt.json"
    assert collector.collect(root, receipt) == 1
    first = json.loads(receipt.read_text())
    assert [(r["status"], r["records"], r["new_events"]) for r in first["checks"]] == [
        ("ok", 1, 2), ("ok", 0, 0), ("failed", None, None)]
    assert first["new_events"] == 2
    assert collector.collect(root, receipt) == 1
    assert json.loads(receipt.read_text())["new_events"] == 0


def test_receipt_retains_last_success_only_for_same_target_configuration(tmp_path, monkeypatch):
    events = tmp_path / "events"
    events.mkdir()
    receipt = events / "collection.json"
    watch = {"publications": [{"id": "trial", "asset": "asset A"}]}
    watchfile = tmp_path / "watchlist.json"
    watchfile.write_text(json.dumps(watch))
    monkeypatch.setattr(collector, "publication_records", lambda target: [])
    assert collector.collect(tmp_path, receipt) == 0
    success = json.loads(receipt.read_text())["checks"][0]["last_success_utc"]
    assert success

    def unavailable(target):
        raise OSError("unavailable")

    monkeypatch.setattr(collector, "publication_records", unavailable)
    for _ in range(2):
        assert collector.collect(tmp_path, receipt) == 1
        check = json.loads(receipt.read_text())["checks"][0]
        assert (check["subject"], check["last_success_utc"], check["records"]) == ("asset A", success, None)
    watch["publications"][0]["asset"] = "asset B"
    watchfile.write_text(json.dumps(watch))
    assert collector.collect(tmp_path, receipt) == 1
    assert json.loads(receipt.read_text())["checks"][0]["last_success_utc"] is None


def test_fda_document_diff_compares_type_and_url_and_requires_previous_snapshot():
    from hijenny.api.public_events import document_changes
    a = {"type": "Label", "url": "https://www.accessdata.fda.gov/drugsatfda_docs/label/old.pdf"}
    b = dict(a, url=a["url"].replace("old", "new"))
    letter = dict(a, type="Letter")
    row = {"source": "Drugs@FDA", "documents": [letter, b]}
    assert document_changes(row) == {}
    assert document_changes(dict(row, previous_documents=[a, letter])) == {"added": [b], "removed": [a]}
    assert document_changes(dict(row, previous_documents=[b, letter])) == {}
    assert document_changes(dict(row, documents=[], previous_documents=[a])) == {"removed": [a]}


def test_schedule_history_distinguishes_missing_failed_and_unavailable(monkeypatch):
    from tools import sync_verified_archive as sync
    def run(history):
        def get(url):
            assert "event=schedule&branch=main" in url
            return json.dumps(history)
        monkeypatch.setattr(sync, "get", get)
        return sync.scheduled_collection()
    assert run({"total_count": 0, "workflow_runs": []})["state"] == "no_runs"
    row = dict(id=42, event="schedule", head_branch="main", status="completed",
               conclusion="failure", created_at="2026-11-27T00:17:00Z")
    receipt = run({"total_count": 1, "workflow_runs": [row]})
    assert (receipt["state"], receipt["run_id"], receipt["conclusion"]) == ("found", "42", "failure")
    assert run({"workflow_runs": [dict(row,event="workflow_dispatch")]})["state"] == "unavailable"
    assert run({"message": "rate limited"})["state"] == "unavailable"


def test_coverage_uses_first_observation_of_latest_full_source_date(tmp_path, monkeypatch):
    from hijenny.api.public_events import latest_collection

    monkeypatch.setattr(public_events, "DATA_DIR", tmp_path)
    with closing(public_events.connect()) as db, db:
        for event_id, disclosed, observed in [
            ("first", "2026-01-10", "2026-01-10T16:01:00Z"),
            ("later", "2026-01-10", "2026-02-12T00:00:00Z"),
            ("older", "2026-01-08", "2026-03-01T00:00:00Z"),
            ("partial", "2026-09", "2026-09-15T00:00:00Z"),
            ("invalid", "2026-02-31", "2026-03-01T00:00:00Z"),
        ]:
            db.execute("INSERT INTO events(id,date,payload) VALUES (?,?,?)",
                       (event_id, disclosed, json.dumps({"source": "EMA", "observed": observed})))
    assert latest_collection()["source_timing"]["EMA"] == {
        "event_id": "first", "source_date": "2026-01-10", "observed_day": "2026-01-11", "days": 1}
