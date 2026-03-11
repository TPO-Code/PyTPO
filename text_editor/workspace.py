from __future__ import annotations

import uuid
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSplitter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.widgets import CodeEditor

MIME_EDITOR_TAB = "application/x-text-editor-tab-id"


class DropZone(Enum):
    NONE = 0
    CENTER = 1
    LEFT = 2
    RIGHT = 3
    TOP = 4
    BOTTOM = 5


class EditorView(CodeEditor):
    activated = Signal(object)
    titleChanged = Signal(object)
    cursorStatusChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor_id = uuid.uuid4().hex
        self.file_path: Path | None = None
        self._line_ending = "\n"
        self._disk_signature: tuple[bool, int, int] | None = None
        self._externally_modified = False
        self.set_word_wrap_enabled(False)
        self.document().modificationChanged.connect(self._emit_title_changed)
        self.cursorPositionChanged.connect(self._emit_cursor_status_changed)
        self.selectionChanged.connect(self._emit_cursor_status_changed)

    def _emit_title_changed(self, _modified: bool) -> None:
        self.titleChanged.emit(self)

    def _emit_cursor_status_changed(self) -> None:
        self.cursorStatusChanged.emit(self)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.activated.emit(self)
        super().focusInEvent(event)

    def display_name(self) -> str:
        return self.file_path.name if self.file_path else "Untitled"

    def tab_title(self) -> str:
        base = self.display_name()
        if self._externally_modified:
            base = f"{base} [Reload?]"
        if self.document().isModified():
            base = f"{base}*"
        return base

    @property
    def line_ending(self) -> str:
        return self._line_ending

    @property
    def externally_modified(self) -> bool:
        return self._externally_modified

    @staticmethod
    def detect_line_ending(text: str) -> str:
        raw = str(text or "")
        first_crlf = raw.find("\r\n")
        first_lf = raw.find("\n")
        first_cr = raw.find("\r")
        if first_crlf >= 0 and (first_lf < 0 or first_crlf <= first_lf):
            return "\r\n"
        if first_lf >= 0:
            return "\n"
        if first_cr >= 0:
            return "\r"
        return "\n"

    @staticmethod
    def file_signature(path: Path | None) -> tuple[bool, int, int] | None:
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return (False, 0, 0)
        return (True, int(getattr(stat, "st_mtime_ns", 0)), int(stat.st_size))

    @staticmethod
    def _normalize_text_for_line_ending(text: str, line_ending: str) -> str:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        if line_ending == "\r\n":
            return normalized.replace("\n", "\r\n")
        if line_ending == "\r":
            return normalized.replace("\n", "\r")
        return normalized

    def set_externally_modified(self, changed: bool) -> None:
        requested = bool(changed)
        if requested == self._externally_modified:
            return
        self._externally_modified = requested
        self.titleChanged.emit(self)

    def mark_disk_state_current(self) -> None:
        self._disk_signature = self.file_signature(self.file_path)
        self.set_externally_modified(False)

    def set_path(self, path: Path | None) -> None:
        self.file_path = path
        self.set_file_path(str(path) if path else None)
        self.titleChanged.emit(self)

    def load_from_path(self, path: Path) -> None:
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                text = handle.read()
        except UnicodeDecodeError as exc:
            raise ValueError("Only UTF-8 text files are supported.") from exc
        self._line_ending = self.detect_line_ending(text)
        self.setPlainText(text)
        self.document().setModified(False)
        self.set_path(path)
        self.mark_disk_state_current()
        self._emit_cursor_status_changed()

    def save_to_path(self, path: Path) -> None:
        path.write_text(
            self._normalize_text_for_line_ending(self.toPlainText(), self._line_ending),
            encoding="utf-8",
            newline="",
        )
        self.document().setModified(False)
        self.set_path(path)
        self.mark_disk_state_current()
        self._emit_cursor_status_changed()


