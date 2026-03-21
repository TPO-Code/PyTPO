from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon
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

from pytpo.services.asset_paths import preferred_shared_asset_path

from ..dbus import (
    run_logout_command,
    run_poweroff_command,
    run_restart_command,
    run_shutdown_command,
    run_suspend_command,
)
from ..settings import TopBarBehaviorSettings


def _icon_path(name: str) -> str:
    return str(preferred_shared_asset_path(f"icons/{name}"))


class FooterSection(QWidget):
    def __init__(
        self,
        *,
        open_terminal: Callable[[], None],
        open_dock: Callable[[], None],
        open_settings: Callable[[], None],
        close_panel: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._open_terminal = open_terminal
        self._open_dock = open_dock
        self._open_settings = open_settings
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

        self.settings_button = QToolButton(self)
        self.settings_button.setText("Settings")
        self.settings_button.clicked.connect(lambda: self._invoke_and_close(self._open_settings))
        actions_row.addWidget(self.settings_button)

        self.power_button = QToolButton(self)
        self.power_button.setText("Power")
        self.power_button.setPopupMode(QToolButton.InstantPopup)
        self.power_menu = QMenu(self)
        self.power_button.setMenu(self.power_menu)
        self._populate_power_menu()
        actions_row.addWidget(self.power_button)

        self.apply_settings(TopBarBehaviorSettings())

    def apply_settings(self, settings: TopBarBehaviorSettings) -> None:
        icon_size = max(12, int(settings.menu_appearance_item_icon_size))
        size = QSize(icon_size, icon_size)
        self._apply_icon_button(self.settings_button, "Settings", "settings.png", size)
        self._apply_icon_button(self.power_button, "Power", "power.svg", size)

    def _apply_icon_button(self, button: QToolButton, fallback_text: str, icon_name: str, size: QSize) -> None:
        icon = QIcon(_icon_path(icon_name))
        icon_pixmap = icon.pixmap(size)
        if icon_pixmap.isNull():
            button.setIcon(QIcon())
            button.setText(fallback_text)
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            return
        button.setIcon(icon)
        button.setIconSize(size)
        button.setText("")
        button.setToolTip(fallback_text)
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)

    def _invoke_and_close(self, callback: Callable[[], None]) -> None:
        self._close_panel()
        callback()

    def _populate_power_menu(self) -> None:
        self.power_menu.clear()

        suspend_action = QAction("Suspend", self)
        suspend_action.triggered.connect(lambda checked=False: self._run_session_action("Suspend", run_suspend_command))
        self.power_menu.addAction(suspend_action)

        restart_action = QAction("Restart", self)
        restart_action.triggered.connect(lambda checked=False: self._run_session_action("Restart", run_restart_command))
        self.power_menu.addAction(restart_action)

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
