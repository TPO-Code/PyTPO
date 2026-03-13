"""Markdown editor tab that embeds a toggleable live preview panel."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from pytpo.ui.editor_workspace import EditorWidget
from TPOPyside.widgets.markdown_viewer_widget import MDHeadFlags, MarkdownViewerWidget


class MarkdownEditorTab(QWidget):
    def __init__(self, *, editor: EditorWidget, parent=None, preview_visible: bool = True):
        super().__init__(parent)
        self._editor = editor
        self.editor_id = str(getattr(editor, "editor_id", "") or id(editor))
        self._last_visible_splitter_sizes: list[int] | None = None
        self._preview: MarkdownViewerWidget | None = None
        self._preview_bg_color: QColor | None = None
        self._preview_host = QWidget(self)
        self._preview_host.setObjectName("MarkdownPreviewHost")
        self._preview_host_layout = QVBoxLayout(self._preview_host)
        self._preview_host_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_host_layout.setSpacing(0)
        self._editor.setMinimumWidth(260)
        self._preview_host.setMinimumWidth(220)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(220)
        self._refresh_timer.timeout.connect(self._render_preview)
        self._pending_refresh_while_hidden = False
        self._pending_hidden_scroll_sync = False
        self._sync_preview_scroll_use_caret = False

        self._scroll_sync_timer = QTimer(self)
        self._scroll_sync_timer.setSingleShot(True)
        self._scroll_sync_timer.setInterval(50)
        self._scroll_sync_timer.timeout.connect(self._sync_preview_scroll_position)

        self._splitter = QSplitter(self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._editor)
        self._splitter.addWidget(self._preview_host)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._apply_default_splitter_sizes(force=True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._splitter, 1)

        self._editor.textChanged.connect(lambda: self._queue_preview_refresh(immediate=False))
        self._editor.cursorPositionChanged.connect(lambda: self._queue_preview_scroll_sync(use_caret=True, immediate=False))
        self._editor.verticalScrollBar().valueChanged.connect(
            lambda _value: self._queue_preview_scroll_sync(use_caret=False, immediate=False)
        )
        self._preview_host.setVisible(False)
        self.set_preview_visible(bool(preview_visible))

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
        return bool(self._editor.save_file())

    def load_file(self, path: str) -> bool:
        ok = bool(self._editor.load_file(path))
        if ok:
            self._queue_preview_refresh(immediate=True)
        return ok

    def serialized_text(self) -> str:
        return self._editor.toPlainText()

    def setFocus(self, *args, **kwargs) -> None:
        self._editor.setFocus(*args, **kwargs)

    def set_preview_visible(self, visible: bool) -> None:
        show_preview = bool(visible)
        if show_preview == self.is_preview_visible():
            return
        if not show_preview:
            sizes = list(self._splitter.sizes())
            if len(sizes) == 2 and sizes[0] > 0 and sizes[1] > 0:
                self._last_visible_splitter_sizes = sizes
            self._preview_host.setVisible(False)
            self._splitter.setSizes([max(260, self.width()), 0])
            self._pending_refresh_while_hidden = True
            self._pending_hidden_scroll_sync = True
            self._refresh_timer.stop()
            self._scroll_sync_timer.stop()
            return

        self._preview_host.setVisible(True)
        self._ensure_preview_widget()
        if self._last_visible_splitter_sizes and len(self._last_visible_splitter_sizes) == 2:
            self._splitter.setSizes(self._last_visible_splitter_sizes)
        else:
            self._apply_default_splitter_sizes(force=True)
        if show_preview:
            self._sync_preview_background_from_editor()
            self._queue_preview_refresh(immediate=True)
            self._queue_preview_scroll_sync(use_caret=True, immediate=True)

    def is_preview_visible(self) -> bool:
        return bool(self._preview_host.isVisible())

    def _ensure_preview_widget(self) -> MarkdownViewerWidget:
        existing = self._preview
        if isinstance(existing, MarkdownViewerWidget):
            return existing
        preview = MarkdownViewerWidget(show_toolbar=False, parent=self._preview_host)
        preview.setHeadFlags(MDHeadFlags.none)
        preview.setMinimumWidth(220)
        self._preview_host_layout.addWidget(preview, 1)
        if self._preview_bg_color is not None:
            setter = getattr(preview, "setPreferredPageBackgroundColor", None)
            if callable(setter):
                setter(self._preview_bg_color)
        self._preview = preview
        return preview

    def _base_url(self) -> QUrl:
        path = str(self.file_path or "").strip()
        if not path:
            return QUrl()
        folder = str(Path(path).resolve().parent)
        url = QUrl.fromLocalFile(folder)
        text = url.toString()
        if text and not text.endswith("/"):
            return QUrl(text + "/")
        return url

    def _queue_preview_refresh(self, *, immediate: bool) -> None:
        if not self.is_preview_visible():
            self._pending_refresh_while_hidden = True
            self._refresh_timer.stop()
            return
        if immediate:
            self._refresh_timer.start(0)
            return
        self._refresh_timer.start()

    def _render_preview(self) -> None:
        if not self.is_preview_visible():
            self._pending_refresh_while_hidden = True
            return
        preview = self._ensure_preview_widget()
        self._sync_preview_background_from_editor()
        text = str(self._editor.toPlainText() or "")
        preview.setMarkdown(text, base_url=self._base_url())
        self._pending_refresh_while_hidden = False
        self._queue_preview_scroll_sync(use_caret=True, immediate=True)

    def _queue_preview_scroll_sync(self, *, use_caret: bool, immediate: bool) -> None:
        self._sync_preview_scroll_use_caret = bool(use_caret)
        if not self.is_preview_visible():
            self._pending_hidden_scroll_sync = True
            self._scroll_sync_timer.stop()
            return
        if immediate:
            self._scroll_sync_timer.start(0)
            return
        self._scroll_sync_timer.start()

    def _sync_preview_scroll_position(self) -> None:
        if not self.is_preview_visible():
            self._pending_hidden_scroll_sync = True
            return
        preview = self._ensure_preview_widget()
        ratio = self._editor_scroll_ratio(use_caret=self._sync_preview_scroll_use_caret)
        preview.scroll_to_ratio(ratio, smooth=False)
        self._pending_hidden_scroll_sync = False

    def _editor_scroll_ratio(self, *, use_caret: bool) -> float:
        if use_caret:
            try:
                cursor = self._editor.textCursor()
                block = int(cursor.blockNumber())
                count = int(self._editor.document().blockCount())
            except Exception:
                block = 0
                count = 0
            if count > 1:
                return max(0.0, min(1.0, float(block) / float(count - 1)))

        bar = self._editor.verticalScrollBar()
        try:
            minimum = int(bar.minimum())
            maximum = int(bar.maximum())
            value = int(bar.value())
        except Exception:
            minimum = maximum = value = 0
        span = max(0, maximum - minimum)
        if span > 0:
            return max(0.0, min(1.0, float(value - minimum) / float(span)))

        try:
            cursor = self._editor.textCursor()
            block = int(cursor.blockNumber())
            count = int(self._editor.document().blockCount())
        except Exception:
            return 0.0
        if count > 1:
            return max(0.0, min(1.0, float(block) / float(count - 1)))
        return 0.0

    def _sync_preview_background_from_editor(self) -> None:
        color = None
        getter = getattr(self._editor, "editor_background_color", None)
        if callable(getter):
            try:
                candidate = getter()
            except Exception:
                candidate = None
            if isinstance(candidate, QColor) and candidate.isValid() and candidate.alpha() > 0:
                color = candidate

        if color is None:
            candidate = getattr(self._editor, "_editor_background_color", None)
            if isinstance(candidate, QColor) and candidate.isValid() and candidate.alpha() > 0:
                color = candidate

        if color is None:
            try:
                palette_color = QColor(self._editor.palette().color(self._editor.backgroundRole()))
            except Exception:
                palette_color = QColor()
            if palette_color.isValid() and palette_color.alpha() > 0:
                color = palette_color

        self._preview_bg_color = color if isinstance(color, QColor) and color.isValid() else None
        if not isinstance(self._preview, MarkdownViewerWidget):
            return
        setter = getattr(self._preview, "setPreferredPageBackgroundColor", None)
        if callable(setter):
            setter(self._preview_bg_color if self._preview_bg_color is not None else "")

    def _apply_default_splitter_sizes(self, *, force: bool = False) -> None:
        if not self.is_preview_visible():
            return
        current = list(self._splitter.sizes())
        if len(current) == 2 and not force and current[0] > 120 and current[1] > 80:
            return
        total = max(640, self.width(), sum(current) if len(current) == 2 else 0)
        editor_w = max(360, int(total * 0.62))
        preview_w = max(220, total - editor_w)
        self._splitter.setSizes([editor_w, preview_w])

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_default_splitter_sizes(force=False)
        if self._pending_refresh_while_hidden and self.is_preview_visible():
            self._queue_preview_refresh(immediate=True)
        if self._pending_hidden_scroll_sync and self.is_preview_visible():
            self._queue_preview_scroll_sync(use_caret=False, immediate=True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_default_splitter_sizes(force=False)
