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
APP_PROP_CODEX_AGENT_BUBBLE_HEADER_COLOR = "theme.codex_agent.bubble.header_color"
APP_PROP_CODEX_AGENT_BUBBLE_TOGGLE_COLOR = "theme.codex_agent.bubble.toggle_color"
APP_PROP_CODEX_AGENT_BUBBLE_PREVIEW_COLOR = "theme.codex_agent.bubble.preview_color"
APP_PROP_CODEX_AGENT_PANEL_BORDER_COLOR = "theme.codex_agent.panel.border_color"
APP_PROP_CODEX_AGENT_PANEL_BACKGROUND_COLOR = "theme.codex_agent.panel.background_color"
APP_PROP_CODEX_AGENT_PANEL_BORDER_WIDTH = "theme.codex_agent.panel.border_width"
APP_PROP_CODEX_AGENT_PANEL_RADIUS = "theme.codex_agent.panel.radius"
APP_PROP_CODEX_AGENT_PANEL_TITLE_COLOR = "theme.codex_agent.panel.title_color"
APP_PROP_CODEX_AGENT_PANEL_TEXT_COLOR = "theme.codex_agent.panel.text_color"
APP_PROP_CODEX_AGENT_PANEL_TITLE_FONT_SIZE = "theme.codex_agent.panel.title_font_size"
APP_PROP_CODEX_AGENT_PANEL_TEXT_FONT_SIZE = "theme.codex_agent.panel.text_font_size"
APP_PROP_CODEX_AGENT_PANEL_PADDING_X = "theme.codex_agent.panel.padding_x"
APP_PROP_CODEX_AGENT_PANEL_PADDING_Y = "theme.codex_agent.panel.padding_y"
APP_PROP_CODEX_AGENT_PANEL_SECTION_SPACING = "theme.codex_agent.panel.section_spacing"
APP_PROP_CODEX_AGENT_PANEL_STEP_SPACING = "theme.codex_agent.panel.step_spacing"
APP_PROP_CODEX_AGENT_PANEL_COMPLETED_COLOR = "theme.codex_agent.panel.completed_color"
APP_PROP_CODEX_AGENT_PANEL_IN_PROGRESS_COLOR = "theme.codex_agent.panel.in_progress_color"
APP_PROP_CODEX_AGENT_PANEL_PENDING_COLOR = "theme.codex_agent.panel.pending_color"
APP_PROP_CODEX_AGENT_COMPOSER_BORDER_COLOR = "theme.codex_agent.composer.border_color"
APP_PROP_CODEX_AGENT_COMPOSER_SHIMMER_COLOR = "theme.codex_agent.composer.shimmer_color"
APP_PROP_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR = "theme.codex_agent.composer.shimmer_highlight_color"
APP_PROP_CODEX_AGENT_LINK_COLOR = "theme.codex_agent.link.color"

DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH = 34
DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT = 20
DEFAULT_EDITOR_SEARCH_TOP_MARGIN_MIN = 30
DEFAULT_EDITOR_OVERVIEW_GAP = 1
DEFAULT_CODEX_AGENT_BUBBLE_TEXT_COLOR = "#e6edf3"
DEFAULT_CODEX_AGENT_BUBBLE_BORDER_WIDTH = "1px"
DEFAULT_CODEX_AGENT_BUBBLE_HEADER_COLOR = "#8d9cb4"
DEFAULT_CODEX_AGENT_BUBBLE_TOGGLE_COLOR = "#9db1cb"
DEFAULT_CODEX_AGENT_BUBBLE_PREVIEW_COLOR = "#c8d2e2"
DEFAULT_CODEX_AGENT_PANEL_BORDER_COLOR = "#3f4b5f"
DEFAULT_CODEX_AGENT_PANEL_BACKGROUND_COLOR = "#232b38"
DEFAULT_CODEX_AGENT_PANEL_BORDER_WIDTH = "1px"
DEFAULT_CODEX_AGENT_PANEL_RADIUS = "0px"
DEFAULT_CODEX_AGENT_PANEL_TITLE_COLOR = "#b7c6dc"
DEFAULT_CODEX_AGENT_PANEL_TEXT_COLOR = "#d7e0ec"
DEFAULT_CODEX_AGENT_PANEL_TITLE_FONT_SIZE = "11px"
DEFAULT_CODEX_AGENT_PANEL_TEXT_FONT_SIZE = "12px"
DEFAULT_CODEX_AGENT_PANEL_PADDING_X = 10
DEFAULT_CODEX_AGENT_PANEL_PADDING_Y = 8
DEFAULT_CODEX_AGENT_PANEL_SECTION_SPACING = 4
DEFAULT_CODEX_AGENT_PANEL_STEP_SPACING = 3
DEFAULT_CODEX_AGENT_PANEL_COMPLETED_COLOR = "#8fd19e"
DEFAULT_CODEX_AGENT_PANEL_IN_PROGRESS_COLOR = "#8fb4ff"
DEFAULT_CODEX_AGENT_PANEL_PENDING_COLOR = "#d7e0ec"
DEFAULT_CODEX_AGENT_COMPOSER_BORDER_COLOR = "#4a4a4a"
DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_COLOR = "rgba(120, 180, 255, 60)"
DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR = "rgba(180, 220, 255, 180)"
DEFAULT_CODEX_AGENT_LINK_COLOR = "#8ab4f8"

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
    header_color = DEFAULT_CODEX_AGENT_BUBBLE_HEADER_COLOR
    toggle_color = DEFAULT_CODEX_AGENT_BUBBLE_TOGGLE_COLOR
    preview_color = DEFAULT_CODEX_AGENT_BUBBLE_PREVIEW_COLOR
    border_color = role_defaults["border_color"]
    background_color = role_defaults["background_color"]
    if qapp is None:
        return {
            "text_color": text_color,
            "border_width": border_width,
            "header_color": header_color,
            "toggle_color": toggle_color,
            "preview_color": preview_color,
            "border_color": border_color,
            "background_color": background_color,
        }
    text_color = str(qapp.property(APP_PROP_CODEX_AGENT_BUBBLE_TEXT_COLOR) or text_color)
    border_width = str(qapp.property(APP_PROP_CODEX_AGENT_BUBBLE_BORDER_WIDTH) or border_width)
    header_color = str(qapp.property(APP_PROP_CODEX_AGENT_BUBBLE_HEADER_COLOR) or header_color)
    toggle_color = str(qapp.property(APP_PROP_CODEX_AGENT_BUBBLE_TOGGLE_COLOR) or toggle_color)
    preview_color = str(qapp.property(APP_PROP_CODEX_AGENT_BUBBLE_PREVIEW_COLOR) or preview_color)
    border_color = str(
        qapp.property(f"theme.codex_agent.roles.{normalized_role}.border_color") or border_color
    )
    background_color = str(
        qapp.property(f"theme.codex_agent.roles.{normalized_role}.background_color") or background_color
    )
    return {
        "text_color": text_color,
        "border_width": border_width,
        "header_color": header_color,
        "toggle_color": toggle_color,
        "preview_color": preview_color,
        "border_color": border_color,
        "background_color": background_color,
    }


