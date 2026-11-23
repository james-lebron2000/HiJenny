"""Parser / extractor data contracts (frozen, Lead-owned).

Parsers turn a file into ParsedDocument.  Extractors consume pages and return
EvidenceDraft objects; they never write to the database themselves (the store
assigns IDs, fingerprints and comparability keys).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    text: str
    bbox: list[float] = field(default_factory=list)   # [x0, y0, x1, y1] in PDF points
    block_type: str = "paragraph"                     # paragraph | table_cell | caption | heading


@dataclass
class ParsedTable:
    page: int
    index: int
    rows: list[list[str]] = field(default_factory=list)
    caption: str | None = None
    bbox: list[float] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"Table {self.index + 1}"


@dataclass
class ParsedPage:
    page_no: int
    text: str
    blocks: list[TextBlock] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0


@dataclass
class ParsedDocument:
    filename: str
    sha256: str
    page_count: int
    pages: list[ParsedPage] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    language: str = "en"
    title: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    parse_quality: float = 1.0
    parse_error: str | None = None


def quote_from_page(page_text: str, start: int, end: int, pad: int = 160) -> str:
    """Deterministic quote builder: context window around a match."""
    left = max(0, start - pad)
    right = min(len(page_text), end + pad)
    snippet = page_text[left:right].strip()
    return " ".join(snippet.split())
