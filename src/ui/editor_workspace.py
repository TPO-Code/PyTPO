import os
import json
import uuid
import weakref
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Qt, QMimeData, QPoint, QRect, QTimer
from PySide6.QtGui import QDrag, QFontDatabase, QBrush, QColor, QPen, QPainter, QTextDocument, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextDocumentLayout,
    #QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.widgets.code_editor import CodeEditor


class DropZone(Enum):
    NONE = 0
    CENTER = 1
    LEFT = 2
    RIGHT = 3
    TOP = 4
    BOTTOM = 5

MIME_EDITOR_TAB = "application/x-pytpo-editor-tab-id"


@dataclass
class DocumentRecord:
    key: str
    document: QTextDocument
    file_path: str | None = None
    views: weakref.WeakSet = field(default_factory=weakref.WeakSet)


def _encode_editor_drag_payload(editor_id: str, file_path: str | None = None) -> bytes:
    payload: dict[str, str] = {"editor_id": str(editor_id or "")}
    if isinstance(file_path, str) and file_path.strip():
        payload["file_path"] = file_path
    return json.dumps(payload).encode("utf-8")


def _decode_editor_drag_payload(raw: bytes) -> tuple[str, str | None]:
    if not raw:
        return "", None
    text = bytes(raw).decode("utf-8", errors="ignore").strip()
    if not text:
        return "", None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            editor_id = str(obj.get("editor_id") or "").strip()
            file_path_raw = obj.get("file_path")
            file_path = str(file_path_raw).strip() if isinstance(file_path_raw, str) and file_path_raw.strip() else None
            return editor_id, file_path
    except Exception:
        pass
    # Backward compatibility: old payload was raw editor_id.
    return text, None

class DropOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._zone = DropZone.NONE
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.hide()

    def set_zone(self, zone: DropZone):
        if self._zone != zone:
            self._zone = zone
            self.update()

    def zone(self) -> DropZone:
        return self._zone

    def paintEvent(self, _event):
        if self._zone == DropZone.NONE:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # base dim
        p.fillRect(self.rect(), QColor(20, 20, 20, 60))

        r = self.rect()
        target = self._zone_rect(r, self._zone)

        p.setPen(QPen(QColor(120, 180, 255, 220), 2))
        p.setBrush(QBrush(QColor(120, 180, 255, 70)))
        p.drawRoundedRect(target, 8, 8)

    def _zone_rect(self, r: QRect, zone: DropZone) -> QRect:
        w = r.width()
        h = r.height()
        if zone == DropZone.CENTER:
            return QRect(int(w * 0.25), int(h * 0.2), int(w * 0.5), int(h * 0.6))
        if zone == DropZone.LEFT:
            return QRect(0, 0, int(w * 0.45), h)
        if zone == DropZone.RIGHT:
            return QRect(int(w * 0.55), 0, int(w * 0.45), h)
        if zone == DropZone.TOP:
            return QRect(0, 0, w, int(h * 0.45))
        if zone == DropZone.BOTTOM:
            return QRect(0, int(h * 0.55), w, int(h * 0.45))
        return QRect()

class EditorWidget(CodeEditor):
    FONT_FALLBACKS = (
        "Cascadia Code",
        "Consolas",
        "JetBrains Mono",
        "Fira Code",
        "Courier New",
        "Monospace",
    )

    def __init__(
            self,
            file_path: str | None = None,
            font_size: int = 10,
            font_family: str | None = None,
            parent=None,
            workspace: "EditorWorkspace | None" = None,
    ):
        super().__init__(parent)
        self._workspace = workspace
        self._doc_record: DocumentRecord | None = None
        self._file_path_local: str | None = None
        self.editor_id = str(uuid.uuid4())

        self.setLineWrapMode(CodeEditor.LineWrapMode.NoWrap)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        resolved_family = self._resolve_font_family(font_family)
        self.set_editor_font_preferences(family=resolved_family, point_size=font_size)

        if file_path:
            self.load_file(file_path)

    @classmethod
    def _resolve_font_family(cls, preferred: str | None) -> str:
        text = str(preferred or "").strip()
        families = set(QFontDatabase.families())
        if text and text in families:
            return text
        for candidate in cls.FONT_FALLBACKS:
            if candidate in families:
                return candidate
        return text or "Monospace"

    @property
    def file_path(self) -> str | None:
        if self._doc_record is not None:
            return self._doc_record.file_path
        return self._file_path_local

    @file_path.setter
    def file_path(self, path: str | None):
        clean = str(path) if path else None
        self._file_path_local = clean
        if self._doc_record is not None:
            self._doc_record.file_path = clean
            if self._workspace is not None:
                self._workspace.sync_document_record_key(self._doc_record)
        self.set_file_path(clean)

    @property
    def doc_key(self) -> str:
        if self._doc_record is not None:
            return self._doc_record.key
        if self.file_path:
            try:
                return str(Path(self.file_path).resolve())
            except Exception:
                return str(self.file_path)
        return f"__editor__/{self.editor_id}"

    def document_record(self) -> DocumentRecord | None:
        return self._doc_record

    def attach_document_record(self, record: DocumentRecord, *, adopt_current_document: bool = False):
        if not isinstance(record, DocumentRecord):
            return
        if self._doc_record is record:
            return

        if self._doc_record is not None:
            try:
                self._doc_record.views.discard(self)
            except Exception:
                pass

        self._doc_record = record
        record.views.add(self)
        self._file_path_local = record.file_path

        if adopt_current_document:
            record.document = self.document()
            if self._workspace is not None:
                self._workspace._adopt_record_document(record, fallback_editor=self)
        elif self.document() is not record.document:
            if self._workspace is not None:
                self._workspace._adopt_record_document(record, fallback_editor=self)
            self.setDocument(record.document)

        self.set_file_path(record.file_path)

    def detach_document_record(self):
        if self._doc_record is None:
            return
        try:
            self._doc_record.views.discard(self)
        except Exception:
            pass
        self._doc_record = None

    def display_name(self) -> str:
        return os.path.basename(self.file_path) if self.file_path else "File"

    def load_file(self, path: str):
        if not os.path.exists(path):
            QMessageBox.warning(self, "Open Error", f"File does not exist:\n{path}")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.setPlainText(f.read())
            self.file_path = path
            self.document().setModified(False)
            self.set_file_path(path)
        except Exception as e:
            QMessageBox.warning(self, "Open Error", f"Could not read file:\n{e}")

    def save_file(self) -> bool:
        if not self.file_path:
            return False
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(self.toPlainText())
            self.document().setModified(False)
            self.set_file_path(self.file_path)
            return True
        except Exception as e:
            QMessageBox.warning(self, "Save Error", f"Could not save file:\n{e}")
            return False


