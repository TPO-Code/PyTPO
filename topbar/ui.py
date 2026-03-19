from __future__ import annotations

import logging
import time

from PySide6.QtCore import QDateTime, Qt, QTimer, Slot, QEvent, QDate
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QWidget,
)

from .constants import NOTIFICATIONS_SERVICE, WATCHER_SERVICES
from .dbus import launch_background_command, load_xlib
from .focus import X11FocusController
from .notifications import NotificationCenter, NotificationCenterButton, NotificationServer
from .system_menu import SystemMenuButton
from .tray import StatusNotifierTrayArea, StatusNotifierWatcher, X11TraySelectionManager

from PySide6.QtWidgets import QPushButton, QToolTip
from topbar.calendar_popup import CalendarPopup

LOGGER = logging.getLogger("topbar.ui")


class TopBar(QWidget):
    def __init__(self):
        super().__init__()
        startup_started = time.perf_counter()
        self._x11_panel_hints_applied = False
        self._auto_hide_enabled = False
        self._is_hidden_to_edge = False
        self._visible_reserve_height = 0  # optional override
        dock_attribute = getattr(Qt.WidgetAttribute, "WA_X11NetWmWindowTypeDock", None)
        if dock_attribute is not None:
            self.setAttribute(dock_attribute, True)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        center_started = time.perf_counter()
        self.notification_center = NotificationCenter(self)
        LOGGER.info("startup timing: NotificationCenter initialized in %.1f ms", (time.perf_counter() - center_started) * 1000.0)

        server_started = time.perf_counter()
        self.notification_server = NotificationServer(self.notification_center, self)
        LOGGER.info("startup timing: NotificationServer initialized in %.1f ms", (time.perf_counter() - server_started) * 1000.0)

        watcher_started = time.perf_counter()
        self.status_notifier_watcher = StatusNotifierWatcher(self)
        LOGGER.info(
            "startup timing: StatusNotifierWatcher initialized in %.1f ms",
            (time.perf_counter() - watcher_started) * 1000.0,
        )

        tray_selection_started = time.perf_counter()
        self.x11_tray_selection_manager = X11TraySelectionManager(self, self)
        LOGGER.info(
            "startup timing: X11TraySelectionManager initialized in %.1f ms",
            (time.perf_counter() - tray_selection_started) * 1000.0,
        )

        screen = QApplication.primaryScreen()
        width = screen.geometry().width() if screen else 1200
        self.setGeometry(0, 0, width, 35)
        self.focus_controller = X11FocusController(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 15, 0)

        self.workspaces_label = QLabel("Workspaces: 1 2 3")
        self.workspaces_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.workspaces_label.setStyleSheet("color: #f1f1f1; font-weight: 600;")
        layout.addWidget(self.workspaces_label, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        layout.addStretch(1)

        tray_area_started = time.perf_counter()
        self.tray_area = StatusNotifierTrayArea(
            self.status_notifier_watcher,
            self.x11_tray_selection_manager,
            self.focus_controller,
            self,
        )
        LOGGER.info("startup timing: StatusNotifierTrayArea initialized in %.1f ms", (time.perf_counter() - tray_area_started) * 1000.0)
        layout.addWidget(self.tray_area, alignment=Qt.AlignRight | Qt.AlignVCenter)

        notifications_button_started = time.perf_counter()
        self.notifications_button = NotificationCenterButton(self.notification_center, self.notification_server, self)
        LOGGER.info(
            "startup timing: NotificationCenterButton initialized in %.1f ms",
            (time.perf_counter() - notifications_button_started) * 1000.0,
        )
        layout.addWidget(self.notifications_button, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self.menu_button = SystemMenuButton(
            open_terminal=self._open_terminal,
            open_dock=self._open_dock_panel,
            focus_controller=self.focus_controller,
            parent=self,
        )
        layout.addWidget(self.menu_button, alignment=Qt.AlignRight | Qt.AlignVCenter)
## clock
        self.clock_btn = QPushButton()
        self.clock_btn.setStyleSheet("color: #f1f1f1; margin-left: 5x; border: 0px")
        self.clock_btn.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        layout.addWidget(self.clock_btn, alignment=Qt.AlignRight | Qt.AlignVCenter)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_runtime_status)
        self._status_timer.start(3000)
        self.clock_btn.installEventFilter(self)
        self.clock_btn.clicked.connect(self.show_calendar)

        self.calendar_popup = CalendarPopup(self)
        self.calendar_popup.calendar.clicked.connect(self.handle_date_selected)
        self.calendar_popup.popupHidden.connect(self._restore_previous_focus)
        self.update_date_tooltip()
## Connections
        self.status_notifier_watcher.itemsChanged.connect(self._refresh_runtime_status)
        self.notification_center.notificationsChanged.connect(self._refresh_runtime_status)

        self.setStyleSheet("background: #5b5b5b; border-bottom: 0px")
        self._last_status_text = ""
        self._refresh_runtime_status()
        QTimer.singleShot(0, self._claim_x11_tray_selection)
        LOGGER.info("startup timing: TopBar.__init__ completed in %.1f ms", (time.perf_counter() - startup_started) * 1000.0)

    @Slot()
    def _update_clock(self) -> None:
        self.clock_btn.setText(QDateTime.currentDateTime().toString("h:mm:ss AP"))
        self.update_date_tooltip()
    
    def update_date_tooltip(self):
        """Always show the real current date."""
        self.clock_btn.setToolTip(QDate.currentDate().toString("dd/MM/yyyy"))
        

    def eventFilter(self, obj, event):
        if obj == self.clock_btn:
            if event.type() == QEvent.Type.Enter:
                self.update_date_tooltip()
                rect = self.clock_btn.rect()
                pos = self.clock_btn.mapToGlobal(rect.center())
                QToolTip.showText(pos, self.clock_btn.toolTip(), self.clock_btn, rect)
            elif event.type() == QEvent.Type.Leave:
                QToolTip.hideText()
        return super().eventFilter(obj, event)

    def handle_date_selected(self, date: QDate):
        # Selection is temporary only. Do not store it.
        print(f"User picked: {date.toString('dd/MM/yyyy')}")
        self.calendar_popup.hide()

    def show_calendar(self):
        # Reset fully every time it opens.
        self.calendar_popup.reset_for_open()

        popup_size = self.calendar_popup.sizeHint()
        button_rect = self.clock_btn.rect()
        global_bottom_right = self.clock_btn.mapToGlobal(button_rect.bottomRight())

        x_pos = global_bottom_right.x() - popup_size.width()
        y_pos = global_bottom_right.y()

        self.calendar_popup.move(x_pos, y_pos)
        self.calendar_popup.show()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._restore_previous_focus()

    def changeEvent(self, event):
        # Keep tooltip fresh if the app regains focus after date rollover.
        if event.type() == QEvent.Type.ActivationChange:
            self.update_date_tooltip()
        super().changeEvent(event)

    @Slot()
    def _restore_previous_focus(self) -> None:
        self.focus_controller.restore_last_external_window_soon(0)
    
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
        self.setToolTip("")

    def showEvent(self, event):
        super().showEvent(event)
        # Wait until the native window + final geometry exist
        QTimer.singleShot(0, self._apply_x11_panel_hints)

    def resizeEvent(self, event):
        super().resizeEvent(event)

        # Reapply after any size change because struts depend on geometry.
        # A singleShot keeps this from fighting Qt during a live resize.
        QTimer.singleShot(0, self._apply_x11_panel_hints)

    def _current_reserved_height(self) -> int:
        """
        Return how much top-edge space should be reserved.

        Full bar:
            reserve self.height()

        Fully auto-hidden:
            reserve 0

        Partial reveal / hover strip:
            reserve that small visible amount instead
        """
        if self._auto_hide_enabled and self._is_hidden_to_edge:
            return self._visible_reserve_height  # often 0, or maybe 1-2 px
        return self.height()

    def _apply_x11_panel_hints(self):
        if not self.isVisible():
            return

        # Only do this on X11
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.platformName().lower() != "xcb":
            return

        wid = int(self.winId())  # ensure native window exists

        reserve_height = max(0, self._current_reserved_height())

        # Geometry in global/root coords
        geom = self.frameGeometry()

        left = geom.left()
        right = geom.right()

        # Top strut: reserve 'reserve_height' pixels from top edge
        top_strut = reserve_height

        # _NET_WM_STRUT values: left, right, top, bottom
        strut = [0, 0, top_strut, 0]

        # _NET_WM_STRUT_PARTIAL values:
        # left, right, top, bottom,
        # left_start_y, left_end_y,
        # right_start_y, right_end_y,
        # top_start_x, top_end_x,
        # bottom_start_x, bottom_end_x
        strut_partial = [
            0, 0, top_strut, 0,
            0, 0,
            0, 0,
            left, right,
            0, 0,
        ]

        self._set_x11_dock_and_strut_properties(wid, strut, strut_partial)
        self._x11_panel_hints_applied = True

    def _set_x11_dock_and_strut_properties(self, wid: int, strut, strut_partial):
        """
        Apply the EWMH panel hints required for the WM to reserve space.
        """
        try:
            X, Xatom, display = load_xlib()
            x_display = display.Display()
        except Exception as exc:
            LOGGER.warning("Could not connect to X11 for panel hints: %r", exc)
            return

        try:
            window = x_display.create_resource_object("window", wid)
            window_type_atom = x_display.intern_atom("_NET_WM_WINDOW_TYPE")
            window_type_dock_atom = x_display.intern_atom("_NET_WM_WINDOW_TYPE_DOCK")
            state_atom = x_display.intern_atom("_NET_WM_STATE")
            state_above_atom = x_display.intern_atom("_NET_WM_STATE_ABOVE")
            state_sticky_atom = x_display.intern_atom("_NET_WM_STATE_STICKY")
            strut_atom = x_display.intern_atom("_NET_WM_STRUT")
            strut_partial_atom = x_display.intern_atom("_NET_WM_STRUT_PARTIAL")

            window.change_property(
                window_type_atom,
                Xatom.ATOM,
                32,
                [window_type_dock_atom],
                X.PropModeReplace,
            )
            window.change_property(
                state_atom,
                Xatom.ATOM,
                32,
                [state_above_atom, state_sticky_atom],
                X.PropModeReplace,
            )
            window.change_property(
                strut_atom,
                Xatom.CARDINAL,
                32,
                [int(value) for value in strut],
                X.PropModeReplace,
            )
            window.change_property(
                strut_partial_atom,
                Xatom.CARDINAL,
                32,
                [int(value) for value in strut_partial],
                X.PropModeReplace,
            )
            x_display.flush()
            x_display.sync()
        except Exception as exc:
            LOGGER.warning("Failed to apply X11 dock/strut hints to window %s: %r", wid, exc)
        finally:
            try:
                x_display.close()
            except Exception:
                pass

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
