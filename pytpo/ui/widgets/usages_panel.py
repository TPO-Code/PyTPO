from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class UsagesPanel(QWidget):
    usagePreviewRequested = Signal(str, int, int)
    usageActivated = Signal(str, int, int)
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running_token = 0
        self._result_count = 0
        self._groups: dict[str, QTreeWidgetItem] = {}

        self.status_label = QLabel("No usages.", self)
        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancelRequested.emit)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.status_label, 1)
        top.addWidget(self.cancel_btn, 0)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Location", "Preview"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self._emit_preview_for_current)
        self.tree.itemDoubleClicked.connect(self._emit_activate_for_item)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.tree)

    def clear_results(self):
        self.tree.clear()
        self._groups.clear()
        self._result_count = 0
        self._running_token = 0
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("No usages.")

    def start_search(self, symbol: str, token: int):
        self.clear_results()
        self._running_token = max(0, int(token))
        sym = str(symbol or "").strip() or "symbol"
        self.status_label.setText(f"Searching usages for {sym}...")
        self.cancel_btn.setEnabled(self._running_token > 0)

    def append_results(self, results_obj: object):
        if not isinstance(results_obj, list):
            return
        for hit in results_obj:
            if not isinstance(hit, dict):
                continue
            file_path = str(hit.get("file_path") or "").strip()
            if not file_path:
                continue
            line = max(1, int(hit.get("line") or 1))
            col = max(1, int(hit.get("column") or 1))
            preview = str(hit.get("preview") or "").strip()

            group = self._groups.get(file_path)
            if group is None:
                group = QTreeWidgetItem(self.tree)
                group.setText(0, file_path)
                group.setText(1, "")
                group.setFirstColumnSpanned(False)
                group.setExpanded(True)
                self._groups[file_path] = group

            child = QTreeWidgetItem(group)
            child.setText(0, f"{line}:{col}")
            child.setText(1, preview)
            child.setData(0, Qt.UserRole, (file_path, line, col))
            self._result_count += 1

            group.setText(1, f"{group.childCount()} hit(s)")

        self.tree.resizeColumnToContents(0)

    def finish_search(self, *, canceled: bool = False, error: str = ""):
        self.cancel_btn.setEnabled(False)
        self._running_token = 0
        if canceled:
            self.status_label.setText(f"Search canceled ({self._result_count} hit(s) shown).")
            return
        if error:
            self.status_label.setText(f"Search failed: {error}")
            return
        self.status_label.setText(f"{self._result_count} usage(s) found.")

    def _emit_preview_for_current(self):
        item = self.tree.currentItem()
        if item is None:
            return
        payload = item.data(0, Qt.UserRole)
        if not isinstance(payload, tuple) or len(payload) != 3:
            return
        file_path, line, col = payload
        self.usagePreviewRequested.emit(str(file_path), int(line), int(col))

    def _emit_activate_for_item(self, item: QTreeWidgetItem, _column: int):
        payload = item.data(0, Qt.UserRole)
        if not isinstance(payload, tuple) or len(payload) != 3:
            return
        file_path, line, col = payload
        self.usageActivated.emit(str(file_path), int(line), int(col))
