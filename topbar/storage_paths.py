from __future__ import annotations

from pathlib import Path

from TPOPyside.storage import suite_storage_namespace

SUITE_NAME = "pytpo"
TOPBAR_APP_NAME = "topbar"


def _namespace():
    return suite_storage_namespace(SUITE_NAME).app(TOPBAR_APP_NAME)


def topbar_config_dir() -> Path:
    path = _namespace().config_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def topbar_settings_path() -> Path:
    return topbar_config_dir() / "settings.json"
