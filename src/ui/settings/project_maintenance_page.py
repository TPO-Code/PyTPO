from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.settings_models import SettingsScope


class ProjectMaintenancePage(QWidget):
    def __init__(self, *, manager: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(12)

        info = QLabel(
            "Project-local IDE artifacts are stored under `.tide/`.\n"
            "Use the actions below to clear caches."
        )
        info.setWordWrap(True)
        root.addWidget(info)

        self.paths_label = QLabel(self._paths_overview_text())
        self.paths_label.setWordWrap(True)
        root.addWidget(self.paths_label)

        row1 = QHBoxLayout()
        self.clear_completion_btn = QPushButton("Clear Completion Cache")
        self.clear_ruff_btn = QPushButton("Clear Ruff Cache")
        row1.addWidget(self.clear_completion_btn)
        row1.addWidget(self.clear_ruff_btn)
        row1.addStretch(1)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        self.clear_all_btn = QPushButton("Clear All Caches")
        row2.addWidget(self.clear_all_btn)
        row2.addStretch(1)
        root.addLayout(row2)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        root.addStretch(1)

        self.clear_completion_btn.clicked.connect(
            lambda: self._clear_cache_paths([self._tide_dir() / "cache"])
        )
        self.clear_ruff_btn.clicked.connect(
            lambda: self._clear_cache_paths([self._tide_dir() / "ruff_cache"])
        )
        self.clear_all_btn.clicked.connect(self._clear_all_caches)

    def create_bindings(self, _binding_cls: Callable[..., Any], _scope: SettingsScope) -> list[Any]:
        return []

    def has_pending_settings_changes(self) -> bool:
        return False

    def apply_settings_changes(self) -> list[str]:
        return []

    def _tide_dir(self) -> Path:
        return Path(self._manager.paths.project_ide_dir).expanduser()

    def _paths_overview_text(self) -> str:
        tide = self._tide_dir()
        return (
            f"Project IDE folder: {tide}\n"
            f"Completion cache: {tide / 'cache'}\n"
            f"Ruff cache: {tide / 'ruff_cache'}"
        )

    def _clear_all_caches(self) -> None:
        self._clear_cache_paths(
            [
                self._tide_dir() / "cache",
                self._tide_dir() / "ruff_cache",
            ]
        )

    def _clear_cache_paths(self, paths: list[Path]) -> None:
        removed: list[str] = []
        failed: list[str] = []
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.exists():
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(str(path))
            except Exception as exc:
                failed.append(f"{path}: {exc}")

        if failed:
            self._set_status("Failed to clear some caches:\n" + "\n".join(failed[:5]), error=True)
            return
        if removed:
            self._set_status("Cleared cache paths:\n" + "\n".join(removed), error=False)
            return
        self._set_status("No cache paths to clear.", error=False)

    def _set_status(self, text: str, *, error: bool) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")


def create_project_maintenance_page(
    *,
    manager: Any,
    scope: SettingsScope,
    binding_cls: Callable[..., Any],
    parent: QWidget | None = None,
) -> tuple[QWidget, list[Any]]:
    page = ProjectMaintenancePage(manager=manager, parent=None)
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll, page.create_bindings(binding_cls, scope)
