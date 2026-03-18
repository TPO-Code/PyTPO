from __future__ import annotations

import concurrent.futures
import sys
from typing import Any, Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
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

from .connectivity import ConnectivitySection
from .footer import FooterSection
from .media_container import MediaContainer
from .sound import SoundSection
from .service import SystemMenuSnapshot, VolumeService, WifiService, collect_system_menu_snapshot

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
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

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
    def __init__(
        self,
        *,
        open_terminal: Callable[[], None],
        open_dock: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self._anchor: QWidget | None = None

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
        self.refresh_all()

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
        self.content.start_live_refresh()
        self.refresh_all()

    def hideEvent(self, event) -> None:
        self.content.stop_live_refresh()
        super().hideEvent(event)

    def _on_content_snapshot_applied(self) -> None:
        if self.isVisible() and self._anchor is not None:
            self.reposition(self._anchor)

    def _apply_style(self) -> None:
        pass


class SystemMenuButton(QToolButton):
    def __init__(
        self,
        *,
        open_terminal: Callable[[], None],
        open_dock: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._panel = TopBarSystemMenuPanel(
            open_terminal=open_terminal,
            open_dock=open_dock,
            parent=self.window(),
        )

        self._wifi_service = WifiService()
        self._volume_service = VolumeService()

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
        self._update_timer.timeout.connect(self._update_label)
        self._update_timer.start()

        # Perform the first label setup immediately upon initialization
        self._update_label()

    def eventFilter(self, watched: QWidget, event) -> bool:
        if self._panel.isVisible() and event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.WindowStateChange):
            QTimer.singleShot(0, lambda: self._panel.reposition(self))
        return super().eventFilter(watched, event)

    @Slot()
    def _toggle_panel(self) -> None:
        self._panel.toggle(self)

    @Slot()
    def _update_label(self) -> None:
        current_network = next((net for net in self._wifi_service.visible_networks() if net.in_use), None)
        wifi_level = self._wifi_icon_level(current_network.signal if current_network is not None else None)

        vol_pct = self._volume_service.volume_percent()
        is_muted = self._volume_service.is_muted()
        volume_icon_name = self._volume_icon_name(vol_pct, is_muted)

        vol_str = f"{vol_pct}%" if vol_pct is not None else "--%"
        if is_muted:
            vol_str = "Muted"

        self.setIcon(self._build_status_icon(wifi_level, volume_icon_name))
        self.setText("")

        wifi_tooltip = (
            f"Wi-Fi: {current_network.ssid} ({current_network.signal}%)"
            if current_network is not None
            else "Wi-Fi: disconnected"
        )
        self.setToolTip(f"{wifi_tooltip}\nVolume: {vol_str}")

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
