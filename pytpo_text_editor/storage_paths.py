from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

from TPOPyside.storage import migrate_file_if_missing, suite_storage_namespace

SUITE_NAME = "pytpo"
TEXT_EDITOR_APP_NAME = "text-editor"
LEGACY_SETTINGS_ORG = "TwoPintOhh"
LEGACY_SETTINGS_APP = "TextEditor"
LEGACY_RECENT_FILES_FILENAME = "recent-files.json"
LEGACY_RECENT_FILES_APP_DIR_NAME = "pytpo-text-editor"


def _namespace():
    return suite_storage_namespace(SUITE_NAME).app(TEXT_EDITOR_APP_NAME)


def text_editor_config_dir() -> Path:
    path = _namespace().config_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def text_editor_data_dir() -> Path:
    path = _namespace().data_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def text_editor_state_dir() -> Path:
    path = _namespace().state_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def text_editor_cache_dir() -> Path:
    path = _namespace().cache_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def text_editor_settings_ini_path() -> Path:
    return text_editor_config_dir() / "settings.ini"


def text_editor_recent_files_path() -> Path:
    return text_editor_state_dir() / "recent-files.json"


def text_editor_settings() -> QSettings:
    migrate_legacy_text_editor_storage()
    return QSettings(str(text_editor_settings_ini_path()), QSettings.IniFormat)


def migrate_legacy_text_editor_storage() -> None:
    _migrate_legacy_qsettings_store()
    _migrate_legacy_recent_files_store()


def _migrate_legacy_qsettings_store() -> None:
    target_path = text_editor_settings_ini_path()
    target = QSettings(str(target_path), QSettings.IniFormat)
    if target.allKeys():
        return

    legacy = QSettings(LEGACY_SETTINGS_ORG, LEGACY_SETTINGS_APP)
    keys = list(legacy.allKeys() or [])
    if not keys:
        return
    for key in keys:
        target.setValue(key, legacy.value(key))
    target.sync()


def _migrate_legacy_recent_files_store() -> None:
    target = text_editor_recent_files_path()
    legacy_data_location = str(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or ""
    ).strip()
    candidates: list[Path] = []
    if legacy_data_location:
        candidates.append(Path(legacy_data_location) / LEGACY_RECENT_FILES_FILENAME)
    candidates.append(
        Path.home() / ".local" / "share" / LEGACY_RECENT_FILES_APP_DIR_NAME / LEGACY_RECENT_FILES_FILENAME
    )
    for legacy_file in candidates:
        if migrate_file_if_missing(legacy_file, target):
            return
