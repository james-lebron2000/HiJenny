"""Collect public facts using only the repository's public watchlist (standard library)."""
import argparse
from functools import cache
import hashlib
import json
import os
import re
import sys
import time
from xml.etree import ElementTree
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hijenny.publication import publication_notices

ROOT = Path(__file__).resolve().parents[1] / "data/public"
AGENT = "HiJenny research jakeljm12@163.com"
STOP_FIELD = "暂停、终止或撤回的登记说明"
STOP_STATUS = {"SUSPENDED": "暂停", "TERMINATED": "提前终止", "WITHDRAWN": "入组前撤回"}
FORMS = {"8-K", "6-K", "10-Q", "10-K", "20-F", "40-F"}


def fetch(url, parse=json.load):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": AGENT}), timeout=30) as response:
                return parse(response)
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sec_records(company):
    cik = company["cik"]
    data = fetch(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if int(data["cik"]) != int(cik):
        raise ValueError("Unexpected SEC filer")
    filings = data["filings"]
    batches = [filings["recent"]]
    for archive in filings["files"]:
        if archive["filingTo"] >= company["since"]:
            batches.append(fetch("https://data.sec.gov/submissions/" + quote(archive["name"], safe="")))
    for batch in batches:
        for i, accession in enumerate(batch["accessionNumber"]):
            form, date = batch["form"][i], batch["filingDate"][i]
            if date < company["since"] or form.split("/")[0] not in FORMS:
                continue
            doc = quote(batch["primaryDocument"][i], safe="")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{doc}"
            row = dict(source="SEC", record=accession, subject=company["company"], field="公司申报",
                       value=form, source_date=date, effective=None, url=url, region=None, asset=None)
            yield row
            items = {item.strip() for item in batch["items"][i].split(",")}
            for code, label in (("1.01", "签订重大最终协议"), ("1.02", "终止重大最终协议")):
                if code in items:
                    yield dict(row, field="重大协议申报项目 " + code, value=label + "（具体交易和资产待核实）")


def fda_records(target):
    application = target["id"]
    if not re.fullmatch(r"(NDA|BLA|ANDA)[0-9]{6}", application):
        raise ValueError("Invalid FDA application number")
    query = urlencode({"search": f'application_number:"{application}"'})
    records = fetch("https://api.fda.gov/drug/drugsfda.json?" + query)["results"]
    if len(records) != 1 or records[0]["application_number"] != application:
        raise ValueError("Unexpected FDA application")
    product = records[0]
    url = "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=" + application[-6:]
    for entry in product["submissions"]:
        if entry["submission_status"] != "AP":
            continue
        kind, number = entry["submission_type"], entry["submission_number"]
        action_day = datetime.strptime(entry["submission_status_date"], "%Y%m%d").date().isoformat()
        field = {"ORIG": "原始申请获批", "SUPPL": "补充申请获批"}.get(kind, f"{kind} 申请获批")
        description = entry.get("submission_class_code_description") or "未列申请分类"
        documents = set()
        for document in entry.get("application_docs", []):
            link = (document.get("url") or "").replace("http://", "https://", 1)
            if link.startswith("https://www.accessdata.fda.gov/drugsatfda_docs/"):
                documents.add((document["type"], link))
        label_trials = target.get("label_trials", {}).get(f"{kind}/{number}", [])
        for trial in label_trials:
            if (("Label", trial["label"]) not in documents or not re.fullmatch(r"NCT[0-9]{8}", trial["id"])
                    or not isinstance(trial["page"], int) or trial["page"] < 1):
                raise ValueError("Invalid trial citation for FDA submission")
        yield dict(label_trials=label_trials, documents=[{"type": t, "url": u} for t, u in sorted(documents)],
                   source="Drugs@FDA", record=f"{application}/{kind}/{number}", subject=product["sponsor_name"],
                   asset=target["asset"], region="美国", field=field, value=description, source_date=action_day,
                   date_label="FDA 决定日", effective=action_day, url=url)


EMA_MEDICINES = "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"


def ema_records(target, dataset):
    product = target["id"]
    rows = [r for r in dataset()["data"] if r["ema_product_number"] == product and r["category"] == "Human"]
    if len(rows) != 1:
        raise ValueError("Expected one EMA human medicine")
    row = rows[0]
    if not re.fullmatch(r"https://www\.ema\.europa\.eu/en/medicines/human/EPAR/[a-z0-9-]+", row["medicine_url"]):
        raise ValueError("Unexpected EMA medicine page")
    updated = datetime.strptime(row["last_updated_date"], "%d/%m/%Y").date().isoformat()
    fields = {"medicine_status": "欧盟集中程序状态", "marketing_authorisation_date": "欧盟上市许可日期",
              "european_commission_decision_date": "欧委会决定日期", "latest_procedure_affecting_product_information": "最近影响产品信息的程序",
              "marketing_authorisation_developer_applicant_holder": "EMA 所列开发方、申请人或持有人"}
    for key, label in fields.items():
        value = row[key] or None
        is_date = key.endswith("_date")
        if value and is_date:
            value = datetime.strptime(value, "%d/%m/%Y").date().isoformat()
        yield dict(source="EMA", record=product, subject=row["name_of_medicine"], asset=target["asset"], region="欧盟",
                   field=label, value=value, date_label="EMA 产品页更新日", source_date=updated,
                   effective=value if is_date else None, url=row["medicine_url"])


def trial_records(trial):
    nct = trial["id"]
    if not re.fullmatch(r"NCT[0-9]{8}", nct):
        raise ValueError("Invalid trial identifier")
    data = fetch(f"https://clinicaltrials.gov/api/v2/studies/{nct}")
    protocol = data["protocolSection"]
    if protocol["identificationModule"]["nctId"] != nct:
        raise ValueError("Unexpected trial identifier")
    status = protocol["statusModule"]
    fields = {"登记状态": status["overallStatus"], "主要完成日期": status.get("primaryCompletionDateStruct"),
              "研究完成日期": status.get("completionDateStruct"), "结果首次公布": status.get("resultsFirstPostDateStruct")}
    stop = STOP_STATUS.get(status["overallStatus"])
    reason = (status.get("whyStopped") or "").strip()
    fields[STOP_FIELD] = f"{stop} · {reason or '登记记录未提供原因'}" if stop else None
    results = data.get("resultsSection") or {}
    measures = results.get("outcomeMeasuresModule", {}).get("outcomeMeasures", [])
    primary = []
    for m in measures:
        if m.get("type") == "PRIMARY":
            result = {k: m[k] for k in ("title", "populationDescription", "timeFrame", "reportingStatus", "paramType",
                                         "dispersionType", "unitOfMeasure", "denoms", "classes") if k in m}
            result["groups"] = [{k: g[k] for k in ("id", "title") if k in g} for g in m.get("groups", [])]
            result["analyses"] = m.get("analyses", [])
            primary.append(result)
    fields["登记主要终点分组结果"] = {"primary_outcomes": primary} if primary else None
    safety = results.get("adverseEventsModule") or {}
    summary = {k: safety[k] for k in ("timeFrame", "frequencyThreshold", "description", "eventGroups") if k in safety}
    fields["登记安全性分组汇总"] = {"safety_summary": summary} if summary else None
    categories = {STOP_FIELD: "study-interruption", "登记主要终点分组结果": "primary-outcomes", "登记安全性分组汇总": "safety-summary"}
    for field, value in fields.items():
        yield dict(source="ClinicalTrials.gov", record=nct, subject=trial["asset"], asset=trial["asset"], region=None,
                   **({"category": categories[field]} if field in categories else {}),
                   field=field, value=value, source_date=status["lastUpdatePostDateStruct"]["date"],
                   effective=None, url=f"https://clinicaltrials.gov/study/{nct}")


def publication_links(trial, root):
    nct = trial["id"]
    for article in root.findall("PubmedArticle"):
        citation = article.find("MedlineCitation")
        pmid = citation.findtext("PMID")
        banks = citation.findall("Article/DataBankList/DataBank")
        if not any(bank.findtext("DataBankName") == "ClinicalTrials.gov" and
                   nct in [entry.text for entry in bank.findall("AccessionNumberList/AccessionNumber")]
                   for bank in banks):
            continue
        if not re.fullmatch(r"[0-9]+", pmid or ""):
            raise ValueError("Invalid linked PMID")
        indexed = article.find("PubmedData/History/PubMedPubDate[@PubStatus='pubmed']")
        date = datetime(*(int(indexed.findtext(part)) for part in ("Year", "Month", "Day"))).date()
        title = "".join(citation.find("Article/ArticleTitle").itertext()).strip()
        link = dict(source="PubMed", record=pmid, subject=trial["asset"], asset=trial["asset"], region=None,
                   trial=nct, field="论文登记关联 · " + nct, value=title, source_date=date.isoformat(),
                   date_label="PubMed 收录", effective=None, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
        yield link
        types = [node.text for node in citation.findall("Article/PublicationTypeList/PublicationType")]
        notices = sorted(set(publication_notices(citation, types)))
        revised = citation.find("DateRevised")
        status = dict(link, field="论文出版状态 · " + nct, category="publication-status",
                      value="；".join(notices) if notices else "本次元数据未见出版提醒")
        if revised is not None:
            status["source_date"] = datetime(*(int(revised.findtext(part)) for part in ("Year", "Month", "Day"))).date().isoformat()
            status["date_label"] = "PubMed 元数据修订"
        yield status


def publication_records(trial):
    nct = trial["id"]
    if not re.fullmatch(r"NCT[0-9]{8}", nct):
        raise ValueError("Invalid trial identifier")
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    params = {"db": "pubmed", "tool": "hijenny", "email": AGENT.split()[-1]}
    time.sleep(0.4)
    search = fetch(base + "esearch.fcgi?" + urlencode(dict(params, term=nct+"[si]", retmax=9999,
                                                          retmode="json")))["esearchresult"]
    ids = search["idlist"]
    if len(ids) != int(search["count"]):
        raise ValueError("Incomplete PubMed identifier list")
    for i in range(0, len(ids), 100):
        batch = ids[i:i+100]
        time.sleep(0.4)
        xml = fetch(base + "efetch.fcgi?" + urlencode(dict(params, id=",".join(batch), retmode="xml")),
                    ElementTree.parse)
        if {a.text for a in xml.findall("PubmedArticle/MedlineCitation/PMID")} != set(batch):
            raise ValueError("Incomplete PubMed citation batch")
        yield from publication_links(trial, xml)


def changes(records, history, now):
    latest = {(r["source"], r["record"], r["field"]): r for r in history}
    for record in records:
        key = record["source"], record["record"], record["field"]
        previous = latest.get(key)
        if previous is None and record.get("category") in ("study-interruption", "primary-outcomes", "safety-summary") and record["value"] is None:
            continue
        if previous:
            if record["source"] == "Drugs@FDA":
                fields = ("value", "source_date", "documents", "label_trials")
                if all(previous.get(k, []) == record.get(k, []) for k in fields):
                    continue
            elif previous["value"] == record["value"] or record["source_date"] < previous["source_date"]:
                continue
        entry = dict(record, previous=previous["value"] if previous else None, kind="change" if previous else "baseline",
                     observed=now)
        entry["id"] = fingerprint([key, record["source_date"], record["value"], previous["id"] if previous else None])
        if record["source"] == "Drugs@FDA" and previous:
            entry["previous_date"] = previous["source_date"]
            entry["previous_documents"] = previous.get("documents", [])
            if previous.get("label_trials", []) != record.get("label_trials", []):
                entry["label_trials_changed"] = True
            entry["id"] = fingerprint([entry["id"], entry["documents"], entry.get("label_trials", [])])
        latest[key] = entry
        yield entry


def collect(root=ROOT, receipt=None):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    files = sorted((root / "events").glob("*.jsonl"))
    history = [json.loads(line) for path in files for line in path.read_text().splitlines()]
    watch = json.loads((root / "watchlist.json").read_text())
    watch_hash = fingerprint(watch)
    previous_file = root / "events" / "collection.json"
    previous = json.loads(previous_file.read_text()) if receipt and previous_file.exists() else {}
    prior_checks = {(row["source"], row["target"]): row for row in previous.get("checks", [])}
    events, failures, checks = [], [], []
    ema_dataset = cache(lambda: fetch(EMA_MEDICINES))
    for group, read in (("ema", lambda target: ema_records(target, ema_dataset)), ("sec", sec_records), ("fda", fda_records), ("trials", trial_records), ("publications", publication_records)):
        for target in watch.get(group, []):
            check = {"source": group, "target": target.get("id") or target.get("cik")}
            check.update(subject=target.get("asset") or target.get("company"), target_sha256=fingerprint(target))
            prior = prior_checks.get((group, check["target"]), {})
            same_target = prior.get("target_sha256") == check["target_sha256"] or previous.get("watchlist_sha256") == watch_hash
            last_success = prior.get("last_success_utc") if same_target else None
            if same_target and not last_success and prior.get("status") == "ok":
                last_success = previous.get("finished_utc")
            try:
                records = list(read(target))
                additions = list(changes(records, history, now))
                events.extend(additions)
                last_success = datetime.now(timezone.utc).isoformat(timespec="seconds")
                check.update(status="ok", records=len({row["record"] for row in records}), new_events=len(additions))
            except Exception as err:
                check.update(status="failed", records=None, new_events=None, error=type(err).__name__)
                failures.append(f"{group} {target.get('id') or target.get('cik')}: {err}")
            check["last_success_utc"] = last_success
            checks.append(check)
    if events:
        folder = root / "events"
        folder.mkdir(exist_ok=True)
        month = folder / (now[:7] + ".jsonl")
        content = month.read_bytes() if month.exists() else b""
        tmp = month.with_suffix(".tmp")
        tmp.write_bytes(content + "".join(json.dumps(row, ensure_ascii=False, sort_keys=True)+"\n" for row in events).encode())
        tmp.replace(month)
    if receipt is not None:
        report = {"started_utc": now, "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "watchlist_sha256": watch_hash, "checks": checks, "new_events": len(events)}
        if os.environ.get("GITHUB_RUN_ID", "").isdigit():
            report.update(run_id=os.environ["GITHUB_RUN_ID"], trigger=os.environ.get("GITHUB_EVENT_NAME"))
        receipt.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"New public events: {len(events)}", flush=True)
    for error in failures:
        print(error, file=sys.stderr)
    return int(bool(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", type=Path, help="Server-only directory containing its own watchlist.json")
    parser.add_argument("--receipt", type=Path, help="Write a machine-readable public collection receipt")
    args = parser.parse_args()
    directory = args.private_dir
    if directory is not None and args.receipt:
        parser.error("--receipt is for the public watchlist")
    if directory is not None and directory.resolve().is_relative_to(ROOT.parents[1]):
        parser.error("Keep private watchlists outside the repository")
    sys.exit(collect(directory or ROOT, args.receipt))