def set_codex_agent_bubble_theme(
    *,
    text_color: Any,
    border_width: Any,
    header_color: Any = DEFAULT_CODEX_AGENT_BUBBLE_HEADER_COLOR,
    toggle_color: Any = DEFAULT_CODEX_AGENT_BUBBLE_TOGGLE_COLOR,
    preview_color: Any = DEFAULT_CODEX_AGENT_BUBBLE_PREVIEW_COLOR,
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
    normalized_header_color = str(header_color or DEFAULT_CODEX_AGENT_BUBBLE_HEADER_COLOR).strip()
    if not normalized_header_color:
        normalized_header_color = DEFAULT_CODEX_AGENT_BUBBLE_HEADER_COLOR
    normalized_toggle_color = str(toggle_color or DEFAULT_CODEX_AGENT_BUBBLE_TOGGLE_COLOR).strip()
    if not normalized_toggle_color:
        normalized_toggle_color = DEFAULT_CODEX_AGENT_BUBBLE_TOGGLE_COLOR
    normalized_preview_color = str(preview_color or DEFAULT_CODEX_AGENT_BUBBLE_PREVIEW_COLOR).strip()
    if not normalized_preview_color:
        normalized_preview_color = DEFAULT_CODEX_AGENT_BUBBLE_PREVIEW_COLOR

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
        qapp.setProperty(APP_PROP_CODEX_AGENT_BUBBLE_HEADER_COLOR, normalized_header_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_BUBBLE_TOGGLE_COLOR, normalized_toggle_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_BUBBLE_PREVIEW_COLOR, normalized_preview_color)
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
        "header_color": normalized_header_color,
        "toggle_color": normalized_toggle_color,
        "preview_color": normalized_preview_color,
        "roles": normalized_roles,
    }


def current_codex_agent_panel_theme(*, app: QApplication | None = None) -> dict[str, str]:
    qapp = app or QApplication.instance()
    border_color = DEFAULT_CODEX_AGENT_PANEL_BORDER_COLOR
    background_color = DEFAULT_CODEX_AGENT_PANEL_BACKGROUND_COLOR
    border_width = DEFAULT_CODEX_AGENT_PANEL_BORDER_WIDTH
    radius = DEFAULT_CODEX_AGENT_PANEL_RADIUS
    title_color = DEFAULT_CODEX_AGENT_PANEL_TITLE_COLOR
    text_color = DEFAULT_CODEX_AGENT_PANEL_TEXT_COLOR
    title_font_size = DEFAULT_CODEX_AGENT_PANEL_TITLE_FONT_SIZE
    text_font_size = DEFAULT_CODEX_AGENT_PANEL_TEXT_FONT_SIZE
    padding_x = DEFAULT_CODEX_AGENT_PANEL_PADDING_X
    padding_y = DEFAULT_CODEX_AGENT_PANEL_PADDING_Y
    section_spacing = DEFAULT_CODEX_AGENT_PANEL_SECTION_SPACING
    step_spacing = DEFAULT_CODEX_AGENT_PANEL_STEP_SPACING
    completed_color = DEFAULT_CODEX_AGENT_PANEL_COMPLETED_COLOR
    in_progress_color = DEFAULT_CODEX_AGENT_PANEL_IN_PROGRESS_COLOR
    pending_color = DEFAULT_CODEX_AGENT_PANEL_PENDING_COLOR
    if qapp is not None:
        border_color = str(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_BORDER_COLOR) or border_color
        )
        background_color = str(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_BACKGROUND_COLOR) or background_color
        )
        border_width = str(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_BORDER_WIDTH) or border_width
        )
        radius = str(qapp.property(APP_PROP_CODEX_AGENT_PANEL_RADIUS) or radius)
        title_color = str(qapp.property(APP_PROP_CODEX_AGENT_PANEL_TITLE_COLOR) or title_color)
        text_color = str(qapp.property(APP_PROP_CODEX_AGENT_PANEL_TEXT_COLOR) or text_color)
        title_font_size = str(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_TITLE_FONT_SIZE) or title_font_size
        )
        text_font_size = str(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_TEXT_FONT_SIZE) or text_font_size
        )
        padding_x = coerce_metric_px_min(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_PADDING_X),
            default=DEFAULT_CODEX_AGENT_PANEL_PADDING_X,
            minimum=0,
        )
        padding_y = coerce_metric_px_min(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_PADDING_Y),
            default=DEFAULT_CODEX_AGENT_PANEL_PADDING_Y,
            minimum=0,
        )
        section_spacing = coerce_metric_px_min(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_SECTION_SPACING),
            default=DEFAULT_CODEX_AGENT_PANEL_SECTION_SPACING,
            minimum=0,
        )
        step_spacing = coerce_metric_px_min(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_STEP_SPACING),
            default=DEFAULT_CODEX_AGENT_PANEL_STEP_SPACING,
            minimum=0,
        )
        completed_color = str(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_COMPLETED_COLOR) or completed_color
        )
        in_progress_color = str(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_IN_PROGRESS_COLOR) or in_progress_color
        )
        pending_color = str(
            qapp.property(APP_PROP_CODEX_AGENT_PANEL_PENDING_COLOR) or pending_color
        )
    return {
        "border_color": border_color,
        "background_color": background_color,
        "border_width": border_width,
        "radius": radius,
        "title_color": title_color,
        "text_color": text_color,
        "title_font_size": title_font_size,
        "text_font_size": text_font_size,
        "padding_x": str(padding_x),
        "padding_y": str(padding_y),
        "section_spacing": str(section_spacing),
        "step_spacing": str(step_spacing),
        "completed_color": completed_color,
        "in_progress_color": in_progress_color,
        "pending_color": pending_color,
    }


