from __future__ import annotations

import concurrent.futures
import math
import sys
from typing import Any, Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCursor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pytpo.services.asset_paths import preferred_shared_asset_path

from topbar.focus import X11FocusController

from .connectivity import ConnectivitySection
from .footer import FooterSection
from .media_container import MediaContainer
from .sound import SoundSection, shared_sound_backend
from .service import SoundSnapshot, SystemMenuSnapshot, VolumeService, WifiService, collect_system_menu_snapshot

_WIFI_ICON_NAMES = {
    0: "internet_0.svg",
    1: "internet_1.svg",
    2: "internet_2.svg",
    3: "internet_3.svg",
    4: "internet_4.svg",
}
_VOLUME_ICON_NAMES = {
    "muted": "volume_muted.svg",
    "low": "volume_1.svg",
    "medium": "volume_2.svg",
    "high": "volume_3.svg",
}
_POWER_ICON_NAME = "power.svg"


def _icon_path(name: str) -> str:
    return str(preferred_shared_asset_path(f"icons/{name}"))


class SystemMenuContent(QWidget):
    snapshotApplied = Signal()

    def __init__(
        self,
        *,
        open_terminal: Callable[[], None],
        open_dock: Callable[[], None],
        close_panel: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="topbar-system-menu")
        self._pending: dict[concurrent.futures.Future, str] = {}
        self._refresh_requested_while_busy = False
        self._snapshot: SystemMenuSnapshot | None = None

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self._live_refresh_timer = QTimer(self)
        self._live_refresh_timer.setInterval(1500)
        self._live_refresh_timer.timeout.connect(self.refresh_all)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.connectivity = ConnectivitySection(self, request_refresh=self.refresh_all)
        root.addWidget(self.connectivity)

        self.sound = SoundSection(self, request_refresh=self.refresh_all)
        root.addWidget(self.sound)

        self.media = MediaContainer(self, request_refresh=self.refresh_all)
        root.addWidget(self.media)

        root.addStretch(1)

        self.footer = FooterSection(
            open_terminal=open_terminal,
            open_dock=open_dock,
            close_panel=close_panel,
            parent=self,
        )
        root.addWidget(self.footer)

        self.destroyed.connect(lambda *_args: self._shutdown())
        QTimer.singleShot(0, self.warm_up)

    def refresh_all(self) -> None:
        self._schedule_refresh()

    def warm_up(self) -> None:
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._pending:
            self._refresh_requested_while_busy = True
            return

        try:
            future = self._executor.submit(collect_system_menu_snapshot)
        except Exception:
            return
        self._pending[future] = "snapshot"
        if not self._result_pump.isActive():
            self._result_pump.start()

    def _drain_pending(self) -> None:
        if not self._pending:
            self._result_pump.stop()
            return

        done: list[concurrent.futures.Future] = []
        for future, kind in list(self._pending.items()):
            if not future.done():
                continue
            done.append(future)
            try:
                result = future.result()
                error = None
            except Exception as exc:
                result = None
                error = exc
            self._handle_result(kind, result, error)

        for future in done:
            self._pending.pop(future, None)

        if not self._pending:
            self._result_pump.stop()
            if self._refresh_requested_while_busy:
                self._refresh_requested_while_busy = False
                self._schedule_refresh()

    def _handle_result(self, kind: str, result: Any, error: Exception | None) -> None:
        if kind != "snapshot" or error is not None or not isinstance(result, SystemMenuSnapshot):
            return
        self._snapshot = result
        self.connectivity.apply_snapshot(result.connectivity)
        self.sound.apply_snapshot(result.sound)
        self.media.apply_snapshot(result.media)
        self.snapshotApplied.emit()

    def start_live_refresh(self) -> None:
        self._live_refresh_timer.start()

    def stop_live_refresh(self) -> None:
        self._live_refresh_timer.stop()

    def _shutdown(self) -> None:
        self._live_refresh_timer.stop()
        self._result_pump.stop()
        for future in list(self._pending.keys()):
            try:
                future.cancel()
            except Exception:
                pass
        self._pending.clear()
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass


