from __future__ import annotations

from PySide6.QtCore import QSettings

from pytpo.storage_paths import shared_file_dialog_settings_path
from TPOPyside.dialogs.reusable_file_dialog import set_default_starred_paths_settings_factory


def shared_file_dialog_settings() -> QSettings:
    return QSettings(str(shared_file_dialog_settings_path()), QSettings.IniFormat)


def configure_shared_file_dialog_defaults() -> None:
    set_default_starred_paths_settings_factory(shared_file_dialog_settings)
