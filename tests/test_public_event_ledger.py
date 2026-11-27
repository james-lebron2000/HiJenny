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
    cutoff = dict(params, observed_after="2026-11-27T00:00:00+08:00")
    assert [r["id"] for r in client.get("/api/public-events", params=cutoff).json()["items"]] == ["2"]
    assert client.get("/api/public-events/count", params=cutoff).json() == {"total": 1}
    assert client.get("/api/public-events/count", params=dict(cutoff, event_id="1")).json() == {"total": 0}
    from xml.etree import ElementTree
    atom = "{http://www.w3.org/2005/Atom}"
    response = client.get("/api/public-events/feed.atom", params=cutoff)
    assert response.headers["content-type"] == "application/atom+xml"
    entry, = ElementTree.fromstring(response.content).findall(atom + "entry")
    assert entry.findtext(atom + "id") == "http://testserver/#/public-events?event=2"
    assert entry.findtext(atom + "updated") == "2026-11-27T15:59:59Z"
    assert entry.find(atom + "link[@rel='related']").get("href") == row["url"]
    historical = {"as_of": "2026-11-27", "source": "SEC"}
    feed = client.get("/api/public-events/feed.atom", params=historical)
    frozen, = ElementTree.fromstring(feed.content).findall(atom + "entry")
    identity = entry.findtext(atom + "id")
    assert frozen.findtext(atom + "id") == identity
    assert frozen.find(atom + "link[@rel='alternate']").get("href") == identity + "&as_of=2026-11-27"
    assert identity + "&as_of=2026-11-27" in client.get("/api/public-events/report.txt", params=historical).text
    empty = client.get("/api/public-events/feed.atom", params=dict(cutoff, asset="missing"))
    assert not ElementTree.fromstring(empty.content).findall(atom + "entry")
    filtered_report = client.get("/api/public-events/report.txt", params=cutoff).text
    assert "仅含首次观察晚于" in filtered_report and "共 1 条" in filtered_report
    assert client.get("/api/public-events", params={"observed_after": "2026-11-27T00:00:00"}).status_code == 422
    result = client.get("/api/public-events", params=dict(params, limit=1)).json()
    assert result["total"] == 2 and result["items"][0]["id"] == "2"
    report = client.get("/api/public-events/report.txt", params=params)
    assert report.text.count(row["url"]) == 2 and "共 2 条" in report.text
    assert "导出时数据库尚无采集回执" in report.text
    links = [line.split(" · ")[1] for line in report.text.splitlines() if line.startswith("HiJenny 单条归档")]
    assert links == ["http://testserver/#/public-events?event=2", "http://testserver/#/public-events?event=1"]
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


def test_jrct_target_preserves_previous_check_without_fetch(tmp_path, monkeypatch):
    watch = {"jrct": [{"id": "jRCT2031230688", "asset": "demo"}]}
    (tmp_path / "watchlist.json").write_text(json.dumps(watch))
    (tmp_path / "events").mkdir()
    receipt = tmp_path / "events" / "collection.json"
    receipt.write_text(json.dumps({"watchlist_sha256": collector.fingerprint(watch),
        "finished_utc": "2026-11-24T00:00:00Z", "checks": [
            {"source": "jrct", "target": "jRCT2031230688", "status": "ok"}]}))
    monkeypatch.setattr(collector, "fetch", lambda *args: pytest.fail("unexpected network read"))
    assert collector.collect(tmp_path, receipt) == 0
    row, = json.loads(receipt.read_text())["checks"]
    assert row["status"] == "manual_review" and row["records"] is None
    assert row["last_success_utc"] == "2026-11-24T00:00:00Z"


def test_rolling_week_feed_recalculates_at_taipei_midnight(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from xml.etree import ElementTree
    from hijenny.api import public_events as routes

    class Clock(datetime):
        instant = datetime(2026, 11, 27, 15, 59, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz)

    monkeypatch.setattr(routes, "datetime", Clock)
    monkeypatch.setattr(public_events, "DATA_DIR", tmp_path)
    with closing(public_events.connect()) as db, db:
        for key, day in [("old", "21"), ("recent", "27"), ("next", "28")]:
            observed = f"2026-11-{day}T00:00:00+08:00"
            row = dict(id=key, source="SEC", asset="demo", subject="demo", kind="reviewed", field="公告",
                       source_date="2026-11-10", record=key, url="https://www.sec.gov/", observed=observed, value=key)
            db.execute("insert into events values (?,?,?,?,?)", (key, "demo", "", row["source_date"], json.dumps(row)))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    path = "/api/public-events/feed.atom?rolling_week=true&source=SEC"
    def keys():
        root = ElementTree.fromstring(client.get(path).content)
        return [entry.text.rsplit("=", 1)[-1] for entry in root.findall("{*}entry/{*}id")]
    assert keys() == ["recent", "old"]
    Clock.instant = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)
    assert keys() == ["next", "recent"]
    assert client.get(path + "&as_of=2026-11-21").status_code == 422



