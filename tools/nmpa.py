"""Recheck reviewed approval notices on the English NMPA site."""
import hashlib
import re
import time
from datetime import date
from html import unescape


def first_paragraph(html):
    body = re.search(r"<!--enpcontent-->(.*?)<!--/enpcontent-->", html, re.S)
    paragraph = re.search(r"<p\b[^>]*>(.*?)</p>", body[1], re.S) if body else None
    if not paragraph:
        raise ValueError("NMPA notice paragraph missing")
    return " ".join(unescape(re.sub(r"<[^>]+>", "", paragraph[1])).split())


def records(target, fetch):
    path = target["path"]
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}/[0-9]{2}/c_[0-9]+\.htm", path):
        raise ValueError("Invalid NMPA notice path")
    url = "https://english.nmpa.gov.cn/" + path
    html = fetch(url, lambda response: response.read().decode("utf-8"))
    if f'<articleid>{target["id"]}</articleid>' not in html or path.split("/")[-1] != f'c_{target["id"]}.htm':
        raise ValueError("Unexpected NMPA notice")
    updated = re.search(r"<span>\s*Updated:\s*([0-9-]+)\s*</span>", html)
    if not updated:
        raise ValueError("NMPA update date missing")
    paragraph = first_paragraph(html)
    if hashlib.sha256(paragraph.encode()).hexdigest() != target["paragraph_sha256"]:
        raise ValueError("NMPA approval paragraph changed; review the summary")
    yield dict(source="NMPA", record=target["id"], subject=target["company"], asset=target["asset"],
               region="中国", field="国家药监局上市批准公告", value=target["summary"],
               date_label="NMPA 英文公告更新日", source_date=date.fromisoformat(updated[1]).isoformat(),
               effective=None, url=url)


def index_records(target, fetch):
    """Read title/date pairs only inside each official drug index listing."""
    requested = target["pages"]
    if requested != "all" and (type(requested) is not int or not 1 <= requested <= 10):
        raise ValueError("Invalid NMPA index page count")
    seen = {}
    number = 1
    total = None
    while True:
        page = "drugs.html" if number == 1 else f"drugs_{number}.html"
        html = fetch("https://english.nmpa.gov.cn/" + page, lambda response: response.read().decode("utf-8"))
        pagination = re.search(r'id="displaypagenum" totalcount="([0-9]+)" currentpage="([0-9]+)"', html)
        if not pagination or int(pagination[2]) != number:
            raise ValueError("Unexpected NMPA index page")
        page_count = int(pagination[1])
        if page_count < number or (total is not None and total != page_count):
            raise ValueError("NMPA index pagination changed during collection")
        total = page_count
        entries = html.split('<div class="list-text">')[1:]
        if not entries:
            raise ValueError("NMPA index listings missing")
        for entry in entries:
            heading = re.match(r'\s*<h3 class="list-tit"><a href="([0-9]{4}-[0-9]{2}/[0-9]{2}/c_([0-9]+)\.htm)">(.*?)</a></h3>\s*<p class="list-date">([0-9-]+)</p>', entry, re.S)
            if not heading:
                raise ValueError("Unexpected NMPA index entry")
            path, article, title_html, day = heading.groups()
            title = " ".join(unescape(re.sub(r"<[^>]+>", "", title_html)).split())
            if not title:
                raise ValueError("Missing NMPA notice title")
            row = dict(source="NMPA", record=article, subject="国家药监局药品栏目", asset=None,
                       region="中国", category="nmpa-index", field="药品栏目新收录标题（正文待核）",
                       value=title, source_date=date.fromisoformat(day).isoformat(), effective=None,
                       date_label="NMPA 栏目所列日期", url="https://english.nmpa.gov.cn/" + path)
            if article in seen and row != seen[article]:
                raise ValueError("Inconsistent NMPA notice across index pages")
            seen[article] = row
        if number >= (total if requested == "all" else min(requested, total)):
            break
        number += 1
        time.sleep(0.25)
    yield from seen.values()
