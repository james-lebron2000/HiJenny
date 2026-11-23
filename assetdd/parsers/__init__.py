"""Format-agnostic document parsing (parser-dev owned).

Public API::

    detect_format(path)      -> "pdf" | "docx" | "pptx" | "xlsx" | None
    parse_file(path)         -> ParsedDocument (never raises for supported formats;
                                hard failures are reported via .parse_error)
    parse_pdf / parse_docx / parse_pptx / parse_xlsx

Every parser returns the frozen dataclasses from :mod:`assetdd.interfaces`.
"""
from __future__ import annotations

from pathlib import Path

from assetdd import config
from assetdd.interfaces import ParsedDocument
from assetdd.parsers.common import SUPPORTED_FORMATS, detect_format, sha256_file
from assetdd.parsers.docx import parse_docx
from assetdd.parsers.pdf import parse_pdf
from assetdd.parsers.pptx import parse_pptx
from assetdd.parsers.xlsx import parse_xlsx

__all__ = [
    "SUPPORTED_FORMATS",
    "UnsupportedFormatError",
    "detect_format",
    "parse_docx",
    "parse_file",
    "parse_pdf",
    "parse_pptx",
    "parse_xlsx",
    "sha256_file",
    "PARSERS",
]


class UnsupportedFormatError(ValueError):
    """Raised by :func:`parse_file` for formats we cannot parse."""


PARSERS = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "pptx": parse_pptx,
    "xlsx": parse_xlsx,
}


def parse_file(path: str | Path) -> ParsedDocument:
    """Dispatch to the right parser based on real file content.

    Unsupported formats raise :class:`UnsupportedFormatError` (caller error);
    any parser crash for a supported format is converted into a
    ``ParsedDocument`` carrying ``parse_error`` so a single corrupt file in a
    seller package cannot abort the INGEST stage.
    """
    p = Path(path)
    fmt = detect_format(p)
    if fmt is None:
        raise UnsupportedFormatError(f"unsupported document format: {p.name}")
    try:
        return PARSERS[fmt](p)
    except Exception as exc:  # noqa: BLE001 - failure isolation happens at ingestion
        try:
            digest = sha256_file(p)
        except OSError:
            digest = ""
        return ParsedDocument(
            filename=p.name,
            sha256=digest,
            page_count=0,
            pages=[],
            tables=[],
            language="en",
            title=None,
            meta={"format": fmt, "parser_version": config.PARSER_VERSION, "page_count_raw": 0},
            parse_quality=0.0,
            parse_error=f"{type(exc).__name__}: {exc}",
        )
