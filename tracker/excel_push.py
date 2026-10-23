"""Describe a benchmark sheet as Excel Graph edits.

OneDrive sometimes returns the shared workbook as a protected package rather
than an xlsx zip. openpyxl cannot read that package, and uploading a rebuilt
file would replace every other tab. These edits add one sheet through Excel
itself, which already has the file open for the signed-in account.
"""

from __future__ import annotations

import re
from io import BytesIO

from .export_xlsx import ExportError

_CELL_REF = re.compile(r"\$([A-Z]{1,3})\$(\d+)")
_LINE = ("4F81BD", "C0504D", "9BBB59", "8064A2", "4BACC6", "F79646")
_CHUNK_ROWS = 30
_BORDER = {"style": "Continuous", "color": "#D6D3D1", "weight": "Thin"}
# openpyxl column width is in characters (8.43 is Excel's default). Graph's
# columnWidth uses a point-like unit where that same default is 54.
_CHAR_WIDTH = 8.43
_POINT_WIDTH = 54.0


def describe(xlsx_bytes: bytes) -> dict:
    """Cell writes, styles, and charts for the first sheet of a workbook."""
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import column_index_from_string, get_column_letter
    except ImportError as exc:
        raise ExportError("openpyxl is required to write an xlsx file") from exc

    book = load_workbook(BytesIO(xlsx_bytes))
    sheet = book.active
    values: dict[tuple[int, int], object] = {}
    formats: dict[tuple[int, int], str] = {}
    fonts: dict[tuple[int, int], tuple] = {}
    fills: dict[tuple[int, int], str] = {}
    aligns: dict[tuple[int, int], tuple] = {}

    for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row or 1, max_col=sheet.max_column or 1):
        for cell in row:
            if not _touched(cell):
                continue
            values[(cell.row, cell.column)] = _stored(cell)
            formats[(cell.row, cell.column)] = cell.number_format or "General"
            fonts[(cell.row, cell.column)] = _font_key(cell)
            fill = _fill_color(cell)
            if fill:
                fills[(cell.row, cell.column)] = fill
            aligns[(cell.row, cell.column)] = _align_key(cell)

    for merged in sheet.merged_cells.ranges:
        origin = (merged.min_row, merged.min_col)
        if origin not in fonts:
            continue
        for row in range(merged.min_row, merged.max_row + 1):
            for col in range(merged.min_col, merged.max_col + 1):
                fonts[(row, col)] = fonts[origin]
                aligns[(row, col)] = aligns[origin]
                if origin in fills:
                    fills[(row, col)] = fills[origin]

    chunks = _chunks(values, formats, get_column_letter)
    style_rows = {
        "base_font": _base_call(fonts, {"name": "Arial", "size": 11, "bold": False}, get_column_letter),
        "base_align": _base_call(
            aligns,
            {"horizontalAlignment": "Center", "verticalAlignment": "Center", "wrapText": True},
            get_column_letter,
        ),
        "fonts": [_font_call(rect, get_column_letter) for rect in _rects({pos: key for pos, key in fonts.items() if key[2]})],
        "fills": [_fill_call(rect, get_column_letter) for rect in _rects(fills)],
        "aligns": [
            _align_call(rect, get_column_letter)
            for rect in _rects({pos: key for pos, key in aligns.items() if key != ("Center", "Center", True)})
        ],
        "borders": _border_calls(set(fonts), get_column_letter),
        "merges": [str(merged) for merged in sheet.merged_cells.ranges],
        "widths": [
            {"address": f"{letter}:{letter}", "width": _column_points(dim.width)}
            for letter, dim in sheet.column_dimensions.items()
            if dim.width
        ],
        "heights": [
            {"address": f"{index}:{index}", "height": dim.height}
            for index, dim in sheet.row_dimensions.items()
            if dim.height
        ],
    }
    charts, hide = _charts(sheet, get_column_letter, column_index_from_string)
    return {"chunks": chunks, **style_rows, "charts": charts, "hide": hide}


def _column_points(width: float) -> float:
    return round(float(width) * _POINT_WIDTH / _CHAR_WIDTH, 2)


def place_request(_title: str) -> dict:
    """The new tab sits immediately to the right of the leftmost tab."""
    return {"position": 1}


def _touched(cell) -> bool:
    if cell.value not in (None, ""):
        return True
    if _fill_color(cell):
        return True
    return bool(cell.number_format and cell.number_format != "General")