def set_codex_agent_panel_theme(
    *,
    border_color: Any,
    background_color: Any,
    border_width: Any = DEFAULT_CODEX_AGENT_PANEL_BORDER_WIDTH,
    radius: Any = DEFAULT_CODEX_AGENT_PANEL_RADIUS,
    title_color: Any = DEFAULT_CODEX_AGENT_PANEL_TITLE_COLOR,
    text_color: Any = DEFAULT_CODEX_AGENT_PANEL_TEXT_COLOR,
    title_font_size: Any = DEFAULT_CODEX_AGENT_PANEL_TITLE_FONT_SIZE,
    text_font_size: Any = DEFAULT_CODEX_AGENT_PANEL_TEXT_FONT_SIZE,
    padding_x: Any = DEFAULT_CODEX_AGENT_PANEL_PADDING_X,
    padding_y: Any = DEFAULT_CODEX_AGENT_PANEL_PADDING_Y,
    section_spacing: Any = DEFAULT_CODEX_AGENT_PANEL_SECTION_SPACING,
    step_spacing: Any = DEFAULT_CODEX_AGENT_PANEL_STEP_SPACING,
    completed_color: Any = DEFAULT_CODEX_AGENT_PANEL_COMPLETED_COLOR,
    in_progress_color: Any = DEFAULT_CODEX_AGENT_PANEL_IN_PROGRESS_COLOR,
    pending_color: Any = DEFAULT_CODEX_AGENT_PANEL_PENDING_COLOR,
    app: QApplication | None = None,
) -> dict[str, str]:
    qapp = app or QApplication.instance()
    normalized_border_color = str(border_color or DEFAULT_CODEX_AGENT_PANEL_BORDER_COLOR).strip()
    normalized_background_color = str(
        background_color or DEFAULT_CODEX_AGENT_PANEL_BACKGROUND_COLOR
    ).strip()
    normalized_border_width = str(border_width or DEFAULT_CODEX_AGENT_PANEL_BORDER_WIDTH).strip()
    normalized_radius = str(radius or DEFAULT_CODEX_AGENT_PANEL_RADIUS).strip()
    normalized_title_color = str(title_color or DEFAULT_CODEX_AGENT_PANEL_TITLE_COLOR).strip()
    normalized_text_color = str(text_color or DEFAULT_CODEX_AGENT_PANEL_TEXT_COLOR).strip()
    normalized_title_font_size = str(
        title_font_size or DEFAULT_CODEX_AGENT_PANEL_TITLE_FONT_SIZE
    ).strip()
    normalized_text_font_size = str(
        text_font_size or DEFAULT_CODEX_AGENT_PANEL_TEXT_FONT_SIZE
    ).strip()
    normalized_padding_x = coerce_metric_px_min(
        padding_x,
        default=DEFAULT_CODEX_AGENT_PANEL_PADDING_X,
        minimum=0,
    )
    normalized_padding_y = coerce_metric_px_min(
        padding_y,
        default=DEFAULT_CODEX_AGENT_PANEL_PADDING_Y,
        minimum=0,
    )
    normalized_section_spacing = coerce_metric_px_min(
        section_spacing,
        default=DEFAULT_CODEX_AGENT_PANEL_SECTION_SPACING,
        minimum=0,
    )
    normalized_step_spacing = coerce_metric_px_min(
        step_spacing,
        default=DEFAULT_CODEX_AGENT_PANEL_STEP_SPACING,
        minimum=0,
    )
    normalized_completed_color = str(
        completed_color or DEFAULT_CODEX_AGENT_PANEL_COMPLETED_COLOR
    ).strip()
    normalized_in_progress_color = str(
        in_progress_color or DEFAULT_CODEX_AGENT_PANEL_IN_PROGRESS_COLOR
    ).strip()
    normalized_pending_color = str(
        pending_color or DEFAULT_CODEX_AGENT_PANEL_PENDING_COLOR
    ).strip()
    if not normalized_border_color:
        normalized_border_color = DEFAULT_CODEX_AGENT_PANEL_BORDER_COLOR
    if not normalized_background_color:
        normalized_background_color = DEFAULT_CODEX_AGENT_PANEL_BACKGROUND_COLOR
    if not normalized_border_width:
        normalized_border_width = DEFAULT_CODEX_AGENT_PANEL_BORDER_WIDTH
    if not normalized_radius:
        normalized_radius = DEFAULT_CODEX_AGENT_PANEL_RADIUS
    if not normalized_title_color:
        normalized_title_color = DEFAULT_CODEX_AGENT_PANEL_TITLE_COLOR
    if not normalized_text_color:
        normalized_text_color = DEFAULT_CODEX_AGENT_PANEL_TEXT_COLOR
    if not normalized_title_font_size:
        normalized_title_font_size = DEFAULT_CODEX_AGENT_PANEL_TITLE_FONT_SIZE
    if not normalized_text_font_size:
        normalized_text_font_size = DEFAULT_CODEX_AGENT_PANEL_TEXT_FONT_SIZE
    if not normalized_completed_color:
        normalized_completed_color = DEFAULT_CODEX_AGENT_PANEL_COMPLETED_COLOR
    if not normalized_in_progress_color:
        normalized_in_progress_color = DEFAULT_CODEX_AGENT_PANEL_IN_PROGRESS_COLOR
    if not normalized_pending_color:
        normalized_pending_color = DEFAULT_CODEX_AGENT_PANEL_PENDING_COLOR
    if qapp is not None:
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_BORDER_COLOR, normalized_border_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_BACKGROUND_COLOR, normalized_background_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_BORDER_WIDTH, normalized_border_width)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_RADIUS, normalized_radius)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_TITLE_COLOR, normalized_title_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_TEXT_COLOR, normalized_text_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_TITLE_FONT_SIZE, normalized_title_font_size)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_TEXT_FONT_SIZE, normalized_text_font_size)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_PADDING_X, normalized_padding_x)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_PADDING_Y, normalized_padding_y)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_SECTION_SPACING, normalized_section_spacing)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_STEP_SPACING, normalized_step_spacing)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_COMPLETED_COLOR, normalized_completed_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_IN_PROGRESS_COLOR, normalized_in_progress_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_PANEL_PENDING_COLOR, normalized_pending_color)
    return {
        "border_color": normalized_border_color,
        "background_color": normalized_background_color,
        "border_width": normalized_border_width,
        "radius": normalized_radius,
        "title_color": normalized_title_color,
        "text_color": normalized_text_color,
        "title_font_size": normalized_title_font_size,
        "text_font_size": normalized_text_font_size,
        "padding_x": str(normalized_padding_x),
        "padding_y": str(normalized_padding_y),
        "section_spacing": str(normalized_section_spacing),
        "step_spacing": str(normalized_step_spacing),
        "completed_color": normalized_completed_color,
        "in_progress_color": normalized_in_progress_color,
        "pending_color": normalized_pending_color,
    }


