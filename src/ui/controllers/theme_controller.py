"""Controller for discovering and applying QSS themes."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication


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
        candidates: list[tuple[str, Path]] = []
        seen_names: set[str] = set()
        for theme_dir in self._theme_search_dirs():
            for item in sorted(theme_dir.iterdir(), key=lambda path: path.name.lower()):
                if not item.is_file():
                    continue
                if item.suffix.lower() != self.THEME_EXTENSION:
                    continue
                key = item.stem.lower()
                if key in seen_names:
                    continue
                seen_names.add(key)
                candidates.append((item.stem, item))
        return candidates

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

    def apply_selected_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return

        resolved = self._resolve_theme_path(self.ide.theme_name)
        if resolved is None:
            fallback_path = Path(__file__).resolve().parents[1] / "styles" / "app.qss"
            try:
                app.setStyleSheet(fallback_path.read_text(encoding="utf-8"))
            except Exception:
                app.setStyleSheet("")
            return

        resolved_name, theme_path = resolved
        try:
            stylesheet = theme_path.read_text(encoding="utf-8")
        except Exception:
            fallback_path = Path(__file__).resolve().parents[1] / "styles" / "app.qss"
            try:
                app.setStyleSheet(fallback_path.read_text(encoding="utf-8"))
            except Exception:
                app.setStyleSheet("")
            return

        app.setStyleSheet(stylesheet)
        if resolved_name != self.ide.theme_name:
            self.ide.theme_name = resolved_name
            self.settings_manager.set("theme", resolved_name, "ide")
            self.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
