"""Read product-specific patent and exclusivity listings in the FDA Orange Book."""
import csv
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from io import BytesIO, TextIOWrapper
import re
from zipfile import ZipFile

DOWNLOAD = "https://www.fda.gov/media/76860/download?attachment"


def read_archive(response):
    return parsedate_to_datetime(response.headers["Last-Modified"]).date().isoformat(), response.read()


USE_CODES = "https://www.accessdata.fda.gov/scripts/cder/ob/results_patent.cfm"


class UseCodeTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.cells = []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells = []
        elif tag == "td":
            self.cells.append([])
            self.in_cell = True
        elif tag == "br" and self.in_cell:
            self.cells[-1].append(" ")

    def handle_data(self, data):
        if self.in_cell:
            self.cells[-1].append(data)

    def handle_endtag(self, tag):
        if tag == "td":
            self.in_cell = False
        if tag == "tr" and len(self.cells) == 2:
            code, text = (" ".join("".join(cell).split()) for cell in self.cells)
            if re.fullmatch(r"U-[0-9]+", code) and text:
                if self.rows.setdefault(code, text) != text:
                    raise ValueError("Conflicting FDA use-code definitions")


def read_definitions(response):
    table = UseCodeTable()
    table.feed(response.read().decode(response.headers.get_content_charset() or "utf-8"))
    table.close()
    if not table.rows:
        raise ValueError("Empty FDA patent use-code dictionary")
    return table.rows


def records(target, dataset, definitions=None, checked=None):
    application = target["id"]
    if not re.fullmatch(r"NDA[0-9]{6}", application):
        raise ValueError("Invalid Orange Book NDA")
    updated, content = dataset()
    number = application[3:]
    with ZipFile(BytesIO(content)) as archive:
        def rows(name):
            with archive.open(name) as stream, TextIOWrapper(stream, encoding="latin-1") as text:
                return [r for r in csv.DictReader(text, delimiter="~") if r["Appl_Type"] == "N" and r["Appl_No"] == number]

        products, patents, exclusivities = (rows(n) for n in ("products.txt", "patent.txt", "exclusivity.txt"))
    if not products:
        raise ValueError("NDA absent from Orange Book product list")

    def iso(text):
        return datetime.strptime(text.strip(), "%b %d, %Y").date().isoformat() if text.strip() else "未列"

    for product in products:
        index = product["Product_No"]
        if definitions is not None:
            mapping = {}
            for patent in patents:
                code = patent["Patent_Use_Code"].strip()
                if patent["Product_No"] == index and code:
                    mapping.setdefault(code, set()).add(patent["Patent_No"])
            dictionary = definitions() if mapping else {}
            lines = [f"{code} · 专利 {', '.join(sorted(numbers))} · {dictionary[code]}" for code, numbers in sorted(mapping.items())]
            yield dict(source="FDA Orange Book", record=application+"/"+index, subject=product["Applicant_Full_Name"],
                       asset=target["asset"], region="美国", field="FDA 专利用途代码释义", category="patent-use-definitions",
                       value="\n".join([product["Strength"], *(lines or ["本版未列用途代码"])]), source_date=checked,
                       date_label="释义核对日（UTC）", effective=None, url=USE_CODES)
            continue
        patent_rows = []
        for r in patents:
            if r["Product_No"] != index:
                continue
            flags = [label for key, label in [("Drug_Substance_Flag", "DS"), ("Drug_Product_Flag", "DP"),
                                              ("Delist_Flag", "撤列请求")] if r[key].strip() == "Y"]
            patent_rows.append(" · ".join([r["Patent_No"], "列示到期 "+iso(r["Patent_Expire_Date_Text"]),
                                          "用途 "+(r["Patent_Use_Code"] or "未列"), *flags, "提交 "+iso(r["Submission_Date"])]))
        exclusive_rows = [r["Exclusivity_Code"]+" · 列示到期 "+iso(r["Exclusivity_Date"]) for r in exclusivities
                          if r["Product_No"] == index]
        for label, entries in [("Orange Book 专利清单", patent_rows), ("Orange Book 独占期清单", exclusive_rows)]:
            yield dict(source="FDA Orange Book", record=application+"/"+index, subject=product["Applicant_Full_Name"],
                       asset=target["asset"], region="美国", field=label, value="\n".join([product["Strength"], *sorted(entries or ["本版未列记录"])]),
                       source_date=updated, date_label="FDA 下载文件更新日", effective=None,
                       url=f"https://www.accessdata.fda.gov/scripts/cder/ob/patent_info.cfm?Product_No={index}&Appl_No={number}&Appl_type=N")
