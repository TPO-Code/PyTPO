from __future__ import annotations

from enum import Enum
from typing import Any

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import QApplication, QSplitter, QTabBar, QTabWidget, QVBoxLayout, QWidget

MIME_WORKSPACE_TAB = "application/x-tpopyside-workspace-tab-id"


class DropZone(Enum):
    NONE = 0
    CENTER = 1
    LEFT = 2
    RIGHT = 3
    TOP = 4
    BOTTOM = 5


def _editor_id(editor: object) -> str:
    return str(getattr(editor, "editor_id", "") or "").strip()


def _editor_tab_title(editor: object) -> str:
    tab_title = getattr(editor, "tab_title", None)
    if callable(tab_title):
        try:
            return str(tab_title())
        except Exception:
            pass

    display_name = getattr(editor, "display_name", None)
    if callable(display_name):
        try:
            return str(display_name())
        except Exception:
            pass

    return "Untitled"


def _qt_signal(obj: object, signal_name: str) -> Any | None:
    signal_obj = getattr(obj, signal_name, None)
    if signal_obj is None:
        return None
    if not hasattr(signal_obj, "connect") or not hasattr(signal_obj, "disconnect"):
        return None
    return signal_obj


def _safe_signal_connect(signal_obj: Any, slot: Any) -> bool:
    try:
        signal_obj.connect(slot)
    except Exception:
        return False
    return True


def _safe_signal_disconnect(signal_obj: Any, slot: Any) -> None:
    try:
        signal_obj.disconnect(slot)
    except Exception:
        pass


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

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._zone == DropZone.NONE:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(15, 23, 42, 50))
        painter.setPen(QPen(QColor(56, 189, 248, 220), 2))
        painter.setBrush(QColor(56, 189, 248, 80))
        painter.drawRoundedRect(self._zone_rect(), 10, 10)

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


class DraggableTabBar(QTabBar):
    def __init__(self, tabs_widget: "WorkspaceTabs", parent: QWidget | None = None) -> None:
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
        editor_id = _editor_id(editor)
        if not self.tabs_widget.workspace.is_editor_widget(editor) or not editor_id:
            super().mouseMoveEvent(event)
            return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME_WORKSPACE_TAB, editor_id.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_WORKSPACE_TAB):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_WORKSPACE_TAB):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(MIME_WORKSPACE_TAB):
            super().dropEvent(event)
            return

        editor_id = bytes(event.mimeData().data(MIME_WORKSPACE_TAB)).decode("utf-8", errors="ignore").strip()
        self.tabs_widget.workspace.move_editor(editor_id, self.tabs_widget, DropZone.CENTER)
        event.acceptProposedAction()


class WorkspaceTabs(QTabWidget):
    def __init__(self, workspace: "SplitterTabWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._overlay = DropOverlay(self)
        self.setTabBar(DraggableTabBar(self, self))
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self.setAcceptDrops(True)
        self.tabCloseRequested.connect(self._on_tab_close_requested)
        self.currentChanged.connect(self._on_current_changed)

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

    def add_editor(self, editor: QWidget) -> None:
        if not self.workspace.is_editor_widget(editor):
            return

        index = self.addTab(editor, _editor_tab_title(editor))

        previous_title_tabs = getattr(editor, "_workspace_title_listener_tabs", None)
        if isinstance(previous_title_tabs, WorkspaceTabs) and previous_title_tabs is not self:
            signal_obj = _qt_signal(editor, "titleChanged")
            if signal_obj is not None:
                _safe_signal_disconnect(signal_obj, previous_title_tabs._refresh_editor_title)

        previous_workspace = getattr(editor, "_workspace_activation_listener_workspace", None)
        if isinstance(previous_workspace, SplitterTabWorkspace) and previous_workspace is not self.workspace:
            signal_obj = _qt_signal(editor, "activated")
            if signal_obj is not None:
                _safe_signal_disconnect(signal_obj, previous_workspace.set_active_editor)

        title_signal = _qt_signal(editor, "titleChanged")
        if title_signal is not None:
            _safe_signal_connect(title_signal, self._refresh_editor_title)

        activate_signal = _qt_signal(editor, "activated")
        if activate_signal is not None:
            _safe_signal_connect(activate_signal, self.workspace.set_active_editor)

        try:
            setattr(editor, "_workspace_title_listener_tabs", self)
            setattr(editor, "_workspace_activation_listener_workspace", self.workspace)
        except Exception:
            pass

        self.setCurrentIndex(index)
        self.workspace.set_active_editor(editor)
        editor.setFocus()

    def _refresh_editor_title(self, editor: object) -> None:
        if not self.workspace.is_editor_widget(editor):
            return
        index = self.indexOf(editor)
        if index >= 0:
            self.setTabText(index, _editor_tab_title(editor))
        self.workspace.notify_state_changed()

    def _on_tab_close_requested(self, index: int) -> None:
        editor = self.widget(index)
        if self.workspace.is_editor_widget(editor):
            self.workspace.close_editor(editor)

    def _on_current_changed(self, index: int) -> None:
        editor = self.widget(index)
        if self.workspace.is_editor_widget(editor):
            self.workspace.set_active_editor(editor)
        else:
            self.workspace.notify_state_changed()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_WORKSPACE_TAB):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(MIME_WORKSPACE_TAB):
            super().dragMoveEvent(event)
            return
        zone = self._compute_zone(event.position().toPoint())
        self._show_overlay(zone)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._hide_overlay()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(MIME_WORKSPACE_TAB):
            super().dropEvent(event)
            return
        editor_id = bytes(event.mimeData().data(MIME_WORKSPACE_TAB)).decode("utf-8", errors="ignore").strip()
        zone = self._compute_zone(event.position().toPoint())
        self._hide_overlay()
        self.workspace.move_editor(editor_id, self, zone)
        event.acceptProposedAction()


