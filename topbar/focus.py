from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtGui import QGuiApplication

from .dbus import load_xlib

LOGGER = logging.getLogger("topbar.focus")


class X11FocusController(QObject):
    def __init__(self, parent: QObject | None = None, *, poll_interval_ms: int = 300) -> None:
        super().__init__(parent)
        self._own_pid = os.getpid()
        self._last_external_window_id = 0
        self._xlib_checked = False
        self._xlib_available = False

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(max(120, int(poll_interval_ms)))
        self._poll_timer.timeout.connect(self._poll_active_window)
        self._poll_timer.start()
        QTimer.singleShot(0, self._poll_active_window)

    @property
    def last_external_window_id(self) -> int:
        return int(self._last_external_window_id or 0)

    def restore_last_external_window_soon(self, delay_ms: int = 0) -> None:
        QTimer.singleShot(max(0, int(delay_ms)), self.restore_last_external_window)

    @Slot()
    def restore_last_external_window(self) -> bool:
        target_window_id = int(self._last_external_window_id or 0)
        if target_window_id <= 0 or not self._supports_x11_focus():
            return False

        current_window_id = self._active_window_id()
        if current_window_id == target_window_id:
            return True

        current_pid = self._window_pid(current_window_id)
        if current_window_id > 0 and current_pid not in (0, self._own_pid):
            return False

        return self._activate_window(target_window_id)

    @Slot()
    def _poll_active_window(self) -> None:
        if not self._supports_x11_focus():
            return
        active_window_id = self._active_window_id()
        if active_window_id <= 0:
            return
        active_pid = self._window_pid(active_window_id)
        if active_pid > 0 and active_pid != self._own_pid:
            self._last_external_window_id = active_window_id

    def _supports_x11_focus(self) -> bool:
        if QGuiApplication.platformName().lower() != "xcb":
            return False
        if self._xlib_checked:
            return self._xlib_available
        self._xlib_checked = True
        try:
            load_xlib()
        except Exception as exc:
            self._xlib_available = False
            LOGGER.warning("X11 focus restore disabled: %r", exc)
            return False
        self._xlib_available = True
        return True

    def _active_window_id(self) -> int:
        try:
            X, _Xatom, display = load_xlib()
            x_display = display.Display()
        except Exception:
            return 0

        try:
            root = x_display.screen().root
            active_atom = x_display.intern_atom("_NET_ACTIVE_WINDOW")
            prop = root.get_full_property(active_atom, X.AnyPropertyType)
            if prop is None or not getattr(prop, "value", None):
                return 0
            return int(prop.value[0])
        except Exception:
            return 0
        finally:
            try:
                x_display.close()
            except Exception:
                pass

    def _window_pid(self, window_id: int) -> int:
        if window_id <= 0:
            return 0
        try:
            X, _Xatom, display = load_xlib()
            x_display = display.Display()
        except Exception:
            return 0

        try:
            window = x_display.create_resource_object("window", int(window_id))
            pid_atom = x_display.intern_atom("_NET_WM_PID")
            prop = window.get_full_property(pid_atom, X.AnyPropertyType)
            if prop is None or not getattr(prop, "value", None):
                return 0
            return int(prop.value[0])
        except Exception:
            return 0
        finally:
            try:
                x_display.close()
            except Exception:
                pass

    def _activate_window(self, window_id: int) -> bool:
        if window_id <= 0:
            return False
        try:
            X, _Xatom, display = load_xlib()
            x_display = display.Display()
            from Xlib.protocol import event
        except Exception:
            return False

        try:
            root = x_display.screen().root
            active_atom = x_display.intern_atom("_NET_ACTIVE_WINDOW")
            target = x_display.create_resource_object("window", int(window_id))
            message = event.ClientMessage(
                window=target,
                client_type=active_atom,
                data=(32, [1, X.CurrentTime, 0, 0, 0]),
            )
            root.send_event(
                message,
                event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
            )
            x_display.flush()
            x_display.sync()
            return True
        except Exception:
            return False
        finally:
            try:
                x_display.close()
            except Exception:
                pass
