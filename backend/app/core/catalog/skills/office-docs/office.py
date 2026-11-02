"""Build a .pptx, .xlsx or .docx from a small JSON spec, in one house style.

The agent calls this instead of writing python-pptx by hand every time: the theme,
the CJK font handling and the table sizing are decisions to make once, not to
re-litigate per document. Only the three libraries the workspace already ships.

    python3 office.py deck  spec.json out.pptx
    python3 office.py sheet spec.json out.xlsx
    python3 office.py doc   spec.json out.docx

Every command reads a JSON file and writes one document. Exit code 1 with a
message on stderr when the spec is malformed - a silent half-built deck is worse
than a refusal.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

# One family for Latin and CJK both. Named for what the *reader* has: Microsoft
# YaHei ships with Windows and is the house font of most Chinese offices. A
# container without it substitutes any font that carries the glyphs, so the
# render an agent checks stays legible even though the name is not installed.
FAMILY = "微软雅黑"

INK = RGBColor(0x1A, 0x22, 0x33)
MUTED = RGBColor(0x5B, 0x66, 0x78)
ACCENT = RGBColor(0x1F, 0x38, 0x64)
RULE = RGBColor(0xD6, 0xDC, 0xE4)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.75)


def _font(run: Any, size: int, *, bold: bool = False, color: RGBColor = INK) -> None:
    """Set a run's font for Latin, CJK and complex scripts at once.

    `run.font.name` writes only `a:latin`, which leaves Chinese text on the
    theme's east-Asian font - the one thing that silently differs between the
    deck an agent previews and the deck a person opens.
    """
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = FAMILY
    rpr = run.font._rPr
    for tag in ("a:ea", "a:cs"):
        element = rpr.find(qn(tag))
        if element is None:
            element = rpr.makeelement(qn(tag), {})
            rpr.append(element)
        element.set("typeface", FAMILY)


def _textbox(slide: Any, left: int, top: int, width: int, height: int) -> Any:
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    return box


def _rule(slide: Any, left: int, top: int, width: int, color: RGBColor = RULE) -> None:
    line = slide.shapes.add_shape(1, left, top, width, Emu(9525))
    line.fill.solid()
    line.fill.fore_color.rgb = color
    line.line.fill.background()
    line.shadow.inherit = False


def _bullet(paragraph: Any, level: int) -> None:
    """Hang a bullet marker without hand-building `a:buChar`.

    The marker is part of the text and the indent is the paragraph's, which keeps
    the XML valid whatever order the schema wants the bullet elements in.
    """
    depth = int(Inches(0.32))
    ppr = paragraph._p.get_or_add_pPr()
    ppr.set("marL", str(depth * (level + 1)))
    ppr.set("indent", str(-depth))


def _body_size(count: int) -> int:
    """Shrink a bullet list until it fits the frame, rather than overflowing it."""
    if count <= 5:
        return 20
    if count <= 7:
        return 18
    if count <= 9:
        return 16
    return 14


def _loud(item: Any) -> bool:
    """A bullet written as an object may ask for emphasis; a bare string may not."""
    return isinstance(item, dict) and bool(item.get("bold"))


# === deck ===================================================================


def _title_slide(deck: Presentation, spec: dict[str, Any]) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    band = slide.shapes.add_shape(1, 0, 0, SLIDE_W, Inches(0.28))
    band.fill.solid()
    band.fill.fore_color.rgb = ACCENT
    band.line.fill.background()
    band.shadow.inherit = False

    title = _textbox(slide, MARGIN, Inches(2.5), SLIDE_W - 2 * MARGIN, Inches(1.5))
    run = title.text_frame.paragraphs[0].add_run()
    run.text = spec.get("title", "")
    _font(run, 40, bold=True, color=ACCENT)

    subtitle = spec.get("subtitle")
    if subtitle:
        box = _textbox(slide, MARGIN, Inches(4.05), SLIDE_W - 2 * MARGIN, Inches(0.8))
        run = box.text_frame.paragraphs[0].add_run()
        run.text = subtitle
        _font(run, 20, color=MUTED)

    stamp = spec.get("date")
    if stamp:
        box = _textbox(slide, MARGIN, Inches(6.3), SLIDE_W - 2 * MARGIN, Inches(0.5))
        run = box.text_frame.paragraphs[0].add_run()
        run.text = stamp
        _font(run, 14, color=MUTED)


def _section_slide(deck: Presentation, title: str) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    fill = slide.shapes.add_shape(1, 0, 0, SLIDE_W, SLIDE_H)
    fill.fill.solid()
    fill.fill.fore_color.rgb = ACCENT
    fill.line.fill.background()
    fill.shadow.inherit = False
    box = _textbox(slide, MARGIN, Inches(3.1), SLIDE_W - 2 * MARGIN, Inches(1.2))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = title
    _font(run, 36, bold=True, color=WHITE)


def _slide_chrome(deck: Presentation, title: str, page: int, footer: str | None) -> Any:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    box = _textbox(slide, MARGIN, Inches(0.55), SLIDE_W - 2 * MARGIN, Inches(0.9))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = title
    _font(run, 28, bold=True, color=ACCENT)
    _rule(slide, MARGIN, Inches(1.45), SLIDE_W - 2 * MARGIN)

    mark = _textbox(slide, SLIDE_W - MARGIN - Inches(1.2), Inches(6.85), Inches(1.2), Inches(0.35))
    para = mark.text_frame.paragraphs[0]
    para.alignment = PP_ALIGN.RIGHT
    run = para.add_run()
    run.text = f"{footer} · {page}" if footer else str(page)
    _font(run, 11, color=MUTED)
    return slide


def _bullets_slide(deck: Presentation, spec: dict[str, Any], page: int, footer: str | None) -> None:
    slide = _slide_chrome(deck, spec.get("title", ""), page, footer)
    items = spec.get("bullets") or []
    box = _textbox(slide, MARGIN, Inches(1.85), SLIDE_W - 2 * MARGIN, Inches(4.7))
    frame = box.text_frame
    size = _body_size(len(items))

    for index, item in enumerate(items):
        text = item["text"] if isinstance(item, dict) else str(item)
        level = int(item.get("level", 0)) if isinstance(item, dict) else 0
        para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        para.space_after = Pt(10 if level == 0 else 6)
        _bullet(para, level)
        run = para.add_run()
        run.text = ("• " if level == 0 else "– ") + text
        _font(
            run,
            size - 2 * level,
            bold=level == 0 and _loud(item),
            color=INK if level == 0 else MUTED,
        )


def _table_slide(deck: Presentation, spec: dict[str, Any], page: int, footer: str | None) -> None:
    slide = _slide_chrome(deck, spec.get("title", ""), page, footer)
    headers = [str(h) for h in spec.get("headers") or []]
    rows = [[str(c) for c in row] for row in spec.get("rows") or []]
    if not headers:
        raise ValueError("a table slide needs 'headers'")

    height = min(Inches(5.0), Inches(0.45) * (len(rows) + 1))
    shape = slide.shapes.add_table(
        len(rows) + 1, len(headers), MARGIN, Inches(1.85), SLIDE_W - 2 * MARGIN, height
    )
    table = shape.table
    table.first_row = True
    table.horz_banding = False

    size = 12 if len(headers) <= 6 else 10
    for column, header in enumerate(headers):
        cell = table.cell(0, column)
        cell.fill.solid()
        cell.fill.fore_color.rgb = ACCENT
        para = cell.text_frame.paragraphs[0]
        run = para.add_run()
        run.text = header
        _font(run, size, bold=True, color=WHITE)

    for row_index, row in enumerate(rows, start=1):
        for column, value in enumerate(row[: len(headers)]):
            cell = table.cell(row_index, column)
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE if row_index % 2 else RGBColor(0xF4, 0xF6, 0xF9)
            para = cell.text_frame.paragraphs[0]
            run = para.add_run()
            run.text = value
            _font(run, size, color=INK)


def _image_slide(deck: Presentation, spec: dict[str, Any], page: int, footer: str | None) -> None:
    from PIL import Image

    slide = _slide_chrome(deck, spec.get("title", ""), page, footer)
    path = Path(spec["path"])
    with Image.open(path) as image:
        width_px, height_px = image.size

    box_w, box_h = SLIDE_W - 2 * MARGIN, Inches(4.9)
    scale = min(box_w / width_px, box_h / height_px)
    width, height = int(width_px * scale), int(height_px * scale)
    slide.shapes.add_picture(
        str(path),
        MARGIN + (box_w - width) // 2,
        Inches(1.85) + (box_h - height) // 2,
        width=width,
        height=height,
    )


_RENDERERS = {
    "bullets": _bullets_slide,
    "table": _table_slide,
    "image": _image_slide,
}


def build_deck(spec: dict[str, Any], out: Path) -> None:
    deck = Presentation()
    deck.slide_width = SLIDE_W
    deck.slide_height = SLIDE_H

    pages = spec.get("slides") or []
    if not pages:
        raise ValueError("a deck needs at least one slide in 'slides'")

    _title_slide(deck, spec)
    footer = spec.get("footer")
    for number, page_spec in enumerate(pages, start=2):
        kind = page_spec.get("type", "bullets")
        if kind == "section":
            _section_slide(deck, page_spec.get("title", ""))
            continue
        renderer = _RENDERERS.get(kind)
        if renderer is None:
            raise ValueError(f"unknown slide type {kind!r}; use bullets, table, image or section")
        renderer(deck, page_spec, number, footer)

    deck.save(str(out))


# === sheet ==================================================================


def build_sheet(spec: dict[str, Any], out: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter, range_boundaries

    headers = [str(h) for h in spec.get("headers") or []]
    rows = spec.get("rows") or []
    if not headers:
        raise ValueError("a sheet needs 'headers'")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = spec.get("sheet") or "Sheet1"
    sheet.append(headers)

    head_fill = PatternFill("solid", fgColor="1F3864")
    for column, _ in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column)
        cell.font = Font(name=FAMILY, bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row in rows:
        sheet.append([self_or_blank(value) for value in row])

    for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row):
        for cell in row:
            cell.font = Font(name=FAMILY)
            if isinstance(cell.value, (int, float)):
                cell.alignment = Alignment(horizontal="right")

    for column, header in enumerate(headers, start=1):
        width = max(
            [len(str(header))]
            + [len(str(row[column - 1])) for row in rows if len(row) >= column]
            + [8]
        )
        # Wide enough for the longest value, capped so one URL cannot push the
        # rest of the table off the screen.
        sheet.column_dimensions[get_column_letter(column)].width = min(46, width * 1.6)

    # Clamped to the rows that hold data: a spec that says `B2:B99` for a
    # two-row table would otherwise leave 97 empty formatted cells, and the used
    # range a person sees when they select the data becomes the whole column.
    last_row = sheet.max_row
    for reference, number_format in (spec.get("number_formats") or {}).items():
        min_col, min_row, max_col, max_row = range_boundaries(reference)
        for row in sheet.iter_rows(
            min_row=min_row,
            max_row=min(max_row, last_row),
            min_col=min_col,
            max_col=max_col,
        ):
            for cell in row:
                cell.number_format = number_format

    sheet.freeze_panes = "A2"
    workbook.save(str(out))


def self_or_blank(value: Any) -> Any:
    """Keep numbers as numbers so Excel can sum them; everything else as text."""
    return value if isinstance(value, (int, float, bool)) or value is None else str(value)


# === doc ====================================================================

_HEADINGS = {"h1": 18, "h2": 15, "h3": 13}


def build_doc(spec: dict[str, Any], out: Path) -> None:
    from docx import Document
    from docx.oxml.ns import qn as docx_qn
    from docx.shared import Cm, Pt as DocxPt

    document = Document()

    # A4, not the template's US Letter: every office this is written for prints
    # A4, and a page size nobody notices until they press print is a reprint.
    for section in document.sections:
        section.page_width = Cm(21)
        section.page_height = Cm(29.7)
        section.left_margin = Cm(2.6)
        section.right_margin = Cm(2.6)

    for style_name in ("Normal",):
        style = document.styles[style_name]
        style.font.name = FAMILY
        style.font.size = DocxPt(11)
        # python-docx sets the Latin font only; the east-Asian font lives in a
        # second attribute Word reads for Chinese text.
        style.element.rPr.rFonts.set(docx_qn("w:eastAsia"), FAMILY)

    title = spec.get("title")
    if title:
        heading = document.add_heading(level=0)
        run = heading.add_run(title)
        run.font.name = FAMILY
        run.font.size = DocxPt(22)
        run._element.rPr.rFonts.set(docx_qn("w:eastAsia"), FAMILY)

    for block in spec.get("blocks") or []:
        kind = block.get("type", "p")
        if kind in _HEADINGS:
            heading = document.add_heading(level=int(kind[1]))
            run = heading.add_run(block.get("text", ""))
            run.font.name = FAMILY
            run.font.size = DocxPt(_HEADINGS[kind])
            run._element.rPr.rFonts.set(docx_qn("w:eastAsia"), FAMILY)
        elif kind == "bullets":
            for item in block.get("items") or []:
                paragraph = document.add_paragraph(style="List Bullet")
                run = paragraph.add_run(str(item))
                run.font.name = FAMILY
                run._element.rPr.rFonts.set(docx_qn("w:eastAsia"), FAMILY)
        elif kind == "table":
            headers = [str(h) for h in block.get("headers") or []]
            rows = block.get("rows") or []
            table = document.add_table(rows=1, cols=len(headers))
            table.style = "Table Grid"
            for index, header in enumerate(headers):
                cell = table.rows[0].cells[index]
                cell.text = header
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.bold = True
                        run.font.name = FAMILY
                        run._element.rPr.rFonts.set(docx_qn("w:eastAsia"), FAMILY)
            for row in rows:
                cells = table.add_row().cells
                for index, value in enumerate(row[: len(headers)]):
                    cells[index].text = str(value)
                    for paragraph in cells[index].paragraphs:
                        for run in paragraph.runs:
                            run.font.name = FAMILY
                            run._element.rPr.rFonts.set(docx_qn("w:eastAsia"), FAMILY)
        else:
            paragraph = document.add_paragraph()
            run = paragraph.add_run(block.get("text", ""))
            run.font.name = FAMILY
            run._element.rPr.rFonts.set(docx_qn("w:eastAsia"), FAMILY)

    document.save(str(out))


# === entry point ============================================================

_BUILDERS = {"deck": build_deck, "sheet": build_sheet, "doc": build_doc}

# Where the person can actually reach the file: the conversation's file panel lists
# this directory and the viewer reads from it, so a document written anywhere else is
# one they cannot find and cannot open.
WORKSPACE = Path(os.environ.get("AGENTICOS_WORKSPACE") or "/workspace")


def deliverable(requested: Path) -> Path:
    """Where the document is really written.

    A deck built under ``/tmp`` builds perfectly and is unreachable: the panel beside
    the chat lists the workspace, the viewer reads from the workspace, and an agent
    that names ``/tmp/blue-shan-app.pptx`` therefore hands over a path nobody can
    open. So the *name* is kept and the directory is moved into the workspace, and the
    line printed back says where it went - the model repeats that line, which is what
    keeps what it tells the person true.

    Skipped where there is no workspace at all (a plain shell on somebody's machine),
    because there is nothing to move it into.
    """
    if not WORKSPACE.is_dir():
        return requested
    try:
        resolved = requested.resolve()
    except OSError:
        return requested
    if resolved == WORKSPACE or WORKSPACE in resolved.parents:
        return requested
    return WORKSPACE / requested.name


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in _BUILDERS:
        print("usage: office.py deck|sheet|doc <spec.json> <out>", file=sys.stderr)
        return 2
    _, kind, spec_path, out_path = argv
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    requested = Path(out_path)
    out = deliverable(requested)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out != requested:
        print(
            f"note: {requested} is outside the workspace, so it could not be opened "
            f"from the chat; wrote {out} instead"
        )
    _BUILDERS[kind](spec, out)
    print(f"{out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