def test_fixed_dose_product_aliases_keep_single_ingredient_queries_distinct():
    from hijenny.asset_names import verified_asset

    product = verified_asset("Opdualag®")
    assert product["name"] == "opdualag" and product["source"].endswith("761234s011lbl.pdf")
    for term in ("NIVOLUMAB AND RELATLIMAB-RMBW", "Opdualag (nivolumab + relatlimab-rmbw)"):
        assert verified_asset(term) == product
    for term in ("nivolumab", "relatlimab-rmbw", "nivolumab and ipilimumab"):
        assert verified_asset(term) is None


def test_overlapping_watch_targets_count_shared_fact_once_per_change(tmp_path, monkeypatch):
    targets = [{"id": key, "asset": "demo"} for key in ("first", "second")]
    (tmp_path / "watchlist.json").write_text(json.dumps({"trials": targets}))
    receipt = tmp_path / "receipt.json"
    record = dict(source="ClinicalTrials.gov", record="shared", field="登记状态",
                  value="A", source_date="2026-11-01")
    monkeypatch.setattr(collector, "trial_records", lambda target: [record.copy()])
    for value, expected in [("A", 1), ("A", 0), ("B", 1)]:
        record["value"] = value
        assert collector.collect(tmp_path, receipt) == 0
        report = json.loads(receipt.read_text())
        assert report["new_events"] == expected
        assert [(c["records"], c["new_events"]) for c in report["checks"]] == [(1, expected), (1, 0)]
    ledger, = (tmp_path / "events").glob("*.jsonl")
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [(row["previous"], row["value"]) for row in rows] == [(None, "A"), ("A", "B")]
    assert len({row["id"] for row in rows}) == 2


def test_atom_conditional_refresh_for_empty_and_changed_feed(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hijenny.api.public_events import router

    monkeypatch.setattr(public_events, "DATA_DIR", tmp_path)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    url = "/api/public-events/feed.atom?source=SEC"
    empty = client.get(url)
    tag = empty.headers["etag"]
    for validator in [tag, '"other", ' + tag[2:], "*"]:
        same = client.get(url, headers={"If-None-Match": validator})
        assert same.status_code == 304 and not same.content and same.headers["etag"] == tag
    row = dict(id="first", source="SEC", asset="demo", subject="company", region=None, record="abc",
               observed="2026-11-27T12:00:00Z", source_date="2026-11-26", url="https://www.sec.gov/",
               kind="baseline", field="公司申报", value="8-K")
    with closing(public_events.connect()) as db, db:
        db.execute("insert into events values (?,?,?,?,?)", ("first", "demo", None, row["source_date"], json.dumps(row)))
    changed = client.get(url, headers={"If-None-Match": tag})
    assert changed.status_code == 200 and changed.headers["etag"] != tag and "8-K" in changed.text

    from xml.etree import ElementTree
    receipt = dict(run_id="123", finished_utc="2026-11-27T13:00:00Z", checks=[
        dict(status="ok"), dict(status="manual_review", source="jrct", target="jRCT-demo"),
        dict(status="failed", source="sec", target="demo")])
    with closing(public_events.connect()) as db, db:
        db.execute("insert into collection_receipt values (1,?)", (json.dumps(receipt),))
    refreshed = client.get(url, headers={"If-None-Match": changed.headers["etag"]})
    root = ElementTree.fromstring(refreshed.content)
    assert refreshed.status_code == 200 and root.findtext("{*}updated") == receipt["finished_utc"]
    assert "成功 1/3" in root.findtext("{*}subtitle") and "jRCT-demo" in root.findtext("{*}subtitle")
    assert "采集失败 · sec · demo" in root.findtext("{*}subtitle")
    assert root.find("{*}link[@rel='related']").get("href").endswith("/actions/runs/123")
    entry, = root.findall("{*}entry")
    assert entry.findtext("{*}updated") == row["observed"]
    assert entry.findtext("{*}id") == ElementTree.fromstring(changed.content).findtext("{*}entry/{*}id")
    assert client.get(url, headers={"If-None-Match": refreshed.headers["etag"]}).status_code == 304


def test_restore_complete_public_archive_offline_and_replay(tmp_path, monkeypatch):
    """Rebuild the public index from this checkout, including its collection receipt."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hijenny.api.public_events import router
    from tools.verify_public_archive import verify

    monkeypatch.setattr(public_events, "DATA_DIR", tmp_path)
    monkeypatch.setattr(public_events, "get", lambda *args: pytest.fail("unexpected network request"))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        def read(suffix):
            response = client.get("/api/public-events" + suffix)
            response.raise_for_status()
            return response.json()

        assert not (tmp_path / "public-events.db").exists()
        for _ in range(2):
            count = public_events.sync(local=True)
            result = verify("", read_json=read)
            assert count == result["events_verified"] > 0
            assert result["sources_verified"] > 0
