#!/usr/bin/env python3
"""Render docs/HANDOFF.md into a shareable Word document (and verify it)."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs/HANDOFF.md"
OUT = ROOT / "docs/HiJenny_Handoff.docx"

NAVY = RGBColor(0x1F, 0x38, 0x64)
BLUE = RGBColor(0x2E, 0x75, 0xB6)
MUTED = RGBColor(0x5D, 0x6B, 0x7F)


def set_east_asia(run, font: str) -> None:
    run.font.name = "Calibri"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font)


def add_hyperlink_text(paragraph, text: str, bold=False) -> None:
    run = paragraph.add_run(text)
    run.font.size = Pt(9.5)
    run.font.bold = bold
    run.font.color.rgb = BLUE
    set_east_asia(run, "Microsoft YaHei")


def add_inline(paragraph, text: str, size: float = 9.5, base_color=RGBColor(0x1B, 0x24, 0x32)) -> None:
    """Support **bold**, `code`, [label](url) and plain text."""
    pattern = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")
    pos = 0
    for match in pattern.finditer(text):
        if match.start() > pos:
            run = paragraph.add_run(text[pos:match.start()])
            run.font.size = Pt(size)
            run.font.color.rgb = base_color
            set_east_asia(run, "Microsoft YaHei")
        token = match.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2])
            run.font.bold = True
            run.font.size = Pt(size)
            set_east_asia(run, "Microsoft YaHei")
        elif token.startswith("`"):
            run = paragraph.add_run(token[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(size - 1)
            run.font.color.rgb = RGBColor(0x6D, 0x3B, 0xBF)
            set_east_asia(run, "Microsoft YaHei")
        else:
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token).groups()
            add_hyperlink_text(paragraph, f"{label}（{url}）", bold=False)
        pos = match.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        run.font.size = Pt(size)
        run.font.color.rgb = base_color
        set_east_asia(run, "Microsoft YaHei")


def add_heading_inline(doc, text: str, level: int) -> None:
    size = {1: 16, 2: 13, 3: 11}[level]
    color = {1: NAVY, 2: BLUE, 3: NAVY}[level]
    p = doc.add_paragraph(style=f"Heading {level}")
    add_inline(p, text, size=size, base_color=color)
    for run in p.runs:
        run.font.name = "Calibri"
        run.font.bold = True
        set_east_asia(run, "Microsoft YaHei")


def shade(paragraph, fill: str) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pPr.append(shd)


def set_cell_text(cell, text: str, bold=False, size=8.5) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    add_inline(paragraph, text, size=size)
    for run in paragraph.runs:
        run.font.bold = bold
        run.font.size = Pt(size)
    paragraph.paragraph_format.space_after = Pt(1)
    paragraph.paragraph_format.space_before = Pt(1)


def split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?", line.strip()))


def add_table(doc, lines: list[str]) -> None:
    rows = [split_row(line) for line in lines]
    if not rows:
        return
    cols = max(len(row) for row in rows)
    table = doc.add_table(rows=0, cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for r_idx, row in enumerate(rows):
        cells = table.add_row().cells
        for c_idx in range(cols):
            value = row[c_idx] if c_idx < len(row) else ""
            set_cell_text(cells[c_idx], value, bold=(r_idx == 0), size=8)
            if r_idx == 0:
                tcPr = cells[c_idx]._tc.get_or_add_tcPr()
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:color"), "auto")
                shd.set(qn("w:fill"), "DCE6F1")
                tcPr.append(shd)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_code_block(doc, lines: list[str]) -> None:
    for line in lines:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.left_indent = Cm(0.3)
        run = p.add_run(line if line else " ")
        run.font.name = "Consolas"
        run.font.size = Pt(7.5)
        set_east_asia(run, "Microsoft YaHei")
        shade(p, "F2F5F9")


def add_page_number_footer(section, label: str = "HiJenny · 项目交接文档") -> None:
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"{label} · 第 ")
    run.font.size = Pt(8)
    run.font.color.rgb = MUTED
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    p._p.append(fld)
    run = p.add_run(" 页")
    run.font.size = Pt(8)
    run.font.color.rgb = MUTED


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(SRC))
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--title", default="HiJenny 项目交接文档")
    parser.add_argument("--footer", default="HiJenny · 项目交接文档")
    parser.add_argument("--author", default="HiJenny 项目组")
    args = parser.parse_args()
    src = Path(args.src)
    out = Path(args.out)
    text = src.read_text(encoding="utf-8")
    lines = text.splitlines()
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(1.9)
    section.right_margin = Cm(1.9)
    add_page_number_footer(section, args.footer)

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(9.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.paragraph_format.space_after = Pt(3)

    for name, size, color in (("Title", 26, NAVY), ("Heading 1", 16, NAVY), ("Heading 2", 13, BLUE), ("Heading 3", 11, NAVY)):
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.color.rgb = color
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.paragraph_format.space_before = Pt(10 if name != "Title" else 0)
        style.paragraph_format.space_after = Pt(5)

    i = 0
    title_seen = False
    in_code = False
    code_lines: list[str] = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                add_code_block(doc, code_lines)
                code_lines = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_lines.append(line.rstrip())
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        if stripped == "---":
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(4)
            pPr = p._p.get_or_add_pPr()
            pbdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "4")
            bottom.set(qn("w:space"), "1")
            bottom.set(qn("w:color"), "C3CCDA")
            pbdr.append(bottom)
            pPr.append(pbdr)
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and is_separator(lines[i + 1]):
            table_lines = [stripped]
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                table_lines.append(lines[j].strip())
                j += 1
            add_table(doc, table_lines)
            i = j
            continue

        if stripped.startswith("# "):
            title = stripped[2:].strip()
            if not title_seen:
                p = doc.add_paragraph(style="Title")
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                add_inline(p, title, size=24, base_color=NAVY)
                title_seen = True
            else:
                add_heading_inline(doc, title, 1)
            i += 1
            continue
        if stripped.startswith("## "):
            add_heading_inline(doc, stripped[3:].strip(), 1)
            i += 1
            continue
        if stripped.startswith("### "):
            add_heading_inline(doc, stripped[4:].strip(), 2)
            i += 1
            continue
        if stripped.startswith("#### "):
            add_heading_inline(doc, stripped[5:].strip(), 3)
            i += 1
            continue

        if stripped == ">":
            i += 1
            continue
        if stripped.startswith("> "):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            add_inline(p, stripped[2:], size=9, base_color=MUTED)
            for run in p.runs:
                run.italic = True
            i += 1
            continue

        if re.match(r"^[-*] \[[ xX]\] ", stripped):
            p = doc.add_paragraph(style="List Bullet")
            add_inline(p, "☐ " + stripped[6:])
            i += 1
            continue
        if stripped.startswith(("- ", "* ")):
            p = doc.add_paragraph(style="List Bullet")
            add_inline(p, stripped[2:])
            i += 1
            continue
        if re.match(r"^\d+\.\s+", stripped):
            p = doc.add_paragraph(style="List Number")
            add_inline(p, re.sub(r"^\d+\.\s+", "", stripped))
            i += 1
            continue

        p = doc.add_paragraph()
        add_inline(p, stripped)
        i += 1

    doc.core_properties.title = args.title
    doc.core_properties.author = args.author
    doc.core_properties.subject = args.title
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print(f"wrote {out}  paragraphs={len(doc.paragraphs)} tables={len(doc.tables)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
