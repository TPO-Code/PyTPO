"""Controller for discovering and applying QSS themes."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.services.theme_compiler import (
    CompiledStructuredTheme,
    STRUCTURED_THEME_EXTENSION,
    ThemeCompileError,
    compile_qsst_file_with_tokens,
)
from src.ui.theme_runtime import (
    DEFAULT_SETTINGS_COLOR_SWATCH_HEIGHT,
    DEFAULT_SETTINGS_COLOR_SWATCH_WIDTH,
    coerce_metric_px,
    refresh_settings_color_swatch_widgets,
    set_settings_color_swatch_size,
)


class ThemeController:
    def __init__(self, ide):
        self.ide = ide

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def _theme_search_dirs(self) -> list[Path]:
        theme_dir = self._shared_themes_dir()
        if theme_dir.exists() and theme_dir.is_dir():
            return [theme_dir]
        return []

    def _theme_candidates(self) -> list[tuple[str, Path]]:
        supported = self._supported_theme_extensions()
        extension_priority = {ext: idx for idx, ext in enumerate(supported)}
        chosen: dict[str, tuple[int, str, Path]] = {}
        for theme_dir in self._theme_search_dirs():
            for item in sorted(theme_dir.iterdir(), key=lambda path: path.name.lower()):
                if not item.is_file():
                    continue
                suffix = item.suffix.lower()
                if suffix not in extension_priority:
                    continue
                key = item.stem.lower()
                priority = extension_priority[suffix]
                current = chosen.get(key)
                if current is not None and priority >= current[0]:
                    continue
                chosen[key] = (priority, item.stem, item)
        candidates = sorted(chosen.values(), key=lambda entry: entry[1].lower())
        return [(name, path) for _priority, name, path in candidates]

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
        if theme_path.suffix.lower() == STRUCTURED_THEME_EXTENSION:
            return compile_qsst_file_with_tokens(theme_path)
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
            app.setStyleSheet(self._fallback_stylesheet())
            return

        resolved_name, theme_path = resolved
        stylesheet, tokens = self._apply_stylesheet_with_fallback(theme_path=theme_path)

        app.setStyleSheet(stylesheet)
        swatch_width, swatch_height = self._settings_color_swatch_size(tokens)
        swatch_width, swatch_height = set_settings_color_swatch_size(
            width=swatch_width,
            height=swatch_height,
            app=app,
        )
        refresh_settings_color_swatch_widgets(app=app, width=swatch_width, height=swatch_height)
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
