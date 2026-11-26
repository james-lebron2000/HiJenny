"""Read selected public fields from the jRCT English registration tables."""
from datetime import datetime
from html.parser import HTMLParser
import re


class RegistryTables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.cells = []
        self.tag = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self.cells = []
        elif tag in ("th", "td"):
            self.tag = tag
            self.cells.append([])
        elif tag == "br" and self.tag:
            self.cells[-1].append(" ")

    def handle_data(self, data):
        if self.tag:
            self.cells[-1].append(data)

    def handle_endtag(self, tag):
        if tag == self.tag:
            self.tag = None
        elif tag == "tr" and len(self.cells) == 2 and self.tables:
            self.tables[-1].append(tuple(" ".join("".join(cell).split()) for cell in self.cells))


def records(target, fetch):
    identifier = target["id"]
    trial = target["trial"]
    if not re.fullmatch(r"jRCT[0-9]{10}", identifier) or not re.fullmatch(r"NCT[0-9]{8}", trial):
        raise ValueError("Invalid trial identifiers")
    url = "https://jrct.mhlw.go.jp/en-latest-detail/" + identifier
    parser = RegistryTables()
    parser.feed(fetch(url, lambda response: response.read().decode("utf-8")))
    parser.close()

    def field(label):
        values = {value for table in parser.tables for key, value in table if key == label}
        if len(values) != 1 or not all(values):
            raise ValueError("Ambiguous or missing jRCT field: " + label)
        return values.pop()

    if field("Trial ID") != identifier or not any(
        ("Secondary ID(s)", trial) in table and ("Issuing Authority", "ClinicalTrials.gov") in table
        for table in parser.tables
    ):
        raise ValueError("Unverified jRCT cross-registration")

    def date(label):
        return datetime.strptime(field(label).replace(".", ""), "%b %d, %Y").date().isoformat()

    updated = date("Last modified on")
    registered = date("Date of registration")
    for label, value in [("日本登记招募状态", field("Recruitment status")), ("日本试验登记日期", registered),
                         ("日本登记主要申办方", field("Primary Sponsor")), ("日本登记所列关联试验", trial)]:
        yield dict(source="jRCT", record=identifier, subject=target["asset"], asset=target["asset"], region="日本",
                   field=label, value=value, source_date=updated, date_label="jRCT 登记更新日",
                   effective=registered if label == "日本试验登记日期" else None, trial=trial, url=url)
