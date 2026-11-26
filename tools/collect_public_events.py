"""Collect public facts using only the repository's public watchlist (standard library)."""
import argparse
import hashlib
import json
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
    for field, value in fields.items():
        yield dict(source="ClinicalTrials.gov", record=nct, subject=trial["asset"], asset=trial["asset"], region=None,
                   **({"category": "study-interruption"} if field == STOP_FIELD else {}),
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
        if previous is None and record.get("category") == "study-interruption" and record["value"] is None:
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


def collect(root=ROOT):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    files = sorted((root / "events").glob("*.jsonl"))
    history = [json.loads(line) for path in files for line in path.read_text().splitlines()]
    watch = json.loads((root / "watchlist.json").read_text())
    events, failures = [], []
    for group, read in (("sec", sec_records), ("fda", fda_records), ("trials", trial_records), ("publications", publication_records)):
        for target in watch.get(group, []):
            try:
                events.extend(list(changes(read(target), history, now)))
            except Exception as err:
                failures.append(f"{group} {target.get('id') or target.get('cik')}: {err}")
    if events:
        folder = root / "events"
        folder.mkdir(exist_ok=True)
        month = folder / (now[:7] + ".jsonl")
        content = month.read_bytes() if month.exists() else b""
        tmp = month.with_suffix(".tmp")
        tmp.write_bytes(content + "".join(json.dumps(row, ensure_ascii=False, sort_keys=True)+"\n" for row in events).encode())
        tmp.replace(month)
    print(f"New public events: {len(events)}", flush=True)
    for error in failures:
        print(error, file=sys.stderr)
    return int(bool(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", type=Path, help="Server-only directory containing its own watchlist.json")
    directory = parser.parse_args().private_dir
    if directory is not None and directory.resolve().is_relative_to(ROOT.parents[1]):
        parser.error("Keep private watchlists outside the repository")
    sys.exit(collect(directory or ROOT))