class DraggableTabBar(QTabBar):
    def __init__(self, tabs_widget: "EditorTabs", parent=None):
        super().__init__(parent)
        self.tabs_widget = tabs_widget
        self._drag_start_pos = QPoint()
        self.setMovable(True)
        self.setAcceptDrops(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(event)
            return

        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        idx = self.tabAt(self._drag_start_pos)
        if idx < 0:
            super().mouseMoveEvent(event)
            return

        ed = self.tabs_widget.widget(idx)
        if not isinstance(ed, EditorWidget):
            super().mouseMoveEvent(event)
            return

        transferable_path = self.tabs_widget.workspace.prepare_editor_for_cross_instance_transfer(ed)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME_EDITOR_TAB, _encode_editor_drag_payload(ed.editor_id, transferable_path))
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def mouseDoubleClickEvent(self, event):
        idx = self.tabAt(event.position().toPoint())
        if idx >= 0:
            self.tabs_widget.tear_out_index(idx)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # Accept drops directly on tab bar
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_EDITOR_TAB):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME_EDITOR_TAB):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(MIME_EDITOR_TAB):
            super().dropEvent(event)
            return
        editor_id, file_path = _decode_editor_drag_payload(bytes(event.mimeData().data(MIME_EDITOR_TAB)))
        # tabbar drop is effectively center/tab insertion
        p_in_tabs = self.tabs_widget.mapFromGlobal(event.globalPosition().toPoint())
        self.tabs_widget._handle_drop_with_zone(editor_id, DropZone.CENTER, p_in_tabs, file_path=file_path)
        event.acceptProposedAction()

    def contextMenuEvent(self, event):
        idx = self.tabAt(event.pos())
        self.tabs_widget.show_tab_context_menu(idx, event.globalPos())
        event.accept()


