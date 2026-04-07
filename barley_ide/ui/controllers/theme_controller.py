"""Controller for discovering and applying QSS themes."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from PySide6.QtWidgets import QApplication

from TPOPyside.shared_assets import (
    load_theme_stylesheet,
    shared_theme_candidates,
    shared_theme_search_dirs,
)
from barley_ide.services.theme_compiler import (
    CompiledStructuredTheme,
    ThemeCompileError,
)
from barley_ide.ui.theme_runtime import (
    DEFAULT_CODEX_AGENT_BUBBLE_BORDER_WIDTH,
    DEFAULT_CODEX_AGENT_BUBBLE_HEADER_COLOR,
    DEFAULT_CODEX_AGENT_BUBBLE_PREVIEW_COLOR,
    DEFAULT_CODEX_AGENT_BUBBLE_TEXT_COLOR,
    DEFAULT_CODEX_AGENT_BUBBLE_TOGGLE_COLOR,
    DEFAULT_CODEX_AGENT_COMPOSER_BORDER_COLOR,
    DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_COLOR,
    DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR,
    DEFAULT_CODEX_AGENT_LINK_COLOR,
    DEFAULT_CODEX_AGENT_PANEL_BACKGROUND_COLOR,
    DEFAULT_CODEX_AGENT_PANEL_BORDER_COLOR,
    DEFAULT_CODEX_AGENT_PANEL_BORDER_WIDTH,
    DEFAULT_CODEX_AGENT_PANEL_PADDING_X,
    DEFAULT_CODEX_AGENT_PANEL_PADDING_Y,
    DEFAULT_CODEX_AGENT_PANEL_RADIUS,
    DEFAULT_CODEX_AGENT_PANEL_SECTION_SPACING,
    DEFAULT_CODEX_AGENT_PANEL_STEP_SPACING,
    DEFAULT_CODEX_AGENT_PANEL_COMPLETED_COLOR,
    DEFAULT_CODEX_AGENT_PANEL_IN_PROGRESS_COLOR,
    DEFAULT_CODEX_AGENT_PANEL_PENDING_COLOR,
    DEFAULT_CODEX_AGENT_PANEL_TEXT_COLOR,
    DEFAULT_CODEX_AGENT_PANEL_TEXT_FONT_SIZE,
    DEFAULT_CODEX_AGENT_PANEL_TITLE_COLOR,
    DEFAULT_CODEX_AGENT_PANEL_TITLE_FONT_SIZE,
    DEFAULT_EDITOR_OVERVIEW_GAP,
    DEFAULT_EDITOR_SEARCH_TOP_MARGIN_MIN,
    DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT,
    DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH,
    coerce_metric_px,
    coerce_metric_px_min,
    refresh_codex_agent_widgets,
    refresh_editor_viewport_widgets,
    refresh_settings_color_swatch_widgets,
    set_codex_agent_bubble_theme,
    set_codex_agent_composer_theme,
    set_codex_agent_link_color,
    set_codex_agent_panel_theme,
    set_editor_viewport_spacing,
    set_settings_color_swatch_size,
)

_GLOBAL_SCROLLBAR_BUTTONLESS_QSS = """
QScrollBar:vertical {
    margin-top: 0px;
    margin-bottom: 0px;
}

QScrollBar:horizontal {
    margin-left: 0px;
    margin-right: 0px;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
    margin: 0px;
    border: none;
    background: transparent;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0px;
    margin: 0px;
    border: none;
    background: transparent;
}

