"""PDF parsing via PyMuPDF (parser-dev owned).

Provenance contract: 1-based real PDF page numbers, per-page contiguous text,
text blocks with ``[x0, y0, x1, y1]`` bounding boxes in PDF points, detected
tables with rows + caption, and quality flags for scanned/image-only pages.
A single bad page must never abort the document: it becomes an empty page plus
a ``page_errors`` entry in the document meta.
"""
from __future__ import annotations

import re
from pathlib import Path
from statistics import median

import fitz  # PyMuPDF

from hijenny import config
from hijenny.interfaces import ParsedDocument, ParsedPage, ParsedTable, TextBlock
from hijenny.parsers import common

# A page with fewer than this many non-whitespace characters is treated as
# "no extractable text" (scanned / image-only / blank).
_MIN_TEXT_CHARS = 10
_MAX_TABLE_ROWS = 200

_HEADING_MAX_CHARS = 180
_CAPTION_RE = re.compile(r"(?i)^(?:table|tbl|figure|fig\.?)\s*[0-9IVX]+")
_IMAGE_BLOCK_TYPE = 1


def parse_pdf(path: str | Path) -> ParsedDocument:
    """Parse a PDF into a :class:`ParsedDocument`.

    Raises only on document-level failures (unreadable file, encryption); the
    caller (:func:`hijenny.parsers.parse_file`) converts those to a
    ``ParsedDocument.parse_error`` so ingestion can isolate the failure.
    """
    p = Path(path)
    digest = common.sha256_file(p)
    doc = fitz.open(str(p))
    try:
        meta_title = ""
        try:
            meta_title = (doc.metadata or {}).get("title") or ""
            meta_title = " ".join(str(meta_title).split()).strip()
        except Exception:  # noqa: BLE001 - metadata is best-effort
            meta_title = ""
        if doc.needs_pass:
            return ParsedDocument(
                filename=p.name,
                sha256=digest,
                page_count=0,
                title=meta_title or None,
                parse_error="PDF is encrypted/password-protected",
                meta={"format": "pdf", "parser_version": config.PARSER_VERSION, "encrypted": True},
            )

        total_pages = int(doc.page_count)
        limit = min(total_pages, max(1, int(config.MAX_PAGES_PER_DOC)))
        pages: list[ParsedPage] = []
        tables: list[ParsedTable] = []
        scanned_pages: list[int] = []
        empty_pages: list[int] = []
        page_errors: list[dict] = []

        for idx in range(limit):
            page_no = idx + 1
            try:
                page = doc.load_page(idx)
                parsed_page, page_tables, flags = _parse_pdf_page(page, page_no, tables)
                pages.append(parsed_page)
                tables.extend(page_tables)
                if flags.get("scanned"):
                    scanned_pages.append(page_no)
                elif flags.get("empty"):
                    empty_pages.append(page_no)
            except Exception as exc:  # noqa: BLE001 - one bad page must not kill the doc
                page_errors.append({"page": page_no, "error": f"{type(exc).__name__}: {exc}"})
                pages.append(ParsedPage(page_no=page_no, text=""))
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001 - closing is best-effort
            pass

    title = meta_title or common.guess_title(pages)
    all_text = "\n".join(page.text for page in pages[:3])
    quality = common.compute_parse_quality(
        pages,
        scanned_pages=scanned_pages,
        page_errors=page_errors,
        table_count=len(tables),
    )
    meta = {
        "format": "pdf",
        "parser_version": config.PARSER_VERSION,
        "page_count_raw": int(total_pages),
        "truncated": int(total_pages) > limit,
        "table_count": len(tables),
        "scanned_pages": scanned_pages,
        "empty_pages": empty_pages,
        "page_errors": page_errors,
        "parse_quality": quality,
    }
    return ParsedDocument(
        filename=p.name,
        sha256=digest,
        page_count=len(pages),
        pages=pages,
        tables=tables,
        language=common.detect_language(all_text),
        title=title or None,
        meta=meta,
        parse_quality=quality,
    )