class SplitterTabWorkspace(QWidget):
    stateChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active_editor: QWidget | None = None

        self.root_splitter = QSplitter(Qt.Horizontal, self)
        self.root_splitter.setChildrenCollapsible(False)
        self.root_splitter.setHandleWidth(6)

        self._primary_tabs = self.create_tabs(self.root_splitter)
        self.root_splitter.addWidget(self._primary_tabs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.root_splitter)

        app = QApplication.instance()
        if app is not None:
            try:
                app.focusChanged.connect(self._on_app_focus_changed)
            except Exception:
                pass

    def create_tabs(self, parent: QWidget | None = None) -> WorkspaceTabs:
        return WorkspaceTabs(self, parent)

    def is_editor_widget(self, widget: object) -> bool:
        return isinstance(widget, QWidget) and bool(_editor_id(widget))

    def notify_state_changed(self) -> None:
        self.stateChanged.emit()

    def all_tabs(self) -> list[WorkspaceTabs]:
        return self.findChildren(WorkspaceTabs)

    def all_editors(self) -> list[QWidget]:
        editors: list[QWidget] = []
        for tabs in self.all_tabs():
            for index in range(tabs.count()):
                editor = tabs.widget(index)
                if self.is_editor_widget(editor):
                    editors.append(editor)
        return editors

    def _tabs_ancestor(self, widget: QWidget | None) -> WorkspaceTabs | None:
        current = widget
        while isinstance(current, QWidget):
            if isinstance(current, WorkspaceTabs) and current.workspace is self:
                return current
            current = current.parentWidget()
        return None

    def _editor_ancestor(self, widget: QWidget | None) -> QWidget | None:
        current = widget
        while isinstance(current, QWidget):
            if self.is_editor_widget(current):
                return current
            current = current.parentWidget()
        return None

    def _on_app_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        focused_editor = self._editor_ancestor(new)
        if not isinstance(focused_editor, QWidget) or not self.is_editor_widget(focused_editor):
            return
        if not isinstance(self._tabs_for_editor(focused_editor), WorkspaceTabs):
            return
        if self._active_editor is focused_editor:
            return
        self._active_editor = focused_editor
        self.notify_state_changed()

    def set_active_editor(self, editor: object) -> None:
        if isinstance(editor, QWidget) and self.is_editor_widget(editor):
            self._active_editor = editor
        self.notify_state_changed()

    def current_editor(self) -> QWidget | None:
        focus_widget = QApplication.focusWidget()
        focused_editor = self._editor_ancestor(focus_widget)
        if isinstance(focused_editor, QWidget) and self.is_editor_widget(focused_editor):
            self._active_editor = focused_editor
            return focused_editor

        focused_tabs = self._tabs_ancestor(focus_widget)
        if isinstance(focused_tabs, WorkspaceTabs):
            focused_current = focused_tabs.currentWidget()
            if isinstance(focused_current, QWidget) and self.is_editor_widget(focused_current):
                self._active_editor = focused_current
                return focused_current

        if isinstance(self._active_editor, QWidget) and self.is_editor_widget(self._active_editor):
            return self._active_editor
        for tabs in self.all_tabs():
            editor = tabs.currentWidget()
            if isinstance(editor, QWidget) and self.is_editor_widget(editor):
                self._active_editor = editor
                return editor
        return None

    def _tabs_for_editor(self, editor: QWidget | None) -> WorkspaceTabs | None:
        if not isinstance(editor, QWidget) or not self.is_editor_widget(editor):
            return None
        for tabs in self.all_tabs():
            if tabs.indexOf(editor) >= 0:
                return tabs
        return None

    def _current_tabs(self) -> WorkspaceTabs:
        focused_tabs = self._tabs_ancestor(QApplication.focusWidget())
        if isinstance(focused_tabs, WorkspaceTabs):
            return focused_tabs

        if isinstance(self._active_editor, QWidget) and self.is_editor_widget(self._active_editor):
            active_tabs = self._tabs_for_editor(self._active_editor)
            if isinstance(active_tabs, WorkspaceTabs):
                return active_tabs

        for tabs in self.all_tabs():
            if tabs.count() > 0:
                return tabs
        return self._primary_tabs

    def _focus_editor(self, editor: QWidget) -> None:
        tabs = self._tabs_for_editor(editor)
        if isinstance(tabs, WorkspaceTabs):
            tabs.setCurrentWidget(editor)
        editor.setFocus()
        self.set_active_editor(editor)

    def add_editor(self, editor: QWidget, *, tabs: WorkspaceTabs | None = None) -> None:
        target_tabs = tabs if isinstance(tabs, WorkspaceTabs) and tabs.workspace is self else self._current_tabs()
        target_tabs.add_editor(editor)
        self.notify_state_changed()

    def confirm_close_editor(self, editor: QWidget, parent: QWidget | None = None) -> bool:
        del parent
        return self.is_editor_widget(editor)

    def close_editor(self, editor: QWidget | None, parent: QWidget | None = None) -> bool:
        if not isinstance(editor, QWidget) or not self.is_editor_widget(editor):
            return True
        if not self.confirm_close_editor(editor, parent):
            return False
        tabs = self._tabs_for_editor(editor)
        if not isinstance(tabs, WorkspaceTabs):
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

    def _split_tabs(self, target_tabs: WorkspaceTabs, orientation: Qt.Orientation, before: bool) -> WorkspaceTabs:
        parent = target_tabs.parentWidget()
        if isinstance(parent, QSplitter) and parent.orientation() == orientation:
            index = parent.indexOf(target_tabs)
            old_sizes = parent.sizes()
            if len(old_sizes) != parent.count():
                old_sizes = [1] * max(1, parent.count())
            target_size = old_sizes[index] if 0 <= index < len(old_sizes) else 0
            if target_size <= 0:
                target_size = 2
            first_half = max(1, target_size // 2)
            second_half = max(1, target_size - first_half)

            new_tabs = self.create_tabs(parent)
            insert_at = index if before else index + 1
            parent.insertWidget(insert_at, new_tabs)

            updated_sizes = list(old_sizes)
            updated_sizes[index] = second_half if before else first_half
            updated_sizes.insert(insert_at, first_half if before else second_half)
            parent.setSizes(updated_sizes)

            target_index = parent.indexOf(target_tabs)
            if target_index >= 0:
                parent.setStretchFactor(target_index, 1)
            new_index = parent.indexOf(new_tabs)
            if new_index >= 0:
                parent.setStretchFactor(new_index, 1)
            return new_tabs

        if not isinstance(parent, QSplitter):
            return target_tabs

        parent_sizes = parent.sizes()
        if len(parent_sizes) != parent.count():
            parent_sizes = [1] * max(1, parent.count())

        replacement = QSplitter(orientation, parent)
        replacement.setChildrenCollapsible(False)
        replacement.setHandleWidth(6)

        index = parent.indexOf(target_tabs)
        target_tabs.setParent(None)
        parent.insertWidget(index, replacement)
        if len(parent_sizes) == parent.count():
            parent.setSizes(parent_sizes)
        if index >= 0:
            parent.setStretchFactor(index, 1)

        new_tabs = self.create_tabs(replacement)
        if before:
            replacement.addWidget(new_tabs)
            replacement.addWidget(target_tabs)
        else:
            replacement.addWidget(target_tabs)
            replacement.addWidget(new_tabs)
        replacement.setStretchFactor(0, 1)
        replacement.setStretchFactor(1, 1)
        replacement.setSizes([1, 1])
        return new_tabs

    @staticmethod
    def _equalize_splitter_pair(splitter: QSplitter, first_index: int, second_index: int) -> None:
        if first_index < 0 or second_index < 0 or first_index == second_index:
            return
        sizes = splitter.sizes()
        if len(sizes) != splitter.count():
            return
        pair_total = max(2, int(sizes[first_index]) + int(sizes[second_index]))
        first_size = max(1, pair_total // 2)
        second_size = max(1, pair_total - first_size)
        sizes[first_index] = first_size
        sizes[second_index] = second_size
        splitter.setSizes(sizes)

    def _rebalance_drop_split(self, source_tabs: WorkspaceTabs, destination: WorkspaceTabs, zone: DropZone) -> None:
        if zone == DropZone.CENTER:
            return
        splitter = destination.parentWidget()
        if not isinstance(splitter, QSplitter):
            return
        source_index = splitter.indexOf(source_tabs)
        destination_index = splitter.indexOf(destination)
        if source_index < 0 or destination_index < 0:
            return
        splitter.setStretchFactor(source_index, 1)
        splitter.setStretchFactor(destination_index, 1)
        self._equalize_splitter_pair(splitter, source_index, destination_index)

    def find_editor(self, editor_id: str) -> tuple[QWidget | None, WorkspaceTabs | None]:
        wanted = str(editor_id or "").strip()
        if not wanted:
            return None, None
        for tabs in self.all_tabs():
            for index in range(tabs.count()):
                editor = tabs.widget(index)
                if not self.is_editor_widget(editor):
                    continue
                if _editor_id(editor) == wanted:
                    return editor, tabs
        return None, None

    def move_editor(self, editor_id: str, target_tabs: WorkspaceTabs, zone: DropZone) -> None:
        editor, source_tabs = self.find_editor(editor_id)
        if not isinstance(editor, QWidget) or not isinstance(source_tabs, WorkspaceTabs):
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
        self._rebalance_drop_split(source_tabs, destination, zone)
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
