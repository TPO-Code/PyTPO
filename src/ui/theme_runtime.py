from __future__ import annotations

import re
from typing import Any

from PySide6.QtWidgets import QApplication, QPushButton, QWidget


APP_PROP_SETTINGS_COLOR_SWATCH_WIDTH = "theme.settings.color_swatch.width"
APP_PROP_SETTINGS_COLOR_SWATCH_HEIGHT = "theme.settings.color_swatch.height"

DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH = 34
DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT = 20

_PX_RE = re.compile(r"^\s*(-?\d+)\s*(px)?\s*$", re.IGNORECASE)


def coerce_metric_px(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return max(1, int(default))
    if isinstance(value, int):
        return max(1, int(value))
    if isinstance(value, float):
        return max(1, int(round(value)))

    text = str(value or "").strip()
    if not text:
        return max(1, int(default))
    match = _PX_RE.fullmatch(text)
    if not match:
        return max(1, int(default))
    try:
        return max(1, int(match.group(1)))
    except Exception:
        return max(1, int(default))


def current_settings_color_swatch_size(*, app: QApplication | None = None) -> tuple[int, int]:
    qapp = app or QApplication.instance()
    if qapp is None:
        return DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH, DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT

    width = coerce_metric_px(
        qapp.property(APP_PROP_SETTINGS_COLOR_SWATCH_WIDTH),
        default=DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH,
    )
    height = coerce_metric_px(
        qapp.property(APP_PROP_SETTINGS_COLOR_SWATCH_HEIGHT),
        default=DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT,
    )
    return width, height


def set_settings_color_swatch_size(
    *,
    width: int,
    height: int,
    app: QApplication | None = None,
) -> tuple[int, int]:
    qapp = app or QApplication.instance()
    normalized_width = coerce_metric_px(width, default=DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH)
    normalized_height = coerce_metric_px(height, default=DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT)
    if qapp is None:
        return normalized_width, normalized_height

    qapp.setProperty(APP_PROP_SETTINGS_COLOR_SWATCH_WIDTH, normalized_width)
    qapp.setProperty(APP_PROP_SETTINGS_COLOR_SWATCH_HEIGHT, normalized_height)
    return normalized_width, normalized_height


def apply_settings_color_swatch_size(widget: QPushButton, *, app: QApplication | None = None) -> None:
    if not isinstance(widget, QPushButton):
        return
    width, height = current_settings_color_swatch_size(app=app)
    widget.setProperty("role", "color-swatch")
    widget.setFixedSize(width, height)


def refresh_settings_color_swatch_widgets(
    *,
    app: QApplication | None = None,
    width: int | None = None,
    height: int | None = None,
) -> None:
    qapp = app or QApplication.instance()
    if qapp is None:
        return

    if width is None or height is None:
        width, height = current_settings_color_swatch_size(app=qapp)
    else:
        width = coerce_metric_px(width, default=DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH)
        height = coerce_metric_px(height, default=DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT)

    for widget in qapp.allWidgets():
        if not isinstance(widget, QWidget):
            continue
        if str(widget.property("role") or "") != "color-swatch":
            continue
        if hasattr(widget, "setFixedSize"):
            try:
                widget.setFixedSize(width, height)
            except Exception:
                continue