class TopBarSystemMenuPanel(QFrame):
    PROXIMITY_CLOSE_DISTANCE = 62

    def __init__(
        self,
        *,
        open_terminal: Callable[[], None],
        open_dock: Callable[[], None],
        focus_controller: X11FocusController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self._anchor: QWidget | None = None
        self._tracking_app_events = False
        self._focus_controller = focus_controller
        self._proximity_timer = QTimer(self)
        self._proximity_timer.setInterval(120)
        self._proximity_timer.timeout.connect(self._hide_if_cursor_far)

        self.setObjectName("topbarSystemMenuPanel")
        self.setFixedWidth(400)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.content = SystemMenuContent(
            open_terminal=open_terminal,
            open_dock=open_dock,
            close_panel=self.hide,
            parent=self,
        )
        root.addWidget(self.content)
        self.content.snapshotApplied.connect(self._on_content_snapshot_applied)

        self._apply_style()

    def toggle(self, anchor: QWidget) -> None:
        self._anchor = anchor
        if self.isVisible():
            self.hide()
            return
        self.reposition(anchor)
        self.show()
        self.raise_()

    def reposition(self, anchor: QWidget | None = None) -> None:
        anchor_widget = anchor or self._anchor
        if anchor_widget is None:
            return

        self.adjustSize()
        anchor_bottom_right = anchor_widget.mapToGlobal(anchor_widget.rect().bottomRight())
        anchor_bottom_left = anchor_widget.mapToGlobal(anchor_widget.rect().bottomLeft())
        screen = QApplication.screenAt(anchor_bottom_right) or anchor_widget.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QApplication.primaryScreen().availableGeometry()

        x = anchor_bottom_right.x() - self.width()
        x = max(available.left() + 12, min(x, available.right() - self.width() - 12))
        y = anchor_bottom_left.y() + 8
        y = min(y, available.bottom() - self.height() - 12)
        y = max(available.top() + 12, y)
        self.move(QPoint(x, y))

    def refresh_all(self) -> None:
        self.content.refresh_all()
        if self.isVisible() and self._anchor is not None:
            self.reposition(self._anchor)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._install_app_event_filter()
        self._proximity_timer.start()
        self.content.start_live_refresh()
        self.refresh_all()

    def hideEvent(self, event) -> None:
        self._remove_app_event_filter()
        self._proximity_timer.stop()
        self.content.stop_live_refresh()
        super().hideEvent(event)
        if self._focus_controller is not None:
            self._focus_controller.restore_last_external_window_soon(0)

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate:
            QTimer.singleShot(0, self._hide_if_focus_lost)
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: QWidget, event) -> bool:
        if not self.isVisible():
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress:
            global_pos_getter = getattr(event, "globalPosition", None)
            if callable(global_pos_getter):
                global_pos = global_pos_getter().toPoint()
                if self._should_hide_for_global_pos(global_pos):
                    self.hide()
        return super().eventFilter(watched, event)

    def _on_content_snapshot_applied(self) -> None:
        if self.isVisible() and self._anchor is not None:
            self.reposition(self._anchor)

    def _install_app_event_filter(self) -> None:
        if self._tracking_app_events:
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._tracking_app_events = True

    def _remove_app_event_filter(self) -> None:
        if not self._tracking_app_events:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._tracking_app_events = False

    def _should_hide_for_global_pos(self, global_pos: QPoint) -> bool:
        if self.frameGeometry().contains(global_pos):
            return False
        if self._anchor is not None and self._widget_global_rect(self._anchor).contains(global_pos):
            return False
        popup = QApplication.activePopupWidget()
        if popup is not None and self._owns_widget(popup) and popup.frameGeometry().contains(global_pos):
            return False
        return True

    def _hide_if_focus_lost(self) -> None:
        if not self.isVisible():
            return
        popup = QApplication.activePopupWidget()
        if popup is not None and self._owns_widget(popup):
            return
        active_window = QApplication.activeWindow()
        if active_window is self or self._owns_widget(active_window):
            return
        self.hide()

    def _hide_if_cursor_far(self) -> None:
        if not self.isVisible():
            return
        popup = QApplication.activePopupWidget()
        if popup is not None and self._owns_widget(popup):
            return
        cursor_pos = QCursor.pos()
        if self._distance_to_rect(cursor_pos, self.frameGeometry()) <= self.PROXIMITY_CLOSE_DISTANCE:
            return
        if self._anchor is not None:
            anchor_rect = self._widget_global_rect(self._anchor)
            if self._distance_to_rect(cursor_pos, anchor_rect) <= self.PROXIMITY_CLOSE_DISTANCE:
                return
        self.hide()

    @staticmethod
    def _widget_global_rect(widget: QWidget) -> QRect:
        return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())

    @staticmethod
    def _distance_to_rect(point: QPoint, rect: QRect) -> float:
        dx = 0 if rect.left() <= point.x() <= rect.right() else min(
            abs(point.x() - rect.left()),
            abs(point.x() - rect.right()),
        )
        dy = 0 if rect.top() <= point.y() <= rect.bottom() else min(
            abs(point.y() - rect.top()),
            abs(point.y() - rect.bottom()),
        )
        return math.hypot(dx, dy)

    def _owns_widget(self, widget: QWidget | None) -> bool:
        current = widget
        while current is not None:
            if current is self:
                return True
            current = current.parentWidget()
        return False

    def _apply_style(self) -> None:
        pass


