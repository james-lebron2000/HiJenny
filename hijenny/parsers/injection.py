"""Prompt-injection scanning of parsed page text (parser-dev owned).

Seller packages are untrusted input.  A document may contain text that tries to
steer an LLM extraction agent ("ignore previous instructions", "reveal the
system prompt", ...).  We never act on that text: we flag it and keep parsing
normally.  Flags are persisted on the document meta as::

    {"injection_flags": [{"page": 3, "pattern": "ignore_previous_instructions",
                          "excerpt": "..."}]}

Extraction/gap agents consume that list and must treat the text as *data*.
"""
from __future__ import annotations

import re
from typing import Sequence

from hijenny.interfaces import ParsedPage, quote_from_page

# (flag id, case-insensitive regex).  Order is stable and defines output order.
INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "ignore_previous_instructions",
        r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|directions?|rules?)",
    ),
    (
        "system_override",
        r"system\s+override",
    ),
    (
        "disregard_above",
        r"disregard\s+(?:all\s+)?(?:the\s+)?(?:above|previous|prior|earlier)",
    ),
    (
        "you_are_now",
        r"you\s+are\s+now",
    ),
    (
        "reveal_system_prompt",
        r"reveal\s+(?:the\s+)?(?:system\s+prompt|hidden\s+prompt|prompt)",
    ),
    (
        "report_fabricated_value",
        r"reports?\s+[\s\S]{0,60}?\s+as\s+\d+(?:\.\d+)?\s*%",
    ),
    (
        "new_instructions",
        r"new\s+instructions?\s*[:\-]",
    ),
    (
        "developer_mode",
        r"(?:developer|god|admin)\s+mode\b",
    ),
)

_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (flag, re.compile(pattern, re.IGNORECASE)) for flag, pattern in INJECTION_PATTERNS
)

MAX_FLAGS_PER_DOCUMENT = 25
_EXCERPT_PAD = 120


def scan_text(text: str) -> list[str]:
    """Return the sorted-by-pattern flag ids found in *text* (deduplicated)."""
    if not text:
        return []
    return [flag for flag, regex in _COMPILED if regex.search(text)]


def scan_pages(pages: Sequence[ParsedPage], limit: int = MAX_FLAGS_PER_DOCUMENT) -> list[dict]:
    """Scan page texts and return structured injection flags.

    Each item: ``{"page": 1-based page, "pattern": flag id, "excerpt": context}``.
    Never raises and never mutates/removes page text — the page is still parsed
    and quotable; this is a data-quality signal, not an instruction.
    """
    flags: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for page in pages:
        text = page.text or ""
        if not text:
            continue
        for flag, regex in _COMPILED:
            match = regex.search(text)
            if match is None:
                continue
            key = (int(page.page_no), flag)
            if key in seen:
                continue
            seen.add(key)
            flags.append(
                {
                    "page": int(page.page_no),
                    "pattern": flag,
                    "excerpt": quote_from_page(text, match.start(), match.end(), pad=_EXCERPT_PAD),
                }
            )
            if len(flags) >= limit:
                return flags
    return flags
