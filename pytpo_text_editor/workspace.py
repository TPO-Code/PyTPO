from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMessageBox, QToolButton, QWidget

from TPOPyside.widgets import CodeEditor
from TPOPyside.widgets.split_tab_workspace import SplitterTabWorkspace, WorkspaceTabs


class EditorView(CodeEditor):
    activated = Signal(object)
    titleChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor_id = uuid.uuid4().hex
        self.file_path: Path | None = None
        self.set_word_wrap_enabled(False)
        self.document().modificationChanged.connect(self._emit_title_changed)

    def _emit_title_changed(self, _modified: bool) -> None:
        self.titleChanged.emit(self)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.activated.emit(self)
        super().focusInEvent(event)

    def display_name(self) -> str:
        return self.file_path.name if self.file_path else "Untitled"

    def tab_title(self) -> str:
        suffix = "*" if self.document().isModified() else ""
        return f"{self.display_name()}{suffix}"

    def set_path(self, path: Path | None) -> None:
        self.file_path = path
        self.set_file_path(str(path) if path else None)
        self.titleChanged.emit(self)

    def load_from_path(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Only UTF-8 text files are supported.") from exc
        self.setPlainText(text)
        self.document().setModified(False)
        self.set_path(path)

    def save_to_path(self, path: Path) -> None:
        path.write_text(self.toPlainText(), encoding="utf-8")
        self.document().setModified(False)
        self.set_path(path)


class EditorWorkspaceTabs(WorkspaceTabs):
    def __init__(self, workspace: "EditorWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(workspace, parent)
        self._workspace = workspace

        self._new_tab_button = QToolButton(self)
        self._new_tab_button.setText("+")
        self._new_tab_button.setToolTip("New File")
        self._new_tab_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_tab_button.clicked.connect(lambda _checked=False: self._workspace.new_file(self))
        self.setCornerWidget(self._new_tab_button, Qt.Corner.TopRightCorner)


class EditorWorkspace(SplitterTabWorkspace):
    def create_tabs(self, parent: QWidget | None = None) -> WorkspaceTabs:
        return EditorWorkspaceTabs(self, parent)

    def is_editor_widget(self, widget: object) -> bool:
        return isinstance(widget, EditorView)

    def all_editors(self) -> list[EditorView]:
        return [editor for editor in super().all_editors() if isinstance(editor, EditorView)]

    def current_editor(self) -> EditorView | None:
        editor = super().current_editor()
        return editor if isinstance(editor, EditorView) else None

    def _canonical_path(self, path: Path) -> Path:
        try:
            return path.resolve()
        except OSError:
            return path

    def _find_open_editor_for_path(self, path: Path) -> EditorView | None:
        wanted = self._canonical_path(path)
        for editor in self.all_editors():
            if editor.file_path and self._canonical_path(editor.file_path) == wanted:
                return editor
        return None

    def new_file(self, target_tabs: WorkspaceTabs | None = None) -> EditorView:
        editor = EditorView()
        tabs = target_tabs if isinstance(target_tabs, WorkspaceTabs) else None
        self.add_editor(editor, tabs=tabs)
        return editor

    def open_path(self, path: Path) -> EditorView | None:
        existing = self._find_open_editor_for_path(path)
        if existing is not None:
            self._focus_editor(existing)
            return existing

        editor = EditorView()
        editor.load_from_path(path)
        self.add_editor(editor)
        return editor

    def save_editor(self, editor: EditorView | None, parent: QWidget | None = None) -> bool:
        if not isinstance(editor, EditorView):
            return False
        if editor.file_path is None:
            return self.save_editor_as(editor, parent)
        try:
            editor.save_to_path(editor.file_path)
        except OSError as exc:
            QMessageBox.critical(parent or self, "Save Failed", str(exc))
            return False
        self.notify_state_changed()
        return True

    def save_editor_as(
        self,
        editor: EditorView | None,
        parent: QWidget | None = None,
        target_path: Path | None = None,
    ) -> bool:
        if not isinstance(editor, EditorView):
            return False
        if target_path is None:
            return False
        try:
            editor.save_to_path(target_path)
        except OSError as exc:
            QMessageBox.critical(parent or self, "Save Failed", str(exc))
            return False
        self.notify_state_changed()
        return True

    def confirm_close_editor(self, editor: QWidget, parent: QWidget | None = None) -> bool:
        if not isinstance(editor, EditorView):
            return True
        if not editor.document().isModified():
            return True
        response = QMessageBox.question(
            parent or self,
            "Unsaved Changes",
            f"Save changes to {editor.display_name()} before closing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if response == QMessageBox.StandardButton.Save:
            return self.save_editor(editor, parent)
        if response == QMessageBox.StandardButton.Cancel:
            return False
        return True
