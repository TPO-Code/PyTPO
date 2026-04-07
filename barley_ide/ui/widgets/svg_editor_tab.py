"""SVG editor tab that embeds a live image preview above the source editor."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from barley_ide.ui.editor_workspace import EditorWidget
from barley_ide.ui.widgets.image_viewer import ImageViewerWidget


class SvgEditorTab(QWidget):
    def __init__(self, *, editor: EditorWidget, parent=None):
        super().__init__(parent)
        self._editor = editor
        self._preview = ImageViewerWidget(parent=self)
        self.editor_id = str(getattr(editor, "editor_id", "") or id(editor))

        self._editor.setMinimumHeight(180)
        self._preview.setMinimumHeight(180)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(160)
        self._refresh_timer.timeout.connect(self._render_preview)

        self._splitter = QSplitter(Qt.Vertical, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._preview)
        self._splitter.addWidget(self._editor)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([420, 260])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._splitter, 1)

        self._editor.textChanged.connect(lambda: self._queue_preview_refresh(immediate=False))
        self._queue_preview_refresh(immediate=True)

    def __getattr__(self, name: str):
        return getattr(self._editor, name)

    def editor_widget(self) -> EditorWidget:
        return self._editor

    @property
    def file_path(self) -> str | None:
        return getattr(self._editor, "file_path", None)

    @file_path.setter
    def file_path(self, path: str | None) -> None:
        self._editor.file_path = path
        self._preview.set_file_path(path)
        self._queue_preview_refresh(immediate=True)

    def set_file_path(self, path: str | None) -> None:
        self.file_path = path

    def display_name(self) -> str:
        return self._editor.display_name()

    def document(self):
        return self._editor.document()

    def textCursor(self):
        return self._editor.textCursor()

    def setTextCursor(self, cursor) -> None:
        self._editor.setTextCursor(cursor)

    def toPlainText(self) -> str:
        return self._editor.toPlainText()

    def setPlainText(self, text: str) -> None:
        self._editor.setPlainText(text)
        self._queue_preview_refresh(immediate=False)

    def save_file(self) -> bool:
        ok = bool(self._editor.save_file())
        if ok:
            self._queue_preview_refresh(immediate=True)
        return ok

    def load_file(self, path: str) -> bool:
        ok = bool(self._editor.load_file(path))
        if ok:
            self._preview.set_file_path(path)
            self._queue_preview_refresh(immediate=True)
        return ok

    def serialized_text(self) -> str:
        return self._editor.toPlainText()

    def setFocus(self, *args, **kwargs) -> None:
        self._editor.setFocus(*args, **kwargs)

    def set_viewer_background(self, value) -> None:
        self._preview.set_viewer_background(value)

    def refresh_preview_from_source(self) -> None:
        self._queue_preview_refresh(immediate=True)

    def _queue_preview_refresh(self, *, immediate: bool) -> None:
        if immediate:
            self._refresh_timer.start(0)
            return
        self._refresh_timer.start()

    def _render_preview(self) -> None:
        svg_text = str(self._editor.toPlainText() or "")
        path = str(self.file_path or "").strip()
        if self._preview.load_svg_text(svg_text, file_path=path or None):
            return
        if path and Path(path).exists():
            self._preview.load_file(path)
