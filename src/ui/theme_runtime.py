from __future__ import annotations

import re
from typing import Any

from PySide6.QtWidgets import QApplication, QPushButton, QWidget


APP_PROP_SETTINGS_COLOR_SWATCH_WIDTH = "theme.settings.color_swatch.width"
APP_PROP_SETTINGS_COLOR_SWATCH_HEIGHT = "theme.settings.color_swatch.height"
APP_PROP_EDITOR_SEARCH_TOP_MARGIN_MIN = "theme.editor.search.top_margin_min"
APP_PROP_EDITOR_OVERVIEW_GAP = "theme.editor.overview.gap"
APP_PROP_CODEX_AGENT_BUBBLE_TEXT_COLOR = "theme.codex_agent.bubble.text_color"
APP_PROP_CODEX_AGENT_BUBBLE_BORDER_WIDTH = "theme.codex_agent.bubble.border_width"

DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH = 34
DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT = 20
DEFAULT_EDITOR_SEARCH_TOP_MARGIN_MIN = 30
DEFAULT_EDITOR_OVERVIEW_GAP = 1
DEFAULT_CODEX_AGENT_BUBBLE_TEXT_COLOR = "#e6edf3"
DEFAULT_CODEX_AGENT_BUBBLE_BORDER_WIDTH = "1px"

_CODEX_AGENT_ROLE_DEFAULTS: dict[str, dict[str, str]] = {
    "default": {"border_color": "#2f3746", "background_color": "#1a1f2a"},
    "user": {"border_color": "#2e6ad9", "background_color": "#16386f"},
    "assistant": {"border_color": "#2f3f5e", "background_color": "#1a1f2a"},
    "thinking": {"border_color": "#7d5ba6", "background_color": "#2a2234"},
    "tools": {"border_color": "#2e5c47", "background_color": "#1d2a24"},
    "diff": {"border_color": "#2c6a4f", "background_color": "#111a14"},
    "system": {"border_color": "#3f4b5f", "background_color": "#232b38"},
    "meta": {"border_color": "#3f4b5f", "background_color": "#232b38"},
}

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


def coerce_metric_px_min(value: Any, *, default: int, minimum: int = 0) -> int:
    floor = max(0, int(minimum))
    fallback = max(floor, int(default))
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return max(floor, int(value))
    if isinstance(value, float):
        return max(floor, int(round(value)))

    text = str(value or "").strip()
    if not text:
        return fallback
    match = _PX_RE.fullmatch(text)
    if not match:
        return fallback
    try:
        return max(floor, int(match.group(1)))
    except Exception:
        return fallback


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


def current_editor_viewport_spacing(*, app: QApplication | None = None) -> tuple[int, int]:
    qapp = app or QApplication.instance()
    if qapp is None:
        return DEFAULT_EDITOR_SEARCH_TOP_MARGIN_MIN, DEFAULT_EDITOR_OVERVIEW_GAP
    search_top_margin = coerce_metric_px_min(
        qapp.property(APP_PROP_EDITOR_SEARCH_TOP_MARGIN_MIN),
        default=DEFAULT_EDITOR_SEARCH_TOP_MARGIN_MIN,
        minimum=0,
    )
    overview_gap = coerce_metric_px_min(
        qapp.property(APP_PROP_EDITOR_OVERVIEW_GAP),
        default=DEFAULT_EDITOR_OVERVIEW_GAP,
        minimum=0,
    )
    return search_top_margin, overview_gap


def set_editor_viewport_spacing(
    *,
    search_top_margin_min: int,
    overview_gap: int,
    app: QApplication | None = None,
) -> tuple[int, int]:
    qapp = app or QApplication.instance()
    normalized_search_top_margin = coerce_metric_px_min(
        search_top_margin_min,
        default=DEFAULT_EDITOR_SEARCH_TOP_MARGIN_MIN,
        minimum=0,
    )
    normalized_overview_gap = coerce_metric_px_min(
        overview_gap,
        default=DEFAULT_EDITOR_OVERVIEW_GAP,
        minimum=0,
    )
    if qapp is None:
        return normalized_search_top_margin, normalized_overview_gap
    qapp.setProperty(APP_PROP_EDITOR_SEARCH_TOP_MARGIN_MIN, normalized_search_top_margin)
    qapp.setProperty(APP_PROP_EDITOR_OVERVIEW_GAP, normalized_overview_gap)
    return normalized_search_top_margin, normalized_overview_gap


