from __future__ import annotations

import sys
from typing import Optional

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


class WorkspaceSwitcher(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("X11 Workspace POC")
        self.resize(420, 120)

        self.ewmh = None
        self._buttons: list[QPushButton] = []

        self.status_label = QLabel("Starting…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.button_row = QHBoxLayout()
        self.button_row.setSpacing(8)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(self.button_row)

        if EWMH is None:
            self._show_import_error()
            return

        try:
            self.ewmh = EWMH()
        except Exception as exc:
            self.status_label.setText(f"Failed to connect to X11/EWMH: {exc}")
            return

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
            "Install pyewmh first:\n\npip install pyewmh",
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

    def refresh_workspaces(self) -> None:
        if self.ewmh is None:
            return

        try:
            count = self._safe_int(self.ewmh.getNumberOfDesktops(), 0)
            current = self._safe_int(self.ewmh.getCurrentDesktop(), 0)
        except Exception as exc:
            self.status_label.setText(f"Workspace query failed: {exc}")
            return

        if count <= 0:
            self.status_label.setText("No workspaces reported by the window manager")
            self._rebuild_buttons(0, 0)
            return

        self.status_label.setText(
            f"Detected {count} workspace(s) | Current: {current + 1}"
        )
        self._rebuild_buttons(count, current)

    def _clear_button_row(self) -> None:
        while self.button_row.count():
            item = self.button_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()

    def _rebuild_buttons(self, count: int, current: int) -> None:
        # Rebuild only if the count changed
        if len(self._buttons) != count:
            self._clear_button_row()
            for index in range(count):
                button = QPushButton(f"{index + 1}")
                button.setCheckable(True)
                button.clicked.connect(
                    lambda checked=False, i=index: self.switch_workspace(i)
                )
                self.button_row.addWidget(button)
                self._buttons.append(button)

        for index, button in enumerate(self._buttons):
            is_current = index == current
            button.setChecked(is_current)
            button.setText(f"[{index + 1}]" if is_current else f"{index + 1}")

    def switch_workspace(self, index: int) -> None:
        if self.ewmh is None:
            return

        try:
            self.ewmh.setCurrentDesktop(index)
            self.ewmh.display.flush()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Workspace switch failed",
                f"Could not switch to workspace {index + 1}:\n{exc}",
            )
            return
        # Refresh soon after switching
        QTimer.singleShot(100, self.refresh_workspaces)


def main() -> int:
    app = QApplication(sys.argv)
    window = WorkspaceSwitcher()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
