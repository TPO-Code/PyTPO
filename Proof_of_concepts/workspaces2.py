from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from ewmh import EWMH
except ImportError:
    EWMH = None


class WorkspaceManagerPOC(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("X11 Workspace Manager POC")
        self.resize(560, 160)

        self.ewmh = None
        self._workspace_buttons: list[QPushButton] = []

        self.status_label = QLabel("Starting…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.workspace_row = QHBoxLayout()
        self.workspace_row.setSpacing(8)

        self.add_button = QPushButton("+")
        self.remove_button = QPushButton("−")
        self.refresh_button = QPushButton("Refresh")

        controls = QHBoxLayout()
        controls.addWidget(self.add_button)
        controls.addWidget(self.remove_button)
        controls.addStretch(1)
        controls.addWidget(self.refresh_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(self.workspace_row)
        layout.addLayout(controls)

        if EWMH is None:
            self._show_import_error()
            return

        try:
            self.ewmh = EWMH()
        except Exception as exc:
            self.status_label.setText(f"Failed to connect to X11/EWMH: {exc}")
            return

        self.add_button.clicked.connect(self.add_workspace)
        self.remove_button.clicked.connect(self.remove_workspace)
        self.refresh_button.clicked.connect(self.refresh_workspaces)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(1000)
        self.refresh_timer.timeout.connect(self.refresh_workspaces)
        self.refresh_timer.start()

        self.refresh_workspaces()

    def _show_import_error(self) -> None:
        self.status_label.setText("ewmh is not installed")
        QMessageBox.information(
            self,
            "Missing dependency",
            "Install it with:\n\nuv pip install ewmh",
        )

    def _safe_int(self, value, default: int = 0) -> int:
        try:
            if value is None:
                return default
            if isinstance(value, (list, tuple)) and value:
                return int(value[0])
            return int(value)
        except Exception:
            return default

    def _flush(self) -> None:
        if self.ewmh is not None:
            self.ewmh.display.flush()

    def _read_workspace_state(self) -> tuple[int, int]:
        if self.ewmh is None:
            return 0, 0
        count = self._safe_int(self.ewmh.getNumberOfDesktops(), 0)
        current = self._safe_int(self.ewmh.getCurrentDesktop(), 0)
        return count, current

    def refresh_workspaces(self) -> None:
        if self.ewmh is None:
            return

        count, current = self._read_workspace_state()

        if count <= 0:
            self.status_label.setText("No workspaces reported by the window manager")
            self._rebuild_workspace_buttons(0, 0)
            self.add_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            return

        self.status_label.setText(
            f"Detected {count} workspace(s) | Current: {current + 1}"
        )
        self._rebuild_workspace_buttons(count, current)
        self.add_button.setEnabled(True)
        self.remove_button.setEnabled(count > 1)

    def _clear_workspace_row(self) -> None:
        while self.workspace_row.count():
            item = self.workspace_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._workspace_buttons.clear()

    def _rebuild_workspace_buttons(self, count: int, current: int) -> None:
        if len(self._workspace_buttons) != count:
            self._clear_workspace_row()
            for index in range(count):
                button = QPushButton(str(index + 1))
                button.setCheckable(True)
                button.clicked.connect(
                    lambda checked=False, i=index: self.switch_workspace(i)
                )
                self.workspace_row.addWidget(button)
                self._workspace_buttons.append(button)

        for index, button in enumerate(self._workspace_buttons):
            is_current = index == current
            button.setChecked(is_current)
            button.setText(f"[{index + 1}]" if is_current else str(index + 1))

    def switch_workspace(self, index: int) -> None:
        if self.ewmh is None:
            return

        try:
            self.ewmh.setCurrentDesktop(index)
            self._flush()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Workspace switch failed",
                f"Could not switch to workspace {index + 1}:\n{exc}",
            )
            return

        QTimer.singleShot(120, self.refresh_workspaces)

    def add_workspace(self) -> None:
        if self.ewmh is None:
            return

        before_count, _before_current = self._read_workspace_state()
        requested = max(1, before_count + 1)

        try:
            self.ewmh.setNumberOfDesktops(requested)
            self._flush()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Add workspace failed",
                f"Could not request {requested} workspaces:\n{exc}",
            )
            return

        QTimer.singleShot(180, lambda: self._finish_resize_check(before_count, requested, "add"))

    def remove_workspace(self) -> None:
        if self.ewmh is None:
            return

        before_count, _before_current = self._read_workspace_state()
        if before_count <= 1:
            self.status_label.setText("Refusing to go below 1 workspace")
            return

        requested = before_count - 1

        try:
            self.ewmh.setNumberOfDesktops(requested)
            self._flush()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Remove workspace failed",
                f"Could not request {requested} workspaces:\n{exc}",
            )
            return

        QTimer.singleShot(180, lambda: self._finish_resize_check(before_count, requested, "remove"))

    def _finish_resize_check(self, before_count: int, requested: int, verb: str) -> None:
        after_count, current = self._read_workspace_state()
        self.refresh_workspaces()

        if after_count == requested:
            if verb == "add":
                self.status_label.setText(
                    f"Workspace add accepted | Total: {after_count} | Current: {current + 1}"
                )
            else:
                self.status_label.setText(
                    f"Workspace removal accepted | Total: {after_count} | Current: {current + 1}"
                )
            return

        self.status_label.setText(
            f"WM ignored request: wanted {requested}, still have {after_count}"
        )


def main() -> int:
    app = QApplication(sys.argv)
    window = WorkspaceManagerPOC()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
