---
name: office-docs
description: When someone asks for a deck, a Word document or a spreadsheet as a file — .pptx, .docx, .xlsx — rather than as an answer in chat.
category: operations
---

# Deck, document, spreadsheet

The deliverable is a **file**, in the format they named. A polished HTML page, a
long chat message or a PDF is not what "做一份 PPT" means, even when it reads well.
Make the file, then link it.

`office.py` beside this skill builds all three from a JSON spec, in one house
style. Use it instead of writing `python-pptx` calls inline: the theme, the CJK
font handling and the table sizing are already decided, and a deck assembled by
hand comes out inconsistent with the last one.

```bash
python3 /workspace/skills/office-docs/office.py deck  spec.json  Q3-review.pptx
python3 /workspace/skills/office-docs/office.py sheet spec.json  Q3-numbers.xlsx
python3 /workspace/skills/office-docs/office.py doc   spec.json  Q3-minutes.docx
```

Write the spec to a file with `write_file`, run the command, and read its one-line
output — it prints the path and byte size, or the reason the spec was refused.

## The deck spec

```json
{
  "title": "2026 年第三季度经营分析",
  "subtitle": "收入、毛利与下季度计划",
  "date": "2026-11-02",
  "footer": "经营分析",
  "slides": [
    {"type": "section", "title": "第一部分 · 收入"},
    {"type": "bullets", "title": "本季度三个结论", "bullets": [
        "营收同比增长 18.4%，主要来自华东区",
        {"text": "毛利率下降 1.2 个百分点", "level": 1},
        "续费率 92%，高于目标 2 个百分点"
    ]},
    {"type": "table", "title": "分区域收入", "headers": ["区域", "收入（万元）", "同比"],
     "rows": [["华东", "4,820", "+24%"], ["华南", "3,110", "+11%"]]},
    {"type": "image", "title": "趋势", "path": "/workspace/chart.png"}
  ]
}
```

- `bullets` items are strings, or objects with `level` (0 or 1) and `bold`.
- `table` needs `headers`; rows are truncated to the header count.
- `image` needs `path` to a file that exists — a chart you drew, not a URL.
- `section` is a full-bleed divider; use it to break a deck over six slides.
- The title slide comes from `title`/`subtitle`/`date`; do not add it to `slides`.

Six to twelve slides for a normal review. A deck that restates the request in
twenty slides is worse than eight that say something.

## The sheet spec

```json
{
  "sheet": "分区域",
  "headers": ["区域", "收入", "毛利率"],
  "rows": [["华东", 4820, 0.421], ["华南", 3110, 0.398]],
  "number_formats": {"B2:B99": "#,##0", "C2:C99": "0.0%"}
}
```

Numbers stay numbers — a cell holding `"4,820"` as text cannot be summed, and the
first thing anybody does with a spreadsheet is sum it. Put the unit in the header
(`收入（万元）`), not in the value.

## The document spec

```json
{
  "title": "季度经营会纪要",
  "blocks": [
    {"type": "h1", "text": "结论"},
    {"type": "p", "text": "本季度完成收入目标。"},
    {"type": "bullets", "items": ["华东超额 8%", "华南缺口 3%"]},
    {"type": "table", "headers": ["议题", "结论"], "rows": [["预算", "维持不变"]]}
  ]
}
```

`h1`/`h2`/`h3`, `p`, `bullets`, `table`. Paragraphs are sentences, not a wall.

## Check it before you hand it over

```bash
soffice --headless --convert-to png --outdir /tmp/check Q3-review.pptx
```

Then `read_file` the PNG. You are looking for two things: text that runs off the
slide, and characters rendered as empty boxes. Fix the spec and rebuild — do not
describe the fix instead of making it. For a document, convert to PDF and check
the page count against what you expected.

`soffice` renders with the fonts installed here, not the ones on their machine.
This workspace carries Noto Sans CJK, so Chinese renders in the check even though
the file names 微软雅黑 — the substitution is what you want, not a problem to fix.
Name 微软雅黑 anyway: the person opening it on Windows has it.

## Charts

Draw with `matplotlib` and drop the PNG into an `image` slide:

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.family"] = "Noto Sans CJK SC"
plt.rcParams["axes.unicode_minus"] = False
```

Without the two `rcParams` lines a Chinese label is a row of empty boxes. `Agg`
because there is no display.

## Where the files go, and what to say

Write them under `/workspace`, with a name somebody can find next week:
`2026-Q3-经营分析.pptx`, not `output.pptx`. They appear in **Documents** and in the
conversation's file panel.

Then: one short message. What you made, how many slides or rows, and the one thing
worth their attention — not a transcription of the content they can open.

Never invent the numbers a deck is built on. If the data is not in the
conversation, a file they attached or a tool you can call, ask for it: a
beautifully typeset wrong figure is the most expensive thing on this list.
