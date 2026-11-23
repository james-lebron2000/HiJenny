"""XLSX parsing via openpyxl (parser-dev owned).

One sheet == one page (``page_no = sheet index + 1``, sheet name kept in the
page text/meta).  Rows are rendered as space-joined cell text so numeric facts
stay contiguous in page text, and also exposed as :class:`ParsedTable` rows.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl

from assetdd import config
from assetdd.interfaces import ParsedDocument, ParsedPage, ParsedTable, TextBlock
from assetdd.parsers import common

_MAX_ROWS_PER_SHEET = 3000
_MAX_COLS = 60
_MAX_CELLS_TOTAL = 60000
_ROW_HEIGHT = 15.0
_PAGE_WIDTH = 612.0
_PAGE_HEIGHT = 792.0


def parse_xlsx(path: str | Path) -> ParsedDocument:
    p = Path(path)
    digest = common.sha256_file(p)
    workbook = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    try:
        pages: list[ParsedPage] = []
        tables: list[ParsedTable] = []
        sheet_meta: list[dict] = []
        body_text_parts: list[str] = []
        total_cells = 0
        truncated = False

        for sheet_idx, sheet in enumerate(workbook.worksheets):
            page_no = sheet_idx + 1
            sheet_title = str(sheet.title or f"Sheet{page_no}")
            rows: list[list[str]] = []
            row_texts: list[str] = []
            for row_idx, row in enumerate(sheet.iter_rows(values_only=True)):
                if row_idx >= _MAX_ROWS_PER_SHEET:
                    truncated = True
                    break
                cells = [common.format_cell(value) for value in list(row)[:_MAX_COLS]]
                while cells and not cells[-1]:
                    cells.pop()
                if not any(cells):
                    continue
                rows.append(cells)
                row_texts.append(" ".join(cell for cell in cells if cell))
                total_cells += len(cells)
                if total_cells > _MAX_CELLS_TOTAL:
                    truncated = True
                    break

            blocks: list[TextBlock] = [
                TextBlock(text=sheet_title, bbox=[0.0, 0.0, _PAGE_WIDTH, _ROW_HEIGHT], block_type="heading")
            ]
            for row_idx, text in enumerate(row_texts):
                y0 = (row_idx + 1) * _ROW_HEIGHT
                blocks.append(
                    TextBlock(
                        text=text,
                        bbox=[0.0, y0, _PAGE_WIDTH, y0 + _ROW_HEIGHT],
                        block_type="table_cell",
                    )
                )
            page_text = common.blocks_to_text(blocks)
            pages.append(
                ParsedPage(
                    page_no=page_no, text=page_text, blocks=blocks, width=_PAGE_WIDTH, height=_PAGE_HEIGHT
                )
            )
            if rows:
                tables.append(
                    ParsedTable(page=page_no, index=len(tables), rows=rows, caption=sheet_title, bbox=[])
                )
            sheet_meta.append(
                {
                    "page": page_no,
                    "title": sheet_title,
                    "rows": len(rows),
                    "cols": max((len(row) for row in rows), default=0),
                }
            )
            body_text_parts.extend(row_texts)
            if total_cells > _MAX_CELLS_TOTAL:
                break
    finally:
        try:
            workbook.close()
        except Exception:  # noqa: BLE001 - closing is best-effort
            pass

    if not pages:
        pages.append(ParsedPage(page_no=1, text="", blocks=[], width=_PAGE_WIDTH, height=_PAGE_HEIGHT))

    body_text = "\n".join(body_text_parts)
    quality = common.compute_parse_quality(pages, table_count=len(tables))
    meta = {
        "format": "xlsx",
        "parser_version": config.PARSER_VERSION,
        "page_count_raw": len(pages),
        "table_count": len(tables),
        "sheets": sheet_meta,
        "truncated": truncated,
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
        title=None,
        meta=meta,
        parse_quality=quality,
    )