class EditorTabs(QTabWidget):
    def __init__(self, workspace: "EditorWorkspace", owner_window: QMainWindow | None = None, parent=None):
        super().__init__(parent)

        self._overlay = DropOverlay(self)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()

        self.workspace = workspace
        self.owner_window = owner_window

        self._tabbar = DraggableTabBar(self, self)
        self.setTabBar(self._tabbar)

        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)

        # Keep both on to be safe across WM/Qt combos
        self.setAcceptDrops(True)
        self.tabBar().setAcceptDrops(True)

        self.tabCloseRequested.connect(self._on_tab_close_requested)
        self.currentChanged.connect(self._on_current_changed)

    def _compute_zone(self, pos_widget):
        # pos_widget is QPoint in EditorTabs coords
        r = self.rect()
        if not r.contains(pos_widget):
            return DropZone.NONE

        # margins that define side-zones
        mx = max(48, int(r.width() * 0.22))
        my = max(38, int(r.height() * 0.22))

        x = pos_widget.x()
        y = pos_widget.y()

        if x < mx:
            return DropZone.LEFT
        if x > r.width() - mx:
            return DropZone.RIGHT
        if y < my:
            return DropZone.TOP
        if y > r.height() - my:
            return DropZone.BOTTOM
        return DropZone.CENTER

    def _show_overlay(self, zone: DropZone):
        self._overlay.set_zone(zone)
        if zone == DropZone.NONE:
            self._overlay.hide()
        else:
            self._overlay.show()
            self._overlay.raise_()

    def _hide_overlay(self):
        self._overlay.set_zone(DropZone.NONE)
        self._overlay.hide()


    def _handle_external_drop(self, editor_id: str, pos_in_tabbar: QPoint):
        ed, src_tabs, src_idx = self.workspace.find_editor_by_id(editor_id)
        if ed is None or src_tabs is None or src_idx < 0:
            return

        # Remove from source
        src_tabs.removeTab(src_idx)

        # Insert into this tabs at pointer position
        target_idx = self.tabBar().tabAt(pos_in_tabbar)
        if target_idx < 0 or target_idx > self.count():
            self.addTab(ed, "")
            new_idx = self.indexOf(ed)
        else:
            self.insertTab(target_idx, ed, "")
            new_idx = target_idx

        self._refresh_tab_title(ed)
        self.setCurrentIndex(new_idx)
        ed.setFocus()

        self.workspace.request_cleanup_empty_panes()

        # auto-close empty tearout source
        if src_tabs.count() == 0 and isinstance(src_tabs.owner_window, EditorTearOutWindow):
            src_tabs.owner_window.close()


    def add_editor(self, ed: EditorWidget):
        idx = self.addTab(ed, ed.display_name())
        self.setCurrentIndex(idx)
        ed.setFocus()
        # Use a QObject-bound slot to avoid stale lambda/editor references after tab/view teardown.
        ed.document().modificationChanged.connect(self._on_document_modification_changed)
        self._refresh_tab_title(ed)

    def _refresh_tab_title(self, ed: EditorWidget):
        idx = self.indexOf(ed)
        if idx < 0:
            return
        try:
            dirty = "*" if ed.document().isModified() else ""
        except RuntimeError:
            return
        pin_prefix = "[pin] " if self._is_tab_pinned(ed) else ""
        self.setTabText(idx, f"{pin_prefix}{ed.display_name()}{dirty}")

    def _on_document_modification_changed(self, _modified: bool):
        # Refresh only editors currently hosted by this tab widget.
        for i in range(self.count()):
            ed = self.widget(i)
            if isinstance(ed, EditorWidget):
                self._refresh_tab_title(ed)

    def _on_current_changed(self, _index: int):
        ed = self.currentWidget()
        if isinstance(ed, EditorWidget):
            ed.setFocus()

    @staticmethod
    def _is_tab_pinned(ed: EditorWidget) -> bool:
        return bool(getattr(ed, "_tab_pinned", False))

    def _set_tab_pinned(self, ed: EditorWidget, pinned: bool) -> None:
        try:
            setattr(ed, "_tab_pinned", bool(pinned))
        except Exception:
            return
        self._refresh_tab_title(ed)

    def _unpin_all_tabs(self) -> None:
        for i in range(self.count()):
            ed = self.widget(i)
            if not isinstance(ed, EditorWidget):
                continue
            if not self._is_tab_pinned(ed):
                continue
            self._set_tab_pinned(ed, False)

    def _confirm_close_editor(self, ed: EditorWidget) -> bool:
        if not ed.document().isModified():
            return True
        if not self.workspace.is_last_view_for_document(ed):
            return True

        ans = QMessageBox.question(
            self,
            "Unsaved Changes",
            f"Save changes to '{ed.display_name()}'?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
            )
        if ans == QMessageBox.Cancel:
            return False
        if ans == QMessageBox.Yes:
            if not ed.save_file():
                return False
        return True

    def _on_tab_close_requested(self, index: int):
        ed = self.widget(index)
        if not isinstance(ed, EditorWidget):
            return
        if self._is_tab_pinned(ed):
            return
        if not self._confirm_close_editor(ed):
            return

        doc_key = ed.doc_key
        self.removeTab(index)
        ed.hide()
        self.workspace.release_document_view(ed, doc_key)
        ed.deleteLater()
        self.workspace.request_cleanup_empty_panes()

        # If this tabs belongs to tearout and became empty, close tearout window
        if self.count() == 0 and isinstance(self.owner_window, EditorTearOutWindow):
            self.owner_window.close()

    def _close_tab_indices(self, indices: list[int]) -> bool:
        closed_any = False
        for idx in sorted({int(i) for i in indices}, reverse=True):
            if idx < 0 or idx >= self.count():
                continue
            ed = self.widget(idx)
            if not isinstance(ed, EditorWidget):
                continue
            if self._is_tab_pinned(ed):
                continue
            before = self.count()
            self._on_tab_close_requested(idx)
            if self.count() < before:
                closed_any = True
        return closed_any

    def _copy_tab_path(self, index: int) -> None:
        if index < 0 or index >= self.count():
            return
        ed = self.widget(index)
        if not isinstance(ed, EditorWidget):
            return
        path = str(getattr(ed, "file_path", "") or "").strip()
        if not path:
            return
        QApplication.clipboard().setText(path)

    def _copy_tab_reference(self, index: int) -> None:
        if index < 0 or index >= self.count():
            return
        ed = self.widget(index)
        if not isinstance(ed, EditorWidget):
            return
        path = str(getattr(ed, "file_path", "") or "").strip()
        if not path:
            return
        cur = ed.textCursor()
        line = int(cur.blockNumber()) + 1
        col = int(cur.positionInBlock()) + 1
        QApplication.clipboard().setText(f"{path}:{line}:{col}")

    def _split_from_tab_index(self, index: int, *, horizontal: bool) -> None:
        if index < 0 or index >= self.count():
            return
        ed = self.widget(index)
        if not isinstance(ed, EditorWidget):
            return
        self.setCurrentIndex(index)
        ed.setFocus()

        # Prefer IDE-level split actions to keep all follow-up hooks in sync.
        host = self.workspace.parentWidget()
        while host is not None:
            method_name = "split_editor_down" if horizontal else "split_editor_right"
            method = getattr(host, method_name, None)
            if callable(method):
                method()
                return
            host = host.parentWidget()

        if horizontal:
            self.workspace.split_editor_down()
        else:
            self.workspace.split_editor_right()

    def show_tab_context_menu(self, index: int, global_pos) -> None:
        menu = QMenu(self)

        has_tab = 0 <= int(index) < self.count()
        ed = self.widget(index) if has_tab else None
        is_editor = isinstance(ed, EditorWidget)
        is_pinned = self._is_tab_pinned(ed) if is_editor else False

        act_close = menu.addAction("Close")
        act_close.setEnabled(is_editor and not is_pinned)
        act_close.triggered.connect(lambda _checked=False, idx=int(index): self._on_tab_close_requested(idx))

        act_close_others = menu.addAction("Close Others")
        act_close_others.setEnabled(is_editor and self.count() > 1)
        act_close_others.triggered.connect(
            lambda _checked=False, keep=int(index): self._close_tab_indices(
                [i for i in range(self.count()) if i != keep]
            )
        )

        act_close_all = menu.addAction("Close All")
        act_close_all.setEnabled(self.count() > 0)
        act_close_all.triggered.connect(
            lambda _checked=False: self._close_tab_indices(list(range(self.count())))
        )

        menu.addSeparator()

        act_split_h = menu.addAction("Split Horizontal")
        act_split_h.setEnabled(is_editor)
        act_split_h.triggered.connect(
            lambda _checked=False, idx=int(index): self._split_from_tab_index(idx, horizontal=True)
        )

        act_split_v = menu.addAction("Split Vertical")
        act_split_v.setEnabled(is_editor)
        act_split_v.triggered.connect(
            lambda _checked=False, idx=int(index): self._split_from_tab_index(idx, horizontal=False)
        )

        menu.addSeparator()

        act_pin = menu.addAction("Pin Tab")
        act_pin.setCheckable(True)
        act_pin.setChecked(is_pinned)
        act_pin.setEnabled(is_editor)
        act_pin.triggered.connect(
            lambda checked, editor=ed: self._set_tab_pinned(editor, bool(checked))
            if isinstance(editor, EditorWidget)
            else None
        )

        act_unpin_all = menu.addAction("Unpin All Tabs")
        has_any_pinned = any(
            isinstance(self.widget(i), EditorWidget) and self._is_tab_pinned(self.widget(i))
            for i in range(self.count())
        )
        act_unpin_all.setEnabled(has_any_pinned)
        act_unpin_all.triggered.connect(lambda _checked=False: self._unpin_all_tabs())

        menu.addSeparator()

        has_path = is_editor and bool(str(getattr(ed, "file_path", "") or "").strip())
        act_copy_path = menu.addAction("Copy Path")
        act_copy_path.setEnabled(has_path)
        act_copy_path.triggered.connect(
            lambda _checked=False, idx=int(index): self._copy_tab_path(idx)
        )

        act_copy_ref = menu.addAction("Copy Reference")
        act_copy_ref.setEnabled(has_path)
        act_copy_ref.triggered.connect(
            lambda _checked=False, idx=int(index): self._copy_tab_reference(idx)
        )

        menu.exec(global_pos)

    def tear_out_index(self, index: int):
        ed = self.widget(index)
        if not isinstance(ed, EditorWidget):
            return
        self.removeTab(index)
        self.workspace.create_tearout_window_with_editor(ed)
        self.workspace.request_cleanup_empty_panes()

        if self.count() == 0 and isinstance(self.owner_window, EditorTearOutWindow):
            self.owner_window.close()

    # ---- Drag/drop between panes/windows ----

    def _handle_drop_with_zone(self, editor_id: str, zone: DropZone, drop_pos_widget, *, file_path: str | None = None):
        ed, src_tabs, src_idx = self.workspace.find_editor_by_id(editor_id)
        if ed is None or src_tabs is None or src_idx < 0:
            if file_path:
                self.workspace.open_editor(os.path.basename(file_path), file_path)
                self.workspace.request_cleanup_empty_panes()
            return

        # Remove from source first
        src_tabs.removeTab(src_idx)

        if zone in (DropZone.LEFT, DropZone.RIGHT, DropZone.TOP, DropZone.BOTTOM):
            # create a sibling pane by splitting THIS pane
            orientation = Qt.Horizontal if zone in (DropZone.LEFT, DropZone.RIGHT) else Qt.Vertical
            new_tabs = self.workspace.split_tabs_for_drop(self, orientation, before=(zone in (DropZone.LEFT, DropZone.TOP)))
            new_tabs.add_editor(ed)
            new_tabs.setCurrentWidget(ed)
            ed.setFocus()
        else:
            # center drop = tab into this pane
            p_tabbar = self.tabBar().mapFrom(self, drop_pos_widget)
            target_idx = self.tabBar().tabAt(p_tabbar)
            if target_idx < 0 or target_idx > self.count():
                self.addTab(ed, "")
                new_idx = self.indexOf(ed)
            else:
                self.insertTab(target_idx, ed, "")
                new_idx = target_idx
            self._refresh_tab_title(ed)
            self.setCurrentIndex(new_idx)
            ed.setFocus()

        self.workspace.request_cleanup_empty_panes()

        if src_tabs.count() == 0 and isinstance(src_tabs.owner_window, EditorTearOutWindow):
            src_tabs.owner_window.close()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_EDITOR_TAB):
            zone = self._compute_zone(event.position().toPoint())
            self._show_overlay(zone)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME_EDITOR_TAB):
            zone = self._compute_zone(event.position().toPoint())
            self._show_overlay(zone)
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._hide_overlay()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(MIME_EDITOR_TAB):
            self._hide_overlay()
            super().dropEvent(event)
            return

        editor_id, file_path = _decode_editor_drag_payload(bytes(event.mimeData().data(MIME_EDITOR_TAB)))
        zone = self._overlay.zone()
        self._hide_overlay()

        self._handle_drop_with_zone(editor_id, zone, event.position().toPoint(), file_path=file_path)
        event.acceptProposedAction()


