from __future__ import annotations

from PySide6.QtGui import QIcon

from .asset_icons import file_icon


class FileIconProvider:
    def __init__(self) -> None:
        pass

    @classmethod
    def icon_for_file_name(cls, file_name: str) -> QIcon | None:
        icon = file_icon(file_name)
        if icon.isNull():
            return None
        return icon
