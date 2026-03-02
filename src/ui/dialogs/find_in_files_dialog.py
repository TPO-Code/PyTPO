from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.custom_dialog import DialogWindow
from src.ui.widgets.find_in_files_results import FindInFilesResultsWidget


class FindInFilesDialog(DialogWindow):
    findRequested = Signal(dict)
    replaceRequested = Signal(dict)
    addDockRequested = Signal(dict)

    def __init__(self, parent=None, use_native_chrome: bool = False):
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Find in Files")
        self.resize(920, 560)

        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("Find text...")
        self.replace_edit = QLineEdit(self)
        self.replace_edit.setPlaceholderText("Replace with...")

        self.case_sensitive = QCheckBox("Case sensitive", self)
        self.whole_word = QCheckBox("Whole word", self)
        self.use_regex = QCheckBox("Use regex", self)

        self.btn_find = QPushButton("Find", self)
        self.btn_replace_all = QPushButton("Replace All", self)
        self.btn_add_dock = QPushButton("Add Results Dock", self)
        self.btn_close = QPushButton("Close", self)

        self.results_widget = FindInFilesResultsWidget(self)

        host = QWidget(self)
        self.set_content_widget(host)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addWidget(QLabel("Find:"), 0, 0)
        form.addWidget(self.find_edit, 0, 1, 1, 5)
        form.addWidget(QLabel("Replace:"), 1, 0)
        form.addWidget(self.replace_edit, 1, 1, 1, 5)
        form.addWidget(self.case_sensitive, 2, 1)
        form.addWidget(self.whole_word, 2, 2)
        form.addWidget(self.use_regex, 2, 3)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addWidget(self.btn_find)
        button_row.addWidget(self.btn_replace_all)
        button_row.addWidget(self.btn_add_dock)
        button_row.addStretch(1)
        button_row.addWidget(self.btn_close)

        layout = QVBoxLayout(host)
        layout.addLayout(form)
        layout.addLayout(button_row)
        layout.addWidget(self.results_widget, 1)

        self.btn_find.clicked.connect(self._emit_find_requested)
        self.btn_replace_all.clicked.connect(self._emit_replace_requested)
        self.btn_add_dock.clicked.connect(self._emit_add_dock_requested)
        self.btn_close.clicked.connect(self.close)
        self.find_edit.returnPressed.connect(self._emit_find_requested)

    def request_payload(self) -> dict:
        return {
            "query": str(self.find_edit.text() or ""),
            "replace_text": str(self.replace_edit.text() or ""),
            "case_sensitive": bool(self.case_sensitive.isChecked()),
            "whole_word": bool(self.whole_word.isChecked()),
            "use_regex": bool(self.use_regex.isChecked()),
        }

    def set_results(self, results_obj: object, *, summary_text: str = "") -> None:
        self.results_widget.set_results(results_obj, summary_text=summary_text)

    def set_find_text_if_empty(self, text: str) -> None:
        if str(self.find_edit.text() or "").strip():
            return
        candidate = str(text or "").strip()
        if not candidate:
            return
        self.find_edit.setText(candidate)
        self.find_edit.selectAll()

    def _emit_find_requested(self) -> None:
        self.findRequested.emit(self.request_payload())

    def _emit_replace_requested(self) -> None:
        self.replaceRequested.emit(self.request_payload())

    def _emit_add_dock_requested(self) -> None:
        payload = self.request_payload()
        payload["results"] = self.results_widget.results_payload()
        payload["summary_text"] = self.results_widget.summary_text()
        self.addDockRequested.emit(payload)
