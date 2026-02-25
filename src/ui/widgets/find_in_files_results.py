from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class FindInFilesResultsWidget(QWidget):
    resultPreviewRequested = Signal(str, int, int)
    resultActivated = Signal(str, int, int)

    COLUMNS = ["File", "Row", "Column", "Preview"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

        self.status_label = QLabel("No search results.", self)

        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.itemSelectionChanged.connect(self._emit_preview_for_current)
        self.table.doubleClicked.connect(self._activate_current_row)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.status_label, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.table)

    def clear_results(self) -> None:
        self._rows = []
        self._render_rows()
        self.status_label.setText("No search results.")

    def set_results(self, results_obj: object, *, summary_text: str = "") -> None:
        rows: list[dict] = []
        if isinstance(results_obj, list):
            for item in results_obj:
                if not isinstance(item, dict):
                    continue
                file_path = str(item.get("file_path") or "").strip()
                if not file_path:
                    continue
                line = max(1, int(item.get("line") or 1))
                col = max(1, int(item.get("column") or 1))
                preview = str(item.get("preview") or "")
                rows.append(
                    {
                        "file_path": file_path,
                        "line": line,
                        "column": col,
                        "preview": preview,
                    }
                )

        rows.sort(
            key=lambda d: (
                str(d.get("file_path") or ""),
                int(d.get("line") or 1),
                int(d.get("column") or 1),
            )
        )
        self._rows = rows
        self._render_rows()

        summary = str(summary_text or "").strip()
        if not summary:
            summary = f"{len(self._rows)} match(es)."
        self.status_label.setText(summary)

    def results_payload(self) -> list[dict]:
        return [dict(row) for row in self._rows]

    def summary_text(self) -> str:
        return str(self.status_label.text() or "").strip()

    def _render_rows(self) -> None:
        self.table.setRowCount(len(self._rows))
        for row_index, result in enumerate(self._rows):
            file_path = str(result.get("file_path") or "")
            line = max(1, int(result.get("line") or 1))
            col = max(1, int(result.get("column") or 1))
            preview = str(result.get("preview") or "")

            values = [file_path, str(line), str(col), preview]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, result)
                if col_index in (1, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, col_index, item)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _result_for_row(self, row: int) -> dict | None:
        if row < 0 or row >= self.table.rowCount():
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def _emit_preview_for_current(self) -> None:
        row = self.table.currentRow()
        result = self._result_for_row(row)
        if not isinstance(result, dict):
            return
        self.resultPreviewRequested.emit(
            str(result.get("file_path") or ""),
            int(result.get("line") or 1),
            int(result.get("column") or 1),
        )

    def _activate_current_row(self, *_args) -> None:
        row = self.table.currentRow()
        result = self._result_for_row(row)
        if not isinstance(result, dict):
            return
        self.resultActivated.emit(
            str(result.get("file_path") or ""),
            int(result.get("line") or 1),
            int(result.get("column") or 1),
        )