class SystemMenuButton(QToolButton):
    def __init__(
        self,
        *,
        open_terminal: Callable[[], None],
        open_dock: Callable[[], None],
        focus_controller: X11FocusController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._panel = TopBarSystemMenuPanel(
            open_terminal=open_terminal,
            open_dock=open_dock,
            focus_controller=focus_controller,
            parent=self.window(),
        )

        self._sound_snapshot: SoundSnapshot | None = None
        self._sound_backend = shared_sound_backend()
        self._status_future: concurrent.futures.Future | None = None
        self._status_refresh_requested_while_busy = False
        self._status_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="topbar-status")
        self._current_wifi_ssid: str | None = None
        self._current_wifi_signal: int | None = None
        self._current_volume_percent: int | None = None
        self._current_is_muted: bool | None = None

        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(28)
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setIconSize(QSize(74, 28))

        self.clicked.connect(self._toggle_panel)
        self.installEventFilter(self)
        if self.window() is not None:
            self.window().installEventFilter(self)

        self._update_timer = QTimer(self)
        self._update_timer.setInterval(2000)
        self._update_timer.timeout.connect(self._schedule_status_refresh)
        self._update_timer.start()
        self._status_pump = QTimer(self)
        self._status_pump.setInterval(40)
        self._status_pump.timeout.connect(self._drain_status_future)
        if self._sound_backend is not None:
            self._sound_backend.snapshotChanged.connect(self._on_sound_snapshot_changed)
        self.destroyed.connect(lambda *_args: self._shutdown_status_refresh())

        # Render immediately with cached values, then refresh off the UI thread.
        self._update_label()
        self._schedule_status_refresh()

    def eventFilter(self, watched: QWidget, event) -> bool:
        if self._panel.isVisible() and event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.WindowStateChange):
            QTimer.singleShot(0, lambda: self._panel.reposition(self))
        return super().eventFilter(watched, event)

    @Slot()
    def _toggle_panel(self) -> None:
        self._panel.toggle(self)

    @staticmethod
    def _fetch_button_status() -> tuple[str | None, int | None, int | None, bool | None]:
        wifi = WifiService()
        current_network = next((net for net in wifi.visible_networks() if net.in_use), None)
        volume = VolumeService()
        return (
            current_network.ssid if current_network is not None else None,
            current_network.signal if current_network is not None else None,
            volume.volume_percent(),
            volume.is_muted(),
        )

    def _schedule_status_refresh(self) -> None:
        if self._status_future is not None:
            self._status_refresh_requested_while_busy = True
            return
        try:
            self._status_future = self._status_executor.submit(self._fetch_button_status)
        except Exception:
            self._status_future = None
            return
        if not self._status_pump.isActive():
            self._status_pump.start()

    def _drain_status_future(self) -> None:
        future = self._status_future
        if future is None:
            self._status_pump.stop()
            return
        if not future.done():
            return

        self._status_future = None
        self._status_pump.stop()
        try:
            wifi_ssid, wifi_signal, volume_percent, is_muted = future.result()
        except Exception:
            wifi_ssid, wifi_signal, volume_percent, is_muted = None, None, None, None

        self._current_wifi_ssid = wifi_ssid
        self._current_wifi_signal = wifi_signal
        if self._sound_snapshot is None:
            self._current_volume_percent = volume_percent
            self._current_is_muted = is_muted
        self._update_label()

        if self._status_refresh_requested_while_busy:
            self._status_refresh_requested_while_busy = False
            self._schedule_status_refresh()

    @Slot()
    def _update_label(self) -> None:
        wifi_signal = self._current_wifi_signal
        wifi_level = self._wifi_icon_level(wifi_signal)

        sound_snapshot = self._sound_snapshot
        if sound_snapshot is not None:
            vol_pct = sound_snapshot.volume_percent
            is_muted = sound_snapshot.is_muted
        else:
            vol_pct = self._current_volume_percent
            is_muted = self._current_is_muted
        volume_icon_name = self._volume_icon_name(vol_pct, is_muted)

        vol_str = f"{vol_pct}%" if vol_pct is not None else "--%"
        if is_muted:
            vol_str = "Muted"

        self.setIcon(self._build_status_icon(wifi_level, volume_icon_name))
        self.setText("")

        wifi_tooltip = (
            f"Wi-Fi: {self._current_wifi_ssid} ({wifi_signal}%)"
            if self._current_wifi_ssid and wifi_signal is not None
            else "Wi-Fi: disconnected"
        )
        self.setToolTip(f"{wifi_tooltip}\nVolume: {vol_str}")

    @Slot(object)
    def _on_sound_snapshot_changed(self, snapshot: object) -> None:
        if isinstance(snapshot, SoundSnapshot):
            self._sound_snapshot = snapshot
            self._current_volume_percent = snapshot.volume_percent
            self._current_is_muted = snapshot.is_muted
            self._update_label()

    def _shutdown_status_refresh(self) -> None:
        self._update_timer.stop()
        self._status_pump.stop()
        future = self._status_future
        self._status_future = None
        if future is not None:
            try:
                future.cancel()
            except Exception:
                pass
        try:
            self._status_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                self._status_executor.shutdown(wait=False)
            except Exception:
                pass

    def _build_status_icon(self, wifi_level: int, volume_icon_name: str) -> QIcon:
        canvas_size = self.iconSize()
        pixmap = QPixmap(canvas_size)
        pixmap.fill(Qt.GlobalColor.transparent)
        icon_size = QSize(20, 20)
        icon_y = max(0, (canvas_size.height() - icon_size.height()) // 2)

        painter = QPainter(pixmap)
        try:
            wifi_icon = self._tinted_icon_pixmap(_WIFI_ICON_NAMES[wifi_level], icon_size)
            volume_icon = self._tinted_icon_pixmap(volume_icon_name, icon_size)
            power_icon = self._tinted_icon_pixmap(_POWER_ICON_NAME, icon_size)
            painter.drawPixmap(0, icon_y, wifi_icon)
            painter.drawPixmap(26, icon_y, volume_icon)
            painter.drawPixmap(52, icon_y, power_icon)
        finally:
            painter.end()

        return QIcon(pixmap)

    def _tinted_icon_pixmap(self, icon_name: str, icon_size: QSize) -> QPixmap:
        source = QIcon(_icon_path(icon_name)).pixmap(icon_size)
        if source.isNull():
            return source

        tinted = QPixmap(source.size())
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        try:
            painter.drawPixmap(0, 0, source)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(tinted.rect(), self.palette().color(QPalette.ColorRole.ButtonText))
        finally:
            painter.end()
        return tinted

    @staticmethod
    def _wifi_icon_level(signal: int | None) -> int:
        if signal is None or signal <= 0:
            return 0
        if signal < 25:
            return 1
        if signal < 50:
            return 2
        if signal < 75:
            return 3
        return 4

    @staticmethod
    def _volume_icon_name(volume_percent: int | None, is_muted: bool | None) -> str:
        if is_muted or volume_percent is None or volume_percent <= 0:
            return _VOLUME_ICON_NAMES["muted"]
        if volume_percent <= 33:
            return _VOLUME_ICON_NAMES["low"]
        if volume_percent <= 66:
            return _VOLUME_ICON_NAMES["medium"]
        return _VOLUME_ICON_NAMES["high"]


def preview_main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    preview_host = QWidget()
    preview_host.setWindowTitle("Topbar Menu Preview")
    preview_host.resize(480, 120)

    layout = QVBoxLayout(preview_host)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(12)

    title = QLabel("Standalone Control Center Preview", preview_host)
    title.setStyleSheet("font-size: 16px; font-weight: 700; color: #f4f4f4;")
    layout.addWidget(title)

    subtitle = QLabel("Use the button below to open the topbar system menu without starting the full topbar.", preview_host)
    subtitle.setWordWrap(True)
    subtitle.setStyleSheet("color: #d5dddf;")
    layout.addWidget(subtitle)

    # Note: If previewing standalone, you can still test the new button look directly
    anchor_button = SystemMenuButton(
        open_terminal=lambda: QMessageBox.information(preview_host, "Preview", "Terminal preview action triggered."),
        open_dock=lambda: QMessageBox.information(preview_host, "Preview", "Dock preview action triggered."),
        parent=preview_host,
    )
    layout.addWidget(anchor_button, alignment=Qt.AlignLeft)

    preview_host.setStyleSheet("background: #34393c;")

    preview_host.show()
    return app.exec()


__all__ = ["SystemMenuButton", "TopBarSystemMenuPanel", "preview_main"]
