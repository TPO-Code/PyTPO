from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QHBoxLayout,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)


class ProblemsPanel(QWidget):
    problemActivated = Signal(str, int, int)  # file, line, col
    importSymbolRequested = Signal(object)  # diagnostic dict
    removeUnusedImportRequested = Signal(object)  # diagnostic dict
    addTdocSymbolRequested = Signal(object)  # diagnostic dict
    capitalizeTdocSectionRequested = Signal(object)  # diagnostic dict
    clearFileRequested = Signal(str)
    clearAllRequested = Signal()
    countChanged = Signal(int)

    COLUMNS = ["Severity", "File", "Line", "Col", "Code", "Message", "Source"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)

        self.table.doubleClicked.connect(self._activate_current_row)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)

    def set_diagnostics(self, by_file: dict[str, list[dict]]):
        rows: list[dict] = []
        for file_path, diagnostics in by_file.items():
            if not isinstance(file_path, str):
                continue
            if not isinstance(diagnostics, list):
                continue
            for d in diagnostics:
                if not isinstance(d, dict):
                    continue
                rows.append(d)

        rows.sort(
            key=lambda d: (
                self._severity_rank(str(d.get("severity", "warning"))),
                str(d.get("file_path", "")),
                int(d.get("line", 1)),
                int(d.get("column", 1)),
                str(d.get("message", "")),
            ),
            reverse=True,
        )
        self._rows = rows
        self._render_rows()
        self.countChanged.emit(len(self._rows))

    def clear(self):
        self._rows = []
        self._render_rows()
        self.countChanged.emit(0)

    def _render_rows(self):
        self.table.setRowCount(len(self._rows))
        for row, diag in enumerate(self._rows):
            severity = str(diag.get("severity") or "warning")
            file_path = str(diag.get("file_path") or "")
            line = int(diag.get("line") or 1)
            col = int(diag.get("column") or 1)
            code = str(diag.get("code") or "")
            message = str(diag.get("message") or "")
            source = str(diag.get("source") or "")

            values = [severity, file_path, str(line), str(col), code, message, source]
            for col_idx, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, diag)
                if col_idx in (2, 3):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col_idx, item)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _activate_current_row(self):
        row = self.table.currentRow()
        diag = self._diag_for_row(row)
        if diag is None:
            return
        self.problemActivated.emit(
            str(diag.get("file_path") or ""),
            int(diag.get("line") or 1),
            int(diag.get("column") or 1),
        )

    def _diag_for_row(self, row: int) -> Optional[dict]:
        if row < 0 or row >= self.table.rowCount():
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        diag = self._diag_for_row(row)
        missing_symbol = self._missing_symbol_from_diag(diag)
        is_unused_import = self._is_unused_import_diag(diag)
        unresolved_tdoc_symbol = self._tdoc_unresolved_symbol_from_diag(diag)
        tdoc_section_to_capitalize = self._tdoc_section_cap_warning_from_diag(diag)
        menu = QMenu(self)

        act_copy_message = QAction("Copy Message", self)
        act_copy_message.setEnabled(diag is not None)
        act_copy_message.triggered.connect(lambda: self._copy_diag_message(diag))
        menu.addAction(act_copy_message)

        act_copy_file_line = QAction("Copy File:Line", self)
        act_copy_file_line.setEnabled(diag is not None)
        act_copy_file_line.triggered.connect(lambda: self._copy_diag_file_line(diag))
        menu.addAction(act_copy_file_line)

        if diag is not None and missing_symbol:
            act_import_symbol = QAction(f"Import '{missing_symbol}'", self)
            act_import_symbol.triggered.connect(lambda: self._emit_import_symbol(diag))
            menu.addAction(act_import_symbol)

        if diag is not None and is_unused_import:
            unused_import_name = self._unused_import_name_from_diag(diag)
            label = f"Remove unused import '{unused_import_name}'" if unused_import_name else "Remove unused import"
            act_remove_unused = QAction(label, self)
            act_remove_unused.triggered.connect(lambda: self._emit_remove_unused_import(diag))
            menu.addAction(act_remove_unused)

        if diag is not None and unresolved_tdoc_symbol:
            act_add_tdoc_symbol = QAction(f"Add '{unresolved_tdoc_symbol}' to .tdocproject", self)
            act_add_tdoc_symbol.triggered.connect(lambda: self._emit_add_tdoc_symbol(diag))
            menu.addAction(act_add_tdoc_symbol)

        if diag is not None and tdoc_section_to_capitalize:
            act_cap_section = QAction(
                f"Capitalize section '{tdoc_section_to_capitalize}'",
                self,
            )
            act_cap_section.triggered.connect(lambda: self._emit_capitalize_tdoc_section(diag))
            menu.addAction(act_cap_section)

        menu.addSeparator()

        act_clear_file = QAction("Clear File Diagnostics", self)
        act_clear_file.setEnabled(diag is not None)
        act_clear_file.triggered.connect(lambda: self._emit_clear_file(diag))
        menu.addAction(act_clear_file)

        act_clear_all = QAction("Clear All Diagnostics", self)
        act_clear_all.triggered.connect(self.clearAllRequested.emit)
        menu.addAction(act_clear_all)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_diag_message(self, diag: Optional[dict]):
        if not isinstance(diag, dict):
            return
        text = str(diag.get("message") or "")
        if not text:
            return
        QApplication.clipboard().setText(text)

    def _copy_diag_file_line(self, diag: Optional[dict]):
        if not isinstance(diag, dict):
            return
        file_path = str(diag.get("file_path") or "")
        if not file_path:
            return
        line = int(diag.get("line") or 1)
        QApplication.clipboard().setText(f"{file_path}:{line}")

    def _emit_clear_file(self, diag: Optional[dict]):
        if not isinstance(diag, dict):
            return
        file_path = str(diag.get("file_path") or "")
        if not file_path:
            return
        self.clearFileRequested.emit(file_path)

    def _emit_import_symbol(self, diag: Optional[dict]):
        if not isinstance(diag, dict):
            return
        if not self._missing_symbol_from_diag(diag):
            return
        self.importSymbolRequested.emit(diag)

    def _emit_remove_unused_import(self, diag: Optional[dict]):
        if not isinstance(diag, dict):
            return
        if not self._is_unused_import_diag(diag):
            return
        self.removeUnusedImportRequested.emit(diag)

    def _emit_add_tdoc_symbol(self, diag: Optional[dict]):
        if not isinstance(diag, dict):
            return
        if not self._tdoc_unresolved_symbol_from_diag(diag):
            return
        self.addTdocSymbolRequested.emit(diag)

    def _emit_capitalize_tdoc_section(self, diag: Optional[dict]):
        if not isinstance(diag, dict):
            return
        if not self._tdoc_section_cap_warning_from_diag(diag):
            return
        self.capitalizeTdocSectionRequested.emit(diag)

    def _missing_symbol_from_diag(self, diag: Optional[dict]) -> str:
        if not isinstance(diag, dict):
            return ""
        message = str(diag.get("message") or "").strip()
        if not message:
            return ""

        patterns = (
            r"(?:Undefined|undefined)\s+name\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)[`'\"]?",
            r"name\s+[`'\"]([A-Za-z_][A-Za-z0-9_]*)[`'\"]\s+is\s+not\s+defined",
        )
        for pattern in patterns:
            m = re.search(pattern, message)
            if m:
                return str(m.group(1) or "").strip()
        return ""

    def _is_unused_import_diag(self, diag: Optional[dict]) -> bool:
        if not isinstance(diag, dict):
            return False
        code = str(diag.get("code") or "").strip().upper()
        if code in {"F401", "W0611"}:
            return True
        message = str(diag.get("message") or "").strip().lower()
        return ("imported but unused" in message) or ("unused import" in message)

    def _unused_import_name_from_diag(self, diag: Optional[dict]) -> str:
        if not isinstance(diag, dict):
            return ""
        message = str(diag.get("message") or "").strip()
        if not message:
            return ""
        patterns = (
            r"[`'\"]([^`'\"]+)[`'\"]\s+imported\s+but\s+unused",
            r"unused\s+import[:\s]+[`'\"]?([^`'\":]+)[`'\"]?",
        )
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return str(match.group(1) or "").strip()
        return ""

    def _tdoc_unresolved_symbol_from_diag(self, diag: Optional[dict]) -> str:
        if not isinstance(diag, dict):
            return ""
        source = str(diag.get("source") or "").strip().lower()
        if source != "tdoc":
            return ""
        message = str(diag.get("message") or "").strip()
        if not message:
            return ""
        match = re.search(r"^Unresolved symbol\s+['\"](.+?)['\"]\s+used at\b", message, flags=re.IGNORECASE)
        if not match:
            return ""
        return str(match.group(1) or "").strip()

    def _tdoc_section_cap_warning_from_diag(self, diag: Optional[dict]) -> str:
        if not isinstance(diag, dict):
            return ""
        source = str(diag.get("source") or "").strip().lower()
        if source != "tdoc":
            return ""
        message = str(diag.get("message") or "").strip()
        if not message:
            return ""
        match = re.search(
            r"^Section header\s+['\"](.+?)['\"]\s+should begin with a capital letter\.$",
            message,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return str(match.group(1) or "").strip()

    def _severity_rank(self, severity: str) -> int:
        s = severity.lower()
        if s == "error":
            return 3
        if s == "warning":
            return 2
        return 1
