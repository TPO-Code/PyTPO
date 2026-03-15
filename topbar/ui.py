from __future__ import annotations

import logging

from PySide6.QtCore import QDateTime, Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .constants import NOTIFICATIONS_SERVICE, WATCHER_SERVICES
from .dbus import launch_background_command, run_logout_command
from .notifications import NotificationCenter, NotificationCenterButton, NotificationServer
from .tray import StatusNotifierTrayArea, StatusNotifierWatcher, X11TraySelectionManager

LOGGER = logging.getLogger("topbar.ui")


class StartupStatusDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("PyTPO TopBar")
        self.setModal(False)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("Welcome to the PyTPO TopBar prototype.")
        title.setStyleSheet("color: #f4f4f4; font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("color: #d8d8d8;")
        layout.addWidget(self.summary_label)

        self.details_label = QLabel()
        self.details_label.setWordWrap(True)
        self.details_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details_label.setStyleSheet(
            "color: #efefef; background: #494949; border: 1px solid #676767; padding: 10px; border-radius: 8px;"
        )
        layout.addWidget(self.details_label)

        close_button = QPushButton("Continue")
        close_button.clicked.connect(self.accept)
        close_button.setStyleSheet(
            "QPushButton { background: #707070; color: white; border: 1px solid #8a8a8a; "
            "border-radius: 6px; padding: 7px 16px; } "
            "QPushButton:hover { background: #7b7b7b; }"
        )
        layout.addWidget(close_button, alignment=Qt.AlignRight)

        self.setStyleSheet("background: #3f3f3f;")

    def set_status(self, summary: str, details: str) -> None:
        self.summary_label.setText(summary)
        self.details_label.setText(details)


class TopBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.notification_center = NotificationCenter(self)
        self.notification_server = NotificationServer(self.notification_center, self)
        self.status_notifier_watcher = StatusNotifierWatcher(self)
        self.x11_tray_selection_manager = X11TraySelectionManager(self, self)
        self._startup_dialog = StartupStatusDialog(self)
        self._startup_dialog_shown = False

        screen = QApplication.primaryScreen()
        width = screen.geometry().width() if screen else 1200
        self.setGeometry(0, 0, width, 35)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 15, 0)

        self.workspaces_label = QLabel("Workspaces: 1 2 3")
        self.workspaces_label.setStyleSheet("color: #f1f1f1; font-weight: 600;")
        layout.addWidget(self.workspaces_label, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        layout.addStretch(1)

        self.tray_area = StatusNotifierTrayArea(self.status_notifier_watcher, self.x11_tray_selection_manager, self)
        layout.addWidget(self.tray_area, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self.notifications_button = NotificationCenterButton(self.notification_center, self.notification_server, self)
        layout.addWidget(self.notifications_button, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self.menu_button = QToolButton(self)
        self.menu_button.setText("Menu")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setCursor(Qt.PointingHandCursor)
        self.menu_button.setFocusPolicy(Qt.NoFocus)
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        self.menu_button.setStyleSheet(
            """
            QToolButton {
                background: transparent;
                color: #f4f4f4;
                border: 1px solid #6f6f6f;
                border-radius: 6px;
                padding: 4px 10px;
                margin-left: 8px;
            }
            QToolButton:hover { background: #6a6a6a; }
            """
        )
        self.menu_button.setMenu(self._build_menu())
        layout.addWidget(self.menu_button, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self.clock_label = QLabel()
        self.clock_label.setStyleSheet("color: #f1f1f1; margin-left: 10px;")
        layout.addWidget(self.clock_label, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_runtime_status)
        self._status_timer.start(3000)

        self.status_notifier_watcher.itemsChanged.connect(self._refresh_runtime_status)
        self.notification_center.notificationsChanged.connect(self._refresh_runtime_status)

        self.setStyleSheet("background: #5b5b5b; border-bottom: 1px solid #3d3d3d;")
        self._last_status_text = ""
        self._refresh_runtime_status()
        QTimer.singleShot(0, self._claim_x11_tray_selection)

    @Slot()
    def _update_clock(self) -> None:
        self.clock_label.setText(QDateTime.currentDateTime().toString("h:mm:ss AP"))

    @Slot()
    def _claim_x11_tray_selection(self) -> None:
        self.x11_tray_selection_manager.claim()
        self.tray_area.sync_items()
        self._refresh_runtime_status()

    def _refresh_runtime_status(self) -> None:
        notify_status = (
            f"Notify: active ({NOTIFICATIONS_SERVICE} at /org/freedesktop/Notifications)"
            if self.notification_server.is_active
            else f"Notify: {self.notification_server.last_error or 'inactive'}"
        )
        watcher_status = (
            f"Watcher: active ({', '.join(self.status_notifier_watcher._service_names or WATCHER_SERVICES)})"
            if self.status_notifier_watcher.is_active
            else f"Watcher: {self.status_notifier_watcher.last_error or 'inactive'}"
        )
        x11_status = (
            f"X11 tray: owner of {self.x11_tray_selection_manager.selection_name or '_NET_SYSTEM_TRAY_S0'}"
            if self.x11_tray_selection_manager.is_owner
            else f"X11 tray: {self.x11_tray_selection_manager.last_error or 'inactive'}"
        )
        status_text = " | ".join((notify_status, watcher_status, x11_status))
        if status_text != self._last_status_text:
            LOGGER.info(status_text)
            self._last_status_text = status_text
        self.setToolTip(status_text)
        self._startup_dialog.set_status(
            "This bar is running as a standalone prototype. The details below show notification, watcher, and X11 tray registration status.",
            status_text,
        )
        if not self._startup_dialog_shown:
            self._startup_dialog_shown = True
            self._startup_dialog.show()
            self._startup_dialog.raise_()
            self._startup_dialog.activateWindow()

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu { background: #4a4a4a; color: #f2f2f2; border: 1px solid #686868; }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background: #666666; }
            """
        )

        show_status_action = menu.addAction("Show Welcome Dialog")
        show_status_action.triggered.connect(self._show_startup_dialog)

        terminal_action = menu.addAction("Open Terminal")
        terminal_action.triggered.connect(self._open_terminal)

        dock_action = menu.addAction("Open Dock Panel")
        dock_action.triggered.connect(self._open_dock_panel)

        menu.addSeparator()

        logout_action = menu.addAction("Logout")
        logout_action.triggered.connect(self._logout_session)

        menu.addSeparator()

        quit_action = menu.addAction("Quit TopBar")
        quit_action.triggered.connect(QApplication.instance().quit)
        return menu

    @Slot()
    def _show_startup_dialog(self) -> None:
        self._startup_dialog.show()
        self._startup_dialog.raise_()
        self._startup_dialog.activateWindow()

    @Slot()
    def _logout_session(self) -> None:
        ok, message = run_logout_command()
        if ok:
            LOGGER.info("Logout command executed: %s", message)
            return
        QMessageBox.warning(self, "Logout Failed", f"Could not log out of the current session.\n\n{message}")

    @Slot()
    def _open_terminal(self) -> None:
        ok, message = launch_background_command("pytpo-terminal")
        if ok:
            LOGGER.info("Launched terminal via %s", message)
            return
        QMessageBox.warning(self, "Launch Failed", f"Could not start the terminal.\n\n{message}")

    @Slot()
    def _open_dock_panel(self) -> None:
        ok, message = launch_background_command("pytpo-dock")
        if ok:
            LOGGER.info("Launched dock panel via %s", message)
            return
        QMessageBox.warning(self, "Launch Failed", f"Could not start the dock panel.\n\n{message}")
