from __future__ import annotations

from pathlib import Path

from TPOPyside.storage import migrate_file_if_missing, suite_storage_namespace

SUITE_NAME = "pytpo"
DOCK_APP_NAME = "dock"
_LEGACY_CONFIG_DIR = Path.home() / ".config" / "custom_dock"


def _namespace():
    return suite_storage_namespace(SUITE_NAME).app(DOCK_APP_NAME)


def dock_config_dir() -> Path:
    path = _namespace().config_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def dock_state_dir() -> Path:
    path = _namespace().state_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def dock_pinned_apps_path() -> Path:
    return dock_config_dir() / "pinned.json"


def dock_settings_path() -> Path:
    return dock_config_dir() / "settings.json"


def dock_debug_log_path() -> Path:
    return dock_state_dir() / "render-debug.log"


def migrate_legacy_dock_storage() -> None:
    migrate_file_if_missing(_LEGACY_CONFIG_DIR / "pinned.json", dock_pinned_apps_path())