def current_codex_agent_composer_theme(*, app: QApplication | None = None) -> dict[str, str]:
    qapp = app or QApplication.instance()
    border_color = DEFAULT_CODEX_AGENT_COMPOSER_BORDER_COLOR
    shimmer_color = DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_COLOR
    shimmer_highlight_color = DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR
    if qapp is not None:
        border_color = str(
            qapp.property(APP_PROP_CODEX_AGENT_COMPOSER_BORDER_COLOR) or border_color
        )
        shimmer_color = str(
            qapp.property(APP_PROP_CODEX_AGENT_COMPOSER_SHIMMER_COLOR) or shimmer_color
        )
        shimmer_highlight_color = str(
            qapp.property(APP_PROP_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR) or shimmer_highlight_color
        )
    return {
        "border_color": border_color,
        "shimmer_color": shimmer_color,
        "shimmer_highlight_color": shimmer_highlight_color,
    }


def set_codex_agent_composer_theme(
    *,
    border_color: Any,
    shimmer_color: Any = DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_COLOR,
    shimmer_highlight_color: Any = DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR,
    app: QApplication | None = None,
) -> dict[str, str]:
    qapp = app or QApplication.instance()
    normalized_border_color = str(border_color or DEFAULT_CODEX_AGENT_COMPOSER_BORDER_COLOR).strip()
    normalized_shimmer_color = str(shimmer_color or DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_COLOR).strip()
    normalized_highlight_color = str(
        shimmer_highlight_color or DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR
    ).strip()
    if not normalized_border_color:
        normalized_border_color = DEFAULT_CODEX_AGENT_COMPOSER_BORDER_COLOR
    if not normalized_shimmer_color:
        normalized_shimmer_color = DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_COLOR
    if not normalized_highlight_color:
        normalized_highlight_color = DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR
    if qapp is not None:
        qapp.setProperty(APP_PROP_CODEX_AGENT_COMPOSER_BORDER_COLOR, normalized_border_color)
        qapp.setProperty(APP_PROP_CODEX_AGENT_COMPOSER_SHIMMER_COLOR, normalized_shimmer_color)
        qapp.setProperty(
            APP_PROP_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR,
            normalized_highlight_color,
        )
    return {
        "border_color": normalized_border_color,
        "shimmer_color": normalized_shimmer_color,
        "shimmer_highlight_color": normalized_highlight_color,
    }


def current_codex_agent_link_color(*, app: QApplication | None = None) -> str:
    qapp = app or QApplication.instance()
    if qapp is None:
        return DEFAULT_CODEX_AGENT_LINK_COLOR
    color = str(qapp.property(APP_PROP_CODEX_AGENT_LINK_COLOR) or DEFAULT_CODEX_AGENT_LINK_COLOR).strip()
    return color or DEFAULT_CODEX_AGENT_LINK_COLOR


def set_codex_agent_link_color(*, color: Any, app: QApplication | None = None) -> str:
    qapp = app or QApplication.instance()
    normalized = str(color or DEFAULT_CODEX_AGENT_LINK_COLOR).strip()
    if not normalized:
        normalized = DEFAULT_CODEX_AGENT_LINK_COLOR
    if qapp is not None:
        qapp.setProperty(APP_PROP_CODEX_AGENT_LINK_COLOR, normalized)
    return normalized


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