def _parse_pdf_page(
    page: "fitz.Page", page_no: int, existing_tables: list[ParsedTable]
) -> tuple[ParsedPage, list[ParsedTable], dict]:
    """Parse one page.  Returns (page, tables, flags); raises on page failure."""
    raw = page.get_text("dict", sort=True)
    blocks: list[TextBlock] = []
    block_sizes: list[float] = []
    font_sizes: list[float] = []
    has_image_block = False

    for block in raw.get("blocks", []):
        if block.get("type") == _IMAGE_BLOCK_TYPE:
            has_image_block = True
            continue
        lines: list[str] = []
        block_max_size = 0.0
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            line_text = "".join(span.get("text", "") for span in spans)
            for span in spans:
                try:
                    size = float(span.get("size", 0.0))
                except (TypeError, ValueError):
                    size = 0.0
                if size > 0:
                    font_sizes.append(size)
                    block_max_size = max(block_max_size, size)
            if line_text.strip():
                lines.append(line_text)
        text = common.join_lines(lines)
        if not text.strip():
            continue
        bbox = common.bbox_from_rect(block.get("bbox") or [])
        blocks.append(TextBlock(text=text, bbox=bbox, block_type="paragraph"))
        block_sizes.append(block_max_size)

    median_size = median(font_sizes) if font_sizes else 0.0
    for block, size in zip(blocks, block_sizes):
        if size >= median_size * 1.15 and len(block.text) <= _HEADING_MAX_CHARS and not block.text.rstrip().endswith((".", ",", ";")):
            block.block_type = "heading"
        elif _CAPTION_RE.match(block.text):
            block.block_type = "caption"

    # --- tables (never fatal) -------------------------------------------------
    page_tables: list[ParsedTable] = []
    try:
        finder = page.find_tables()
        for raw_table in getattr(finder, "tables", []):
            rows = _table_rows(raw_table, page)
            if not rows:
                continue
            table_bbox = common.bbox_from_rect(getattr(raw_table, "bbox", []))
            caption = _caption_for_table(blocks, table_bbox)
            page_tables.append(
                ParsedTable(
                    page=page_no,
                    index=len(existing_tables) + len(page_tables),
                    rows=rows,
                    caption=caption,
                    bbox=table_bbox,
                )
            )
            for block in blocks:
                if common.overlap_ratio(block.bbox, table_bbox) > 0.5:
                    block.block_type = "table_cell"
    except Exception:  # noqa: BLE001 - table detection is best-effort per page
        page_tables = []

    page_text = common.blocks_to_text(blocks)
    width = float(page.rect.width)
    height = float(page.rect.height)
    flags: dict = {}
    if len(page_text.strip()) < _MIN_TEXT_CHARS:
        has_images = has_image_block
        if not has_images:
            try:
                has_images = bool(page.get_images(full=False))
            except Exception:  # noqa: BLE001
                has_images = False
        if not has_images:
            try:
                has_images = len(page.get_drawings()) > 0
            except Exception:  # noqa: BLE001
                has_images = False
        if has_images:
            flags["scanned"] = True
            flags["image_only"] = True
        else:
            flags["empty"] = True

    return (
        ParsedPage(page_no=page_no, text=page_text, blocks=blocks, width=width, height=height),
        page_tables,
        flags,
    )


def _clean_table_rows(raw_rows) -> list[list[str]]:
    """Normalise ``find_tables().extract()`` output (None cells, empty rows)."""
    rows: list[list[str]] = []
    for raw_row in raw_rows or []:
        row = [" ".join(str(cell).split()) if cell is not None else "" for cell in raw_row]
        if any(row):
            rows.append(row)
    return rows[:_MAX_TABLE_ROWS]


