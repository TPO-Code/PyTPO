from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, QSize, QTimer
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QAbstractButton, QTabBar, QWidget

from tpo_assets import icon, icon_path

BUG_ICON_NAME = "life/bug"
CLOSE_ICON_NAME = "ui/x"
PAUSE_ICON_NAME = "ui/media/pause"
PLAY_ICON_NAME = "ui/media/play"
SETTINGS_ICON_NAME = "ui/settings"
STOP_ICON_NAME = "ui/media/stop"

_NONE_ICON_FILENAME = "none.svg"
def color_hex(color: QColor) -> str:
    if color.alpha() >= 255:
        return color.name(QColor.NameFormat.HexRgb)
    return f"#{color.red():02x}{color.green():02x}{color.blue():02x}{color.alpha():02x}"


def app_palette_color_hex(
    role: QPalette.ColorRole = QPalette.ColorRole.ButtonText,
    *,
    group: QPalette.ColorGroup = QPalette.ColorGroup.Active,
) -> str:
    return color_hex(QApplication.palette().color(group, role))


def has_asset_icon(name: str) -> bool:
    asset_name = str(name or "").strip()
    if not asset_name:
        return False
    return icon_path(asset_name).name != _NONE_ICON_FILENAME


def asset_icon(name: str, *, foreground: str | None = "#FFFFFF") -> QIcon:
    if not has_asset_icon(name):
        return QIcon()
    return icon(name, foreground=foreground)


def app_palette_icon(name: str, *, role: QPalette.ColorRole = QPalette.ColorRole.ButtonText) -> QIcon:
    if not has_asset_icon(name):
        return QIcon()
    return icon(name, foreground=app_palette_color_hex(role))


def file_icon_name(file_name: str) -> str:
    text = str(file_name or "").strip().lower()
    if not text:
        return ""
    _base, ext = os.path.splitext(text)
    if not ext:
        return ""
    return ext


def file_icon(file_name: str, *, role: QPalette.ColorRole = QPalette.ColorRole.Text) -> QIcon:
    name = file_icon_name(file_name)
    if not name or not has_asset_icon(name):
        return QIcon()
    return icon(name, foreground=app_palette_color_hex(role))


class _TabCloseIconStyler(QObject):
    def __init__(self, tab_bar: QTabBar) -> None:
        super().__init__(tab_bar)
        self._tab_bar = tab_bar
        tab_bar.installEventFilter(self)
        self.refresh()
        QTimer.singleShot(0, self.refresh)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._tab_bar and event.type() in {
            QEvent.Type.ChildAdded,
            QEvent.Type.EnabledChange,
            QEvent.Type.LayoutRequest,
            QEvent.Type.PaletteChange,
            QEvent.Type.Show,
            QEvent.Type.StyleChange,
        }:
            QTimer.singleShot(0, self.refresh)
        return super().eventFilter(watched, event)

    def refresh(self) -> None:
        enabled_color = app_palette_color_hex(QPalette.ColorRole.ButtonText)
        disabled_color = app_palette_color_hex(
            QPalette.ColorRole.ButtonText,
            group=QPalette.ColorGroup.Disabled,
        )
        for index in range(self._tab_bar.count()):
            for side in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide):
                button = self._tab_bar.tabButton(index, side)
                if not isinstance(button, QAbstractButton):
                    continue
                close_icon = asset_icon(
                    CLOSE_ICON_NAME,
                    foreground=enabled_color if button.isEnabled() else disabled_color,
                )
                if close_icon.isNull():
                    continue
                button.setText("")
                button.setIcon(close_icon)
                button.setIconSize(QSize(12, 12))


def apply_tab_close_icon(widget: QWidget) -> None:
    if bool(widget.property("_pytpo_close_icon_applied")):
        return
    if isinstance(widget, QTabBar):
        styler = _TabCloseIconStyler(widget)
        setattr(widget, "_pytpo_close_icon_styler", styler)
    widget.setProperty("_pytpo_close_icon_applied", True)
