from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.custom_dialog import DialogWindow
from src.ui.dialogs.file_dialog_bridge import get_open_file_name
from src.ui.interpreter_utils import (
    discover_project_interpreters,
    interpreter_browse_directory_hint,
    normalize_interpreter_for_project,
)


class InterpreterPickerDialog(DialogWindow):
    def __init__(
        self,
        *,
        manager: Any,
        project_root: str,
        title: str,
        initial_value: str = "",
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=False, parent=parent)
        self._manager = manager
        self._project_root = str(project_root or "")
        self.setWindowTitle(str(title or "Select Python Interpreter"))
        self.resize(760, 180)
        self._detected_values: list[str] = []

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        hint = QLabel("Choose a Python interpreter, or browse to one.")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row_host = QWidget(host)
        row = QHBoxLayout(row_host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.interpreter_combo = QComboBox(row_host)
        self.interpreter_combo.setEditable(True)
        self.interpreter_combo.setInsertPolicy(QComboBox.NoInsert)
        self.interpreter_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.interpreter_combo.setMinimumContentsLength(32)
        row.addWidget(self.interpreter_combo, 1)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_detected_interpreters)
        row.addWidget(btn_refresh)

        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self._browse_interpreter)
        row.addWidget(btn_browse)

        root.addWidget(row_host)

        detected_host = QWidget(host)
        detected_row = QHBoxLayout(detected_host)
        detected_row.setContentsMargins(0, 0, 0, 0)
        detected_row.setSpacing(6)
        detected_row.addWidget(QLabel("Detected in project:"))
        self.detected_combo = QComboBox(detected_host)
        self.detected_combo.setEditable(False)
        detected_row.addWidget(self.detected_combo, 1)
        btn_use_detected = QPushButton("Use")
        btn_use_detected.clicked.connect(self._use_selected_detected)
        detected_row.addWidget(btn_use_detected)
        root.addWidget(detected_host)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=host)
        self._buttons.accepted.connect(self._accept_if_valid)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._refresh_detected_interpreters(preferred=initial_value)

    def interpreter_value(self) -> str:
        return normalize_interpreter_for_project(
            str(self.interpreter_combo.currentText() or "").strip(),
            self._project_root,
        )

    def _refresh_detected_interpreters(self, *, preferred: str | None = None) -> None:
        current = str(preferred if preferred is not None else self.interpreter_value()).strip()
        detected = discover_project_interpreters(self._project_root)
        self._detected_values = list(detected)

        self.detected_combo.blockSignals(True)
        self.detected_combo.clear()
        if detected:
            self.detected_combo.addItems(detected)
        else:
            self.detected_combo.addItem("(no project interpreters found)")
        self.detected_combo.setEnabled(bool(detected))
        self.detected_combo.blockSignals(False)

        options = list(detected)
        for fallback in ("python", "python3"):
            if fallback not in options:
                options.append(fallback)
        if current and current not in options:
            options.insert(0, current)

        self.interpreter_combo.blockSignals(True)
        self.interpreter_combo.clear()
        self.interpreter_combo.addItems(options)
        self.interpreter_combo.setEditText(current)
        self.interpreter_combo.blockSignals(False)

    def _use_selected_detected(self) -> None:
        if not self._detected_values:
            return
        idx = int(self.detected_combo.currentIndex())
        if idx < 0 or idx >= len(self._detected_values):
            return
        selected = str(self._detected_values[idx] or "").strip()
        if not selected:
            return
        self.interpreter_combo.setEditText(selected)

    def _browse_interpreter(self) -> None:
        selected, _selected_filter = get_open_file_name(
            parent=self,
            manager=self._manager,
            caption="Select Python Interpreter",
            directory=interpreter_browse_directory_hint(self.interpreter_value(), self._project_root),
            file_filter="All Files (*)",
        )
        if not selected:
            return
        normalized = normalize_interpreter_for_project(selected, self._project_root)
        self._refresh_detected_interpreters(preferred=normalized)

    def _accept_if_valid(self) -> None:
        value = self.interpreter_value()
        if not value:
            self.interpreter_combo.setFocus(Qt.OtherFocusReason)
            return
        self.accept()

    @classmethod
    def pick_interpreter(
        cls,
        *,
        manager: Any,
        project_root: str,
        title: str,
        initial_value: str = "",
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> tuple[str, bool]:
        dialog = cls(
            manager=manager,
            project_root=project_root,
            title=title,
            initial_value=initial_value,
            use_native_chrome=use_native_chrome,
            parent=parent,
        )
        if dialog.exec() != QDialog.Accepted:
            return "", False
        return dialog.interpreter_value(), True
