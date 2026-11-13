"""Convert the requirements questionnaire (Markdown) into a fillable Word document.

Usage: python tools/md_to_docx.py docs/00-需求调研问卷.md docs/00-需求调研问卷.docx
Supports the subset of Markdown used in this project: headings, paragraphs,
bullet/ordered lists, tables, fenced code blocks, blockquotes, inline bold and
backticked code.
"""

from __future__ import annotations

import re
import sys

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BODY_FONT = "Microsoft YaHei"
MONO_FONT = "Consolas"
BODY_SIZE = Pt(10.5)


def set_run_font(run, name: str = BODY_FONT, size: Pt | None = None, bold: bool | None = None,
                 italic: bool | None = None, color: RGBColor | None = None) -> None:
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)
    if size is not None:
        run.font.size = size
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color is not None:
        run.font.color.rgb = color


def shade(element, fill: str) -> None:
    """Apply paragraph/table-cell shading."""
    pr = element.get_or_add_pPr() if hasattr(element, "get_or_add_pPr") else element
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pr.append(shd)


def add_inline(paragraph, text: str, base_size: Pt = BODY_SIZE, base_bold: bool = False) -> None:
    """Add text with **bold** and `code` spans."""
    text = text.replace("🔴", "（必答）").replace("⚪", "（选答）")
    pattern = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
    pos = 0
    for match in pattern.finditer(text):
        if match.start() > pos:
            set_run_font(paragraph.add_run(text[pos:match.start()]), size=base_size, bold=base_bold)
        token = match.group(0)
        if token.startswith("**"):
            set_run_font(paragraph.add_run(token[2:-2]), size=base_size, bold=True)
        else:
            set_run_font(paragraph.add_run(token[1:-1]), name=MONO_FONT, size=Pt(9.5))
        pos = match.end()
    if pos < len(text):
        set_run_font(paragraph.add_run(text[pos:]), size=base_size, bold=base_bold)


def parse_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        raw = lines[i].strip().strip("|")
        cells = [c.strip() for c in raw.split("|")]
        rows.append(cells)
        i += 1
    # drop separator row (| --- | --- |)
    if len(rows) >= 2 and all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in rows[1] if c):
        rows.pop(1)
    return rows, i


def add_table(document: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    cols = max(len(r) for r in rows)
    table = document.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for r_idx, row in enumerate(rows):
        tr_pr = table.rows[r_idx]._tr.get_or_add_trPr()
        cant_split = OxmlElement("w:cantSplit")
        tr_pr.append(cant_split)
        if r_idx == 0:
            tr_pr.append(OxmlElement("w:tblHeader"))
        for c_idx in range(cols):
            cell = table.cell(r_idx, c_idx)
            cell.text = ""
            text = row[c_idx] if c_idx < len(row) else ""
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            add_inline(paragraph, text, base_size=Pt(9), base_bold=(r_idx == 0))
            if r_idx == 0:
                shade(cell._tc.get_or_add_tcPr(), "E8EDF5")
        if r_idx == 0:
            for cell in table.rows[0].cells:
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.bold = True
    document.add_paragraph()


def add_code_block(document: Document, lines: list[str]) -> None:
    for line in lines:
        paragraph = document.add_paragraph()
        pf = paragraph.paragraph_format
        pf.space_after = Pt(0)
        pf.space_before = Pt(0)
        pf.left_indent = Cm(0.4)
        run = paragraph.add_run(line if line else " ")
        set_run_font(run, name=MONO_FONT, size=Pt(8.5))
        shade(paragraph._p.get_or_add_pPr(), "F4F5F7")
    document.add_paragraph()


def build(source: str, target: str) -> None:
    with open(source, encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for side in ("top_margin", "bottom_margin"):
        setattr(section, side, Cm(2.0))
    for side in ("left_margin", "right_margin"):
        setattr(section, side, Cm(2.0))

    normal = document.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = BODY_SIZE
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.25

    for level, size in ((1, 15), (2, 13), (3, 11.5), (4, 11)):
        style = document.styles[f"Heading {level}"]
        style.font.name = BODY_FONT
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0x1F, 0x3B, 0x60)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)

    i = 0
    first_heading = True
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # fenced code block
        if stripped.startswith("```"):
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i].rstrip())
                i += 1
            i += 1
            add_code_block(document, block)
            continue

        # table
        if stripped.startswith("|"):
            rows, i = parse_table(lines, i)
            add_table(document, rows)
            continue

        # heading
        match = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if match:
            level = len(match.group(1))
            text = match.group(2).strip()
            heading = document.add_heading(level=level)
            add_inline(heading, text, base_size=Pt((15, 13, 11.5, 11)[level - 1]), base_bold=True)
            for run in heading.runs:
                run.font.color.rgb = RGBColor(0x1F, 0x3B, 0x60)
            heading.paragraph_format.space_before = Pt(10 if level <= 2 else 6)
            heading.paragraph_format.space_after = Pt(4)
            if first_heading and level == 1:
                heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
                first_heading = False
            i += 1
            continue

        # horizontal rule
        if re.fullmatch(r"-{3,}", stripped):
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            text = stripped.lstrip("> ").strip()
            if text.startswith("- "):
                text = text[2:]
                paragraph = document.add_paragraph(style="List Bullet")
            else:
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.6)
            add_inline(paragraph, text, base_size=Pt(10))
            for run in paragraph.runs:
                run.italic = True
                run.font.color.rgb = RGBColor(0x44, 0x4A, 0x55)
            i += 1
            continue

        # bullets
        bullet = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if bullet:
            indent = len(bullet.group(1))
            level = 1 if indent < 2 else (2 if indent < 4 else 3)
            style = {1: "List Bullet", 2: "List Bullet 2", 3: "List Bullet 3"}[level]
            paragraph = document.add_paragraph(style=style)
            add_inline(paragraph, bullet.group(2).strip())
            paragraph.paragraph_format.space_after = Pt(2)
            i += 1
            continue

        # ordered list
        ordered = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        if ordered:
            paragraph = document.add_paragraph(style="List Number")
            add_inline(paragraph, ordered.group(2).strip())
            paragraph.paragraph_format.space_after = Pt(2)
            i += 1
            continue

        # plain paragraph
        paragraph = document.add_paragraph()
        add_inline(paragraph, stripped)
        i += 1

    document.core_properties.title = "Civitai 提示词批量下载器 · 需求调研问卷"
    document.core_properties.author = "lead (PM)"
    document.save(target)
    print(f"saved: {target}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: md_to_docx.py <source.md> <target.docx>")
    build(sys.argv[1], sys.argv[2])
