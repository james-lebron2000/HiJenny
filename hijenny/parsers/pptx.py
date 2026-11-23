"""PPTX parsing via python-pptx (parser-dev owned).

One slide == one page (page_no = 1-based slide number).  Slide text, table rows
and speaker notes are captured as text blocks; tables are also exposed as
:class:`ParsedTable` with the slide title as caption.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation as _load_pptx

from hijenny import config
from hijenny.interfaces import ParsedDocument, ParsedPage, ParsedTable, TextBlock
from hijenny.parsers import common

_EMU_PER_POINT = 12700.0


def parse_pptx(path: str | Path) -> ParsedDocument:
    p = Path(path)
    digest = common.sha256_file(p)
    prs = _load_pptx(str(p))

    page_width = float(prs.slide_width) / _EMU_PER_POINT if prs.slide_width else 720.0
    page_height = float(prs.slide_height) / _EMU_PER_POINT if prs.slide_height else 540.0

    pages: list[ParsedPage] = []
    tables: list[ParsedTable] = []
    title: str | None = None
    body_text_parts: list[str] = []

    for slide_idx, slide in enumerate(prs.slides):
        page_no = slide_idx + 1
        blocks: list[TextBlock] = []

        title_shape = None
        try:
            title_shape = slide.shapes.title
        except Exception:  # noqa: BLE001 - malformed placeholder
            title_shape = None
        slide_title = ""
        if title_shape is not None and getattr(title_shape, "has_text_frame", False):
            slide_title = " ".join(title_shape.text_frame.text.split())

        shapes = list(slide.shapes)
        if title_shape is not None:
            shapes = [title_shape] + [shape for shape in shapes if shape.shape_id != title_shape.shape_id]

        for shape in shapes:
            if getattr(shape, "has_table", False):
                rows = _shape_table_rows(shape)
                if rows:
                    tables.append(
                        ParsedTable(page=page_no, index=len(tables), rows=rows, caption=slide_title or None, bbox=[])
                    )
                    blocks.extend(common.rows_to_blocks(rows))
            elif getattr(shape, "has_text_frame", False):
                is_title = shape is title_shape
                for paragraph in shape.text_frame.paragraphs:
                    text = " ".join((paragraph.text or "").split())
                    if text:
                        blocks.append(
                            TextBlock(text=text, bbox=[], block_type="heading" if is_title else "paragraph")
                        )
                        body_text_parts.append(text)

        notes = ""
        try:
            if slide.has_notes_slide:
                notes = " ".join((slide.notes_slide.notes_text_frame.text or "").split())
        except Exception:  # noqa: BLE001 - notes are optional
            notes = ""
        if notes:
            blocks.append(TextBlock(text=notes, bbox=[], block_type="paragraph"))
            body_text_parts.append(notes)

        pages.append(
            ParsedPage(
                page_no=page_no,
                text=common.blocks_to_text(blocks),
                blocks=blocks,
                width=page_width,
                height=page_height,
            )
        )
        if title is None and slide_title:
            title = slide_title

    if title is None:
        try:
            title = " ".join((prs.core_properties.title or "").split()) or None
        except Exception:  # noqa: BLE001
            title = None
    if title is None:
        title = common.guess_title(pages)

    body_text = "\n".join(body_text_parts)
    quality = common.compute_parse_quality(pages, table_count=len(tables))
    meta = {
        "format": "pptx",
        "parser_version": config.PARSER_VERSION,
        "page_count_raw": len(pages),
        "slide_count": len(pages),
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
        title=title[:200] if title else None,
        meta=meta,
        parse_quality=quality,
    )


def _shape_table_rows(shape) -> list[list[str]]:
    rows: list[list[str]] = []
    try:
        raw_rows = shape.table.rows
    except Exception:  # noqa: BLE001
        return rows
    for row in raw_rows:
        cells = [" ".join((cell.text or "").split()) for cell in row.cells]
        if any(cells):
            rows.append(cells)
    return rows
