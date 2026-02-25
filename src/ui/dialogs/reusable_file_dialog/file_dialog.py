from __future__ import annotations

import fnmatch
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QFileInfo, QPoint, QSize, QStandardPaths, Qt, Signal, QEvent, QUrl, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QPainter,
    QPixmap,
    QIcon,
    QKeySequence,
    QShortcut,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedLayout,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFileIconProvider,
)

from src.ui.custom_dialog import DialogWindow
from .config import BackgroundOptions, FileDialogResult, SidebarLocation


class _Columns:
    STAR = 0
    NAME = 1
    SIZE = 2
    TYPE = 3
    MODIFIED = 4


class _PathPillBar(QWidget):
    """Breadcrumb-like path bar with editable location mode."""

    pathClicked = Signal(str)
    navigateRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PathPillBar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._mode = "path"
        self._current_path = Path.home()
        self._virtual_label = ""
        self._click_tracking_start: QPoint | None = None
        self._click_tracking_moved = False
        # Guard against startup event-filter callbacks before widgets are fully created.
        self._crumb_container: QWidget | None = None
        self._line_edit: QLineEdit | None = None

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(0)

        self._crumb_container = QWidget(self)
        self._crumb_container.setObjectName("PathPillContainer")
        self._layout = QHBoxLayout(self._crumb_container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._line_edit = QLineEdit(self)
        self._line_edit.setObjectName("PathEditLine")
        self._line_edit.setClearButtonEnabled(True)
        self._line_edit.returnPressed.connect(self._on_return_pressed)
        self._line_edit.editingFinished.connect(self._on_editing_finished)
        self._stack.addWidget(self._crumb_container)
        self._stack.addWidget(self._line_edit)
        self._stack.setCurrentWidget(self._crumb_container)
        self._line_edit.installEventFilter(self)
        self._crumb_container.installEventFilter(self)

    def set_path(self, path: Path | str):
        try:
            candidate = Path(os.path.abspath(str(Path(path).expanduser())))
        except Exception:
            candidate = Path.home()
        self._mode = "path"
        self._current_path = candidate
        self._rebuild_for_path(candidate)
        self._stack.setCurrentWidget(self._crumb_container)

    def set_virtual_label(self, label: str):
        self._mode = "virtual"
        self._virtual_label = label
        self._clear()
        chip = QPushButton(label)
        chip.setObjectName("PathPillButton")
        chip.setEnabled(False)
        self._layout.addWidget(chip)
        self._layout.addStretch(1)
        self._stack.setCurrentWidget(self._crumb_container)

    def focus_for_edit(self, *, select_all: bool = True):
        if self._mode == "path":
            text = self._current_path.as_posix()
        else:
            text = self._virtual_label
        self._line_edit.setText(text)
        self._stack.setCurrentWidget(self._line_edit)
        self._line_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if select_all:
            self._line_edit.selectAll()
        else:
            self._line_edit.setCursorPosition(len(text))

    def _begin_click_tracking(self, global_pos: QPoint):
        self._click_tracking_start = QPoint(global_pos)
        self._click_tracking_moved = False

    def _update_click_tracking(self, global_pos: QPoint):
        if self._click_tracking_start is None:
            return
        if self._click_tracking_moved:
            return
        delta = global_pos - self._click_tracking_start
        if delta.manhattanLength() >= QApplication.startDragDistance():
            self._click_tracking_moved = True

    def _end_click_tracking(self):
        self._click_tracking_start = None
        self._click_tracking_moved = False

    def eventFilter(self, watched, event):
        line_edit = self._line_edit
        crumb_container = self._crumb_container
        if line_edit is None or crumb_container is None:
            return super().eventFilter(watched, event)

        if watched is line_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                text = line_edit.text().strip()
                if text:
                    self.navigateRequested.emit(text)

                # Leave edit mode AFTER this key event completes
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._stack.setCurrentWidget(crumb_container))

                event.accept()
                return True  # <-- critical: stop propagation to dialog/open button

            if key == Qt.Key.Key_Escape:
                self._stack.setCurrentWidget(crumb_container)
                event.accept()
                return True

        if (
                watched is crumb_container
                and event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and self._stack.currentWidget() is crumb_container
        ):
            self._begin_click_tracking(event.globalPosition().toPoint())
            return False

        if watched is crumb_container and event.type() == QEvent.Type.MouseMove:
            self._update_click_tracking(event.globalPosition().toPoint())
            return False

        if (
                watched is crumb_container
                and event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
        ):
            should_focus = (
                self._stack.currentWidget() is crumb_container
                and self._click_tracking_start is not None
                and not self._click_tracking_moved
            )
            self._end_click_tracking()
            if should_focus:
                self.focus_for_edit()
                return True
            return False

        # Optional safety: block Enter while crumb container has focus
        if watched is crumb_container and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                event.accept()
                return True

        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin_click_tracking(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._update_click_tracking(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            should_focus = (
                self._stack.currentWidget() is self._crumb_container
                and self._click_tracking_start is not None
                and not self._click_tracking_moved
            )
            self._end_click_tracking()
            if should_focus:
                self.focus_for_edit()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _on_return_pressed(self):
        # Enter handled in eventFilter to guarantee propagation is stopped.
        pass


    def _on_editing_finished(self):
        if self._stack.currentWidget() is self._line_edit:
            self._stack.setCurrentWidget(self._crumb_container)

    def _clear(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_for_path(self, target: Path):
        self._clear()

        parts = list(target.parts)
        if not parts:
            parts = [target.as_posix()]

        if os.name == "nt":
            cumulative = Path(parts[0])
        else:
            cumulative = Path(parts[0]) if parts[0] == os.sep else Path(parts[0])

        for idx, part in enumerate(parts):
            if idx == 0:
                label = "Home" if target == Path.home() else part
                crumb_path = cumulative
            else:
                cumulative = cumulative / part
                label = part
                crumb_path = cumulative

            button = QPushButton(label)
            button.setObjectName("PathPillButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setAutoDefault(False)
            button.setDefault(False)
            path_str = crumb_path.as_posix()
            button.clicked.connect(lambda _=False, p=path_str: self.pathClicked.emit(p))
            self._layout.addWidget(button)

            if idx < len(parts) - 1:
                separator = QLabel("/")
                separator.setObjectName("PathPillSeparator")
                self._layout.addWidget(separator)

        self._layout.addStretch(1)


class FileDialog(DialogWindow):
    """Export-friendly file dialog with starred paths, custom sidebar, and themed UI."""

    AcceptMode = QFileDialog.AcceptMode
    FileMode = QFileDialog.FileMode
    Option = QFileDialog.Option

    def __init__(
        self,
        parent: QWidget | None = None,
        caption: str = "Select File",
        directory: str | Path | None = None,
        name_filters: Iterable[str] | str | None = None,
        sidebar_locations: Iterable[SidebarLocation] | None = None,
        starred_paths: Iterable[str | Path] | None = None,
        background: BackgroundOptions | None = None,
        *,
        use_native_chrome: bool = False,
        resizable: bool = True,
    ):
        super().__init__(
            parent=parent,
            use_native_chrome=use_native_chrome,
            resizable=resizable,
        )
        self.setObjectName("FileDialog")
        self.resize(1000, 680)
        self.setMinimumSize(700, 420)
        self.setWindowTitle(caption)

        self._caption = caption
        self._accept_mode = self.AcceptMode.AcceptOpen
        self._file_mode = self.FileMode.AnyFile
        self._options = QFileDialog.Options(QFileDialog.Option.DontUseNativeDialog)
        self._default_suffix = ""

        self._selected_files: list[str] = []
        self._selected_name_filter = ""
        self._name_filters: list[str] = []

        self._history: list[Path] = []
        self._history_index = -1
        self._in_starred_view = False
        self._show_hidden = False
        self._search_query = ""
        self._view_mode = "list"

        self._list_icon_size = 22
        self._grid_icon_size = 108

        self._clipboard_paths: list[Path] = []
        self._clipboard_mode = "copy"

        self._starred_paths: set[str] = set()
        self._sidebar_locations: list[SidebarLocation] = []
        self._sidebar_path_items: dict[str, QListWidgetItem] = {}

        self._current_directory = Path(directory).expanduser() if directory else Path.home()
        if not self._current_directory.exists() or not self._current_directory.is_dir():
            self._current_directory = Path.home()

        self._icon_provider = QFileIconProvider()
        self._entries: list[Path] = []
        self._updating_model = False

        self._background_pixmap: QPixmap | None = None
        self._background_brightness = 1.0
        self._background_scale_mode = "stretch"
        self._background_tint = QColor(0, 0, 0, 0)
        self._stylesheet_path: Path | None = None
        self._extra_qss = ""

        self._build_ui()
        self._connect_signals()
        self._apply_default_stylesheet()

        self.setSidebarLocations(sidebar_locations)
        self.setStarredPaths(starred_paths or [])

        if isinstance(name_filters, str):
            self.setNameFilter(name_filters)
        elif name_filters is not None:
            self.setNameFilters(name_filters)
        else:
            self.setNameFilters(["All Files (*)"])

        self._navigate_to(self._current_directory, add_to_history=True)

        if background:
            self.setBackground(background)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        self._root = QWidget(self.content_host)
        self._root.setObjectName("FileDialogRoot")
        self.content_layout.addWidget(self._root)

        layout = QVBoxLayout(self._root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._top_bar = QWidget(self)
        self._top_bar.setObjectName("DialogTopBar")
        self._top_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top_layout = QHBoxLayout(self._top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        self._back_btn = QToolButton(self)
        self._back_btn.setObjectName("BackButton")
        self._back_btn.setText("<")

        self._forward_btn = QToolButton(self)
        self._forward_btn.setObjectName("ForwardButton")
        self._forward_btn.setText(">")

        self._up_btn = QToolButton(self)
        self._up_btn.setObjectName("UpButton")
        self._up_btn.setText("Up")

        self._path_bar = _PathPillBar(self)

        self._search_edit = QLineEdit(self)
        self._search_edit.setObjectName("SearchLineEdit")
        self._search_edit.setPlaceholderText("Search in current folder")
        self._search_edit.setClearButtonEnabled(True)

        self._quick_btn = QToolButton(self)
        self._quick_btn.setObjectName("QuickAccessButton")
        self._quick_btn.setText("Actions")
        self._quick_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._quick_menu = QMenu(self)
        self._quick_btn.setMenu(self._quick_menu)

        top_layout.addWidget(self._back_btn)
        top_layout.addWidget(self._forward_btn)
        top_layout.addWidget(self._up_btn)
        top_layout.addWidget(self._path_bar, 1)
        top_layout.addWidget(self._search_edit)
        top_layout.addWidget(self._quick_btn)

        self._splitter = QSplitter(self)
        self._splitter.setObjectName("DialogSplitter")

        self._sidebar = QListWidget(self)
        self._sidebar.setObjectName("LocationSidebar")
        self._sidebar.setMinimumWidth(180)
        self._splitter.addWidget(self._sidebar)

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("FileViewStack")

        self._table = QTableWidget(self)
        self._table.setObjectName("FileTableView")
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["★", "Name", "Size", "Type", "Modified"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_Columns.STAR, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_Columns.STAR, 38)
        header.setSectionResizeMode(_Columns.NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_Columns.SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_Columns.TYPE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_Columns.MODIFIED, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._grid = QListWidget(self)
        self._grid.setObjectName("FileGridView")
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._stack.addWidget(self._table)
        self._stack.addWidget(self._grid)
        self._splitter.addWidget(self._stack)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([220, 780])

        self._entry_row = QWidget(self)
        self._entry_row.setObjectName("DialogEntryRow")
        entry_layout = QGridLayout(self._entry_row)
        entry_layout.setContentsMargins(2, 2, 2, 2)
        entry_layout.setHorizontalSpacing(8)
        entry_layout.setVerticalSpacing(4)

        self._filename_label = QLabel("File name:")
        self._filename_edit = QLineEdit(self)
        self._filename_edit.setObjectName("FilenameEdit")
        self._filename_edit.setPlaceholderText("Enter a file or folder")

        self._filter_label = QLabel("Filter:")
        self._filter_combo = QComboBox(self)
        self._filter_combo.setObjectName("FilterCombo")

        entry_layout.addWidget(self._filename_label, 0, 0)
        entry_layout.addWidget(self._filename_edit, 0, 1)
        entry_layout.addWidget(self._filter_label, 0, 2)
        entry_layout.addWidget(self._filter_combo, 0, 3)
        entry_layout.setColumnStretch(1, 4)
        entry_layout.setColumnStretch(3, 2)

        self._buttons = QDialogButtonBox(self)
        self._buttons.setObjectName("DialogButtonBox")
        self._accept_btn = self._buttons.addButton("Open", QDialogButtonBox.ButtonRole.AcceptRole)
        self._cancel_btn = self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)

        layout.addWidget(self._splitter, 1)
        layout.addWidget(self._entry_row)
        layout.addWidget(self._buttons)

        self.set_title_text_visible(False)
        self.add_window_center_control(self._top_bar)
        self._title_balance_spacer = QWidget(self)
        self._title_balance_spacer.setObjectName("DialogTitleBalanceSpacer")
        self._title_balance_spacer.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.add_window_left_control(self._title_balance_spacer)
        self._schedule_title_bar_balance()

        self._apply_zoom_sizes()

    def _schedule_title_bar_balance(self):
        QTimer.singleShot(0, self._sync_title_bar_balance)

    def _sync_title_bar_balance(self):
        if not hasattr(self, "_title_balance_spacer"):
            return
        if self.title_bar is None:
            self._title_balance_spacer.setFixedWidth(0)
            return

        right_width = self.title_bar.right_layout.sizeHint().width()
        tools_width = self.title_bar.tools_layout.sizeHint().width()
        self._title_balance_spacer.setFixedWidth(max(0, right_width + tools_width))

    def _connect_signals(self):
        self._back_btn.clicked.connect(self._go_back)
        self._forward_btn.clicked.connect(self._go_forward)
        self._up_btn.clicked.connect(self._go_up)
        self._path_bar.pathClicked.connect(lambda path: self._navigate_to(Path(path), add_to_history=True))
        self._path_bar.navigateRequested.connect(self._on_path_entered)
        self._path_edit_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        self._path_edit_shortcut.activated.connect(self._path_bar.focus_for_edit)

        self._search_edit.textChanged.connect(self._on_search_changed)
        self._search_edit.returnPressed.connect(self._reload_entries)

        self._quick_menu.aboutToShow.connect(self._populate_quick_menu)

        self._sidebar.itemClicked.connect(self._on_sidebar_clicked)

        self._table.itemDoubleClicked.connect(self._on_table_double_clicked)
        self._table.itemSelectionChanged.connect(self._sync_filename_with_selection)
        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        self._grid.itemDoubleClicked.connect(self._on_grid_double_clicked)
        self._grid.itemSelectionChanged.connect(self._sync_filename_with_selection)
        self._grid.customContextMenuRequested.connect(self._on_grid_context_menu)

        self._filter_combo.currentTextChanged.connect(self._on_filter_changed)
        self._filename_edit.returnPressed.connect(self.accept)

        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

    def _on_path_entered(self, text: str):
        """
        Navigate if input resolves to an existing path.
        Otherwise treat input as a search query in current directory subtree.
        """
        print(f"[PATH ENTERED] raw={text!r} cwd={self._current_directory}")
        candidate = self._path_from_user_input(text)
        print(f"[PATH ENTERED] candidate={candidate!r} exists={candidate.exists() if candidate else None}")
        if candidate is None:
            return

        if candidate.exists():
            if candidate.is_dir():
                self._navigate_to(candidate, add_to_history=True)
                self._search_edit.clear()
            else:
                parent = candidate.parent
                if parent.exists():
                    self._navigate_to(parent, add_to_history=True)
                    self._filename_edit.setText(candidate.name)
            return

        # Not an existing path => search fallback
        query = str(text).strip()
        self._search_edit.setText(query)
        self._search_edit.setFocus()
        self._search_edit.selectAll()

        # recursive branch search fallback
        self._entries = self._search_in_branch(self._current_directory, query)
        self._populate_table(self._entries)
        self._populate_grid(self._entries)
        self._sync_filename_with_selection()
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setFileMode(self, mode: QFileDialog.FileMode):
        self._file_mode = mode
        if mode == self.FileMode.Directory:
            self._filename_label.setText("Directory:")
        else:
            self._filename_label.setText("File name:")
        self._update_accept_button_text()

    def fileMode(self) -> QFileDialog.FileMode:
        return self._file_mode

    def setAcceptMode(self, mode: QFileDialog.AcceptMode):
        self._accept_mode = mode
        self._update_accept_button_text()

    def acceptMode(self) -> QFileDialog.AcceptMode:
        return self._accept_mode

    def setOptions(self, options: QFileDialog.Options):
        self._options = options

    def options(self) -> QFileDialog.Options:
        return self._options

    def setDirectory(self, directory: str | Path):
        if not directory:
            return
        target = Path(directory).expanduser()
        if target.exists() and target.is_dir():
            self._navigate_to(target, add_to_history=True)

    def directory(self) -> str:
        return self._current_directory.as_posix() if self._current_directory else ""

    def setNameFilter(self, filter_str: str):
        filters = [part.strip() for part in filter_str.split(";;") if part.strip()]
        self.setNameFilters(filters)

    def setNameFilters(self, filters: Iterable[str]):
        self._name_filters = [entry for entry in filters if str(entry).strip()]
        if not self._name_filters:
            self._name_filters = ["All Files (*)"]

        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        self._filter_combo.addItems(self._name_filters)
        self._filter_combo.setCurrentIndex(0)
        self._filter_combo.blockSignals(False)

        self._selected_name_filter = self._filter_combo.currentText()
        self._reload_entries()

    def nameFilters(self) -> list[str]:
        return list(self._name_filters)

    def selectNameFilter(self, filter_str: str):
        index = self._filter_combo.findText(filter_str)
        if index >= 0:
            self._filter_combo.setCurrentIndex(index)

    def selectedNameFilter(self) -> str:
        return self._selected_name_filter

    def setDefaultSuffix(self, suffix: str):
        self._default_suffix = suffix.strip().lstrip(".")

    def defaultSuffix(self) -> str:
        return self._default_suffix

    def selectFile(self, filename: str):
        if not filename:
            return
        target = Path(filename).expanduser()
        if target.is_dir() and target.exists():
            self._navigate_to(target, add_to_history=True)
            return
        if target.parent.exists():
            self._navigate_to(target.parent, add_to_history=True)
            self._filename_edit.setText(target.name)

    def selectedFiles(self) -> list[str]:
        if self._selected_files:
            return list(self._selected_files)
        return self._gather_pending_selection()

    def setSidebarLocations(self, locations: Iterable[SidebarLocation] | None):
        if locations is None:
            self._sidebar_locations = self._default_sidebar_locations()
        else:
            self._sidebar_locations = list(locations)
        self._populate_sidebar()

    def sidebarLocations(self) -> list[SidebarLocation]:
        return list(self._sidebar_locations)

    def setStarredPaths(self, paths: Iterable[str | Path]):
        normalized: set[str] = set()
        for path in paths:
            norm = self._normalize_path(path)
            if norm:
                normalized.add(norm)
        self._starred_paths = normalized
        self._reload_entries()

    def starredPaths(self) -> list[str]:
        return sorted(self._starred_paths)

    def setBackground(self, options: BackgroundOptions):
        self._background_brightness = max(0.0, min(2.0, float(options.brightness)))
        scale_mode = str(options.scale_mode or "stretch").strip().lower()
        if scale_mode not in {"stretch", "fit_width", "fit_height", "tile"}:
            scale_mode = "stretch"
        self._background_scale_mode = scale_mode
        tint_strength = max(0.0, min(1.0, float(options.tint_strength)))
        tint_color = QColor(str(options.tint_color or "").strip() or "#000000")
        if not tint_color.isValid():
            tint_color = QColor("#000000")
        tint_color.setAlpha(max(0, min(255, int(round(tint_strength * 255.0)))))
        self._background_tint = tint_color

        pixmap: QPixmap | None = None
        if options.image_path:
            candidate = Path(options.image_path).expanduser()
            if candidate.exists():
                loaded = QPixmap(str(candidate))
                if not loaded.isNull():
                    pixmap = loaded
        self._background_pixmap = pixmap

        self._stylesheet_path = Path(options.qss_path).expanduser() if options.qss_path else None
        self._extra_qss = options.extra_qss or ""
        self._apply_default_stylesheet()
        self.update()

    def resultPayload(self) -> FileDialogResult:
        return FileDialogResult(
            accepted=(self.result() == QDialog.DialogCode.Accepted),
            selected_files=self.selectedFiles() if self.result() == QDialog.DialogCode.Accepted else [],
            selected_name_filter=self.selectedNameFilter(),
            starred_paths=self.starredPaths(),
        )

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def getOpenFileName(
        parent: QWidget | None = None,
        caption: str = "Open File",
        directory: str = "",
        filter: str = "",
        *,
        starred_paths: Iterable[str | Path] | None = None,
        sidebar_locations: Iterable[SidebarLocation] | None = None,
        background: BackgroundOptions | None = None,
    ) -> tuple[str, str, list[str]]:
        dialog = FileDialog(
            parent=parent,
            caption=caption,
            directory=directory,
            name_filters=filter or "All Files (*)",
            sidebar_locations=sidebar_locations,
            starred_paths=starred_paths,
            background=background,
        )
        dialog.setFileMode(FileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(FileDialog.AcceptMode.AcceptOpen)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            files = dialog.selectedFiles()
            return (files[0] if files else "", dialog.selectedNameFilter(), dialog.starredPaths())
        return ("", dialog.selectedNameFilter(), dialog.starredPaths())

    @staticmethod
    def getOpenFileNames(
        parent: QWidget | None = None,
        caption: str = "Open Files",
        directory: str = "",
        filter: str = "",
        *,
        starred_paths: Iterable[str | Path] | None = None,
        sidebar_locations: Iterable[SidebarLocation] | None = None,
        background: BackgroundOptions | None = None,
    ) -> tuple[list[str], str, list[str]]:
        dialog = FileDialog(
            parent=parent,
            caption=caption,
            directory=directory,
            name_filters=filter or "All Files (*)",
            sidebar_locations=sidebar_locations,
            starred_paths=starred_paths,
            background=background,
        )
        dialog.setFileMode(FileDialog.FileMode.ExistingFiles)
        dialog.setAcceptMode(FileDialog.AcceptMode.AcceptOpen)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return (dialog.selectedFiles(), dialog.selectedNameFilter(), dialog.starredPaths())
        return ([], dialog.selectedNameFilter(), dialog.starredPaths())

    @staticmethod
    def getSaveFileName(
        parent: QWidget | None = None,
        caption: str = "Save File",
        directory: str = "",
        filter: str = "",
        *,
        starred_paths: Iterable[str | Path] | None = None,
        sidebar_locations: Iterable[SidebarLocation] | None = None,
        background: BackgroundOptions | None = None,
    ) -> tuple[str, str, list[str]]:
        dialog = FileDialog(
            parent=parent,
            caption=caption,
            directory=directory,
            name_filters=filter or "All Files (*)",
            sidebar_locations=sidebar_locations,
            starred_paths=starred_paths,
            background=background,
        )
        dialog.setFileMode(FileDialog.FileMode.AnyFile)
        dialog.setAcceptMode(FileDialog.AcceptMode.AcceptSave)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            files = dialog.selectedFiles()
            return (files[0] if files else "", dialog.selectedNameFilter(), dialog.starredPaths())
        return ("", dialog.selectedNameFilter(), dialog.starredPaths())

    @staticmethod
    def getExistingDirectory(
        parent: QWidget | None = None,
        caption: str = "Select Folder",
        directory: str = "",
        *,
        starred_paths: Iterable[str | Path] | None = None,
        sidebar_locations: Iterable[SidebarLocation] | None = None,
        background: BackgroundOptions | None = None,
    ) -> tuple[str, list[str]]:
        dialog = FileDialog(
            parent=parent,
            caption=caption,
            directory=directory,
            sidebar_locations=sidebar_locations,
            starred_paths=starred_paths,
            background=background,
        )
        dialog.setFileMode(FileDialog.FileMode.Directory)
        dialog.setAcceptMode(FileDialog.AcceptMode.AcceptOpen)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            files = dialog.selectedFiles()
            return (files[0] if files else "", dialog.starredPaths())
        return ("", dialog.starredPaths())

    # ------------------------------------------------------------------
    # Dialog events
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent):
        self._selected_files = self._selected_files or self._gather_pending_selection()
        super().closeEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self.rect()
        if self._background_pixmap and not self._background_pixmap.isNull():
            mode = self._background_scale_mode
            if mode == "tile":
                painter.drawTiledPixmap(rect, self._background_pixmap)
            elif mode == "fit_width":
                scaled = self._background_pixmap.scaledToWidth(
                    max(1, rect.width()),
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = int((rect.width() - scaled.width()) / 2)
                y = int((rect.height() - scaled.height()) / 2)
                painter.drawPixmap(x, y, scaled)
            elif mode == "fit_height":
                scaled = self._background_pixmap.scaledToHeight(
                    max(1, rect.height()),
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = int((rect.width() - scaled.width()) / 2)
                y = int((rect.height() - scaled.height()) / 2)
                painter.drawPixmap(x, y, scaled)
            else:
                scaled = self._background_pixmap.scaled(
                    rect.size(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                painter.drawPixmap(rect.topLeft(), scaled)

        brightness = self._background_brightness
        if brightness < 1.0:
            alpha = int((1.0 - brightness) * 220)
            if alpha > 0:
                painter.fillRect(rect, QColor(0, 0, 0, alpha))
        elif brightness > 1.0:
            alpha = int((brightness - 1.0) * 180)
            if alpha > 0:
                painter.fillRect(rect, QColor(255, 255, 255, min(alpha, 180)))

        if self._background_tint.alpha() > 0:
            painter.fillRect(rect, self._background_tint)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self._schedule_title_bar_balance()

    # ------------------------------------------------------------------
    # Navigation and loading
    # ------------------------------------------------------------------

    def _default_sidebar_locations(self) -> list[SidebarLocation]:
        bookmarks = [
            ("Home", QStandardPaths.StandardLocation.HomeLocation, "user-home"),
            ("Desktop", QStandardPaths.StandardLocation.DesktopLocation, "user-desktop"),
            ("Documents", QStandardPaths.StandardLocation.DocumentsLocation, "folder-documents"),
            ("Downloads", QStandardPaths.StandardLocation.DownloadLocation, "folder-download"),
            ("Music", QStandardPaths.StandardLocation.MusicLocation, "folder-music"),
            ("Pictures", QStandardPaths.StandardLocation.PicturesLocation, "folder-pictures"),
            ("Videos", QStandardPaths.StandardLocation.MoviesLocation, "folder-videos"),
        ]
        output: list[SidebarLocation] = []
        for label, location, icon_name in bookmarks:
            path_str = QStandardPaths.writableLocation(location)
            if path_str:
                output.append(SidebarLocation(label=label, path=path_str, icon_name=icon_name))
        return output

    def _populate_sidebar(self):
        self._sidebar.blockSignals(True)
        self._sidebar.clear()
        self._sidebar_path_items.clear()

        starred = QListWidgetItem("Starred")
        starred.setIcon(QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton))
        starred.setData(Qt.ItemDataRole.UserRole, "special:starred")
        self._sidebar.addItem(starred)

        for location in self._sidebar_locations:
            normalized = self._normalize_path(location.path)
            if not normalized:
                continue
            icon = QIcon.fromTheme(location.icon_name)
            if icon.isNull():
                icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            item = QListWidgetItem(icon, location.label)
            item.setData(Qt.ItemDataRole.UserRole, normalized)
            item.setToolTip(normalized)
            self._sidebar.addItem(item)
            self._sidebar_path_items[normalized] = item

        self._sidebar.blockSignals(False)
        self._update_sidebar_selection()

    def _update_sidebar_selection(self):
        self._sidebar.blockSignals(True)
        try:
            if self._in_starred_view:
                self._sidebar.setCurrentRow(0)
            else:
                normalized = self._normalize_path(self._current_directory)
                if normalized in self._sidebar_path_items:
                    self._sidebar.setCurrentItem(self._sidebar_path_items[normalized])
                else:
                    self._sidebar.clearSelection()
                    self._sidebar.setCurrentRow(-1)
        finally:
            self._sidebar.blockSignals(False)

    def _navigate_to(self, directory: Path, *, add_to_history: bool):
        if not directory.exists() or not directory.is_dir():
            return

        resolved = self._normalize_filesystem_path(directory)
        self._in_starred_view = False
        self._current_directory = resolved
        self._path_bar.set_path(resolved)

        if add_to_history:
            if self._history_index < len(self._history) - 1:
                self._history = self._history[: self._history_index + 1]
            if not self._history or self._history[-1] != resolved:
                self._history.append(resolved)
                self._history_index = len(self._history) - 1

        self._update_nav_buttons()
        self._update_sidebar_selection()
        self._reload_entries()

    def _go_back(self):
        if self._history_index > 0:
            self._history_index -= 1
            self._current_directory = self._history[self._history_index]
            self._in_starred_view = False
            self._path_bar.set_path(self._current_directory)
            self._update_nav_buttons()
            self._update_sidebar_selection()
            self._reload_entries()

    def _go_forward(self):
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._current_directory = self._history[self._history_index]
            self._in_starred_view = False
            self._path_bar.set_path(self._current_directory)
            self._update_nav_buttons()
            self._update_sidebar_selection()
            self._reload_entries()

    def _go_up(self):
        if self._in_starred_view:
            return
        parent = self._current_directory.parent
        if parent != self._current_directory:
            self._navigate_to(parent, add_to_history=True)

    def _update_nav_buttons(self):
        self._back_btn.setEnabled(self._history_index > 0)
        self._forward_btn.setEnabled(self._history_index < len(self._history) - 1)
        self._up_btn.setEnabled(not self._in_starred_view and self._current_directory.parent != self._current_directory)

    def _on_sidebar_clicked(self, item: QListWidgetItem):
        target = item.data(Qt.ItemDataRole.UserRole)
        if target == "special:starred":
            self._in_starred_view = True
            self._path_bar.set_virtual_label("Starred")
            self._update_nav_buttons()
            self._reload_entries()
            return

        if target:
            self._navigate_to(Path(target), add_to_history=True)

    def _reload_entries(self):
        entries: list[Path] = []
        if self._in_starred_view:
            for starred in sorted(self._starred_paths):
                candidate = Path(starred)
                if candidate.exists():
                    entries.append(candidate)
        else:
            try:
                entries = [entry for entry in self._current_directory.iterdir()]
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Directory", f"Unable to read directory:\n{exc}")
                entries = []

        filtered: list[Path] = []
        query = self._search_query.strip().lower()
        patterns = self._current_filter_patterns()

        for entry in entries:
            name = entry.name
            if not self._show_hidden and name.startswith("."):
                continue

            if query and query not in name.lower():
                continue

            if patterns and entry.is_file():
                if not any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns):
                    continue

            filtered.append(entry)

        filtered.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        self._entries = filtered

        self._populate_table(filtered)
        self._populate_grid(filtered)
        self._sync_filename_with_selection()

    # ------------------------------------------------------------------
    # View population
    # ------------------------------------------------------------------

    def _populate_table(self, entries: list[Path]):
        self._updating_model = True
        try:
            self._table.setRowCount(len(entries))
            self._table.clearContents()

            for row, entry in enumerate(entries):
                normalized = self._normalize_path(entry)
                icon = self._icon_provider.icon(QFileInfo(str(entry)))

                star_item = QTableWidgetItem()
                star_item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                star_item.setData(Qt.ItemDataRole.UserRole, normalized)
                star_item.setCheckState(
                    Qt.CheckState.Checked if normalized in self._starred_paths else Qt.CheckState.Unchecked
                )
                self._table.setItem(row, _Columns.STAR, star_item)

                name_item = QTableWidgetItem(entry.name)
                name_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                name_item.setData(Qt.ItemDataRole.UserRole, normalized)
                name_item.setIcon(icon)
                self._table.setItem(row, _Columns.NAME, name_item)

                size_item = QTableWidgetItem(self._human_size(entry) if entry.is_file() else "")
                size_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                self._table.setItem(row, _Columns.SIZE, size_item)

                type_item = QTableWidgetItem("Folder" if entry.is_dir() else self._file_type(entry))
                type_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                self._table.setItem(row, _Columns.TYPE, type_item)

                modified_item = QTableWidgetItem(self._modified_time(entry))
                modified_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                self._table.setItem(row, _Columns.MODIFIED, modified_item)

                self._table.setRowHeight(row, max(30, self._list_icon_size + 8))
        finally:
            self._updating_model = False

    def _populate_grid(self, entries: list[Path]):
        self._grid.blockSignals(True)
        try:
            self._grid.clear()
            for entry in entries:
                normalized = self._normalize_path(entry)
                icon = self._icon_provider.icon(QFileInfo(str(entry)))
                name = entry.name
                label = f"★ {name}" if normalized in self._starred_paths else name
                item = QListWidgetItem(icon, label)
                item.setData(Qt.ItemDataRole.UserRole, normalized)
                item.setToolTip(normalized)
                self._grid.addItem(item)
        finally:
            self._grid.blockSignals(False)

    def _sync_filename_with_selection(self):
        selected = self._selected_paths()
        if not selected:
            self._filename_edit.clear()
            return

        if self._file_mode == self.FileMode.ExistingFiles and len(selected) > 1:
            self._filename_edit.setText("; ".join(Path(path).name for path in selected))
            return

        self._filename_edit.setText(Path(selected[0]).name)

    def _selected_paths(self) -> list[str]:
        if self._view_mode == "grid":
            paths = []
            for item in self._grid.selectedItems():
                path = item.data(Qt.ItemDataRole.UserRole)
                if path:
                    paths.append(path)
            return paths

        rows = {index.row() for index in self._table.selectedIndexes()}
        selected: list[str] = []
        for row in sorted(rows):
            item = self._table.item(row, _Columns.NAME)
            if item is None:
                continue
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                selected.append(path)
        return selected

    # ------------------------------------------------------------------
    # Context menu and quick actions
    # ------------------------------------------------------------------

    def _populate_quick_menu(self):
        self._quick_menu.clear()

        show_hidden = QAction("Show Hidden Files", self)
        show_hidden.setCheckable(True)
        show_hidden.setChecked(self._show_hidden)
        show_hidden.toggled.connect(self._toggle_hidden)
        self._quick_menu.addAction(show_hidden)

        view_menu = self._quick_menu.addMenu("View Mode")
        list_mode = QAction("List", self)
        list_mode.setCheckable(True)
        list_mode.setChecked(self._view_mode == "list")
        list_mode.triggered.connect(lambda checked: self._set_view_mode("list") if checked else None)
        grid_mode = QAction("Grid", self)
        grid_mode.setCheckable(True)
        grid_mode.setChecked(self._view_mode == "grid")
        grid_mode.triggered.connect(lambda checked: self._set_view_mode("grid") if checked else None)
        view_menu.addAction(list_mode)
        view_menu.addAction(grid_mode)

        zoom_menu = self._quick_menu.addMenu("Zoom")
        zoom_in = QAction("Zoom In", self)
        zoom_in.triggered.connect(lambda: self._change_zoom(1))
        zoom_out = QAction("Zoom Out", self)
        zoom_out.triggered.connect(lambda: self._change_zoom(-1))
        zoom_reset = QAction("Reset Zoom", self)
        zoom_reset.triggered.connect(self._reset_zoom)
        zoom_menu.addAction(zoom_in)
        zoom_menu.addAction(zoom_out)
        zoom_menu.addSeparator()
        zoom_menu.addAction(zoom_reset)

        self._quick_menu.addSeparator()

        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(self._copy_selection)
        paste_action = QAction("Paste", self)
        paste_action.triggered.connect(self._paste_into_current)
        paste_action.setEnabled(bool(self._clipboard_paths) and not self._in_starred_view)
        self._quick_menu.addAction(copy_action)
        self._quick_menu.addAction(paste_action)

        new_menu = self._quick_menu.addMenu("New")
        new_file_action = QAction("File...", self)
        new_file_action.triggered.connect(self._new_file)
        new_folder_action = QAction("Folder...", self)
        new_folder_action.triggered.connect(self._new_folder)
        new_menu.addAction(new_file_action)
        new_menu.addAction(new_folder_action)
        new_menu.setEnabled(not self._in_starred_view)

    def _on_table_context_menu(self, pos: QPoint):
        global_pos = self._table.viewport().mapToGlobal(pos)
        self._show_file_context_menu(global_pos)

    def _on_grid_context_menu(self, pos: QPoint):
        global_pos = self._grid.viewport().mapToGlobal(pos)
        self._show_file_context_menu(global_pos)

    def _show_file_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)

        open_action = QAction("Open", self)
        open_action.triggered.connect(self._activate_primary_selection)
        menu.addAction(open_action)

        star_action = QAction("Toggle Star", self)
        star_action.triggered.connect(self._toggle_star_on_selection)
        menu.addAction(star_action)

        menu.addSeparator()

        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(self._copy_selection)
        paste_action = QAction("Paste", self)
        paste_action.triggered.connect(self._paste_into_current)
        paste_action.setEnabled(bool(self._clipboard_paths) and not self._in_starred_view)
        menu.addAction(copy_action)
        menu.addAction(paste_action)

        new_menu = menu.addMenu("New")
        new_file_action = QAction("File...", self)
        new_file_action.triggered.connect(self._new_file)
        new_folder_action = QAction("Folder...", self)
        new_folder_action.triggered.connect(self._new_folder)
        new_menu.addAction(new_file_action)
        new_menu.addAction(new_folder_action)
        new_menu.setEnabled(not self._in_starred_view)

        menu.addSeparator()

        zoom_menu = menu.addMenu("Zoom")
        zoom_in = QAction("Zoom In", self)
        zoom_in.triggered.connect(lambda: self._change_zoom(1))
        zoom_out = QAction("Zoom Out", self)
        zoom_out.triggered.connect(lambda: self._change_zoom(-1))
        zoom_reset = QAction("Reset Zoom", self)
        zoom_reset.triggered.connect(self._reset_zoom)
        zoom_menu.addAction(zoom_in)
        zoom_menu.addAction(zoom_out)
        zoom_menu.addAction(zoom_reset)

        menu.exec(global_pos)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_table_double_clicked(self, item: QTableWidgetItem):
        if item.column() == _Columns.STAR:
            return
        self._activate_path(item.data(Qt.ItemDataRole.UserRole))

    def _on_grid_double_clicked(self, item: QListWidgetItem):
        self._activate_path(item.data(Qt.ItemDataRole.UserRole))

    def _activate_primary_selection(self):
        selected = self._selected_paths()
        if selected:
            self._activate_path(selected[0])

    def _activate_path(self, raw_path: str | None):
        if not raw_path:
            return
        path = Path(raw_path)
        if path.is_dir():
            self._navigate_to(path, add_to_history=True)
            return

        self._filename_edit.setText(path.name)
        if self._file_mode != self.FileMode.Directory:
            self.accept()

    def _on_table_item_changed(self, item: QTableWidgetItem):
        if self._updating_model:
            return
        if item.column() != _Columns.STAR:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._starred_paths.add(path)
        else:
            self._starred_paths.discard(path)
        self._populate_grid(self._entries)

    def _toggle_star_on_selection(self):
        selected = self._selected_paths()
        if not selected:
            return
        should_star = any(path not in self._starred_paths for path in selected)
        for path in selected:
            if should_star:
                self._starred_paths.add(path)
            else:
                self._starred_paths.discard(path)
        self._reload_entries()

    def _copy_selection(self):
        selected = self._selected_paths()
        self._clipboard_paths = [Path(path) for path in selected]
        self._clipboard_mode = "copy"

        if selected:
            QApplication.clipboard().setText("\n".join(selected))

    def _paste_into_current(self):
        if self._in_starred_view:
            return
        if not self._current_directory.exists():
            return
        if not self._clipboard_paths:
            return

        errors: list[str] = []

        for source in self._clipboard_paths:
            if not source.exists():
                errors.append(f"Missing source: {source}")
                continue

            destination = self._ensure_unique_path(self._current_directory / source.name)
            try:
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source.name}: {exc}")

        if errors:
            QMessageBox.warning(self, "Paste", "\n".join(errors))

        self._reload_entries()

    def _new_folder(self):
        if self._in_starred_view:
            return
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text="New Folder")
        if not ok:
            return
        clean_name = (name or "").strip() or "New Folder"
        target = self._ensure_unique_path(self._current_directory / clean_name)
        try:
            target.mkdir(parents=False, exist_ok=False)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "New Folder", f"Unable to create folder:\n{exc}")
            return
        self._reload_entries()

    def _new_file(self):
        if self._in_starred_view:
            return
        name, ok = QInputDialog.getText(self, "New File", "File name:", text="New File.txt")
        if not ok:
            return
        clean_name = (name or "").strip() or "New File.txt"
        target = self._ensure_unique_path(self._current_directory / clean_name)
        try:
            target.touch(exist_ok=False)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "New File", f"Unable to create file:\n{exc}")
            return
        self._reload_entries()

    # ------------------------------------------------------------------
    # Search/filter/zoom
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str):
        self._search_query = text.strip()
        self._reload_entries()

    def _on_filter_changed(self, text: str):
        self._selected_name_filter = text
        self._reload_entries()

    def _toggle_hidden(self, checked: bool):
        self._show_hidden = bool(checked)
        self._reload_entries()

    def _set_view_mode(self, mode: str):
        if mode not in {"list", "grid"}:
            return
        self._view_mode = mode
        self._stack.setCurrentWidget(self._table if mode == "list" else self._grid)
        self._sync_filename_with_selection()

    def _change_zoom(self, delta: int):
        if self._view_mode == "grid":
            self._grid_icon_size = max(72, min(220, self._grid_icon_size + delta * 14))
        else:
            self._list_icon_size = max(16, min(64, self._list_icon_size + delta * 4))
        self._apply_zoom_sizes()

    def _reset_zoom(self):
        self._list_icon_size = 22
        self._grid_icon_size = 108
        self._apply_zoom_sizes()

    def _apply_zoom_sizes(self):
        self._table.setIconSize(QSize(self._list_icon_size, self._list_icon_size))
        for row in range(self._table.rowCount()):
            self._table.setRowHeight(row, max(30, self._list_icon_size + 8))

        self._grid.setIconSize(QSize(self._grid_icon_size, self._grid_icon_size))
        self._grid.setGridSize(QSize(self._grid_icon_size + 52, self._grid_icon_size + 60))
        self._grid.setSpacing(max(8, self._grid_icon_size // 10))
    def _search_in_branch(self, root: Path, query: str) -> list[Path]:
        """
        Recursive search from root.
        Supports:
          - glob-like patterns (*.png, **/*.py, file?.txt)
          - extension shortcut (.png)
          - plain substring fallback
        """
        q = (query or "").strip()
        if not q or not root.exists() or not root.is_dir():
            return []

        # normalize ".png" -> "*.png"
        pattern_mode = any(ch in q for ch in "*?[]")
        if q.startswith(".") and not pattern_mode:
            q = f"*{q}"
            pattern_mode = True

        results: list[Path] = []
        try:
            for p in root.rglob("*"):
                name = p.name
                if not self._show_hidden and name.startswith("."):
                    continue

                if pattern_mode:
                    if fnmatch.fnmatch(name.lower(), q.lower()):
                        results.append(p)
                else:
                    if q.lower() in name.lower():
                        results.append(p)
        except Exception:
            return []

        results.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        return results

    # ------------------------------------------------------------------
    # Accept/reject and selection
    # ------------------------------------------------------------------

    def _update_accept_button_text(self):
        if self._file_mode == self.FileMode.Directory:
            self._accept_btn.setText("Select")
        elif self._accept_mode == self.AcceptMode.AcceptSave:
            self._accept_btn.setText("Save")
        else:
            self._accept_btn.setText("Open")

    def _gather_pending_selection(self) -> list[str]:
        if self._file_mode == self.FileMode.Directory:
            selected_dirs = [path for path in self._selected_paths() if Path(path).is_dir()]
            if selected_dirs:
                return selected_dirs[:1]
            return [self._current_directory.as_posix()] if self._current_directory else []

        typed = self._filename_edit.text().strip()
        if typed:
            resolved = self._resolve_typed_path(typed)
            if resolved:
                if self._accept_mode == self.AcceptMode.AcceptSave:
                    return [resolved]
                if Path(resolved).exists():
                    return [resolved]

        selected = self._selected_paths()
        if self._file_mode == self.FileMode.ExistingFile:
            return selected[:1]
        return selected

    def _resolve_typed_path(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        candidate = self._path_from_user_input(raw)
        if candidate is None:
            return ""

        if not candidate.suffix:
            if self._default_suffix:
                candidate = candidate.with_suffix(f".{self._default_suffix}")
            else:
                patterns = self._current_filter_patterns()
                if patterns:
                    first = patterns[0]
                    if first.startswith("*."):
                        candidate = candidate.with_suffix(first[1:])

        return self._normalize_filesystem_path(candidate).as_posix()

    def accept(self):
        selection = self._gather_pending_selection()
        if not selection:
            QMessageBox.warning(self, self._caption, "Please select a file or folder.")
            return

        if self._file_mode in {self.FileMode.ExistingFile, self.FileMode.ExistingFiles}:
            missing = [path for path in selection if not Path(path).exists()]
            if missing:
                QMessageBox.warning(self, self._caption, "Some selected files no longer exist.")
                return

        if self._file_mode == self.FileMode.Directory:
            invalid = [path for path in selection if not Path(path).is_dir()]
            if invalid:
                QMessageBox.warning(self, self._caption, "Please choose a directory.")
                return

        self._selected_files = selection
        super().accept()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_filter_patterns(self) -> list[str]:
        text = self._selected_name_filter or self._filter_combo.currentText()
        if not text:
            return []

        if "(" in text and ")" in text:
            inside = text[text.find("(") + 1 : text.rfind(")")]
            return [token.strip() for token in inside.split() if token.strip() and token.strip() != "*"]

        simple = text.strip()
        if simple and any(ch in simple for ch in ("*", "?")):
            return [simple]

        return []

    def _modified_time(self, path: Path) -> str:
        try:
            mtime = path.stat().st_mtime
            return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def _file_type(self, path: Path) -> str:
        suffix = path.suffix.lstrip(".").upper()
        return f"{suffix} File" if suffix else "File"

    def _human_size(self, path: Path) -> str:
        try:
            size = float(path.stat().st_size)
        except Exception:
            return ""
        units = ["B", "KB", "MB", "GB", "TB"]
        idx = 0
        while size >= 1024 and idx < len(units) - 1:
            size /= 1024
            idx += 1
        if idx == 0:
            return f"{int(size)} {units[idx]}"
        return f"{size:.1f} {units[idx]}"

    def _normalize_path(self, path: str | Path | None) -> str | None:
        if not path:
            return None
        try:
            return self._normalize_filesystem_path(Path(path)).as_posix()
        except Exception:
            try:
                return Path(path).expanduser().absolute().as_posix()
            except Exception:
                return None

    def _normalize_filesystem_path(self, path: Path) -> Path:
        # Keep user-visible navigation stable by avoiding symlink resolution here.
        # `resolve()` can jump to an unexpected root when symlinks are involved.
        expanded = Path(path).expanduser()
        return Path(os.path.abspath(str(expanded)))

    def _path_from_user_input(self, text: str | None) -> Path | None:
        """
        Parses user input into a Path object with minimal interference.
        """
        raw = str(text or "").strip()
        if not raw:
            return None

        # 1. Minimal cleaning: just strip quotes which are common in copy-paste
        cleaned = raw.strip("\"'")

        # 2. Handle file:// URI scheme (common browser paste)
        if cleaned.startswith("file://"):
            cleaned = cleaned[7:]
            # On Windows /C:/Path needs to become C:/Path
            if os.name == 'nt' and cleaned.startswith('/') and len(cleaned) > 2 and cleaned[2] == ':':
                cleaned = cleaned.lstrip('/')

        # 3. Standard shell expansions (User choice should be respected)
        cleaned = os.path.expandvars(cleaned)
        cleaned = os.path.expanduser(cleaned)

        # 4. Create path candidate
        candidate = Path(cleaned)

        # 5. Handle relative paths (e.g. user typed "src" while in "/project")
        if not candidate.is_absolute():
            base = self._current_directory if self._current_directory else Path.home()
            candidate = base / candidate

        # We do NOT return None here if it doesn't exist. 
        # We return the path object so _on_path_entered can decide what to do.
        return self._normalize_filesystem_path(candidate)

    def _ensure_unique_path(self, candidate: Path) -> Path:
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        parent = candidate.parent
        index = 1

        while True:
            trial = parent / f"{stem} ({index}){suffix}"
            if not trial.exists():
                return trial
            index += 1

    def _apply_default_stylesheet(self):
        stylesheet_path = self._stylesheet_path
        if stylesheet_path is None:
            stylesheet_path = Path(__file__).resolve().parent / "styles" / "default.qss"

        stylesheet = ""
        try:
            stylesheet = stylesheet_path.read_text(encoding="utf-8")
        except OSError:
            stylesheet = ""

        if self._extra_qss:
            stylesheet = f"{stylesheet}\n{self._extra_qss}\n"

        self.setStyleSheet(stylesheet)
