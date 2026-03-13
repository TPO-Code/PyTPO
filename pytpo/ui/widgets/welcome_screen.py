from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class WelcomeScreenWidget(QWidget):
    """
    Polished, app-style-consistent welcome surface for "no project open" mode.
    Intentionally avoids local QSS so global application theme remains authoritative.
    """

    openProjectFolderRequested = Signal()
    newProjectRequested = Signal()
    cloneRepositoryRequested = Signal()
    createFromTemplateRequested = Signal()
    openRecentProjectRequested = Signal(str)
    removeRecentProjectRequested = Signal(str)
    revealRecentProjectRequested = Signal(str)
    clearRecentProjectsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_recent_paths: list[str] = []
        self._visible_recent_paths: list[str] = []

        self._recent_list: QListWidget | None = None
        self._filter_edit: QLineEdit | None = None
        self._title_label: QLabel | None = None

        self._setup_ui()

    # ----------------------------- public API -----------------------------

    def set_recent_projects(self, paths: list[str]) -> None:
        clean: list[str] = []
        seen: set[str] = set()

        for raw in paths:
            p = str(raw or "").strip()
            if not p:
                continue
            norm = os.path.normpath(p)
            # De-dup while preserving order
            key = norm.lower()
            if key in seen:
                continue
            seen.add(key)
            clean.append(norm)

        self._all_recent_paths = clean
        self._apply_filter()

    # ----------------------------- UI setup -----------------------------

    def _setup_ui(self) -> None:
        self.setObjectName("WelcomeScreenRoot")

        # Root: center a bounded-width shell.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        center_row = QHBoxLayout()
        center_row.setContentsMargins(24, 18, 24, 18)
        center_row.setSpacing(0)
        center_row.addStretch(1)

        shell = QWidget(self)
        shell.setObjectName("WelcomeShell")
        shell.setMaximumWidth(1060)
        shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        center_row.addWidget(shell, 1)
        center_row.addStretch(1)

        root.addLayout(center_row, 1)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(8, 8, 8, 8)
        shell_layout.setSpacing(16)

        # Header / hero
        header = QVBoxLayout()
        header.setSpacing(6)

        title = QLabel("Welcome to PyTPO", shell)
        # Let global style handle fonts/colors; we only nudge hierarchy.
        title_font = title.font()
        title_font.setPointSize(max(15, title_font.pointSize() + 3))
        title_font.setBold(True)
        title.setFont(title_font)

        subtitle = QLabel(
            "Open an existing folder, create a new project, or clone from GitHub.",
            shell,
        )
        subtitle.setWordWrap(True)

        header.addWidget(title)
        header.addWidget(subtitle)
        shell_layout.addLayout(header)

        self._title_label = title

        # Primary actions
        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        btn_open = QPushButton("Open Project Folder", shell)
        btn_open.setDefault(True)
        btn_open.clicked.connect(self.openProjectFolderRequested.emit)

        btn_new = QPushButton("New Project", shell)
        btn_new.clicked.connect(self.newProjectRequested.emit)

        btn_clone = QPushButton("Clone Repository", shell)
        btn_clone.clicked.connect(self.cloneRepositoryRequested.emit)

        btn_template = QPushButton("Create From Template", shell)
        btn_template.clicked.connect(self.createFromTemplateRequested.emit)
        btn_template.setMinimumHeight(32)


        # Keep button sizes consistent via size hints, no custom stylesheet needed.
        for b in (btn_open, btn_new, btn_template, btn_clone):
            b.setMinimumHeight(34)

        action_row.addWidget(btn_open)
        action_row.addWidget(btn_new)
        action_row.addWidget(btn_clone)
        action_row.addWidget(btn_template)
        action_row.addStretch(1)
        shell_layout.addLayout(action_row)


        # Recent projects section container
        recent_section = QFrame(shell)
        recent_section.setObjectName("WelcomeRecentSection")
        # No QSS: use frame shape for subtle structural grouping via theme defaults.
        recent_section.setFrameShape(QFrame.Shape.StyledPanel)
        recent_section.setFrameShadow(QFrame.Shadow.Raised)

        recent_layout = QVBoxLayout(recent_section)
        recent_layout.setContentsMargins(12, 10, 12, 10)
        recent_layout.setSpacing(8)

        recent_header_row = QHBoxLayout()
        recent_header_row.setSpacing(8)

        recent_label = QLabel("Recent Projects", recent_section)
        recent_label_font = recent_label.font()
        recent_label_font.setBold(True)
        recent_label.setFont(recent_label_font)

        filter_edit = QLineEdit(recent_section)
        filter_edit.setPlaceholderText("Filter recent projects…")
        filter_edit.textChanged.connect(self._apply_filter)
        filter_edit.setClearButtonEnabled(True)
        filter_edit.setMinimumWidth(260)
        filter_edit.setMaximumWidth(420)
        self._filter_edit = filter_edit

        recent_header_row.addWidget(recent_label)
        recent_header_row.addStretch(1)
        recent_header_row.addWidget(filter_edit)

        recent_layout.addLayout(recent_header_row)

        recent_list = QListWidget(recent_section)
        recent_list.setObjectName("WelcomeRecentList")
        recent_list.setSpacing(4)
        recent_list.setUniformItemSizes(False)
        recent_list.setAlternatingRowColors(False)
        recent_list.itemActivated.connect(self._on_recent_item_activated)
        recent_list.itemDoubleClicked.connect(self._on_recent_item_activated)
        recent_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        recent_list.customContextMenuRequested.connect(self._show_recent_context_menu)
        self._recent_list = recent_list

        recent_layout.addWidget(recent_list, 1)
        shell_layout.addWidget(recent_section, 1)

        # Initial placeholder
        self._render_recent_items([])

    # ----------------------------- rendering -----------------------------

    def _apply_filter(self) -> None:
        text = ""
        if self._filter_edit is not None:
            text = self._filter_edit.text().strip().lower()

        if not text:
            visible = list(self._all_recent_paths)
        else:
            visible = []
            for p in self._all_recent_paths:
                name = Path(p).name.lower()
                path_low = p.lower()
                if text in name or text in path_low:
                    visible.append(p)

        self._visible_recent_paths = visible
        self._render_recent_items(visible)

    def _render_recent_items(self, paths: list[str]) -> None:
        lst = self._recent_list
        if lst is None:
            return

        lst.clear()

        if not paths:
            placeholder = QListWidgetItem("No recent projects")
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            # compact placeholder row
            placeholder.setSizeHint(QSize(100, 34))
            lst.addItem(placeholder)
            return

        for path in paths:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setSizeHint(QSize(100, 58))
            lst.addItem(item)
            lst.setItemWidget(item, self._build_recent_row(path))

    def _build_recent_row(self, path: str) -> QWidget:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(10, 6, 10, 6)
        row_layout.setSpacing(10)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        name = os.path.basename(path.rstrip(os.sep)) or path
        exists = os.path.isdir(path)

        name_lbl = QLabel(name, row_widget)
        name_font = name_lbl.font()
        name_font.setBold(True)
        name_lbl.setFont(name_font)

        path_lbl = QLabel(path, row_widget)
        path_lbl.setToolTip(path)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        meta_lbl = QLabel("Missing on disk", row_widget)
        meta_lbl.setVisible(not exists)
        if not exists:
            meta_font = meta_lbl.font()
            meta_font.setItalic(True)
            meta_lbl.setFont(meta_font)

        text_col.addWidget(name_lbl)
        text_col.addWidget(path_lbl)
        text_col.addWidget(meta_lbl)

        action_col = QHBoxLayout()
        action_col.setContentsMargins(0, 0, 0, 0)
        action_col.setSpacing(6)

        open_btn = QToolButton(row_widget)
        open_btn.setText("Open")
        open_btn.setEnabled(exists)
        open_btn.clicked.connect(lambda _checked=False, p=path: self.openRecentProjectRequested.emit(p))

        remove_btn = QToolButton(row_widget)
        remove_btn.setText("Remove")
        remove_btn.clicked.connect(lambda _checked=False, p=path: self.removeRecentProjectRequested.emit(p))

        action_col.addWidget(open_btn)
        action_col.addWidget(remove_btn)

        row_layout.addLayout(text_col, 1)
        row_layout.addLayout(action_col)

        # Elide path after layout has concrete width
        self._apply_elided_path(path_lbl, path)

        return row_widget

    # ----------------------------- events -----------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Re-apply path elision across visible rows when width changes.
        lst = self._recent_list
        if lst is None:
            return
        for i in range(lst.count()):
            item = lst.item(i)
            path = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if not path:
                continue
            row_w = lst.itemWidget(item)
            if row_w is None:
                continue
            labels = row_w.findChildren(QLabel)
            # second label in row is path label in our construction order
            if len(labels) < 2:
                continue
            path_lbl = labels[1]
            self._apply_elided_path(path_lbl, path)

    # ----------------------------- helpers -----------------------------

    def _apply_elided_path(self, label: QLabel, full_path: str) -> None:
        if not label:
            return
        # Leave some breathing room for row actions.
        available = max(80, label.width() - 6)
        fm = QFontMetrics(label.font())
        elided = fm.elidedText(full_path, Qt.TextElideMode.ElideMiddle, available)
        label.setText(elided)
        label.setToolTip(full_path)

    def _on_recent_item_activated(self, item: QListWidgetItem) -> None:
        path = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if path:
            self.openRecentProjectRequested.emit(path)

    def _show_recent_context_menu(self, pos: QPoint) -> None:
        lst = self._recent_list
        if lst is None:
            return

        item = lst.itemAt(pos)
        if item is None:
            return

        path = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not path:
            return

        exists = os.path.isdir(path)

        menu = QMenu(lst)
        open_action = menu.addAction("Open Project")
        reveal_action = menu.addAction("Reveal in File Manager")
        remove_action = menu.addAction("Remove From Recent")
        menu.addSeparator()
        clear_action = menu.addAction("Clear Recent Projects")

        open_action.setEnabled(exists)
        reveal_action.setEnabled(exists)
        clear_action.setEnabled(bool(self._all_recent_paths))

        chosen = menu.exec(lst.mapToGlobal(pos))
        if chosen is open_action:
            self.openRecentProjectRequested.emit(path)
        elif chosen is reveal_action:
            self.revealRecentProjectRequested.emit(path)
        elif chosen is remove_action:
            self.removeRecentProjectRequested.emit(path)
        elif chosen is clear_action:
            self.clearRecentProjectsRequested.emit()
