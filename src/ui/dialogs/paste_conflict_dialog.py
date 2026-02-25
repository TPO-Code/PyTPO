from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.custom_dialog import DialogWindow


def default_copy_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return "copy"
    stem, ext = os.path.splitext(text)
    if stem:
        return f"{stem}.copy{ext}"
    return f"{text}.copy"


class SinglePasteConflictDialog(DialogWindow):
    def __init__(
        self,
        *,
        source_name: str,
        destination_dir: str,
        existing_name: str,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=False, parent=parent)
        self.setWindowTitle("Paste Conflict")
        self.resize(640, 230)
        self._destination_dir = str(destination_dir or "")
        self._existing_name = str(existing_name or "").strip()
        self._choice = "cancel"
        self._rename_name = default_copy_name(self._existing_name)

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        info = QLabel(
            f"A file or folder named '{self._existing_name}' already exists.\n"
            f"Source: {str(source_name or '').strip()}\n"
            "Choose how to proceed."
        )
        info.setWordWrap(True)
        root.addWidget(info)

        rename_row = QHBoxLayout()
        rename_row.setSpacing(8)
        rename_label = QLabel("New name:")
        self.rename_edit = QLineEdit(self._rename_name)
        rename_row.addWidget(rename_label)
        rename_row.addWidget(self.rename_edit, 1)
        root.addLayout(rename_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        actions = QHBoxLayout()
        self.rename_btn = QPushButton("Rename")
        self.rename_btn.setDefault(True)
        self.overwrite_btn = QPushButton("Overwrite")
        self.cancel_btn = QPushButton("Cancel")
        actions.addStretch(1)
        actions.addWidget(self.rename_btn)
        actions.addWidget(self.overwrite_btn)
        actions.addWidget(self.cancel_btn)
        root.addLayout(actions)

        self.rename_btn.clicked.connect(self._accept_rename)
        self.overwrite_btn.clicked.connect(self._accept_overwrite)
        self.cancel_btn.clicked.connect(self.reject)

    @property
    def choice(self) -> str:
        return self._choice

    @property
    def rename_name(self) -> str:
        return self._rename_name

    def _accept_overwrite(self) -> None:
        self._choice = "overwrite"
        self.accept()

    def _accept_rename(self) -> None:
        candidate = str(self.rename_edit.text() or "").strip()
        err = self._validate_rename(candidate)
        if err:
            self.status_label.setText(f"<span style='color:#d46a6a;'>{err}</span>")
            return
        self._rename_name = candidate
        self._choice = "rename"
        self.accept()

    def _validate_rename(self, name: str) -> str:
        text = str(name or "").strip()
        if not text:
            return "Name cannot be empty."
        if text in {".", ".."}:
            return "Invalid name."
        if "/" in text or "\\" in text:
            return "Use a simple name without path separators."
        if text == self._existing_name:
            return "Name must be different to avoid conflict."
        target = os.path.abspath(os.path.join(self._destination_dir, text))
        if os.path.exists(target):
            return "That name already exists in this folder."
        return ""


class MultiPasteOverwriteDialog(DialogWindow):
    def __init__(
        self,
        *,
        conflict_names: list[str],
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Overwrite Existing Files")
        self.resize(700, 420)
        self._overwrite = False

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        info = QLabel(
            f"{len(conflict_names)} destination items already exist.\n"
            "Do you want to overwrite all conflicting items?"
        )
        info.setWordWrap(True)
        root.addWidget(info)

        self.list_widget = QListWidget()
        for name in conflict_names:
            self.list_widget.addItem(str(name or "").strip())
        root.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        self.overwrite_btn = QPushButton("Overwrite All")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setDefault(True)
        actions.addStretch(1)
        actions.addWidget(self.overwrite_btn)
        actions.addWidget(self.cancel_btn)
        root.addLayout(actions)

        self.overwrite_btn.clicked.connect(self._accept_overwrite)
        self.cancel_btn.clicked.connect(self.reject)

    @property
    def overwrite(self) -> bool:
        return bool(self._overwrite)

    def _accept_overwrite(self) -> None:
        self._overwrite = True
        self.accept()


def prompt_single_paste_conflict(
    *,
    source_name: str,
    destination_dir: str,
    existing_name: str,
    use_native_chrome: bool,
    parent: QWidget | None = None,
) -> tuple[str, str]:
    dialog = SinglePasteConflictDialog(
        source_name=source_name,
        destination_dir=destination_dir,
        existing_name=existing_name,
        use_native_chrome=use_native_chrome,
        parent=parent,
    )
    if dialog.exec() <= 0:
        return "cancel", ""
    if dialog.choice == "overwrite":
        return "overwrite", ""
    if dialog.choice == "rename":
        return "rename", dialog.rename_name
    return "cancel", ""


def confirm_multi_paste_overwrite(
    *,
    conflict_names: list[str],
    use_native_chrome: bool,
    parent: QWidget | None = None,
) -> bool:
    dialog = MultiPasteOverwriteDialog(
        conflict_names=conflict_names,
        use_native_chrome=use_native_chrome,
        parent=parent,
    )
    if dialog.exec() <= 0:
        return False
    return dialog.overwrite