class EditorTearOutWindow(QMainWindow):
    def __init__(self, workspace: "EditorWorkspace", parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(900, 600)

        self.tabs = EditorTabs(workspace=workspace, owner_window=self, parent=self)
        self.setCentralWidget(self.tabs)

    def closeEvent(self, event):
        # move remaining tabs back to primary pane
        remaining = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, EditorWidget):
                remaining.append(w)

        for ed in remaining:
            idx = self.tabs.indexOf(ed)
            if idx >= 0:
                self.tabs.removeTab(idx)
            self.workspace.move_editor_to_primary_tabs(ed)

        event.accept()


class EditorWorkspace(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._tearouts: list[EditorTearOutWindow] = []
        self._documents: dict[str, DocumentRecord] = {}
        self._cleanup_pending = False
        self._default_editor_font_size = 10
        self._default_editor_font_family: str | None = None

        self.root_splitter = QSplitter(Qt.Horizontal, self)
        self.root_splitter.setChildrenCollapsible(False)
        self.root_splitter.setHandleWidth(6)

        first_tabs = EditorTabs(workspace=self, owner_window=None, parent=self.root_splitter)
        self.root_splitter.addWidget(first_tabs)
        self.root_splitter.setStretchFactor(0, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.root_splitter)

    def set_editor_font_defaults(
            self,
            *,
            font_size: int | None = None,
            font_family: str | None = None,
    ) -> None:
        if font_size is not None:
            try:
                self._default_editor_font_size = max(1, int(font_size))
            except Exception:
                self._default_editor_font_size = 10
        if font_family is not None:
            text = str(font_family or "").strip()
            self._default_editor_font_family = text or None

    def _effective_font_size(self, font_size: int | None) -> int:
        if font_size is None:
            return int(self._default_editor_font_size)
        try:
            return max(1, int(font_size))
        except Exception:
            return int(self._default_editor_font_size)

    def _effective_font_family(self, font_family: str | None) -> str | None:
        if isinstance(font_family, str) and font_family.strip():
            return font_family.strip()
        return self._default_editor_font_family

    # -------- document registry --------

    def _canonical_path(self, path: str) -> str:
        try:
            return str(Path(path).resolve())
        except Exception:
            return os.path.abspath(path)

    def _doc_key_for(self, file_path: str | None) -> str:
        if file_path:
            return self._canonical_path(file_path)
        return "__editor__/missing"

    @staticmethod
    def _is_document_alive(document: QTextDocument | None) -> bool:
        if not isinstance(document, QTextDocument):
            return False
        try:
            _ = document.isModified()
        except RuntimeError:
            return False
        return True

    def _adopt_record_document(self, record: DocumentRecord, fallback_editor: EditorWidget | None = None) -> QTextDocument:
        """
        Ensure the record has a live QTextDocument owned by the workspace.
        This prevents editor-view deletion from destroying shared documents.
        """
        doc = record.document
        if self._is_document_alive(doc):
            if doc.parent() is not self:
                doc.setParent(self)
            return doc

        text = ""
        modified = False
        if isinstance(fallback_editor, EditorWidget):
            try:
                text = fallback_editor.toPlainText()
                modified = bool(fallback_editor.document().isModified())
            except RuntimeError:
                text = ""
                modified = False

        if not text:
            for view in list(record.views):
                if not isinstance(view, EditorWidget):
                    continue
                try:
                    text = view.toPlainText()
                    modified = bool(view.document().isModified())
                    break
                except RuntimeError:
                    continue

        new_doc = QTextDocument(self)
        new_doc.setPlainText(text)
        new_doc.setModified(modified)
        record.document = new_doc
        return new_doc

    def _ensure_record_for_editor(self, ed: EditorWidget) -> DocumentRecord:
        existing = ed.document_record()
        if existing is not None:
            self._adopt_record_document(existing, fallback_editor=ed)
            return existing

        key = self._doc_key_for(ed.file_path) if ed.file_path else f"__editor__/{ed.editor_id}"
        record = self._documents.get(key)
        adopt_current = False
        if record is None:
            record = DocumentRecord(
                key=key,
                document=ed.document(),
                file_path=ed.file_path,
            )
            self._documents[key] = record
            self._adopt_record_document(record, fallback_editor=ed)
            adopt_current = True
        else:
            self._adopt_record_document(record, fallback_editor=ed)
        ed.attach_document_record(record, adopt_current_document=adopt_current)
        return record

    def sync_document_record_key(self, record: DocumentRecord):
        if not isinstance(record, DocumentRecord):
            return
        desired = self._doc_key_for(record.file_path) if record.file_path else record.key
        if desired == record.key:
            self._documents[record.key] = record
            return
        conflict = self._documents.get(desired)
        if conflict is not None and conflict is not record:
            return
        if self._documents.get(record.key) is record:
            self._documents.pop(record.key, None)
        record.key = desired
        self._documents[desired] = record

    def document_key_for_editor(self, ed: EditorWidget) -> str:
        record = self._ensure_record_for_editor(ed)
        self.sync_document_record_key(record)
        return record.key

    def is_last_view_for_document(self, ed: EditorWidget) -> bool:
        record = self._ensure_record_for_editor(ed)
        count = 0
        for view in list(record.views):
            if isinstance(view, EditorWidget):
                count += 1
        return count <= 1

    def _prune_orphan_document_records(self):
        stale_keys: list[str] = []
        for key, record in self._documents.items():
            has_live_view = any(isinstance(view, EditorWidget) for view in list(record.views))
            if not has_live_view:
                stale_keys.append(key)
        for key in stale_keys:
            self._documents.pop(key, None)

    def release_document_view(self, ed: EditorWidget, _doc_key: str | None = None):
        record = ed.document_record()
        if record is None:
            self._disarm_editor_before_delete(ed)
            self._prune_orphan_document_records()
            return
        try:
            record.views.discard(ed)
        except Exception:
            pass
        if not any(isinstance(view, EditorWidget) for view in list(record.views)):
            if self._documents.get(record.key) is record:
                self._documents.pop(record.key, None)
        ed.detach_document_record()
        self._disarm_editor_before_delete(ed)

    @staticmethod
    def _disarm_editor_before_delete(ed: EditorWidget):
        """
        Detach an editor from shared document state before deletion.
        This avoids Qt teardown races when a view closes while another
        view still references the same QTextDocument.
        """
        if not isinstance(ed, EditorWidget):
            return
        try:
            ed.blockSignals(True)
        except RuntimeError:
            return
        try:
            ed.hide_completion_popup()
        except Exception:
            pass
        try:
            scratch = QTextDocument(ed)
            scratch.setDocumentLayout(QPlainTextDocumentLayout(scratch))
            ed.setDocument(scratch)
            ed.set_file_path(None)
        except RuntimeError:
            return

    def _new_view_for_record(
            self,
            record: DocumentRecord,
            font_size: int | None = None,
            font_family: str | None = None,
            source_editor: EditorWidget | None = None,
    ) -> EditorWidget:
        self._adopt_record_document(record, fallback_editor=source_editor)
        ed = EditorWidget(
            None,
            font_size=self._effective_font_size(font_size),
            font_family=self._effective_font_family(font_family),
            workspace=self,
        )
        ed.attach_document_record(record)

        if isinstance(source_editor, EditorWidget):
            src_cursor = source_editor.textCursor()
            dst_cursor = ed.textCursor()
            dst_cursor.setPosition(src_cursor.selectionStart())
            dst_cursor.setPosition(src_cursor.selectionEnd(), QTextCursor.KeepAnchor)
            ed.setTextCursor(dst_cursor)
            ed.verticalScrollBar().setValue(source_editor.verticalScrollBar().value())
            ed.horizontalScrollBar().setValue(source_editor.horizontalScrollBar().value())
        return ed

    def _find_editor_for_doc_key(self, doc_key: str, preferred_tabs: EditorTabs | None = None) -> EditorWidget | None:
        if preferred_tabs is not None:
            for i in range(preferred_tabs.count()):
                w = preferred_tabs.widget(i)
                if isinstance(w, EditorWidget) and self.document_key_for_editor(w) == doc_key:
                    return w
        for ed in self.all_editors():
            if self.document_key_for_editor(ed) == doc_key:
                return ed
        return None

    def split_tabs_for_drop(self, target_tabs: EditorTabs, orientation: Qt.Orientation, before: bool) -> EditorTabs:
        parent = target_tabs.parentWidget()

        if isinstance(parent, QSplitter) and parent.orientation() == orientation:
            idx = parent.indexOf(target_tabs)
            insert_idx = idx if before else idx + 1
            new_tabs = EditorTabs(workspace=self, owner_window=None, parent=parent)
            parent.insertWidget(insert_idx, new_tabs)
            parent.setStretchFactor(insert_idx, 1)
            return new_tabs

        # wrap target in a new splitter of requested orientation
        new_splitter = QSplitter(orientation)
        new_splitter.setChildrenCollapsible(False)
        new_splitter.setHandleWidth(6)

        new_tabs = EditorTabs(workspace=self, owner_window=None, parent=new_splitter)

        if isinstance(parent, QSplitter):
            idx = parent.indexOf(target_tabs)
            target_tabs.setParent(None)
            parent.insertWidget(idx, new_splitter)
        else:
            old = self.root_splitter
            old.setParent(None)
            self.root_splitter = new_splitter
            self.layout().addWidget(self.root_splitter)

        if before:
            new_splitter.addWidget(new_tabs)
            new_splitter.addWidget(target_tabs)
        else:
            new_splitter.addWidget(target_tabs)
            new_splitter.addWidget(new_tabs)

        new_splitter.setStretchFactor(0, 1)
        new_splitter.setStretchFactor(1, 1)
        return new_tabs

    # compatibility no-ops
    def editor_docks(self):
        return []

    def dock_for_editor(self, _editor):
        return None

    def refresh_dock_title(self, _editor):
        pass

    # -------- root validity guard --------
    def _ensure_valid_root_splitter(self):
        try:
            _ = self.root_splitter.count()
            return
        except RuntimeError:
            pass

        self.root_splitter = QSplitter(Qt.Horizontal, self)
        self.root_splitter.setChildrenCollapsible(False)
        self.root_splitter.setHandleWidth(6)

        # clear layout and re-add
        lay = self.layout()
        if lay is None:
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
        else:
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
        lay.addWidget(self.root_splitter)

        self.root_splitter.addWidget(EditorTabs(workspace=self, owner_window=None, parent=self.root_splitter))

    # -------- traversal --------

    def _all_tabs_in_widget(self, w: QWidget) -> list[EditorTabs]:
        out = []
        stack = [w]
        while stack:
            obj = stack.pop()
            if isinstance(obj, EditorTabs):
                out.append(obj)
            elif isinstance(obj, QSplitter):
                try:
                    n = obj.count()
                except RuntimeError:
                    continue
                for i in range(n):
                    child = obj.widget(i)
                    if child is not None:
                        stack.append(child)
        return out

    def all_tabs(self) -> list[EditorTabs]:
        self._ensure_valid_root_splitter()
        tabs = self._all_tabs_in_widget(self.root_splitter)

        for tw in list(self._tearouts):
            if tw is None:
                continue
            try:
                cw = tw.centralWidget()
            except RuntimeError:
                continue
            if cw is not None:
                tabs.extend(self._all_tabs_in_widget(cw))
        return tabs

    def all_editors(self) -> list[EditorWidget]:
        result = []
        for tabs in self.all_tabs():
            for i in range(tabs.count()):
                w = tabs.widget(i)
                if isinstance(w, EditorWidget):
                    result.append(w)
        return result

    def _main_tabs(self) -> list[EditorTabs]:
        self._ensure_valid_root_splitter()
        return self._all_tabs_in_widget(self.root_splitter)

    def _ensure_one_main_tabs(self) -> EditorTabs:
        tabs = self._main_tabs()
        if tabs:
            return tabs[0]
        t = EditorTabs(workspace=self, owner_window=None, parent=self.root_splitter)
        self.root_splitter.addWidget(t)
        return t

    def _current_tabs(self) -> EditorTabs | None:
        fw = QApplication.focusWidget()
        w = fw
        while w is not None:
            if isinstance(w, EditorTabs):
                return w
            w = w.parentWidget()

        for t in self._main_tabs():
            if t.count() > 0:
                return t
        mts = self._main_tabs()
        return mts[0] if mts else None

    def active_editor(self):
        fw = QApplication.focusWidget()
        w = fw
        while w is not None:
            if isinstance(w, EditorWidget):
                return w
            w = w.parentWidget()

        tabs = self._current_tabs()
        if tabs:
            cw = tabs.currentWidget()
            if isinstance(cw, EditorWidget):
                return cw
        return None

    # -------- find/open --------

    def find_editor_by_id(self, editor_id: str):
        for tabs in self.all_tabs():
            for i in range(tabs.count()):
                w = tabs.widget(i)
                if isinstance(w, EditorWidget) and w.editor_id == editor_id:
                    return w, tabs, i
        return None, None, -1

    def _find_editor_for_path(self, path: str):
        target = self._canonical_path(path)
        preferred = self._current_tabs()
        if preferred is not None:
            for i in range(preferred.count()):
                w = preferred.widget(i)
                if isinstance(w, EditorWidget) and w.file_path and self._canonical_path(w.file_path) == target:
                    return w
        for ed in self.all_editors():
            if ed.file_path and self._canonical_path(ed.file_path) == target:
                return ed
        return None

    def _focus_editor_widget(self, ed: EditorWidget):
        for tabs in self.all_tabs():
            idx = tabs.indexOf(ed)
            if idx >= 0:
                tabs.setCurrentIndex(idx)
                ed.setFocus()
                return

    def prepare_editor_for_cross_instance_transfer(self, ed: EditorWidget) -> str | None:
        if not isinstance(ed, EditorWidget):
            return None
        file_path = getattr(ed, "file_path", None)
        if not isinstance(file_path, str) or not file_path.strip():
            return None
        if ed.document().isModified():
            if not ed.save_file():
                return None
        try:
            return self._canonical_path(file_path)
        except Exception:
            return file_path

    def open_editor(
            self,
            title: str,
            path: str,
            font_size: int | None = None,
            font_family: str | None = None,
            *,
            force_new_view: bool = False,
    ):
        self._ensure_valid_root_splitter()
        tabs = self._current_tabs() or self._ensure_one_main_tabs()

        cpath = self._canonical_path(path)
        if not force_new_view:
            existing = self._find_editor_for_path(cpath)
            if existing:
                self._focus_editor_widget(existing)
                return existing
        record = self._documents.get(cpath)
        if record is None:
            ed = EditorWidget(
                None,
                font_size=self._effective_font_size(font_size),
                font_family=self._effective_font_family(font_family),
                workspace=self,
            )
            if os.path.exists(cpath):
                ed.load_file(cpath)
            else:
                ed.file_path = cpath
            record = DocumentRecord(
                key=cpath,
                document=ed.document(),
                file_path=cpath,
            )
            self._documents[cpath] = record
            self._adopt_record_document(record, fallback_editor=ed)
            ed.attach_document_record(record, adopt_current_document=True)
        else:
            self._adopt_record_document(record)
            ed = self._new_view_for_record(record, font_size=font_size, font_family=font_family)

        tabs.add_editor(ed)
        return ed

    def ensure_editor_available(self, font_size: int = 10):
        _ = font_size
        return

    # -------- split --------

    def _split_tabs(self, tabs: EditorTabs, orientation: Qt.Orientation) -> EditorTabs:
        parent = tabs.parentWidget()

        if isinstance(parent, QSplitter) and parent.orientation() == orientation:
            new_tabs = EditorTabs(workspace=self, owner_window=None, parent=parent)
            idx = parent.indexOf(tabs)
            parent.insertWidget(idx + 1, new_tabs)
            parent.setStretchFactor(idx, 1)
            parent.setStretchFactor(idx + 1, 1)
            return new_tabs

        new_splitter = QSplitter(orientation)
        new_splitter.setChildrenCollapsible(False)
        new_splitter.setHandleWidth(6)
        new_tabs = EditorTabs(workspace=self, owner_window=None, parent=new_splitter)

        if isinstance(parent, QSplitter):
            idx = parent.indexOf(tabs)
            tabs.setParent(None)
            parent.insertWidget(idx, new_splitter)
        else:
            old = self.root_splitter
            old.setParent(None)
            self.root_splitter = new_splitter
            self.layout().addWidget(self.root_splitter)

        new_splitter.addWidget(tabs)
        new_splitter.addWidget(new_tabs)
        new_splitter.setStretchFactor(0, 1)
        new_splitter.setStretchFactor(1, 1)
        return new_tabs

    def split_editor_right(self, font_size: int | None = None, font_family: str | None = None):
        tabs = self._current_tabs()
        if tabs is None:
            return

        src = tabs.currentWidget()
        if not isinstance(src, EditorWidget):
            return
        new_tabs = self._split_tabs(tabs, Qt.Horizontal)
        record = self._ensure_record_for_editor(src)
        ed = self._new_view_for_record(
            record,
            font_size=font_size,
            font_family=font_family,
            source_editor=src,
        )

        new_tabs.add_editor(ed)

    def split_editor_down(self, font_size: int | None = None, font_family: str | None = None):
        tabs = self._current_tabs()
        if tabs is None:
            return

        src = tabs.currentWidget()
        if not isinstance(src, EditorWidget):
            return
        new_tabs = self._split_tabs(tabs, Qt.Vertical)
        record = self._ensure_record_for_editor(src)
        ed = self._new_view_for_record(
            record,
            font_size=font_size,
            font_family=font_family,
            source_editor=src,
        )

        new_tabs.add_editor(ed)

    # -------- tearout --------

    def create_tearout_window_with_editor(self, ed: EditorWidget):
        tw = EditorTearOutWindow(self)
        self._tearouts.append(tw)
        tw.tabs.add_editor(ed)
        tw.setWindowTitle(ed.display_name())
        tw.show()
        ed.setFocus()

        def _cleanup(*_):
            if tw in self._tearouts:
                self._tearouts.remove(tw)

        tw.destroyed.connect(_cleanup)

    def move_editor_to_primary_tabs(self, ed: EditorWidget):
        tabs = self._ensure_one_main_tabs()
        tabs.add_editor(ed)

    # -------- cleanup --------

    def request_cleanup_empty_panes(self):
        if self._cleanup_pending:
            return
        self._cleanup_pending = True
        QTimer.singleShot(0, self._run_deferred_cleanup)

    def _run_deferred_cleanup(self):
        self._cleanup_pending = False
        try:
            self.cleanup_empty_panes()
        except RuntimeError:
            # Workspace may be mid-destruction; ignore deferred cleanup then.
            return

    def cleanup_empty_panes(self):
        self._ensure_valid_root_splitter()

        changed = True
        while changed:
            changed = False
            for tabs in self._main_tabs():
                if tabs.count() > 0:
                    continue
                parent = tabs.parentWidget()
                tabs.setParent(None)
                tabs.deleteLater()
                self._collapse_splitter_if_needed(parent)
                changed = True
                break

        # always keep at least one main tabs pane
        self._ensure_one_main_tabs()
        self._prune_orphan_document_records()

    def _collapse_splitter_if_needed(self, splitter):
        if not isinstance(splitter, QSplitter):
            return

        try:
            count = splitter.count()
        except RuntimeError:
            return

        if count > 1:
            return

        parent = splitter.parentWidget()

        if count == 0:
            splitter.setParent(None)
            splitter.deleteLater()
            if isinstance(parent, QSplitter):
                self._collapse_splitter_if_needed(parent)
            return

        child = splitter.widget(0)
        if child is None:
            return
        child.setParent(None)

        if isinstance(parent, QSplitter):
            idx = parent.indexOf(splitter)
            splitter.setParent(None)
            splitter.deleteLater()
            parent.insertWidget(idx, child)
            self._collapse_splitter_if_needed(parent)
        else:
            # replace root safely
            old = self.root_splitter
            old.setParent(None)
            old.deleteLater()

            if isinstance(child, QSplitter):
                self.root_splitter = child
            else:
                self.root_splitter = QSplitter(Qt.Horizontal, self)
                self.root_splitter.setChildrenCollapsible(False)
                self.root_splitter.setHandleWidth(6)
                self.root_splitter.addWidget(child)

            lay = self.layout()
            if lay is None:
                lay = QVBoxLayout(self)
                lay.setContentsMargins(0, 0, 0, 0)
            else:
                while lay.count():
                    item = lay.takeAt(0)
                    w = item.widget()
                    if w is not None:
                        w.setParent(None)
            lay.addWidget(self.root_splitter)
        self._prune_orphan_document_records()
