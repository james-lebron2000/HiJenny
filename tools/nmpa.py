"""Recheck reviewed approval notices on the English NMPA site."""
import hashlib
import re
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
