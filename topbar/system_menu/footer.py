from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..dbus import run_logout_command, run_poweroff_command, run_shutdown_command, run_suspend_command


class FooterSection(QWidget):
    def __init__(
        self,
        *,
        open_terminal: Callable[[], None],
        open_dock: Callable[[], None],
        close_panel: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._open_terminal = open_terminal
        self._open_dock = open_dock
        self._close_panel = close_panel

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        actions_title = QLabel("Quick Actions", self)
        actions_title.setObjectName("systemMenuSectionTitle")
        root.addWidget(actions_title)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        root.addLayout(actions_row)

        self.terminal_button = QPushButton("Terminal", self)
        self.terminal_button.clicked.connect(lambda: self._invoke_and_close(self._open_terminal))
        actions_row.addWidget(self.terminal_button)

        self.dock_button = QPushButton("Dock", self)
        self.dock_button.clicked.connect(lambda: self._invoke_and_close(self._open_dock))
        actions_row.addWidget(self.dock_button)

        self.power_button = QToolButton(self)
        self.power_button.setText("Power")
        self.power_button.setPopupMode(QToolButton.InstantPopup)
        self.power_menu = QMenu(self)
        self.power_button.setMenu(self.power_menu)
        self._populate_power_menu()
        actions_row.addWidget(self.power_button)

    def _invoke_and_close(self, callback: Callable[[], None]) -> None:
        self._close_panel()
        callback()

    def _populate_power_menu(self) -> None:
        self.power_menu.clear()

        suspend_action = QAction("Suspend", self)
        suspend_action.triggered.connect(lambda checked=False: self._run_session_action("Suspend", run_suspend_command))
        self.power_menu.addAction(suspend_action)

        poweroff_action = QAction("Power Off", self)
        poweroff_action.triggered.connect(lambda checked=False: self._run_session_action("Power Off", run_poweroff_command))
        self.power_menu.addAction(poweroff_action)

        shutdown_action = QAction("Shutdown", self)
        shutdown_action.triggered.connect(lambda checked=False: self._run_session_action("Shutdown", run_shutdown_command))
        self.power_menu.addAction(shutdown_action)

        self.power_menu.addSeparator()

        logout_action = QAction("Log Out", self)
        logout_action.triggered.connect(lambda checked=False: self._run_session_action("Log Out", run_logout_command))
        self.power_menu.addAction(logout_action)

    def _run_session_action(self, label: str, callback: Callable[[], tuple[bool, str]]) -> None:
        self._close_panel()
        ok, message = callback()
        if ok:
            return
        QMessageBox.warning(self, f"{label} Failed", f"Could not {label.lower()}.\n\n{message}")