def refresh_editor_viewport_widgets(*, app: QApplication | None = None) -> None:
    qapp = app or QApplication.instance()
    if qapp is None:
        return
    for widget in qapp.allWidgets():
        apply_margins = getattr(widget, "_apply_viewport_margins", None)
        if callable(apply_margins):
            try:
                apply_margins()
            except Exception:
                pass
        position_search = getattr(widget, "_position_search_bar", None)
        if callable(position_search):
            try:
                position_search()
            except Exception:
                pass


def current_codex_agent_bubble_theme(
    role: str,
    *,
    app: QApplication | None = None,
) -> dict[str, str]:
    qapp = app or QApplication.instance()
    normalized_role = str(role or "").strip() or "default"
    role_defaults = _CODEX_AGENT_ROLE_DEFAULTS.get(normalized_role, _CODEX_AGENT_ROLE_DEFAULTS["default"])
    text_color = DEFAULT_CODEX_AGENT_BUBBLE_TEXT_COLOR
    border_width = DEFAULT_CODEX_AGENT_BUBBLE_BORDER_WIDTH
    border_color = role_defaults["border_color"]
    background_color = role_defaults["background_color"]
    if qapp is None:
        return {
            "text_color": text_color,
            "border_width": border_width,
            "border_color": border_color,
            "background_color": background_color,
        }
    text_color = str(qapp.property(APP_PROP_CODEX_AGENT_BUBBLE_TEXT_COLOR) or text_color)
    border_width = str(qapp.property(APP_PROP_CODEX_AGENT_BUBBLE_BORDER_WIDTH) or border_width)
    border_color = str(
        qapp.property(f"theme.codex_agent.roles.{normalized_role}.border_color") or border_color
    )
    background_color = str(
        qapp.property(f"theme.codex_agent.roles.{normalized_role}.background_color") or background_color
    )
    return {
        "text_color": text_color,
        "border_width": border_width,
        "border_color": border_color,
        "background_color": background_color,
    }


def set_codex_agent_bubble_theme(
    *,
    text_color: Any,
    border_width: Any,
    role_colors: dict[str, dict[str, Any]] | None = None,
    app: QApplication | None = None,
) -> dict[str, Any]:
    qapp = app or QApplication.instance()
    normalized_text_color = str(text_color or DEFAULT_CODEX_AGENT_BUBBLE_TEXT_COLOR).strip()
    if not normalized_text_color:
        normalized_text_color = DEFAULT_CODEX_AGENT_BUBBLE_TEXT_COLOR
    normalized_border_width = str(border_width or DEFAULT_CODEX_AGENT_BUBBLE_BORDER_WIDTH).strip()
    if not normalized_border_width:
        normalized_border_width = DEFAULT_CODEX_AGENT_BUBBLE_BORDER_WIDTH

    normalized_roles: dict[str, dict[str, str]] = {}
    for role_name, defaults in _CODEX_AGENT_ROLE_DEFAULTS.items():
        provided = role_colors.get(role_name, {}) if isinstance(role_colors, dict) else {}
        border_color = str(provided.get("border_color") or defaults["border_color"]).strip()
        background_color = str(provided.get("background_color") or defaults["background_color"]).strip()
        normalized_roles[role_name] = {
            "border_color": border_color or defaults["border_color"],
            "background_color": background_color or defaults["background_color"],
        }

    if qapp is not None:
        qapp.setProperty(APP_PROP_CODEX_AGENT_BUBBLE_TEXT_COLOR, normalized_text_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_BUBBLE_BORDER_WIDTH, normalized_border_width)
        for role_name, values in normalized_roles.items():
            qapp.setProperty(
                f"theme.codex_agent.roles.{role_name}.border_color",
                values["border_color"],
            )
            qapp.setProperty(
                f"theme.codex_agent.roles.{role_name}.background_color",
                values["background_color"],
            )

    return {
        "text_color": normalized_text_color,
        "border_width": normalized_border_width,
        "roles": normalized_roles,
    }


def refresh_codex_agent_widgets(*, app: QApplication | None = None) -> None:
    qapp = app or QApplication.instance()
    if qapp is None:
        return
    for widget in qapp.allWidgets():
        apply_theme = getattr(widget, "_apply_codex_agent_theme", None)
        if callable(apply_theme):
            try:
                apply_theme()
            except Exception:
                pass