def _table_rows(raw_table, page: "fitz.Page") -> list[list[str]]:
    """Build table rows with verbatim cell text.

    PyMuPDF 2.x can split ``42.0`` into ``"420\\n."`` in
    ``table.extract()`` (cell text is rebuilt from glyph fragments).  The
    page's word list is unaffected, so we prefer rebuilding every cell from
    the words whose centres fall inside the cell bbox, and fall back to the
    library extraction when cell geometry is unavailable.
    """
    raw_rows = _clean_table_rows(raw_table.extract())
    try:
        words = page.get_text("words")
        rebuilt = _rebuild_rows_from_words(raw_table, raw_rows, words)
    except Exception:  # noqa: BLE001 - geometry parsing is best-effort
        rebuilt = None
    if rebuilt:
        rebuilt_chars = sum(len("".join(row)) for row in rebuilt)
        raw_chars = sum(len("".join(row)) for row in raw_rows)
        # Accept the rebuild unless it lost most of the cell content (e.g. a
        # scanned/image cell with no extractable words).
        if rebuilt_chars >= max(1, int(raw_chars * 0.6)):
            return rebuilt[:_MAX_TABLE_ROWS]
    return raw_rows


def _rebuild_rows_from_words(raw_table, raw_rows: list[list[str]], words) -> list[list[str]] | None:
    cells = getattr(raw_table, "cells", None)
    rows_n = getattr(raw_table, "row_count", None) or len(raw_rows)
    cols_n = getattr(raw_table, "col_count", None) or max((len(r) for r in raw_rows), default=0)
    if not cells or rows_n <= 0 or cols_n <= 0 or len(cells) != rows_n * cols_n:
        return None
    rows: list[list[str]] = []
    for r in range(rows_n):
        row: list[str] = []
        for c in range(cols_n):
            # PyMuPDF exposes cells column-major: index = column * row_count + row.
            bbox = cells[c * rows_n + r]
            row.append(_cell_text_from_words(words, bbox))
        rows.append(row)
    return [row for row in rows if any(cell.strip() for cell in row)]


def _cell_text_from_words(words, bbox, pad: float = 1.5) -> str:
    """Verbatim cell text: words whose centre lies inside the cell bbox."""
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return ""
    by_line: dict[int, list[tuple[float, str]]] = {}
    for word in words:
        try:
            wx0, wy0, wx1, wy1, text = (float(word[0]), float(word[1]), float(word[2]), float(word[3]), str(word[4]))
        except (IndexError, TypeError, ValueError):
            continue
        cx, cy = (wx0 + wx1) / 2.0, (wy0 + wy1) / 2.0
        if cx < x0 - pad or cx > x1 + pad or cy < y0 - pad or cy > y1 + pad:
            continue
        line_key = int(round(cy / 4.0))
        by_line.setdefault(line_key, []).append((wx0, text))
    if not by_line:
        return ""
    lines = []
    for key in sorted(by_line):
        line_words = [text for _, text in sorted(by_line[key], key=lambda item: item[0])]
        lines.append(" ".join(line_words))
    return "\n".join(lines)


def _caption_for_table(blocks: list[TextBlock], table_bbox: list[float]) -> str | None:
    """Find the nearest 'Table N: ...' caption above or below a table bbox."""
    if len(table_bbox) < 4:
        return None
    tx0, ty0, tx1, ty1 = table_bbox
    best: tuple[float, str] | None = None
    for block in blocks:
        if len(block.bbox) < 4:
            continue
        text = block.text.strip()
        if not _CAPTION_RE.match(text):
            continue
        bx0, by0, bx1, by1 = block.bbox
        horizontal_overlap = min(tx1, bx1) - max(tx0, bx0)
        if horizontal_overlap <= -4:
            continue
        if by1 <= ty0 + 2:  # caption above the table
            gap = ty0 - by1
        elif by0 >= ty1 - 2:  # caption below the table
            gap = by0 - ty1
        else:
            gap = 0.0
        if gap > 120:
            continue
        if best is None or gap < best[0]:
            best = (gap, text)
    return best[1][:300] if best else None
