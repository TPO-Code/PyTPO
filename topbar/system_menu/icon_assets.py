from __future__ import annotations

from PySide6.QtGui import QColor, QIcon

from tpo_assets import icon

WIFI_ICON_NAMES = {
    0: "ui/internet_0",
    1: "ui/internet_1",
    2: "ui/internet_2",
    3: "ui/internet_3",
    4: "ui/internet_4",
}

VOLUME_ICON_NAMES = {
    "muted": "ui/volume_muted",
    "low": "ui/volume_1",
    "medium": "ui/volume_2",
    "high": "ui/volume_3",
}

MEDIA_ICON_NAMES = {
    "loop_off": "ui/media/loop_off",
    "loop_playlist": "ui/media/loop_playlist",
    "loop_track": "ui/media/loop_track",
    "next": "ui/media/next",
    "pause": "ui/media/pause",
    "play": "ui/media/play",
    "previous": "ui/media/previous",
    "shuffle": "ui/media/shuffle",
    "skip_back": "ui/media/skip_back",
    "skip_forward": "ui/media/skip_forward",
    "stop": "ui/media/stop",
}

POWER_ICON_NAME = "ui/power"
SETTINGS_ICON_NAME = "ui/settings"


def asset_icon(name: str, *, foreground: str | None = "#FFFFFF") -> QIcon:
    return icon(name, foreground=foreground)


def color_hex(color: QColor) -> str:
    if color.alpha() >= 255:
        return color.name(QColor.NameFormat.HexRgb)
    return f"#{color.red():02x}{color.green():02x}{color.blue():02x}{color.alpha():02x}"


def loop_icon_name(loop_status: str) -> str:
    normalized = (loop_status or "None").strip().capitalize()
    if normalized == "Track":
        return MEDIA_ICON_NAMES["loop_track"]
    if normalized == "Playlist":
        return MEDIA_ICON_NAMES["loop_playlist"]
    return MEDIA_ICON_NAMES["loop_off"]


def volume_icon_name(volume_percent: int | None, is_muted: bool | None) -> str:
    if is_muted or volume_percent is None or volume_percent <= 0:
        return VOLUME_ICON_NAMES["muted"]
    if volume_percent <= 33:
        return VOLUME_ICON_NAMES["low"]
    if volume_percent <= 66:
        return VOLUME_ICON_NAMES["medium"]
    return VOLUME_ICON_NAMES["high"]
