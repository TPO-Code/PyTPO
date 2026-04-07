from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QApplication

from TPOPyside.shared_assets import (
    load_theme_stylesheet,
    resolve_shared_theme_path,
    shared_theme_candidates,
    shared_theme_dir,
    shared_theme_names,
)
from TPOPyside.theme_compiler import ThemeCompileError
from TPOPyside.theme_runtime import (
    DEFAULT_EDITOR_OVERVIEW_GAP,
    DEFAULT_EDITOR_SEARCH_TOP_MARGIN_MIN,
    DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT,
    DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH,
    coerce_metric_px,
    coerce_metric_px_min,
    refresh_editor_viewport_widgets,
    refresh_settings_color_swatch_widgets,
    set_editor_viewport_spacing,
    set_settings_color_swatch_size,
)
from .desktop_integration import editor_settings

THEME_KEY = "appearance/theme"
THEME_EXTENSIONS = (".qsst", ".qss")
DEFAULT_THEME_NAME = "Default"


@dataclass(frozen=True, slots=True)
class ThemeApplyResult:
    applied_name: str
    error: str | None = None


class TextEditorThemeManager:
    def themes_dir(self) -> Path:
        return shared_theme_dir()

    def available_themes(self) -> list[str]:
        return shared_theme_names(extensions=THEME_EXTENSIONS)

    def current_theme_name(self) -> str:
        value = str(editor_settings().value(THEME_KEY, DEFAULT_THEME_NAME) or "").strip()
        return value or DEFAULT_THEME_NAME

    def set_current_theme_name(self, theme_name: str) -> None:
        settings = editor_settings()
        settings.setValue(THEME_KEY, str(theme_name or "").strip() or DEFAULT_THEME_NAME)
        settings.sync()

    def apply_saved_theme(self) -> ThemeApplyResult:
        return self.apply_theme(self.current_theme_name(), persist=False)

    def apply_theme(self, theme_name: str, *, persist: bool = True) -> ThemeApplyResult:
        app = QApplication.instance()
        if app is None:
            return ThemeApplyResult(applied_name=DEFAULT_THEME_NAME, error="No QApplication instance is active.")

        resolved = self.resolve_theme_path(theme_name)
        if resolved is None:
            resolved = self.resolve_theme_path(DEFAULT_THEME_NAME)
        if resolved is None:
            app.setStyleSheet("")
            return ThemeApplyResult(applied_name=DEFAULT_THEME_NAME, error="No shared themes were found.")

        resolved_name, theme_path = resolved
        try:
            stylesheet, tokens = self._load_stylesheet(theme_path)
            app.setStyleSheet(stylesheet)
            self._apply_runtime_tokens(tokens, app=app)
        except ThemeCompileError as exc:
            app.setStyleSheet("")
            return ThemeApplyResult(applied_name=resolved_name, error=str(exc))
        except OSError as exc:
            app.setStyleSheet("")
            return ThemeApplyResult(applied_name=resolved_name, error=str(exc))

        if persist:
            self.set_current_theme_name(resolved_name)
        return ThemeApplyResult(applied_name=resolved_name, error=None)

    def resolve_theme_path(self, theme_name: str) -> tuple[str, Path] | None:
        return resolve_shared_theme_path(theme_name, extensions=THEME_EXTENSIONS)

    def _theme_candidates(self) -> list[tuple[str, Path]]:
        return shared_theme_candidates(extensions=THEME_EXTENSIONS)

    def _load_stylesheet(self, theme_path: Path) -> tuple[str, dict[str, Any] | None]:
        return load_theme_stylesheet(theme_path)

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

    def _apply_runtime_tokens(self, tokens: dict[str, Any] | None, *, app: QApplication) -> None:
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
        refresh_editor_viewport_widgets(app=app)
