from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QWidget

from .debug import log_dock_debug


@dataclass(frozen=True, slots=True)
class DockStrutReservation:
    strut: tuple[int, int, int, int]
    strut_partial: tuple[int, int, int, int, int, int, int, int, int, int, int, int]


def build_bottom_strut_reservation(
    *,
    window_rect: QRect,
    screen_rect: QRect,
    reserve_space: bool,
) -> DockStrutReservation:
    if not reserve_space or window_rect.isNull() or screen_rect.isNull():
        return DockStrutReservation((0, 0, 0, 0), (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    if not screen_rect.intersects(window_rect):
        return DockStrutReservation((0, 0, 0, 0), (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    reserved_bottom = max(0, screen_rect.bottom() - window_rect.top() + 1)
    start_x = max(screen_rect.left(), window_rect.left())
    end_x = min(screen_rect.right(), window_rect.right())
    if end_x < start_x:
        start_x = screen_rect.left()
        end_x = screen_rect.left()

    return DockStrutReservation(
        (0, 0, 0, reserved_bottom),
        (0, 0, 0, reserved_bottom, 0, 0, 0, 0, 0, 0, start_x, end_x),
    )


class X11DockWindowManager:
    def __init__(self, widget: QWidget) -> None:
        self._widget = widget
        self._last_signature: tuple[Any, ...] | None = None

    def is_supported(self) -> bool:
        app = QApplication.instance()
        platform_name = app.platformName().lower() if app is not None else ""
        return platform_name == "xcb"

    def sync(self, *, reserve_space: bool, window_rect: QRect | None = None) -> None:
        if not self.is_supported():
            return

        native_window_id = self._native_window_id()
        if native_window_id <= 0:
            return

        effective_rect = QRect(window_rect) if window_rect is not None else QRect(self._widget.frameGeometry())
        screen_rect = self._screen_geometry()
        reservation = build_bottom_strut_reservation(
            window_rect=effective_rect,
            screen_rect=screen_rect,
            reserve_space=reserve_space,
        )
        signature = (
            native_window_id,
            reserve_space,
            effective_rect.getRect(),
            screen_rect.getRect(),
            reservation.strut,
            reservation.strut_partial,
        )
        if signature == self._last_signature:
            return

        try:
            self._write_x11_properties(
                native_window_id=native_window_id,
                reservation=reservation,
            )
        except Exception as exc:
            log_dock_debug(
                "dock-x11-window-properties-failed",
                win_id=native_window_id,
                reserve_space=reserve_space,
                error=repr(exc),
            )
            return

        self._last_signature = signature
        log_dock_debug(
            "dock-x11-window-properties-synced",
            win_id=native_window_id,
            reserve_space=reserve_space,
            window_rect=effective_rect.getRect(),
            screen_rect=screen_rect.getRect(),
            strut=reservation.strut,
            strut_partial=reservation.strut_partial,
        )

    def _screen_geometry(self) -> QRect:
        screen = self._widget.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.geometry() if screen is not None else QRect()

    def _native_window_id(self) -> int:
        try:
            return int(self._widget.winId())
        except Exception:
            return 0

    def _write_x11_properties(self, *, native_window_id: int, reservation: DockStrutReservation) -> None:
        X, Xatom, display = _load_xlib()
        x_display = display.Display()
        try:
            window = x_display.create_resource_object("window", native_window_id)

            atom = x_display.intern_atom
            window_type_atom = atom("_NET_WM_WINDOW_TYPE")
            window_type_dock_atom = atom("_NET_WM_WINDOW_TYPE_DOCK")
            state_atom = atom("_NET_WM_STATE")
            skip_taskbar_atom = atom("_NET_WM_STATE_SKIP_TASKBAR")
            skip_pager_atom = atom("_NET_WM_STATE_SKIP_PAGER")
            sticky_atom = atom("_NET_WM_STATE_STICKY")
            desktop_atom = atom("_NET_WM_DESKTOP")
            strut_atom = atom("_NET_WM_STRUT")
            strut_partial_atom = atom("_NET_WM_STRUT_PARTIAL")

            window.change_property(window_type_atom, Xatom.ATOM, 32, [int(window_type_dock_atom)], X.PropModeReplace)
            window.change_property(
                state_atom,
                Xatom.ATOM,
                32,
                [int(skip_taskbar_atom), int(skip_pager_atom), int(sticky_atom)],
                X.PropModeReplace,
            )
            window.change_property(desktop_atom, Xatom.CARDINAL, 32, [0xFFFFFFFF], X.PropModeReplace)
            window.change_property(strut_atom, Xatom.CARDINAL, 32, list(reservation.strut), X.PropModeReplace)
            window.change_property(
                strut_partial_atom,
                Xatom.CARDINAL,
                32,
                list(reservation.strut_partial),
                X.PropModeReplace,
            )
            x_display.flush()
            x_display.sync()
        finally:
            try:
                x_display.close()
            except Exception:
                pass


def _load_xlib():
    try:
        from Xlib import X, Xatom, display
    except Exception as exc:
        raise RuntimeError("python-xlib is required for X11 dock window management.") from exc
    return X, Xatom, display
