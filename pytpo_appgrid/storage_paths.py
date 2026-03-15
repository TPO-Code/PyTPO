from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths


def appgrid_config_dir() -> Path:
    root = QStandardPaths.writableLocation(QStandardPaths.ConfigLocation)
    return Path(root).expanduser() / "pytpo-appgrid"


def appgrid_settings_path() -> Path:
    return appgrid_config_dir() / "settings.json"