def _stored(cell):
    value = cell.value
    if value is None or value == "":
        return None
    if getattr(cell, "data_type", None) == "s" and isinstance(value, str) and value.startswith("="):
        return "'" + value
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ") if hasattr(value, "hour") else value.isoformat()
    return value


def _font_key(cell) -> tuple:
    font = cell.font
    size = font.size or 11
    if float(size) == int(size):
        size = int(size)
    return (font.name or "Arial", size, bool(font.bold))


def _align_key(cell) -> tuple:
    align = cell.alignment
    horizontal = (align.horizontal or "center").capitalize()
    vertical = (align.vertical or "center").capitalize()
    return (horizontal, vertical, bool(align.wrap_text))


def _fill_color(cell) -> str | None:
    fill = cell.fill
    if fill is None or fill.patternType != "solid":
        return None
    color = fill.fgColor
    if color is None:
        return None
    try:
        rgb = color.rgb
    except Exception:
        return None
    if not isinstance(rgb, str):
        return None
    hexes = rgb[-6:]
    if len(hexes) != 6 or any(ch not in "0123456789ABCDEFabcdef" for ch in hexes):
        return None
    if hexes.upper() == "000000" and rgb in ("00000000", "0"):
        return None
    return "#" + hexes.upper()


def _chunks(values, formats, get_column_letter) -> list[dict]:
    if not values:
        return []
    rows = [row for row, _col in values]
    cols = [col for _row, col in values]
    min_row, max_row = min(rows), max(rows)
    min_col, max_col = min(cols), max(cols)
    width = max_col - min_col + 1
    chunks = []
    top = min_row
    while top <= max_row:
        bottom = min(top + _CHUNK_ROWS - 1, max_row)
        formulas = []
        number_format = []
        for row in range(top, bottom + 1):
            formulas.append([values.get((row, col)) for col in range(min_col, max_col + 1)])
            number_format.append([formats.get((row, col), "General") for col in range(min_col, max_col + 1)])
        chunks.append({
            "address": _span(min_col, top, width, bottom - top + 1, get_column_letter),
            "formulas": formulas,
            "numberFormat": number_format,
        })
        top = bottom + 1
    return chunks


def _rects(points: dict[tuple[int, int], object]) -> list[tuple]:
    seen = set()
    found = []
    for row, col in sorted(points):
        if (row, col) in seen:
            continue
        key = points[(row, col)]
        width = 1
        while points.get((row, col + width)) == key and (row, col + width) not in seen:
            width += 1
        height = 1
        while all(
            points.get((row + height, col + offset)) == key and (row + height, col + offset) not in seen
            for offset in range(width)
        ):
            height += 1
        for down in range(height):
            for across in range(width):
                seen.add((row + down, col + across))
        found.append((row, col, height, width, key))
    return found


def _base_call(points: dict, body: dict, get_column_letter) -> dict | None:
    if not points:
        return None
    rows = [row for row, _col in points]
    cols = [col for _row, col in points]
    return {
        "address": _span(min(cols), min(rows), max(cols) - min(cols) + 1, max(rows) - min(rows) + 1, get_column_letter),
        "body": body,
    }


def _font_call(rect, get_column_letter) -> dict:
    row, col, height, width, (name, size, bold) = rect
    return {
        "address": _span(col, row, width, height, get_column_letter),
        "body": {"name": name, "size": size, "bold": bold},
    }


def _fill_call(rect, get_column_letter) -> dict:
    row, col, height, width, color = rect
    return {"address": _span(col, row, width, height, get_column_letter), "body": {"color": color}}


def _align_call(rect, get_column_letter) -> dict:
    row, col, height, width, (horizontal, vertical, wrap) = rect
    return {
        "address": _span(col, row, width, height, get_column_letter),
        "body": {
            "horizontalAlignment": horizontal,
            "verticalAlignment": vertical,
            "wrapText": wrap,
        },
    }


def _border_calls(cells: set[tuple[int, int]], get_column_letter) -> list[dict]:
    calls = []
    for row, col, height, width in _bands(cells):
        address = _span(col, row, width, height, get_column_letter)
        edges = ["EdgeTop", "EdgeBottom", "EdgeLeft", "EdgeRight"]
        if height > 1:
            edges.append("InsideHorizontal")
        if width > 1:
            edges.append("InsideVertical")
        for edge in edges:
            calls.append({"address": address, "edge": edge, "body": dict(_BORDER)})
    return calls


