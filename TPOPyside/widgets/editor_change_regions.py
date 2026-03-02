from __future__ import annotations

import re
from typing import Iterable

from PySide6.QtGui import QColor, QTextCursor, QTextDocument, QTextFormat
from PySide6.QtWidgets import QTextEdit

DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX = "#ffcc0030"
DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX = "#ff4d4d24"

# Explicit precedence rule used by all editor change overlays.
CHANGE_REGION_PRECEDENCE: tuple[str, ...] = ("dirty", "uncommitted", "normal")

_HEX_COLOR_RE = re.compile(r"^#(?P<rgb>[0-9a-fA-F]{6})(?P<alpha>[0-9a-fA-F]{2})?$")


def resolve_change_region_layer(
    line_number: int,
    *,
    dirty_lines: set[int],
    uncommitted_lines: set[int],
) -> str:
    line = int(line_number)
    if line in dirty_lines:
        return "dirty"
    if line in uncommitted_lines:
        return "uncommitted"
    return "normal"


def normalize_line_numbers(lines: Iterable[int] | None) -> set[int]:
    if lines is None:
        return set()
    out: set[int] = set()
    for raw in lines:
        try:
            line = int(raw)
        except Exception:
            continue
        if line > 0:
            out.add(line)
    return out


def parse_editor_overlay_color(value: object, fallback: str | QColor) -> QColor:
    fallback_color = QColor(fallback) if not isinstance(fallback, QColor) else QColor(fallback)
    if not fallback_color.isValid():
        fallback_color = QColor("#ffcc0030")

    if isinstance(value, QColor):
        color = QColor(value)
        return color if color.isValid() else fallback_color

    text = str(value or "").strip()
    if not text:
        return fallback_color

    match = _HEX_COLOR_RE.fullmatch(text)
    if not match:
        return fallback_color

    rgb = str(match.group("rgb") or "")
    alpha_hex = str(match.group("alpha") or "")
    try:
        red = int(rgb[0:2], 16)
        green = int(rgb[2:4], 16)
        blue = int(rgb[4:6], 16)
        alpha = int(alpha_hex, 16) if alpha_hex else 255
    except Exception:
        return fallback_color
    return QColor(red, green, blue, alpha)


def build_change_region_selections(
    document: QTextDocument,
    *,
    dirty_lines: set[int],
    uncommitted_lines: set[int],
    dirty_color: QColor,
    uncommitted_color: QColor,
    max_lines: int = 12000,
) -> list[QTextEdit.ExtraSelection]:
    if not isinstance(document, QTextDocument):
        return []

    dirty = normalize_line_numbers(dirty_lines)
    uncommitted = normalize_line_numbers(uncommitted_lines)
    if not dirty and not uncommitted:
        return []

    line_count = int(document.blockCount())
    if line_count <= 0:
        return []

    if len(dirty) > max_lines:
        dirty = set(sorted(dirty)[:max_lines])
    if len(uncommitted) > max_lines:
        uncommitted = set(sorted(uncommitted)[:max_lines])

    selections: list[QTextEdit.ExtraSelection] = []

    def _append_for_layer(layer: str, color: QColor) -> None:
        if not color.isValid():
            return
        for line in sorted(dirty | uncommitted):
            if line < 1 or line > line_count:
                continue
            resolved = resolve_change_region_layer(
                line,
                dirty_lines=dirty,
                uncommitted_lines=uncommitted,
            )
            if resolved != layer:
                continue
            block = document.findBlockByNumber(line - 1)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            cursor = QTextCursor(block)
            cursor.clearSelection()
            selection.cursor = cursor
            selection.format.setBackground(color)
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selections.append(selection)

    # Paint uncommitted first, then dirty so dirty wins on overlap.
    _append_for_layer("uncommitted", uncommitted_color)
    _append_for_layer("dirty", dirty_color)
    return selections
