"""Shared parser helpers (parser-dev owned).

No third-party imports here beyond the stdlib so that the helpers stay cheap to
import from every format parser.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from hijenny.interfaces import ParsedPage, TextBlock

SUPPORTED_FORMATS: tuple[str, ...] = ("pdf", "docx", "pptx", "xlsx")

_EXT_FORMAT: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
}

# Content-type markers inside an OOXML zip archive.
_ZIP_MARKERS: tuple[tuple[str, str], ...] = (
    ("wordprocessingml", "docx"),
    ("presentationml", "pptx"),
    ("spreadsheetml", "xlsx"),
)


def detect_format(path: str | Path) -> str | None:
    """Return the canonical format name for *path*, or ``None`` if unsupported.

    Detection is extension-first (cheap and deterministic) and falls back to
    magic-byte sniffing so that a mis-named upload can still be parsed.  PDF is
    sniffed by its ``%PDF`` header; OOXML is sniffed by the zip content types.
    """
    p = Path(path)
    fmt = _EXT_FORMAT.get(p.suffix.lower())
    if fmt:
        return fmt
    try:
        with p.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(p) as zf:
                try:
                    content_types = zf.read("[Content_Types].xml").decode("utf-8", "ignore").lower()
                except KeyError:
                    return None
        except (zipfile.BadZipFile, OSError):
            return None
        for marker, name in _ZIP_MARKERS:
            if marker in content_types:
                return name
    return None


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file (used for dedup and provenance)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_line(line: str) -> str:
    """NFKC-normalise and strip trailing whitespace from one extracted line."""
    return unicodedata.normalize("NFKC", line).replace("\u00a0", " ").rstrip()


def join_lines(lines: Iterable[str]) -> str:
    """Join extracted lines, re-joining words broken by end-of-line hyphenation.

    This keeps the resulting page text contiguous normal text so that verbatim
    quotes (whitespace-normalised substrings) can be matched reliably.
    """
    out: list[str] = []
    for raw in lines:
        line = clean_line(raw)
        if not line.strip():
            continue
        if out and out[-1].endswith("-") and len(out[-1]) > 1 and line[:1].islower():
            out[-1] = out[-1][:-1] + line.lstrip()
        else:
            out.append(line.strip())
    return "\n".join(out)


def blocks_to_text(blocks: Sequence[TextBlock]) -> str:
    """Concatenate block text with newlines (page text = contiguous block text)."""
    return "\n".join(b.text.strip() for b in blocks if b.text and b.text.strip())


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def detect_language(text: str) -> str:
    """Very small heuristic language tag: ``en`` unless CJK characters dominate."""
    if not text:
        return "en"
    cjk = len(_CJK_RE.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk >= 20 and cjk > latin * 0.15:
        return "zh"
    return "en"


def compute_parse_quality(
    pages: Sequence[ParsedPage],
    scanned_pages: Sequence[int] = (),
    page_errors: Sequence[Any] = (),
    table_count: int = 0,
) -> float:
    """Parse quality in ``[0, 1]``: share of pages with extractable text.

    Image-only/scanned pages are implicitly low quality because they carry no
    text; hard page failures and table extraction are folded in as small
    penalties/bonuses.  Deterministic and no OCR involved.
    """
    total = len(pages)
    if total == 0:
        return 0.0
    textful = 0
    sparse = 0
    for page in pages:
        n = len(page.text.strip())
        if n >= 32:
            textful += 1
        elif n > 0:
            sparse += 1
    base = (textful + 0.5 * sparse) / total
    if page_errors:
        base *= max(0.0, 1.0 - 0.5 * len(page_errors) / total)
    if table_count and base > 0:
        base = min(1.0, base + 0.02)
    return round(max(0.0, min(1.0, base)), 3)


def first_pages_text(pages: Sequence[ParsedPage], n_pages: int = 2, max_chars: int = 8000) -> str:
    """Concatenated text of the leading pages, for classification/version hints."""
    parts: list[str] = []
    for page in pages[:n_pages]:
        if page.text:
            parts.append(page.text)
    return "\n".join(parts)[:max_chars]


def guess_title(pages: Sequence[ParsedPage]) -> str | None:
    """Best-effort document title from the first page's blocks."""
    for page in pages[:1]:
        for block in page.blocks:
            text = block.text.strip()
            if block.block_type == "heading" and 2 <= len(text) <= 200:
                return text
        for block in page.blocks:
            text = block.text.strip()
            if 2 <= len(text) <= 200 and not text.lower().startswith(("table", "figure")):
                return text
    return None


def format_cell(value: Any) -> str:
    """Render a spreadsheet/presentation cell deterministically as text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value)}"
        return f"{value:.10g}"
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            return str(value)
    return str(value).strip()


def rows_to_blocks(rows: Sequence[Sequence[str]], block_type: str = "table_cell") -> list[TextBlock]:
    """One text block per non-empty table row; cells joined by spaces.

    Cell text is joined with a space (not ``|``) so that ``ORR 42.0`` style
    facts stay contiguous in page text for downstream extraction; the structured
    rows are still available on :class:`ParsedTable`.
    """
    blocks: list[TextBlock] = []
    for row in rows:
        text = " ".join(cell for cell in row if cell and str(cell).strip())
        if text:
            blocks.append(TextBlock(text=text, bbox=[], block_type=block_type))
    return blocks


def bbox_from_rect(rect: Any) -> list[float]:
    """Convert a MuPDF rect-like object to a plain ``[x0, y0, x1, y1]`` list."""
    try:
        x0, y0, x1, y1 = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    except (TypeError, ValueError, IndexError):
        return []
    return [x0, y0, x1, y1]


def bbox_union(boxes: Iterable[Sequence[float]]) -> list[float]:
    xs: list[float] = []
    ys: list[float] = []
    for box in boxes:
        if len(box) < 4:
            continue
        xs.extend((float(box[0]), float(box[2])))
        ys.extend((float(box[1]), float(box[3])))
    if not xs:
        return []
    return [min(xs), min(ys), max(xs), max(ys)]


def overlap_ratio(inner: Sequence[float], outer: Sequence[float]) -> float:
    """Fraction of ``inner`` area covered by ``outer`` (0 when either is empty)."""
    if len(inner) < 4 or len(outer) < 4:
        return 0.0
    ix0, iy0, ix1, iy1 = (float(v) for v in inner[:4])
    ox0, oy0, ox1, oy1 = (float(v) for v in outer[:4])
    area = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if area <= 0:
        return 0.0
    w = max(0.0, min(ix1, ox1) - max(ix0, ox0))
    h = max(0.0, min(iy1, oy1) - max(iy0, oy0))
    return (w * h) / area
