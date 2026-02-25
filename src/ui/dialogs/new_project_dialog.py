from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.settings_manager import SettingsManager
from src.ui.custom_dialog import DialogWindow
from src.ui.dialogs.file_dialog_bridge import get_existing_directory


_INVALID_FOLDER_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


class NewProjectDialog(DialogWindow):
    def __init__(
        self,
        *,
        manager: SettingsManager,
        default_create_in: str,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=False, parent=parent)
        self.setWindowTitle("New Project")
        self.resize(560, 280)

        self._manager = manager
        self._default_create_in = str(default_create_in or "").strip() or str(Path.home())
        self._folder_name_touched = False
        self.created_project_path: str | None = None

        self._build_ui()
        self._load_initial_values()
        self._refresh_create_enabled()

    def _build_ui(self) -> None:
        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.project_name_edit = QLineEdit()
        self.project_name_edit.setPlaceholderText("My Project")

        create_in_row = QHBoxLayout()
        self.create_in_edit = QLineEdit()
        self.create_in_edit.setPlaceholderText("Directory")
        self.browse_btn = QPushButton("Browse")
        create_in_row.addWidget(self.create_in_edit, 1)
        create_in_row.addWidget(self.browse_btn)

        self.folder_name_edit = QLineEdit()
        self.folder_name_edit.setPlaceholderText("my-project")

        root.addWidget(QLabel("Project Name"))
        root.addWidget(self.project_name_edit)
        root.addWidget(QLabel("Create In"))
        root.addLayout(create_in_row)
        root.addWidget(QLabel("Folder Name"))
        root.addWidget(self.folder_name_edit)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.create_btn = QPushButton("Create Project")
        self.create_btn.setDefault(True)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.create_btn)
        root.addLayout(actions)

        self.project_name_edit.textChanged.connect(self._on_project_name_changed)
        self.create_in_edit.textChanged.connect(self._refresh_create_enabled)
        self.folder_name_edit.textEdited.connect(self._on_folder_name_edited)
        self.folder_name_edit.textChanged.connect(self._refresh_create_enabled)
        self.browse_btn.clicked.connect(self._browse_create_in)
        self.cancel_btn.clicked.connect(self.reject)
        self.create_btn.clicked.connect(self._create_clicked)

    def _load_initial_values(self) -> None:
        create_in = str(
            self._manager.get("projects.last_create_in", scope_preference="ide", default=self._default_create_in) or ""
        ).strip()
        if not create_in:
            create_in = self._default_create_in
        if not os.path.isdir(create_in):
            create_in = self._default_create_in
        self.create_in_edit.setText(create_in)

        default_name = str(self._manager.get("defaults.name", scope_preference="ide", default="My Python Project") or "")
        project_name = default_name.strip() or "My Python Project"
        self.project_name_edit.setText(project_name)
        self.folder_name_edit.setText(self._derive_folder_name(project_name))
        self._folder_name_touched = False

    def _on_project_name_changed(self, text: str) -> None:
        if not self._folder_name_touched:
            self.folder_name_edit.setText(self._derive_folder_name(text))
        self._refresh_create_enabled()

    def _on_folder_name_edited(self, _text: str) -> None:
        self._folder_name_touched = True

    def _browse_create_in(self) -> None:
        start = str(self.create_in_edit.text() or "").strip() or self._default_create_in
        selected = get_existing_directory(
            parent=self,
            manager=self._manager,
            caption="Select Directory",
            directory=start,
        )
        if selected:
            self.create_in_edit.setText(selected)

    def _create_clicked(self) -> None:
        project_name = str(self.project_name_edit.text() or "").strip()
        create_in = str(self.create_in_edit.text() or "").strip()
        folder_name = str(self.folder_name_edit.text() or "").strip()

        if not project_name:
            self._set_status("Project name is required.", error=True)
            return
        if not create_in:
            self._set_status("Create in path is required.", error=True)
            return
        if not self._is_valid_folder_name(folder_name):
            self._set_status("Folder name is invalid.", error=True)
            return

        parent_dir = Path(create_in).expanduser()
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._set_status("Could not create selected parent directory.", error=True)
            return

        target = (parent_dir / folder_name).resolve()
        if target.exists():
            self._set_status(f"Destination already exists: {target}", error=True)
            return

        try:
            target.mkdir(parents=True, exist_ok=False)
        except Exception as exc:
            self._set_status(f"Could not create project folder: {exc}", error=True)
            return

        try:
            self._manager.set("projects.last_create_in", str(parent_dir), "ide")
            self._manager.save_all(scopes={"ide"}, only_dirty=True)
        except Exception:
            pass

        self.created_project_path = str(target)
        self.accept()

    def reject(self) -> None:
        if self.created_project_path:
            super().reject()
            return
        super().reject()

    def _refresh_create_enabled(self) -> None:
        project_name = str(self.project_name_edit.text() or "").strip()
        create_in = str(self.create_in_edit.text() or "").strip()
        folder_name = str(self.folder_name_edit.text() or "").strip()
        enabled = bool(project_name and create_in and self._is_valid_folder_name(folder_name))
        self.create_btn.setEnabled(enabled)

    def _is_valid_folder_name(self, name: str) -> bool:
        text = str(name or "").strip()
        if not text:
            return False
        if text in {".", ".."}:
            return False
        if "/" in text or "\\" in text:
            return False
        if _INVALID_FOLDER_CHARS_RE.search(text):
            return False
        return True

    @staticmethod
    def _derive_folder_name(project_name: str) -> str:
        text = str(project_name or "").strip().lower()
        if not text:
            return "new-project"
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"[^a-z0-9._-]", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-.")
        return text or "new-project"

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")