def _bands(cells: set[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """One rectangle per run of consecutive rows, spanning that run's columns."""
    rows = sorted({row for row, _col in cells})
    if not rows:
        return []
    groups = []
    start = previous = rows[0]
    for row in rows[1:]:
        if row == previous + 1:
            previous = row
            continue
        groups.append((start, previous))
        start = previous = row
    groups.append((start, previous))
    bands = []
    for top, bottom in groups:
        cols = [col for row, col in cells if top <= row <= bottom]
        bands.append((top, min(cols), bottom - top + 1, max(cols) - min(cols) + 1))
    return bands


def _span(col: int, row: int, width: int, height: int, get_column_letter) -> str:
    start = f"{get_column_letter(col)}{row}"
    if width == 1 and height == 1:
        return start
    return f"{start}:{get_column_letter(col + width - 1)}{row + height - 1}"


def _charts(sheet, get_column_letter, column_index_from_string) -> tuple[list[dict], list[str]]:
    charts = []
    cursor_row = 1
    start_col = (sheet.max_column or 1) + 2
    widest = 0
    for chart in getattr(sheet, "_charts", []):
        series = []
        for item in chart.series:
            built = _series(item, column_index_from_string)
            if built is not None:
                series.append(built)
        if not series:
            continue
        formulas = [[None, *[item["name"] for item in series]]]
        for item in series:
            _col, row1, row2 = item["x"]
            y_col, y1, _y2 = item["y"]
            for offset in range(row2 - row1 + 1):
                row = [f"={get_column_letter(_col)}{row1 + offset}"]
                for other in series:
                    if other is item:
                        row.append(f"={get_column_letter(y_col)}{y1 + offset}")
                    else:
                        row.append("=NA()")
                formulas.append(row)
        width = 1 + len(series)
        height = len(formulas)
        address = _span(start_col, cursor_row, width, height, get_column_letter)
        origin_col, origin_row = _anchor(chart)
        charts.append({
            "address": address,
            "source": address,
            "formulas": formulas,
            "title": _title(chart),
            "start": f"{get_column_letter(origin_col)}{origin_row}",
            "end": f"{get_column_letter(origin_col + 12)}{origin_row + 16}",
            "colors": ["#" + _LINE[index % len(_LINE)] for index in range(len(series))],
        })
        widest = max(widest, width)
        cursor_row += height + 1
    hide = [f"{get_column_letter(start_col + offset)}:{get_column_letter(start_col + offset)}" for offset in range(widest)]
    return charts, hide


def _series(item, column_index_from_string) -> dict | None:
    x_ref = _ref(getattr(item, "xVal", None))
    y_ref = _ref(getattr(item, "yVal", None)) or _ref(getattr(item, "val", None))
    if not x_ref or not y_ref:
        return None
    x_span = _vertical(x_ref, column_index_from_string)
    y_span = _vertical(y_ref, column_index_from_string)
    if x_span is None or y_span is None or (x_span[2] - x_span[1]) != (y_span[2] - y_span[1]):
        return None
    title = getattr(item, "tx", None)
    name = getattr(title, "v", None) or ""
    return {"name": str(name), "x": x_span, "y": y_span}


def _ref(value) -> str:
    num_ref = getattr(value, "numRef", None)
    return getattr(num_ref, "f", None) or ""


def _vertical(formula: str, column_index_from_string) -> tuple[int, int, int] | None:
    found = _CELL_REF.findall(formula)
    if not found:
        return None
    columns = {column_index_from_string(col) for col, _row in found}
    if len(columns) != 1:
        return None
    rows = [int(row) for _col, row in found]
    return next(iter(columns)), min(rows), max(rows)


def _anchor(chart) -> tuple[int, int]:
    origin = getattr(chart.anchor, "_from", None)
    if origin is None:
        return 2, 2
    return int(origin.col) + 1, int(origin.row) + 1


def _title(chart) -> str:
    title = chart.title
    rich = getattr(getattr(title, "tx", None), "rich", None)
    if rich is None:
        return ""
    parts = []
    for paragraph in rich.paragraphs or []:
        for run in getattr(paragraph, "r", None) or []:
            if getattr(run, "t", None):
                parts.append(str(run.t))
    return "".join(parts)
