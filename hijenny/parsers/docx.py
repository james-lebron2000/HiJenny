"""DOCX parsing via python-docx (parser-dev owned).

DOCX has no intrinsic page numbers, so pages are reconstructed deterministically
from explicit page breaks (``w:br type="page"`` / ``w:lastRenderedPageBreak`` /
``pageBreakBefore``) and capped page-like sections of ~4000 characters.  Each
table row also becomes a text block so table facts stay in contiguous page text.
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document as _load_docx
from docx.oxml.ns import qn
from docx.table import Table as _DocxTable
from docx.text.paragraph import Paragraph as _DocxParagraph

from hijenny import config
from hijenny.interfaces import ParsedDocument, ParsedPage, ParsedTable, TextBlock
from hijenny.parsers import common

_PAGE_CHAR_LIMIT = 4000
_TABLE_CAPTION_RE = re.compile(r"(?i)\b(?:table|tbl)\b")
_PAGE_WIDTH = 612.0
_PAGE_HEIGHT = 792.0


def parse_docx(path: str | Path) -> ParsedDocument:
    p = Path(path)
    digest = common.sha256_file(p)
    document = _load_docx(str(p))

    pages: list[ParsedPage] = []
    tables: list[ParsedTable] = []
    blocks: list[TextBlock] = []
    page_no = 1
    page_chars = 0
    recent: list[str] = []
    title_candidates: list[str] = []
    body_text_parts: list[str] = []

    def flush_page() -> None:
        nonlocal blocks, page_chars
        if not blocks:
            return
        pages.append(
            ParsedPage(
                page_no=page_no,
                text=common.blocks_to_text(blocks),
                blocks=blocks,
                width=_PAGE_WIDTH,
                height=_PAGE_HEIGHT,
            )
        )
        blocks = []
        page_chars = 0

    def start_new_page() -> None:
        nonlocal page_no
        flush_page()
        page_no += 1

    def add_text(text: str, block_type: str = "paragraph") -> None:
        nonlocal page_chars
        if page_chars and page_chars + len(text) > _PAGE_CHAR_LIMIT:
            start_new_page()
        blocks.append(TextBlock(text=text, bbox=[], block_type=block_type))
        page_chars += len(text)
        body_text_parts.append(text)

    for item in _iter_block_items(document):
        if isinstance(item, _DocxParagraph):
            text = " ".join((item.text or "").split())
            style = (item.style.name or "").lower() if item.style is not None else ""
            if _page_break_before(item) and (blocks or pages):
                start_new_page()
            if text:
                is_heading = style.startswith(("heading", "title"))
                add_text(text, "heading" if is_heading else "paragraph")
                recent.append(text)
                del recent[:-5]
                if (style.startswith("title") or style.startswith("heading 1")) and len(text) <= 200:
                    title_candidates.append(text)
            if _paragraph_has_page_break(item):
                start_new_page()
        else:
            rows = _table_rows(item)
            if not rows:
                continue
            caption = _caption_from_recent(recent)
            tables.append(  # type: ignore[arg-type]
                ParsedTable(page=page_no, index=len(tables), rows=rows, caption=caption, bbox=[])
            )
            for row in rows:
                row_text = " ".join(cell for cell in row if cell)
                if row_text:
                    add_text(row_text, "table_cell")
            recent.append(caption or "table")

    flush_page()

    if not pages:
        pages.append(ParsedPage(page_no=1, text="", blocks=[], width=_PAGE_WIDTH, height=_PAGE_HEIGHT))

    title = title_candidates[0] if title_candidates else common.guess_title(pages)
    if title is not None:
        title = title[:200]
    body_text = "\n".join(body_text_parts)
    quality = common.compute_parse_quality(pages, table_count=len(tables))
    meta = {
        "format": "docx",
        "parser_version": config.PARSER_VERSION,
        "page_count_raw": len(pages),
        "table_count": len(tables),
        "scanned_pages": [],
        "empty_pages": [page.page_no for page in pages if not page.text.strip()],
        "page_errors": [],
    }
    return ParsedDocument(
        filename=p.name,
        sha256=digest,
        page_count=len(pages),
        pages=pages,
        tables=tables,
        language=common.detect_language(body_text),
        title=title or None,
        meta=meta,
        parse_quality=quality,
    )


def _iter_block_items(document):
    """Yield paragraphs and tables in document order."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield _DocxParagraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield _DocxTable(child, document)


def _paragraph_has_page_break(paragraph) -> bool:
    element = paragraph._p
    for br in element.iter(qn("w:br")):
        if br.get(qn("w:type")) == "page":
            return True
    for _ in element.iter(qn("w:lastRenderedPageBreak")):
        return True
    return False


def _page_break_before(paragraph) -> bool:
    p_pr = paragraph._p.pPr
    if p_pr is None:
        return False
    node = p_pr.find(qn("w:pageBreakBefore"))
    if node is None:
        return False
    return node.get(qn("w:val")) not in {"0", "false", "off"}


def _table_rows(table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [" ".join((cell.text or "").split()) for cell in row.cells]
        if any(cells):
            rows.append(cells)
    return rows


def _caption_from_recent(recent: list[str]) -> str | None:
    for text in reversed(recent):
        if _TABLE_CAPTION_RE.search(text):
            return text[:300]
    return None
