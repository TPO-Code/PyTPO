from __future__ import annotations

from PySide6.QtCore import QSettings

from TPOPyside.dialogs.reusable_file_dialog import set_default_starred_paths_settings_factory


def shared_file_dialog_settings() -> QSettings:
    return QSettings("TwoPintOhh", "SharedFileDialog")


def configure_shared_file_dialog_defaults() -> None:
    set_default_starred_paths_settings_factory(shared_file_dialog_settings)
