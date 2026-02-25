from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from src.services.document_outline_service import OutlineSymbol


class SymbolOutlinePanel(QWidget):
    symbolActivated = Signal(str, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_file_path = ""

        self.status_label = QLabel("No symbols.", self)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Symbol", "Kind", "Line"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemClicked.connect(self._emit_activate_for_item)
        self.tree.itemActivated.connect(self._emit_activate_for_item)
        self.tree.itemDoubleClicked.connect(self._emit_activate_for_item)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.status_label)
        layout.addWidget(self.tree, 1)

    def clear_outline(self, message: str = "No symbols.") -> None:
        self._active_file_path = ""
        self.tree.clear()
        self.status_label.setText(str(message or "No symbols."))

    def set_outline(self, *, file_path: str, symbols: list[OutlineSymbol], error: str = "") -> None:
        self.tree.clear()
        self._active_file_path = str(file_path or "").strip()

        if error:
            self.status_label.setText(str(error))
            return

        if not symbols:
            self.status_label.setText("No symbols in this file.")
            return

        display = os.path.basename(self._active_file_path) if self._active_file_path else "current file"
        self.status_label.setText(f"{len(symbols)} top-level symbol(s) in {display}.")

        for symbol in symbols:
            self._append_symbol(None, symbol)

        self.tree.expandToDepth(1)
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)
        self.tree.resizeColumnToContents(2)

    def _append_symbol(self, parent: QTreeWidgetItem | None, symbol: OutlineSymbol) -> None:
        host = parent if isinstance(parent, QTreeWidgetItem) else self.tree
        item = QTreeWidgetItem(host)
        item.setText(0, str(symbol.name or "<symbol>"))
        item.setText(1, str(symbol.kind or "symbol"))
        item.setText(2, str(max(1, int(symbol.line or 1))))
        item.setData(
            0,
            Qt.UserRole,
            (
                self._active_file_path,
                max(1, int(symbol.line or 1)),
                max(1, int(symbol.column or 1)),
            ),
        )
        for child in symbol.children:
            self._append_symbol(item, child)

    def _emit_activate_for_item(self, item: QTreeWidgetItem, _column: int) -> None:
        payload = item.data(0, Qt.UserRole)
        if not isinstance(payload, tuple) or len(payload) != 3:
            return
        file_path, line, col = payload
        self.symbolActivated.emit(str(file_path or ""), int(line or 1), int(col or 1))
