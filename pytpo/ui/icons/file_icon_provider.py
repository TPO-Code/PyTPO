from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QIcon


class FileIconProvider:
    _EXTENSION_ICON_CACHE: dict[str, QIcon] = {}
    _CACHE_READY = False

    def __init__(self) -> None:
        self._ensure_cache()

    @classmethod
    def icon_for_file_name(cls, file_name: str) -> QIcon | None:
        cls._ensure_cache()
        ext = cls._file_extension_key(file_name)
        if not ext:
            return None
        icon = cls._EXTENSION_ICON_CACHE.get(ext)
        if icon is None or icon.isNull():
            return None
        return icon

    @classmethod
    def _ensure_cache(cls) -> None:
        if cls._CACHE_READY:
            return
        cls._CACHE_READY = True
        cls._EXTENSION_ICON_CACHE = {}

        icons_dir = cls._icons_dir()
        if not icons_dir.is_dir():
            return

        for icon_path in sorted(icons_dir.glob("*.png")):
            ext = icon_path.stem.strip().lower().lstrip(".")
            if not ext:
                continue
            icon = QIcon(str(icon_path))
            if icon.isNull():
                continue
            cls._EXTENSION_ICON_CACHE[ext] = icon

    @staticmethod
    def _icons_dir() -> Path:
        return Path(__file__).resolve().parents[2] / "icons"

    @staticmethod
    def _file_extension_key(name: str) -> str:
        text = str(name or "").strip().lower()
        if not text:
            return ""
        _base, ext = os.path.splitext(text)
        return ext.lstrip(".")