QScrollBar::up-arrow:vertical,
QScrollBar::down-arrow:vertical,
QScrollBar::left-arrow:horizontal,
QScrollBar::right-arrow:horizontal {
    width: 0px;
    height: 0px;
    background: transparent;
}
"""


class ThemeController:
    def __init__(self, ide):
        self.ide = ide

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def _theme_search_dirs(self) -> list[Path]:
        return shared_theme_search_dirs()

    def _theme_candidates(self) -> list[tuple[str, Path]]:
        return shared_theme_candidates(extensions=self._supported_theme_extensions())

    def _supported_theme_extensions(self) -> tuple[str, ...]:
        configured = getattr(self.ide, "THEME_EXTENSIONS", None)
        extensions: list[str] = []
        if isinstance(configured, (list, tuple, set)):
            for item in configured:
                ext = str(item or "").strip().lower()
                if not ext:
                    continue
                if not ext.startswith("."):
                    ext = f".{ext}"
                if ext not in extensions:
                    extensions.append(ext)
        if not extensions:
            legacy = str(getattr(self.ide, "THEME_EXTENSION", ".qss") or ".qss").strip().lower()
            if not legacy.startswith("."):
                legacy = f".{legacy}"
            extensions.append(legacy or ".qss")
        if ".qss" not in extensions:
            extensions.append(".qss")
        return tuple(extensions)

    @staticmethod
    def _fallback_theme_path() -> Path:
        return Path(__file__).resolve().parents[1] / "styles" / "app.qss"

    def _fallback_stylesheet(self) -> str:
        fallback_path = self._fallback_theme_path()
        try:
            return fallback_path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _load_theme_for_path(self, theme_path: Path) -> CompiledStructuredTheme | None:
        if theme_path.suffix.lower() == ".qsst":
            stylesheet, tokens = load_theme_stylesheet(theme_path)
            return CompiledStructuredTheme(stylesheet=stylesheet, resolved_tokens=tokens or {})
        return None

    def _report_theme_error(self, message: str) -> None:
        status_getter = getattr(self.ide, "statusBar", None)
        if not callable(status_getter):
            return
        try:
            status = status_getter()
            status.showMessage(str(message or "").strip(), 5200)
        except Exception:
            pass

    def _read_legacy_stylesheet(self, theme_path: Path) -> str:
        return theme_path.read_text(encoding="utf-8")

    @staticmethod
    def _append_global_stylesheet_overrides(stylesheet: str) -> str:
        base = str(stylesheet or "").rstrip()
        override = _GLOBAL_SCROLLBAR_BUTTONLESS_QSS.strip()
        if not override:
            return base
        if override in base:
            return base
        if not base:
            return override
        return f"{base}\n\n{override}"

    def _apply_stylesheet_with_fallback(self, *, theme_path: Path) -> tuple[str, dict[str, Any] | None]:
        try:
            compiled = self._load_theme_for_path(theme_path)
            if compiled is not None:
                return compiled.stylesheet, compiled.resolved_tokens
            return self._read_legacy_stylesheet(theme_path), None
        except ThemeCompileError as exc:
            self._report_theme_error(f"Theme compile error ({theme_path.name}): {exc}")
            return self._fallback_stylesheet(), None
        except Exception:
            return self._fallback_stylesheet(), None

    @staticmethod
    def _settings_color_swatch_size(tokens: dict[str, Any] | None) -> tuple[int, int]:
        width = DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH
        height = DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT
        if not isinstance(tokens, dict):
            return width, height

        base_size = tokens.get("components.settings.color_swatch_size")
        if base_size is not None:
            metric = coerce_metric_px(base_size, default=height)
            width = metric
            height = metric

        width = coerce_metric_px(tokens.get("components.settings.color_swatch_width"), default=width)
        height = coerce_metric_px(tokens.get("components.settings.color_swatch_height"), default=height)
        return width, height

    @staticmethod
    def _editor_viewport_spacing(tokens: dict[str, Any] | None) -> tuple[int, int]:
        search_top_margin = DEFAULT_EDITOR_SEARCH_TOP_MARGIN_MIN
        overview_gap = DEFAULT_EDITOR_OVERVIEW_GAP
        if not isinstance(tokens, dict):
            return search_top_margin, overview_gap
        search_top_margin = coerce_metric_px_min(
            tokens.get("components.editor.search_top_margin_min"),
            default=search_top_margin,
            minimum=0,
        )
        overview_gap = coerce_metric_px_min(
            tokens.get("components.editor.overview_gap"),
            default=overview_gap,
            minimum=0,
        )
        return search_top_margin, overview_gap

    @staticmethod
    def _codex_agent_bubble_theme(tokens: dict[str, Any] | None) -> tuple[str, str, str, str, str, dict[str, dict[str, str]]]:
        text_color = DEFAULT_CODEX_AGENT_BUBBLE_TEXT_COLOR
        border_width = DEFAULT_CODEX_AGENT_BUBBLE_BORDER_WIDTH
        header_color = DEFAULT_CODEX_AGENT_BUBBLE_HEADER_COLOR
        toggle_color = DEFAULT_CODEX_AGENT_BUBBLE_TOGGLE_COLOR
        preview_color = DEFAULT_CODEX_AGENT_BUBBLE_PREVIEW_COLOR
        role_colors: dict[str, dict[str, str]] = {}
        if isinstance(tokens, dict):
            text_color = str(
                tokens.get("components.codex_agent.bubble.text_color") or text_color
            ).strip() or text_color
            border_width = str(
                tokens.get("components.codex_agent.bubble.border_width") or border_width
            ).strip() or border_width
            header_color = str(
                tokens.get("components.codex_agent.bubble.header_color") or header_color
            ).strip() or header_color
            toggle_color = str(
                tokens.get("components.codex_agent.bubble.toggle_color") or toggle_color
            ).strip() or toggle_color
            preview_color = str(
                tokens.get("components.codex_agent.bubble.preview_color") or preview_color
            ).strip() or preview_color
            for role_name in ("default", "user", "assistant", "thinking", "tools", "diff", "system", "meta"):
                role_colors[role_name] = {
                    "border_color": str(
                        tokens.get(f"components.codex_agent.roles.{role_name}.border_color") or ""
                    ).strip(),
                    "background_color": str(
                        tokens.get(f"components.codex_agent.roles.{role_name}.background_color") or ""
                    ).strip(),
                }
        return text_color, border_width, header_color, toggle_color, preview_color, role_colors

    @staticmethod
    def _codex_agent_composer_theme(tokens: dict[str, Any] | None) -> tuple[str, str, str]:
        border_color = DEFAULT_CODEX_AGENT_COMPOSER_BORDER_COLOR
        shimmer_color = DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_COLOR
        shimmer_highlight_color = DEFAULT_CODEX_AGENT_COMPOSER_SHIMMER_HIGHLIGHT_COLOR
        if isinstance(tokens, dict):
            border_color = str(
                tokens.get("components.codex_agent.composer.border_color") or border_color
            ).strip() or border_color
            shimmer_color = str(
                tokens.get("components.codex_agent.composer.shimmer_color") or shimmer_color
            ).strip() or shimmer_color
            shimmer_highlight_color = str(
                tokens.get("components.codex_agent.composer.shimmer_highlight_color")
                or shimmer_highlight_color
            ).strip() or shimmer_highlight_color
        return border_color, shimmer_color, shimmer_highlight_color

    @staticmethod
    def _codex_agent_link_color(tokens: dict[str, Any] | None) -> str:
        color = DEFAULT_CODEX_AGENT_LINK_COLOR
        if isinstance(tokens, dict):
            color = str(tokens.get("components.codex_agent.link.color") or color).strip() or color
        return color

    @staticmethod
    def _codex_agent_panel_theme(tokens: dict[str, Any] | None) -> dict[str, Any]:
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
        if isinstance(tokens, dict):
            border_color = str(
                tokens.get("components.codex_agent.panel.border_color") or border_color
            ).strip() or border_color
            background_color = str(
                tokens.get("components.codex_agent.panel.background_color") or background_color
            ).strip() or background_color
            border_width = str(
                tokens.get("components.codex_agent.panel.border_width") or border_width
            ).strip() or border_width
            radius = str(
                tokens.get("components.codex_agent.panel.radius") or radius
            ).strip() or radius
            title_color = str(
                tokens.get("components.codex_agent.panel.title_color") or title_color
            ).strip() or title_color
            text_color = str(
                tokens.get("components.codex_agent.panel.text_color") or text_color
            ).strip() or text_color
            title_font_size = str(
                tokens.get("components.codex_agent.panel.title_font_size") or title_font_size
            ).strip() or title_font_size
            text_font_size = str(
                tokens.get("components.codex_agent.panel.text_font_size") or text_font_size
            ).strip() or text_font_size
            padding_x = coerce_metric_px_min(
                tokens.get("components.codex_agent.panel.padding_x"),
                default=padding_x,
                minimum=0,
            )
            padding_y = coerce_metric_px_min(
                tokens.get("components.codex_agent.panel.padding_y"),
                default=padding_y,
                minimum=0,
            )
            section_spacing = coerce_metric_px_min(
                tokens.get("components.codex_agent.panel.section_spacing"),
                default=section_spacing,
                minimum=0,
            )
            step_spacing = coerce_metric_px_min(
                tokens.get("components.codex_agent.panel.step_spacing"),
                default=step_spacing,
                minimum=0,
            )
            completed_color = str(
                tokens.get("components.codex_agent.panel.completed_color") or completed_color
            ).strip() or completed_color
            in_progress_color = str(
                tokens.get("components.codex_agent.panel.in_progress_color") or in_progress_color
            ).strip() or in_progress_color
            pending_color = str(
                tokens.get("components.codex_agent.panel.pending_color") or pending_color
            ).strip() or pending_color
        return {
            "border_color": border_color,
            "background_color": background_color,
            "border_width": border_width,
            "radius": radius,
            "title_color": title_color,
            "text_color": text_color,
            "title_font_size": title_font_size,
            "text_font_size": text_font_size,
            "padding_x": padding_x,
            "padding_y": padding_y,
            "section_spacing": section_spacing,
            "step_spacing": step_spacing,
            "completed_color": completed_color,
            "in_progress_color": in_progress_color,
            "pending_color": pending_color,
        }

    def available_themes(self) -> list[str]:
        return [name for name, _ in self._theme_candidates()]

    def _resolve_theme_path(self, theme_name: str) -> tuple[str, Path] | None:
        candidates = self._theme_candidates()
        if not candidates:
            return None

        selected = (theme_name or "").strip().lower()
        if selected:
            for candidate_name, candidate_path in candidates:
                if candidate_name.lower() == selected:
                    return candidate_name, candidate_path

        return candidates[0]

    def refresh_active_theme_for_saved_path(self, saved_path: str) -> bool:
        resolved = self._resolve_theme_path(self.ide.theme_name)
        if resolved is None:
            return False

        resolved_name, theme_path = resolved
        try:
            saved_cpath = self._canonical_path(saved_path)
            active_theme_cpath = self._canonical_path(str(theme_path))
        except Exception:
            return False

        if saved_cpath != active_theme_cpath:
            try:
                saved_stem = Path(saved_cpath).stem.lower()
                saved_suffix = Path(saved_cpath).suffix.lower()
            except Exception:
                return False
            if saved_stem != resolved_name.lower() or saved_suffix not in self._supported_theme_extensions():
                return False

        self.apply_selected_theme()
        return True

    def apply_selected_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return

        resolved = self._resolve_theme_path(self.ide.theme_name)
        if resolved is None:
            app.setStyleSheet(self._append_global_stylesheet_overrides(self._fallback_stylesheet()))
            return

        resolved_name, theme_path = resolved
        stylesheet, tokens = self._apply_stylesheet_with_fallback(theme_path=theme_path)
        app.setStyleSheet(self._append_global_stylesheet_overrides(stylesheet))
        swatch_width, swatch_height = self._settings_color_swatch_size(tokens)
        swatch_width, swatch_height = set_settings_color_swatch_size(
            width=swatch_width,
            height=swatch_height,
            app=app,
        )
        refresh_settings_color_swatch_widgets(app=app, width=swatch_width, height=swatch_height)
        editor_search_top_margin, editor_overview_gap = self._editor_viewport_spacing(tokens)
        set_editor_viewport_spacing(
            search_top_margin_min=editor_search_top_margin,
            overview_gap=editor_overview_gap,
            app=app,
        )
        (
            codex_text_color,
            codex_border_width,
            codex_header_color,
            codex_toggle_color,
            codex_preview_color,
            codex_role_colors,
        ) = self._codex_agent_bubble_theme(tokens)
        set_codex_agent_bubble_theme(
            text_color=codex_text_color,
            border_width=codex_border_width,
            header_color=codex_header_color,
            toggle_color=codex_toggle_color,
            preview_color=codex_preview_color,
            role_colors=codex_role_colors,
            app=app,
        )
        composer_border_color, composer_shimmer_color, composer_shimmer_highlight_color = (
            self._codex_agent_composer_theme(tokens)
        )
        set_codex_agent_composer_theme(
            border_color=composer_border_color,
            shimmer_color=composer_shimmer_color,
            shimmer_highlight_color=composer_shimmer_highlight_color,
            app=app,
        )
        set_codex_agent_link_color(
            color=self._codex_agent_link_color(tokens),
            app=app,
        )
        codex_panel_theme = self._codex_agent_panel_theme(tokens)
        set_codex_agent_panel_theme(
            border_color=codex_panel_theme["border_color"],
            background_color=codex_panel_theme["background_color"],
            border_width=codex_panel_theme["border_width"],
            radius=codex_panel_theme["radius"],
            title_color=codex_panel_theme["title_color"],
            text_color=codex_panel_theme["text_color"],
            title_font_size=codex_panel_theme["title_font_size"],
            text_font_size=codex_panel_theme["text_font_size"],
            padding_x=codex_panel_theme["padding_x"],
            padding_y=codex_panel_theme["padding_y"],
            section_spacing=codex_panel_theme["section_spacing"],
            step_spacing=codex_panel_theme["step_spacing"],
            completed_color=codex_panel_theme["completed_color"],
            in_progress_color=codex_panel_theme["in_progress_color"],
            pending_color=codex_panel_theme["pending_color"],
            app=app,
        )
        refresh_editor_viewport_widgets(app=app)
        refresh_codex_agent_widgets(app=app)
        if resolved_name != self.ide.theme_name:
            self.ide.theme_name = resolved_name
            self.settings_manager.set("theme", resolved_name, "ide")
            self.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
        apply_tree_fonts = getattr(self.ide, "_apply_tree_font_settings_to_all", None)
        if callable(apply_tree_fonts):
            try:
                apply_tree_fonts()
            except Exception:
                pass
