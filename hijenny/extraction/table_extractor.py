"""Table-first evidence extraction.

The parser flattens every detected table into a run of ``table_cell`` blocks,
one block per table row, with the row's cells separated by ``\n`` (bbox covers
the row band).  XLSX rows are additionally space-joined into a single cell.
This module reconstructs those rows, maps the columns heuristically and emits
one ``EvidenceDraft`` per row/arm with:

* the table label/caption preserved on the draft,
* a verbatim quote built from the cited page text (cells appear in page text in
  the same order),
* dimensions taken from the same row/caption/cell (population, arm, cutoff,
  assessment method), never from another table.

Only facts whose value cell could be located verbatim on the page are emitted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Sequence

from hijenny.extraction.common import (
    DocumentContext,
    DraftCollector,
    block_bbox,
    block_text,
    block_type,
    canonical_endpoint_def,
    detect_method,
    field_category,
    find_cutoffs,
    make_draft,
    nearest_population,
    normalize_ws,
)
from hijenny.schemas import (
    EvidenceCategory,
    EvidenceDraft,
    FieldName,
    normalize_date,
    normalize_population,
)

_CAPTION_RE = re.compile(r"^(?:Table|Figure)\s*[0-9IVX]+", re.IGNORECASE)
_NUM_RE = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)")
_PCT_CELL_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_CI_CELL_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d{1,3}(?:\.\d+)?)")

_PERCENT_FIELDS = {
    FieldName.ORR,
    FieldName.DCR,
    FieldName.CR,
    FieldName.PR,
    FieldName.SD,
    FieldName.PD,
    FieldName.ANY_AE,
    FieldName.GRADE3_AE,
    FieldName.TREATMENT_RELATED_AE,
    FieldName.SAE,
    FieldName.TREATMENT_RELATED_DEATH,
    FieldName.DOSE_REDUCTION,
    FieldName.DOSE_INTERRUPTION,
    FieldName.DISCONTINUATION,
    FieldName.AESI,
    FieldName.MALE_PCT,
}
_TIME_FIELDS = {FieldName.DOR, FieldName.PFS, FieldName.OS, FieldName.FOLLOW_UP, FieldName.TTR}
_COUNT_FIELDS = {
    FieldName.SAMPLE_SIZE,
    FieldName.N_ITT,
    FieldName.N_SAFETY,
    FieldName.N_FAS,
    FieldName.N_PP,
}
_RATIO_FIELDS = {FieldName.HR}

# Ordered endpoint-label lexicon (labels in the wild -> canonical FieldName).
_ENDPOINT_LABELS: tuple[tuple[re.Pattern[str], FieldName], ...] = (
    (re.compile(r"\bobjective\s+response\s+rate\b|\boverall\s+response\s+rate\b|\bORR\b", re.I), FieldName.ORR),
    (re.compile(r"\bdisease\s+control\s+rate\b|\bDCR\b", re.I), FieldName.DCR),
    (re.compile(r"\bduration\s+of\s+response\b|\bDoR\b|\bDOR\b", re.I), FieldName.DOR),
    (re.compile(r"\bprogression[-\s]free\s+survival\b|\bPFS\b", re.I), FieldName.PFS),
    (re.compile(r"\boverall\s+survival\b|\bOS\b", re.I), FieldName.OS),
    (re.compile(r"\bhazard\s+ratio\b|\bHR\b", re.I), FieldName.HR),
    (re.compile(r"\bcomplete\s+response(?:\s+rate)?\b|\bCR\b", re.I), FieldName.CR),
    (re.compile(r"\bpartial\s+response(?:\s+rate)?\b|\bPR\b", re.I), FieldName.PR),
    (re.compile(r"\bstable\s+disease\b|\bSD\b", re.I), FieldName.SD),
    (re.compile(r"\bprogressive\s+disease\b|\bPD\b(?![- ]?L)", re.I), FieldName.PD),
    (re.compile(r"\bmedian\s+follow[-\s]up\b|\bfollow[-\s]up\b", re.I), FieldName.FOLLOW_UP),
    (re.compile(r"\bany[-\s]grade\s+(?:adverse\s+events?|AEs?|TEAEs?)\b|\bTEAEs?\b", re.I), FieldName.ANY_AE),
    (re.compile(r"\bgrade\s*(?:>=|≥|>)?\s*(?:3|III)\b[^.]{0,40}?(?:adverse|AE)", re.I), FieldName.GRADE3_AE),
    (re.compile(r"\bserious\s+adverse\s+events?\b|\bserious\s+AEs?\b|\bSAEs?\b", re.I), FieldName.SAE),
    (
        re.compile(r"\btreatment[-\s]related\s+(?:adverse\s+events?|AEs?)\b|\bTRAEs?\b", re.I),
        FieldName.TREATMENT_RELATED_AE,
    ),
    (re.compile(r"\btreatment[-\s]related\s+deaths?\b|\bfatal\s+(?:adverse\s+event|AE)s?\b", re.I), FieldName.TREATMENT_RELATED_DEATH),
    (re.compile(r"\bdose\s+reductions?\b", re.I), FieldName.DOSE_REDUCTION),
    (re.compile(r"\bdose\s+interruptions?\b", re.I), FieldName.DOSE_INTERRUPTION),
    (re.compile(r"\bdiscontinu\w*[^.]{0,40}?(?:adverse|AEs?|toxicit)", re.I), FieldName.DISCONTINUATION),
    (re.compile(r"\badverse\s+events?\s+of\s+special\s+interest\b|\bAESIs?\b", re.I), FieldName.AESI),
)

_POPULATION_COUNT_LABELS: tuple[tuple[re.Pattern[str], FieldName], ...] = (
    (re.compile(r"\bITT\s+(?:population|set)\b", re.I), FieldName.N_ITT),
    (re.compile(r"\bsafety\s+(?:population|set)\b", re.I), FieldName.N_SAFETY),
    (re.compile(r"\bFAS\s+(?:population|set)\b|\bfull\s+analysis\s+set\b", re.I), FieldName.N_FAS),
    (re.compile(r"\b(?:per[-\s]protocol|PP)\s+(?:population|set)\b", re.I), FieldName.N_PP),
)

_HEADER_ENDPOINT_COLS = re.compile(r"\b(?:endpoint|end\s+point|parameter|response|category|characteristic|metric|item|event|term)\b", re.I)
_HEADER_VALUE_COLS = re.compile(r"\b(?:value|percent|%|rate|result|estimate|median)\b", re.I)
_HEADER_N_COLS = re.compile(r"^(?:n|number|patients|subjects|count)$", re.I)
_HEADER_CI_COLS = re.compile(r"\b(?:ci|confidence|95\s*%)\b", re.I)
_HEADER_POP_COLS = re.compile(r"\b(?:population|analysis\s+set)\b", re.I)
_HEADER_ARM_COLS = re.compile(r"\b(?:arm|group)\b", re.I)
_HEADER_CUTOFF_COLS = re.compile(r"\b(?:cut\s*-?\s*off|as\s+of)\b", re.I)
_HEADER_METHOD_COLS = re.compile(r"\b(?:assessment|method|criteria)\b", re.I)


@dataclass
class RecoveredTable:
    page: int
    caption: str | None = None
    rows: list[list[str]] = field(default_factory=list)
    index: int = 0

    @property
    def label(self) -> str:
        if self.caption:
            return normalize_ws(self.caption)[:200]
        return f"Table {self.index + 1}"


# ---------------------------------------------------------------------------
# Table recovery from persisted page blocks
# ---------------------------------------------------------------------------
def tables_from_blocks(page_no: int, blocks: Sequence) -> list[RecoveredTable]:
    """Reconstruct tables from ``table_cell`` blocks (one block == one row)."""
    tables: list[RecoveredTable] = []
    rows: list[list[str]] = []
    pending_caption: str | None = None
    counter = 0

    def flush(caption: str | None) -> None:
        nonlocal rows, counter
        if rows:
            tables.append(RecoveredTable(page=page_no, caption=caption, rows=list(rows), index=counter))
            counter += 1
        rows = []

    for block in blocks:
        text = block_text(block)
        if not text.strip():
            continue
        btype = block_type(block)
        if btype == "table_cell":
            cells = [normalize_ws(part) for part in text.split("\n")]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(cells)
            continue

        lines = [normalize_ws(part) for part in text.split("\n")]
        lines = [line for line in lines if line]
        if not lines:
            continue
        caption_index = next(
            (idx for idx, line in enumerate(lines) if _CAPTION_RE.match(line)), None
        )
        if btype == "heading":
            flush(pending_caption)
            pending_caption = lines[0][:300]
            continue
        if caption_index is not None:
            prefix = lines[:caption_index]
            if not rows:
                # Caption sits above its table: remember it for the next rows.
                pending_caption = lines[caption_index][:300]
                continue
            if prefix and len(prefix) >= 2 and any(any(ch.isdigit() for ch in cell) for cell in prefix):
                # A trailing data row was merged into the caption paragraph.
                rows.append(prefix)
            flush(lines[caption_index][:300])
            continue
        # Ordinary paragraph: closes any open table (keeps a pending caption).
        if rows:
            flush(pending_caption)
        elif _CAPTION_RE.match(lines[0]):
            pending_caption = lines[0][:300]
    flush(pending_caption)
    return tables


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def endpoint_field(label: str) -> FieldName | None:
    if not label:
        return None
    for pattern, field_name in _ENDPOINT_LABELS:
        if pattern.search(label):
            return field_name
    return None


def _population_count_field(label: str) -> FieldName | None:
    if not label:
        return None
    for pattern, field_name in _POPULATION_COUNT_LABELS:
        if pattern.search(label):
            return field_name
    return None


def _numbers_in(text: str) -> list[tuple[float, int, int]]:
    out: list[tuple[float, int, int]] = []
    for match in _NUM_RE.finditer(text):
        try:
            out.append((float(match.group(1).replace(",", "")), match.start(), match.end()))
        except ValueError:
            continue
    return out


def _field_kind(field_name: FieldName) -> str:
    if field_name in _PERCENT_FIELDS:
        return "percent"
    if field_name in _TIME_FIELDS:
        return "months"
    if field_name in _COUNT_FIELDS:
        return "count"
    if field_name in _RATIO_FIELDS:
        return "ratio"
    return "text"


def _unit_for_field(field_name: FieldName, cell_text: str) -> str | None:
    kind = _field_kind(field_name)
    if kind == "percent":
        return "%"
    if kind == "months":
        lowered = cell_text.lower()
        if "week" in lowered:
            return "weeks"
        if "day" in lowered:
            return "days"
        return "months"
    if kind == "count":
        return "patients"
    if kind == "ratio":
        return "ratio"
    return None


def _parse_value_cell(cell_text: str, field_name: FieldName) -> tuple[float, str | None] | None:
    """Parse a table value cell into (number, raw text) for the field kind."""
    text = normalize_ws(cell_text)
    if not text or text.upper() in {"NR", "NA", "N/A", "-", "—"}:
        return None
    kind = _field_kind(field_name)
    if kind == "percent":
        match = _PCT_CELL_RE.search(text)
        if match:
            try:
                value = float(match.group(1))
            except ValueError:
                return None
            if 0.0 <= value <= 100.0:
                return value, match.group(0)
        return None
    if kind == "months":
        match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:months?|mo|weeks?|days?)\b", text, re.I)
        if match:
            try:
                value = float(match.group(1))
            except ValueError:
                return None
            if 0.0 < value <= 600.0:
                return value, match.group(0)
        return None
    numbers = _numbers_in(text)
    if not numbers:
        return None
    if kind == "ratio":
        for value, _start, _end in numbers:
            if 0.0 < value <= 10.0:
                return value, f"{value:g}"
        return None
    if kind == "count":
        for value, _start, _end in numbers:
            if 0.0 < value <= 1_000_000 and abs(value - round(value)) < 1e-9:
                return float(int(value)), f"{int(value)}"
        return None
    return None


def _parse_flattened_row(
    cell_text: str, *, allow_bare_percent: bool
) -> tuple[FieldName, float, str, dict, tuple[int, int]] | None:
    """Parse a space-joined single-cell row (XLSX sheets / flattened rows)."""
    text = normalize_ws(cell_text)
    if len(text) < 3:
        return None
    label_match = None
    field_name = None
    for pattern, candidate in _ENDPOINT_LABELS:
        match = pattern.search(text)
        if match and (label_match is None or match.start() < label_match.start()):
            label_match = match
            field_name = candidate
    population_count_field = _population_count_field(text)
    if field_name is None:
        if population_count_field is None:
            return None
        # Row is a population-size row: value is the first integer after the label.
        numbers = _numbers_in(text)
        if not numbers:
            return None
        value = numbers[0][0]
        if not (0 < value <= 1_000_000):
            return None
        return population_count_field, float(int(value)), "patients", {}, (0, len(text))

    kind = _field_kind(field_name)
    rest_start = label_match.end()
    rest = text[rest_start:]
    # Values end where a dimension token starts (population / method / date).
    cut = len(rest)
    population_hit = nearest_population(text, (rest_start, len(text)))
    for pattern in (
        re.compile(r"\b(?:ITT|FAS|mITT|PP)\b"),
        re.compile(r"\b(?:RECIST|iRECIST|Lugano|RANO|Choi)\b", re.I),
        re.compile(r"\d{4}-\d{2}-\d{2}"),
    ):
        match = pattern.search(rest)
        if match and match.start() < cut:
            cut = match.start()
    values_region = rest[:cut]
    numbers = _numbers_in(values_region)

    value: float | None = None
    raw: str | None = None
    if kind == "percent":
        pct_matches = list(_PCT_CELL_RE.finditer(values_region))
        if pct_matches:
            value = float(pct_matches[0].group(1))
            raw = pct_matches[0].group(0)
        elif allow_bare_percent and numbers:
            # XLSX "Value" column carries bare percentages (42.0) after an n column.
            value = numbers[-1][0]
            raw = f"{value:g}"
    elif kind == "months":
        month_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:months?|mo|weeks?|days?)\b", rest, re.I)
        if month_match:
            value = float(month_match.group(1))
            raw = month_match.group(0)
        elif numbers:
            value = numbers[0][0]
            raw = f"{value:g}"
    elif kind == "ratio":
        for number, _s, _e in numbers:
            if 0.0 < number <= 10.0:
                value = number
                raw = f"{number:g}"
                break
    elif kind == "count":
        for number, _s, _e in numbers:
            if 0.0 < number <= 1_000_000 and abs(number - round(number)) < 1e-9:
                value = float(int(number))
                raw = f"{int(number)}"
                break
    if value is None:
        return None
    if kind == "percent" and not (0.0 <= value <= 100.0):
        return None
    if kind == "months" and not (0.0 < value <= 600.0):
        return None
    unit = _unit_for_field(field_name, rest)
    value_span = None
    span_match = re.search(re.escape(raw), rest) if raw else None
    if span_match:
        value_span = (rest_start + span_match.start(), rest_start + span_match.end())
    dims: dict = {}
    population = nearest_population(text, value_span or (rest_start, len(text)))
    if population:
        dims["population"] = population
    method = detect_method(text)
    if method:
        dims["assessment_method"] = method
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if date_match:
        dims["data_cutoff"] = date_match.group(0)
    return field_name, value, unit or "", dims, value_span or (rest_start, len(text))


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------
class TableExtractor:
    def __init__(self, context: DocumentContext) -> None:
        self.context = context
        self.collector = DraftCollector()

    def extract(self) -> list[EvidenceDraft]:
        for page in self.context.pages:
            if not page.blocks:
                continue
            tables = tables_from_blocks(page.page_no, page.blocks)
            for table in tables:
                self._extract_table(page, table)
        return self.collector.drafts()

    # -- one table ----------------------------------------------------------
    def _extract_table(self, page, table: RecoveredTable) -> None:
        if not table.rows:
            return
        page_norm = page.norm
        header, header_index = _detect_header(table.rows)
        allow_bare_percent = False
        column_map: dict[str, int] = {}
        if header is not None:
            column_map = _map_columns(header)
            header_text = " ".join(header).lower()
            allow_bare_percent = bool(
                re.search(r"\b(?:value|percent|rate|%)", header_text)
            ) or len(header) == 1
        # Subgroup results tables: "Subgroup | n | ORR" rows carry subgroup ORR
        # percentages, which are emitted as SUBGROUP_ANALYSIS evidence.
        header_cells = list(header or (table.rows[0] if table.rows else []))
        if any("subgroup" in str(cell).lower() for cell in header_cells) or re.search(
            r"\bsubgroup", table.caption or "", re.I
        ):
            if header is None and table.rows and any(
                "subgroup" in str(cell).lower() for cell in table.rows[0]
            ):
                header = list(table.rows[0])
                header_index = 0
                start_row = 1
            self._extract_subgroup_table(page, table, header or [], header_index)
            return
        start_row = (header_index + 1) if header_index is not None else 0
        data_rows = table.rows[start_row:]
        if not data_rows:
            return

        # Wide multi-arm layout: header columns beyond the endpoint/value columns
        # are arm labels (e.g. "Treatment arm (n=60)").
        arm_columns: list[tuple[int, str]] = []
        if header is not None:
            for col_index, cell in enumerate(header):
                if col_index in column_map.values():
                    continue
                if _HEADER_ARM_COLS.search(cell):
                    arm_columns.append((col_index, normalize_ws(cell)))

        # Track the page-text search position so repeated numbers in different
        # rows keep their own quote.
        hint = 0
        for row in data_rows:
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) == 1:
                parsed = _parse_flattened_row(row[0], allow_bare_percent=allow_bare_percent)
                if parsed is None:
                    continue
                field_name, value, unit, flattened_dims, span = parsed
                hint = self._emit_row(
                    page,
                    table,
                    field_name,
                    value,
                    unit or None,
                    row_cells=[row[0]],
                    dims_cells=flattened_dims,
                    hint=hint,
                    label_text=row[0],
                    caption=table.caption,
                    allow_bare_percent=allow_bare_percent,
                    value_span=span,
                )
                continue

            endpoint_col = column_map.get("endpoint", 0)
            endpoint_cell = row[endpoint_col] if endpoint_col < len(row) else ""
            field_name = endpoint_field(endpoint_cell)
            label_text = endpoint_cell
            if field_name is None:
                population_field = _population_count_field(endpoint_cell)
                if population_field is not None:
                    size = _find_count(row, column_map, exclude=endpoint_col)
                    if size is not None:
                        hint = self._emit_row(
                            page,
                            table,
                            population_field,
                            size,
                            "patients",
                            row_cells=[endpoint_cell],
                            dims_cells={},
                            hint=hint,
                            label_text=endpoint_cell,
                            caption=table.caption,
                            allow_bare_percent=allow_bare_percent,
                        )
                continue

            value_cell = _find_value_cell(row, column_map, endpoint_col)
            if arm_columns:
                for col_index, arm_label in arm_columns:
                    if col_index >= len(row):
                        continue
                    parsed = _parse_value_cell(row[col_index], field_name)
                    if parsed is None:
                        continue
                    value, raw = parsed
                    dims_cells = _dims_cells(row, column_map)
                    dims_cells["arm"] = arm_label
                    hint = self._emit_row(
                        page,
                        table,
                        field_name,
                        value,
                        _unit_for_field(field_name, row[col_index]),
                        row_cells=[endpoint_cell, row[col_index]],
                        dims_cells=dims_cells,
                        hint=hint,
                        label_text=endpoint_cell,
                        caption=table.caption,
                        allow_bare_percent=allow_bare_percent,
                    )
                continue

            parsed = _parse_value_cell(value_cell, field_name) if value_cell else None
            if parsed is None:
                # Fall back to the first percent/number cell in the row.
                for col_index, cell in enumerate(row):
                    if col_index == endpoint_col:
                        continue
                    parsed = _parse_value_cell(cell, field_name)
                    if parsed is not None:
                        value_cell = cell
                        break
            if parsed is None:
                continue
            value, _raw = parsed
            dims_cells = _dims_cells(row, column_map)
            hint = self._emit_row(
                page,
                table,
                field_name,
                value,
                _unit_for_field(field_name, value_cell),
                row_cells=[endpoint_cell, value_cell],
                dims_cells=dims_cells,
                hint=hint,
                label_text=endpoint_cell,
                caption=table.caption,
                allow_bare_percent=allow_bare_percent,
            )
            # Confidence interval from a dedicated CI column or the value cell.
            ci_source = _find_ci_source(row, column_map, value_cell)
            if ci_source:
                ci_match = _CI_CELL_RE.search(ci_source)
                if ci_match:
                    lower, upper = float(ci_match.group(1)), float(ci_match.group(2))
                    if 0.0 <= lower < upper <= 1000.0:
                        self._emit_ci(page, table, field_name, lower, upper, value_cell, dims_cells, hint)

    # -- subgroup results tables -------------------------------------------
    def _extract_subgroup_table(
        self, page, table: RecoveredTable, header: Sequence[str], header_index: int | None
    ) -> None:
        rows = table.rows[(header_index + 1) if header_index is not None else 0 :]
        if not rows:
            return
        label_col = 0
        value_col: int | None = None
        lowered_header = [str(cell).lower() for cell in header]
        for index, cell in enumerate(lowered_header):
            if "subgroup" in cell:
                label_col = index
            if value_col is None and re.search(r"\b(?:orr|response|rate|%|value)\b", cell):
                value_col = index
        hint = 0
        for row in rows:
            if len(row) < 2:
                continue
            label = normalize_ws(row[label_col]) if label_col < len(row) else ""
            if not label:
                continue
            percent_cell = ""
            if value_col is not None and value_col < len(row) and _PCT_CELL_RE.search(row[value_col]):
                percent_cell = row[value_col]
            if not percent_cell:
                for index, cell in enumerate(row):
                    if index != label_col and _PCT_CELL_RE.search(cell):
                        percent_cell = cell
                        break
            match = _PCT_CELL_RE.search(percent_cell or "")
            if not match:
                continue
            value = float(match.group(1))
            if not (0.0 <= value <= 100.0):
                continue
            quote, hint = _quote_for_cells(
                page.norm, primary_cell=label, required_cells=[percent_cell], hint=hint
            )
            if quote is None:
                continue
            draft = make_draft(
                FieldName.SUBGROUP_ANALYSIS,
                page=page.page_no,
                quote=quote,
                value_num=value,
                value_text=f"{label}: {normalize_ws(percent_cell)}",
                table=table.label,
                confidence=0.85,
                extractor="table",
            )
            self.collector.add(draft)
        # Document-level canonical marker that subgroup *results* are present.
        anchor = None
        for needle in ("Results are presented below", "subgroup results", table.caption or ""):
            if not needle:
                continue
            position = page.norm.find(normalize_ws(needle))
            if position >= 0:
                anchor = position
                break
        if anchor is None:
            return
        lo = max(0, anchor - 500)
        hi = min(len(page.norm), anchor + len(normalize_ws(table.caption or "")) + 120)
        quote = page.norm[lo:hi].strip()
        if not quote:
            return
        self.collector.add(
            make_draft(
                FieldName.SUBGROUP_ANALYSIS,
                page=page.page_no,
                quote=quote,
                value_text="subgroup results reported",
                table=table.label,
                confidence=0.8,
                extractor="table",
            )
        )

    # -- emitters -----------------------------------------------------------
    def _emit_row(
        self,
        page,
        table: RecoveredTable,
        field_name: FieldName,
        value: float,
        unit: str | None,
        *,
        row_cells: Sequence[str],
        dims_cells: dict,
        hint: int,
        label_text: str,
        caption: str | None,
        allow_bare_percent: bool,
        value_span: tuple[int, int] | None = None,
    ) -> int:
        quote, new_hint = _quote_for_cells(
            page.norm,
            primary_cell=row_cells[0],
            required_cells=[cell for cell in row_cells[1:] if cell],
            hint=hint,
        )
        if quote is None:
            quote = _quote_for_number(page.norm, value, hint)
        if quote is None:
            return hint
        dims = _resolve_table_dims(
            self.context,
            page_no=page.page_no,
            field_name=field_name,
            row_population=dims_cells.get("population"),
            row_arm=dims_cells.get("arm"),
            row_cutoff=dims_cells.get("data_cutoff"),
            row_method=dims_cells.get("assessment_method"),
            caption=caption,
        )
        value_text = None
        if field_name in _COUNT_FIELDS:
            value_text = f"{int(round(value))}"
        draft = make_draft(
            field_name,
            page=page.page_no,
            quote=quote,
            value_num=value,
            value_text=value_text,
            unit=unit or None,
            population=dims.get("population"),
            arm=dims.get("arm"),
            trial_key=dims.get("trial_key"),
            data_cutoff=dims.get("data_cutoff"),
            endpoint_def=dims.get("endpoint_def"),
            assessment_method=dims.get("assessment_method"),
            table=table.label,
            confidence=0.9,
            extractor="table",
            notes=dims.get("notes"),
        )
        self.collector.add(draft)
        return max(new_hint, hint)

    def _emit_ci(
        self,
        page,
        table: RecoveredTable,
        field_name: FieldName,
        lower: float,
        upper: float,
        value_cell: str,
        dims_cells: dict,
        hint: int,
    ) -> None:
        quote, _ = _quote_for_cells(page.norm, primary_cell=value_cell, required_cells=[], hint=hint)
        if quote is None:
            return
        dims = _resolve_table_dims(
            self.context,
            page_no=page.page_no,
            field_name=field_name,
            row_population=dims_cells.get("population"),
            row_arm=dims_cells.get("arm"),
            row_cutoff=dims_cells.get("data_cutoff"),
            row_method=dims_cells.get("assessment_method"),
            caption=table.caption,
        )
        from hijenny.extraction.common import ENDPOINT_TOKENS

        endpoint_token = ENDPOINT_TOKENS.get(field_name)
        ci_unit = "%" if field_name in _PERCENT_FIELDS else (None if field_name in _RATIO_FIELDS else _unit_for_field(field_name, value_cell))
        for ci_field, value in ((FieldName.CI_LOWER, lower), (FieldName.CI_UPPER, upper)):
            draft = make_draft(
                ci_field,
                page=page.page_no,
                quote=quote,
                value_num=value,
                unit=ci_unit,
                population=dims.get("population"),
                arm=dims.get("arm"),
                trial_key=dims.get("trial_key"),
                data_cutoff=dims.get("data_cutoff"),
                endpoint_def=(
                    canonical_endpoint_def(endpoint_token, field_name, dims.get("assessment_method"))
                    if endpoint_token
                    else None
                ),
                assessment_method=dims.get("assessment_method"),
                table=table.label,
                confidence=0.85,
                extractor="table",
                notes=dims.get("notes"),
            )
            self.collector.add(draft)


# ---------------------------------------------------------------------------
# Layout detection helpers
# ---------------------------------------------------------------------------
def _detect_header(rows: Sequence[Sequence[str]]) -> tuple[list[str] | None, int | None]:
    for index, row in enumerate(rows[:3]):
        if not row:
            continue
        if len(row) == 1:
            text = row[0].lower()
            if re.search(r"\b(?:endpoint|value|population|percent|assessment|cutoff|category|parameter)\b", text):
                return list(row), index
            continue
        hits = sum(
            1
            for cell in row
            if _HEADER_ENDPOINT_COLS.search(cell)
            or _HEADER_VALUE_COLS.search(cell)
            or _HEADER_N_COLS.match(cell.strip())
            or _HEADER_CI_COLS.search(cell)
            or _HEADER_POP_COLS.search(cell)
            or _HEADER_ARM_COLS.search(cell)
            or _HEADER_CUTOFF_COLS.search(cell)
            or _HEADER_METHOD_COLS.search(cell)
        )
        if hits >= 2:
            return list(row), index
    return None, None


def _map_columns(header: Sequence[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, raw_cell in enumerate(header):
        cell = normalize_ws(raw_cell)
        if "endpoint" not in mapping and _HEADER_ENDPOINT_COLS.search(cell):
            mapping["endpoint"] = index
        elif "value" not in mapping and _HEADER_VALUE_COLS.search(cell):
            mapping["value"] = index
        elif "ci" not in mapping and _HEADER_CI_COLS.search(cell):
            mapping["ci"] = index
        elif "population" not in mapping and _HEADER_POP_COLS.search(cell):
            mapping["population"] = index
        elif "arm" not in mapping and _HEADER_ARM_COLS.search(cell):
            mapping["arm"] = index
        elif "cutoff" not in mapping and _HEADER_CUTOFF_COLS.search(cell):
            mapping["cutoff"] = index
        elif "assessment" not in mapping and _HEADER_METHOD_COLS.search(cell):
            mapping["assessment"] = index
        elif "n" not in mapping and _HEADER_N_COLS.match(cell):
            mapping["n"] = index
    if "endpoint" not in mapping:
        mapping["endpoint"] = 0
    return mapping


def _find_value_cell(row: Sequence[str], column_map: dict[str, int], endpoint_col: int) -> str:
    value_col = column_map.get("value")
    if value_col is not None and value_col < len(row) and value_col != endpoint_col:
        cell = row[value_col]
        if _PCT_CELL_RE.search(cell) or _NUM_RE.search(cell):
            return cell
    # Prefer the first cell containing a percent sign, then the first number.
    for index, cell in enumerate(row):
        if index == endpoint_col:
            continue
        if _PCT_CELL_RE.search(cell):
            return cell
    for index, cell in enumerate(row):
        if index == endpoint_col:
            continue
        if _NUM_RE.search(cell):
            return cell
    fallback = column_map.get("value")
    if fallback is not None and fallback < len(row):
        return row[fallback]
    return ""


def _find_count(row: Sequence[str], column_map: dict[str, int], exclude: int) -> float | None:
    candidates: list[float] = []
    for index, cell in enumerate(row):
        if index == exclude:
            continue
        for value, _start, _end in _numbers_in(cell):
            if 0 < value <= 1_000_000 and abs(value - round(value)) < 1e-9:
                candidates.append(float(int(value)))
    return candidates[-1] if candidates else None


def _dims_cells(row: Sequence[str], column_map: dict[str, int]) -> dict:
    dims: dict = {}
    for name, key in (
        ("population", "population"),
        ("arm", "arm"),
        ("cutoff", "data_cutoff"),
        ("assessment", "assessment_method"),
    ):
        index = column_map.get(name)
        if index is not None and index < len(row):
            cell = normalize_ws(row[index])
            if cell:
                dims[key] = cell
    return dims


def _find_ci_source(row: Sequence[str], column_map: dict[str, int], value_cell: str) -> str | None:
    ci_col = column_map.get("ci")
    if ci_col is not None and ci_col < len(row) and _CI_CELL_RE.search(row[ci_col]):
        return row[ci_col]
    if _CI_CELL_RE.search(value_cell or ""):
        return value_cell
    return None


def _resolve_table_dims(
    context: DocumentContext,
    *,
    page_no: int,
    field_name: FieldName,
    row_population: str | None,
    row_arm: str | None,
    row_cutoff: str | None,
    row_method: str | None,
    caption: str | None,
) -> dict:
    category = field_category(field_name)
    provenance: dict = {}

    population = normalize_population(row_population) if row_population else None
    if not population and caption:
        mentions = nearest_population(caption, (0, len(caption)))
        if mentions:
            population = normalize_population(mentions)
    if not population:
        if category == EvidenceCategory.CLINICAL_SAFETY and context.safety_population:
            population = context.safety_population
            provenance["population"] = {"fallback": "doc_level", "dim": "population", "source_page": 1}
        elif category in (EvidenceCategory.CLINICAL_EFFICACY, EvidenceCategory.PATIENT_BASELINE):
            population, note = context.population_at(page_no, 10**6)
            if population and note:
                provenance["population"] = note

    arm = normalize_ws(row_arm) if row_arm else None

    cutoff = normalize_date(row_cutoff) if row_cutoff else None
    if not cutoff and caption:
        caption_cutoffs = find_cutoffs(caption)
        if len(set(caption_cutoffs)) == 1:
            cutoff = caption_cutoffs[0]
    if not cutoff and category in (EvidenceCategory.CLINICAL_EFFICACY, EvidenceCategory.CLINICAL_SAFETY) and context.cutoff_default:
        cutoff = context.cutoff_default
        provenance["data_cutoff"] = {"fallback": "doc_level", "dim": "data_cutoff"}

    method = detect_method(row_method) if row_method else None
    if not method and caption:
        method = detect_method(caption)
    if not method and category == EvidenceCategory.CLINICAL_EFFICACY and context.method_default:
        method = context.method_default
        provenance["assessment_method"] = {"fallback": "doc_level", "dim": "assessment_method"}

    endpoint_def = None
    if category in (EvidenceCategory.CLINICAL_EFFICACY, EvidenceCategory.CLINICAL_SAFETY):
        from hijenny.extraction.common import ENDPOINT_TOKENS

        endpoint_def = canonical_endpoint_def(
            ENDPOINT_TOKENS.get(field_name, str(field_name).upper()), field_name, method
        )

    notes = None
    if provenance:
        notes = json.dumps({"fallback": "dimension_resolution", "dims": provenance}, sort_keys=True)
    return {
        "population": population,
        "arm": arm,
        "data_cutoff": cutoff,
        "assessment_method": method,
        "endpoint_def": endpoint_def,
        "trial_key": context.trial_key,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Quote construction
# ---------------------------------------------------------------------------
def _quote_for_cells(
    page_norm: str,
    *,
    primary_cell: str,
    required_cells: Sequence[str],
    hint: int = 0,
) -> tuple[str | None, int]:
    primary = normalize_ws(primary_cell)
    if len(primary) < 2:
        return None, hint
    start = page_norm.find(primary, max(0, hint))
    if start == -1:
        start = page_norm.find(primary)
    if start == -1:
        return None, hint
    end = start + len(primary)
    position = end
    for raw_cell in required_cells:
        cell = normalize_ws(raw_cell)
        if len(cell) < 1:
            continue
        found = page_norm.find(cell, position, start + 500)
        if found == -1:
            found = page_norm.find(cell, hints_floor(hint), start + 800)
        if found == -1:
            return None, hint
        end = max(end, found + len(cell))
        position = found + len(cell)
    quote = page_norm[start:end].strip()
    if not quote:
        return None, hint
    return quote, end


def hints_floor(hint: int) -> int:
    return max(0, hint)


def _quote_for_number(page_norm: str, value: float, hint: int) -> str | None:
    candidates = [f"{value:g}"]
    if abs(value - round(value)) < 1e-9:
        candidates.append(f"{int(round(value))}")
        candidates.append(f"{int(round(value)):,}")
    candidates.append(f"{value:.1f}")
    for token in candidates:
        position = page_norm.find(token, max(0, hint))
        if position == -1:
            position = page_norm.find(token)
        if position != -1:
            lo = max(0, position - 160)
            hi = min(len(page_norm), position + len(token) + 160)
            return page_norm[lo:hi].strip()
    return None


__all__ = [
    "RecoveredTable",
    "TableExtractor",
    "endpoint_field",
    "tables_from_blocks",
]