class DropOverlay(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zone = DropZone.NONE
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.hide()

    def set_zone(self, zone: DropZone) -> None:
        self._zone = zone
        self.update()

    def _zone_rect(self):
        rect = self.rect()
        width = rect.width()
        height = rect.height()
        if self._zone == DropZone.LEFT:
            return rect.adjusted(0, 0, -(width // 2), 0)
        if self._zone == DropZone.RIGHT:
            return rect.adjusted(width // 2, 0, 0, 0)
        if self._zone == DropZone.TOP:
            return rect.adjusted(0, 0, 0, -(height // 2))
        if self._zone == DropZone.BOTTOM:
            return rect.adjusted(0, height // 2, 0, 0)
        return rect.adjusted(width // 6, height // 6, -(width // 6), -(height // 6))

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._zone == DropZone.NONE:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(15, 23, 42, 50))
        painter.setPen(QPen(QColor(56, 189, 248, 220), 2))
        painter.setBrush(QColor(56, 189, 248, 80))
        painter.drawRoundedRect(self._zone_rect(), 10, 10)


class DraggableTabBar(QTabBar):
    def __init__(self, tabs_widget: "EditorTabs", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tabs_widget = tabs_widget
        self._drag_start_pos = QPoint()
        self.setMovable(True)
        self.setAcceptDrops(True)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not (event.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        index = self.tabAt(self._drag_start_pos)
        editor = self.tabs_widget.widget(index)
        if not isinstance(editor, EditorView):
            super().mouseMoveEvent(event)
            return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME_EDITOR_TAB, editor.editor_id.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_EDITOR_TAB):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_EDITOR_TAB):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(MIME_EDITOR_TAB):
            super().dropEvent(event)
            return
        editor_id = bytes(event.mimeData().data(MIME_EDITOR_TAB)).decode("utf-8", errors="ignore").strip()
        self.tabs_widget.workspace.move_editor(editor_id, self.tabs_widget, DropZone.CENTER)
        event.acceptProposedAction()


class EditorTabs(QTabWidget):
    def __init__(self, workspace: "EditorWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._overlay = DropOverlay(self)
        self.setTabBar(DraggableTabBar(self, self))
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self.setAcceptDrops(True)
        self.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabCloseRequested.connect(self._on_tab_close_requested)
        self.currentChanged.connect(self._on_current_changed)
        self.tabBar().customContextMenuRequested.connect(self._show_tab_context_menu)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())

    def _compute_zone(self, pos: QPoint) -> DropZone:
        rect = self.rect()
        if not rect.contains(pos):
            return DropZone.NONE
        mx = max(48, rect.width() // 5)
        my = max(36, rect.height() // 5)
        if pos.x() < mx:
            return DropZone.LEFT
        if pos.x() > rect.width() - mx:
            return DropZone.RIGHT
        if pos.y() < my:
            return DropZone.TOP
        if pos.y() > rect.height() - my:
            return DropZone.BOTTOM
        return DropZone.CENTER

    def _show_overlay(self, zone: DropZone) -> None:
        self._overlay.set_zone(zone)
        if zone == DropZone.NONE:
            self._overlay.hide()
        else:
            self._overlay.show()
            self._overlay.raise_()

    def _hide_overlay(self) -> None:
        self._overlay.set_zone(DropZone.NONE)
        self._overlay.hide()

    def add_editor(self, editor: EditorView) -> None:
        index = self.addTab(editor, editor.tab_title())
        previous_title_tabs = getattr(editor, "_title_listener_tabs", None)
        if isinstance(previous_title_tabs, EditorTabs) and previous_title_tabs is not self:
            try:
                editor.titleChanged.disconnect(previous_title_tabs._refresh_editor_title)
            except Exception:
                pass

        previous_workspace = getattr(editor, "_activation_listener_workspace", None)
        if isinstance(previous_workspace, EditorWorkspace) and previous_workspace is not self.workspace:
            try:
                editor.activated.disconnect(previous_workspace.set_active_editor)
            except Exception:
                pass

        editor.titleChanged.connect(self._refresh_editor_title)
        editor.activated.connect(self.workspace.set_active_editor)
        editor._title_listener_tabs = self
        editor._activation_listener_workspace = self.workspace
        self.setCurrentIndex(index)
        self.workspace.set_active_editor(editor)
        editor.setFocus()

    def _refresh_editor_title(self, editor: object) -> None:
        if not isinstance(editor, EditorView):
            return
        index = self.indexOf(editor)
        if index >= 0:
            self.setTabText(index, editor.tab_title())
            tooltip = str(editor.file_path) if editor.file_path is not None else "Unsaved file"
            if editor.externally_modified:
                tooltip = f"{tooltip}\nFile changed on disk."
            self.setTabToolTip(index, tooltip)
        self.workspace.notify_state_changed()

    def _on_tab_close_requested(self, index: int) -> None:
        editor = self.widget(index)
        if isinstance(editor, EditorView):
            self.workspace.close_editor(editor)

    def _on_current_changed(self, index: int) -> None:
        editor = self.widget(index)
        if isinstance(editor, EditorView):
            self.workspace.set_active_editor(editor)
        else:
            self.workspace.notify_state_changed()

    def _show_tab_context_menu(self, pos: QPoint) -> None:
        index = self.tabBar().tabAt(pos)
        if index < 0:
            return
        editor = self.widget(index)
        if not isinstance(editor, EditorView):
            return
        menu = QMenu(self)
        act_close = QAction("Close", menu)
        act_close_others = QAction("Close Others", menu)
        act_close_all = QAction("Close All", menu)
        menu.addAction(act_close)
        menu.addAction(act_close_others)
        menu.addAction(act_close_all)
        if editor.file_path is not None:
            menu.addSeparator()
            act_reload = QAction("Reload from Disk", menu)
            act_copy_path = QAction("Copy Path", menu)
            act_reveal = QAction("Reveal in File Manager", menu)
            menu.addAction(act_reload)
            menu.addAction(act_copy_path)
            menu.addAction(act_reveal)
        else:
            act_reload = None
            act_copy_path = None
            act_reveal = None
        chosen = menu.exec(self.tabBar().mapToGlobal(pos))
        if chosen is act_close:
            self.workspace.close_editor(editor, self.window())
            return
        if chosen is act_close_others:
            self.workspace.close_other_editors(editor, self.window())
            return
        if chosen is act_close_all:
            self.workspace.request_close_all(self.window())
            return
        if chosen is act_reload:
            self.workspace.reload_editor_from_disk(editor, self.window(), force=True)
            return
        if chosen is act_copy_path and editor.file_path is not None:
            QApplication.clipboard().setText(str(editor.file_path))
            return
        if chosen is act_reveal and editor.file_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(editor.file_path.parent)))
            return

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_EDITOR_TAB):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(MIME_EDITOR_TAB):
            super().dragMoveEvent(event)
            return
        zone = self._compute_zone(event.position().toPoint())
        self._show_overlay(zone)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._hide_overlay()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(MIME_EDITOR_TAB):
            super().dropEvent(event)
            return
        editor_id = bytes(event.mimeData().data(MIME_EDITOR_TAB)).decode("utf-8", errors="ignore").strip()
        zone = self._compute_zone(event.position().toPoint())
        self._hide_overlay()
        self.workspace.move_editor(editor_id, self, zone)
        event.acceptProposedAction()


class EditorWorkspace(QWidget):
    stateChanged = Signal()
    activeEditorChanged = Signal(object)
    editorOpened = Signal(object)
    editorSaved = Signal(object)
    externalChangeDetected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active_editor: EditorView | None = None

        self.root_splitter = QSplitter(Qt.Horizontal, self)
        self.root_splitter.setChildrenCollapsible(False)
        self.root_splitter.setHandleWidth(6)

        self._primary_tabs = EditorTabs(self, self.root_splitter)
        self.root_splitter.addWidget(self._primary_tabs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.root_splitter)

    def notify_state_changed(self) -> None:
        self.stateChanged.emit()

    def all_tabs(self) -> list[EditorTabs]:
        return self.findChildren(EditorTabs)

    def all_editors(self) -> list[EditorView]:
        return self.findChildren(EditorView)

    def set_active_editor(self, editor: object) -> None:
        if isinstance(editor, EditorView):
            self._active_editor = editor
            self.activeEditorChanged.emit(editor)
        self.notify_state_changed()

    def current_editor(self) -> EditorView | None:
        if isinstance(self._active_editor, EditorView):
            return self._active_editor
        for tabs in self.all_tabs():
            editor = tabs.currentWidget()
            if isinstance(editor, EditorView):
                self._active_editor = editor
                return editor
        return None

    def _tabs_for_editor(self, editor: EditorView | None) -> EditorTabs | None:
        if not isinstance(editor, EditorView):
            return None
        for tabs in self.all_tabs():
            if tabs.indexOf(editor) >= 0:
                return tabs
        return None

    def _current_tabs(self) -> EditorTabs:
        editor = self.current_editor()
        tabs = self._tabs_for_editor(editor)
        if isinstance(tabs, EditorTabs):
            return tabs
        return self._primary_tabs

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

    def _focus_editor(self, editor: EditorView) -> None:
        tabs = self._tabs_for_editor(editor)
        if isinstance(tabs, EditorTabs):
            tabs.setCurrentWidget(editor)
        editor.setFocus()
        self.set_active_editor(editor)

    def new_file(self) -> EditorView:
        editor = EditorView()
        self._current_tabs().add_editor(editor)
        self.notify_state_changed()
        return editor

    def open_path(self, path: Path) -> EditorView | None:
        existing = self._find_open_editor_for_path(path)
        if existing is not None:
            self._focus_editor(existing)
            return existing

        editor = EditorView()
        editor.load_from_path(path)
        self._current_tabs().add_editor(editor)
        self.editorOpened.emit(editor)
        self.notify_state_changed()
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
        self.editorSaved.emit(editor)
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
        self.editorSaved.emit(editor)
        self.notify_state_changed()
        return True

    def reload_editor_from_disk(
        self,
        editor: EditorView | None,
        parent: QWidget | None = None,
        *,
        force: bool = False,
    ) -> bool:
        if not isinstance(editor, EditorView) or editor.file_path is None:
            return False
        if editor.document().isModified() and not force:
            response = QMessageBox.question(
                parent or self,
                "Reload From Disk",
                f"Discard unsaved changes and reload {editor.display_name()} from disk?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if response != QMessageBox.StandardButton.Yes:
                return False
        try:
            editor.load_from_path(editor.file_path)
        except ValueError as exc:
            QMessageBox.warning(parent or self, "Reload Failed", str(exc))
            return False
        except OSError as exc:
            QMessageBox.critical(parent or self, "Reload Failed", str(exc))
            return False
        self.notify_state_changed()
        return True

    def maybe_prompt_reload(self, editor: EditorView | None, parent: QWidget | None = None) -> bool:
        if not isinstance(editor, EditorView) or not editor.externally_modified or editor.file_path is None:
            return False
        if editor.document().isModified():
            response = QMessageBox.question(
                parent or self,
                "File Changed on Disk",
                (
                    f"{editor.display_name()} changed on disk.\n\n"
                    "Reload from disk and discard unsaved changes?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Ignore,
                QMessageBox.StandardButton.Yes,
            )
            if response == QMessageBox.StandardButton.Yes:
                return self.reload_editor_from_disk(editor, parent, force=True)
            return False
        response = QMessageBox.question(
            parent or self,
            "File Changed on Disk",
            f"{editor.display_name()} changed on disk.\n\nReload the file now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Ignore,
            QMessageBox.StandardButton.Yes,
        )
        if response == QMessageBox.StandardButton.Yes:
            return self.reload_editor_from_disk(editor, parent, force=True)
        return False

    def check_external_file_changes(self) -> list[EditorView]:
        changed: list[EditorView] = []
        for editor in self.all_editors():
            if not isinstance(editor, EditorView) or editor.file_path is None:
                continue
            current_signature = editor.file_signature(editor.file_path)
            if current_signature is None:
                continue
            known_signature = editor._disk_signature
            if known_signature is None:
                editor._disk_signature = current_signature
                continue
            if current_signature != known_signature:
                editor.set_externally_modified(True)
                editor._disk_signature = current_signature
                changed.append(editor)
                self.externalChangeDetected.emit(editor)
        return changed

    def _confirm_close_editor(self, editor: EditorView, parent: QWidget | None = None) -> bool:
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

    def close_editor(self, editor: EditorView | None, parent: QWidget | None = None) -> bool:
        if not isinstance(editor, EditorView):
            return True
        if not self._confirm_close_editor(editor, parent):
            return False
        tabs = self._tabs_for_editor(editor)
        if not isinstance(tabs, EditorTabs):
            return True
        index = tabs.indexOf(editor)
        if index >= 0:
            tabs.removeTab(index)
        editor.deleteLater()
        if self._active_editor is editor:
            self._active_editor = None
        self._cleanup_empty_panes()
        self.notify_state_changed()
        return True

    def close_current_editor(self, parent: QWidget | None = None) -> bool:
        return self.close_editor(self.current_editor(), parent)

    def request_close_all(self, parent: QWidget | None = None) -> bool:
        editors = list(self.all_editors())
        for editor in editors:
            if not self.close_editor(editor, parent):
                return False
        return True

    def close_other_editors(self, keep_editor: EditorView | None, parent: QWidget | None = None) -> bool:
        if not isinstance(keep_editor, EditorView):
            return True
        for editor in list(self.all_editors()):
            if editor is keep_editor:
                continue
            if not self.close_editor(editor, parent):
                return False
        self._focus_editor(keep_editor)
        return True

    def _split_tabs(self, target_tabs: EditorTabs, orientation: Qt.Orientation, before: bool) -> EditorTabs:
        parent = target_tabs.parentWidget()
        if isinstance(parent, QSplitter) and parent.orientation() == orientation:
            index = parent.indexOf(target_tabs)
            new_tabs = EditorTabs(self, parent)
            parent.insertWidget(index if before else index + 1, new_tabs)
            return new_tabs

        if not isinstance(parent, QSplitter):
            return target_tabs

        replacement = QSplitter(orientation, parent)
        replacement.setChildrenCollapsible(False)
        replacement.setHandleWidth(6)

        index = parent.indexOf(target_tabs)
        target_tabs.setParent(None)
        parent.insertWidget(index, replacement)

        new_tabs = EditorTabs(self, replacement)
        if before:
            replacement.addWidget(new_tabs)
            replacement.addWidget(target_tabs)
        else:
            replacement.addWidget(target_tabs)
            replacement.addWidget(new_tabs)
        return new_tabs

    def find_editor(self, editor_id: str) -> tuple[EditorView | None, EditorTabs | None]:
        for tabs in self.all_tabs():
            for index in range(tabs.count()):
                editor = tabs.widget(index)
                if isinstance(editor, EditorView) and editor.editor_id == editor_id:
                    return editor, tabs
        return None, None

    def move_editor(self, editor_id: str, target_tabs: EditorTabs, zone: DropZone) -> None:
        editor, source_tabs = self.find_editor(editor_id)
        if not isinstance(editor, EditorView) or not isinstance(source_tabs, EditorTabs):
            return

        destination = target_tabs
        if zone == DropZone.LEFT:
            destination = self._split_tabs(target_tabs, Qt.Horizontal, before=True)
        elif zone == DropZone.RIGHT:
            destination = self._split_tabs(target_tabs, Qt.Horizontal, before=False)
        elif zone == DropZone.TOP:
            destination = self._split_tabs(target_tabs, Qt.Vertical, before=True)
        elif zone == DropZone.BOTTOM:
            destination = self._split_tabs(target_tabs, Qt.Vertical, before=False)

        if source_tabs is destination and zone == DropZone.CENTER:
            self._focus_editor(editor)
            return

        source_index = source_tabs.indexOf(editor)
        if source_index >= 0:
            source_tabs.removeTab(source_index)
        destination.add_editor(editor)
        self._cleanup_empty_panes()
        self.notify_state_changed()

    def _cleanup_empty_panes(self) -> None:
        tabs_list = self.all_tabs()
        if len(tabs_list) <= 1:
            return
        for tabs in tabs_list:
            if tabs.count() != 0:
                continue
            parent = tabs.parentWidget()
            if not isinstance(parent, QSplitter):
                continue
            tabs.setParent(None)
            tabs.deleteLater()
            self._collapse_splitter(parent)

    def _collapse_splitter(self, splitter: QSplitter) -> None:
        while splitter is not self.root_splitter and splitter.count() == 1:
            child = splitter.widget(0)
            parent = splitter.parentWidget()
            if not isinstance(parent, QSplitter) or child is None:
                return
            index = parent.indexOf(splitter)
            child.setParent(None)
            splitter.deleteLater()
            parent.insertWidget(index, child)
            splitter = parent
