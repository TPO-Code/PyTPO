from __future__ import annotations

from PySide6.QtCore import QSettings

from barley_ide.storage_paths import shared_file_dialog_settings_path
from TPOPyside.dialogs.reusable_file_dialog import (
    load_starred_paths,
    save_starred_paths,
    set_default_starred_paths_settings_factory,
)

_LEGACY_SHARED_DIALOG_ORG = "TwoPintOhh"
_LEGACY_SHARED_DIALOG_APP = "SharedFileDialog"


def _migrate_legacy_starred_paths(settings: QSettings) -> QSettings:
    if load_starred_paths(settings):
        return settings
    legacy = QSettings(_LEGACY_SHARED_DIALOG_ORG, _LEGACY_SHARED_DIALOG_APP)
    legacy_paths = load_starred_paths(legacy)
    if legacy_paths:
        save_starred_paths(settings, legacy_paths)
    return settings


def shared_file_dialog_settings() -> QSettings:
    settings = QSettings(str(shared_file_dialog_settings_path()), QSettings.IniFormat)
    return _migrate_legacy_starred_paths(settings)


def configure_shared_file_dialog_defaults() -> None:
    set_default_starred_paths_settings_factory(shared_file_dialog_settings)
