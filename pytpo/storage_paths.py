from __future__ import annotations

import os
from pathlib import Path

from TPOPyside.storage import (
    AppStorageNamespace,
    merge_tree_missing,
    migrate_file_if_missing,
    migrate_tree_if_missing,
    suite_storage_namespace,
)

SUITE_NAME = "pytpo"
IDE_APP_NAME = "ide"
SHARED_APP_NAME = "shared"
_IDE_APP_DIR_OVERRIDE_ENV = "PYTPO_IDE_APP_DIR"


def _suite_namespace():
    return suite_storage_namespace(SUITE_NAME)


def ide_storage_namespace() -> AppStorageNamespace:
    override = str(os.environ.get(_IDE_APP_DIR_OVERRIDE_ENV, "") or "").strip()
    if override:
        root = Path(override).expanduser()
        return AppStorageNamespace(
            suite_name=SUITE_NAME,
            app_name=IDE_APP_NAME,
            config_dir=root,
            data_dir=root / "data",
            state_dir=root / "state",
            cache_dir=root / "cache",
        )
    return _suite_namespace().app(IDE_APP_NAME)


def shared_storage_namespace() -> AppStorageNamespace:
    return _suite_namespace().app(SHARED_APP_NAME)


def ide_config_dir() -> Path:
    namespace = ide_storage_namespace()
    namespace.config_dir.mkdir(parents=True, exist_ok=True)
    return namespace.config_dir


def ide_data_dir() -> Path:
    namespace = ide_storage_namespace()
    namespace.data_dir.mkdir(parents=True, exist_ok=True)
    return namespace.data_dir


def ide_state_dir() -> Path:
    namespace = ide_storage_namespace()
    namespace.state_dir.mkdir(parents=True, exist_ok=True)
    return namespace.state_dir


def ide_cache_dir() -> Path:
    namespace = ide_storage_namespace()
    namespace.cache_dir.mkdir(parents=True, exist_ok=True)
    return namespace.cache_dir


def ide_settings_path() -> Path:
    return ide_config_dir() / "ide-settings.json"


def ide_spell_words_path() -> Path:
    return ide_data_dir() / "spell-user-words.txt"


def ide_templates_dir() -> Path:
    path = ide_data_dir() / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ide_no_project_workspace_dir() -> Path:
    path = ide_state_dir() / "no-project-workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ide_github_token_fallback_path() -> Path:
    namespace = ide_storage_namespace()
    return namespace.secrets_path("github-token.json")


def shared_file_dialog_settings_path() -> Path:
    namespace = shared_storage_namespace()
    namespace.config_dir.mkdir(parents=True, exist_ok=True)
    return namespace.config_path("file-dialog.ini")


def migrate_legacy_ide_storage(legacy_repo_root: str | Path | None = None) -> None:
    if legacy_repo_root is None:
        repo_root = Path(__file__).resolve().parents[1]
    else:
        repo_root = Path(legacy_repo_root).expanduser()
    legacy_dir = repo_root / ".pytpo"
    if not legacy_dir.is_dir():
        return

    migrate_file_if_missing(legacy_dir / "ide-settings.json", ide_settings_path())
    migrate_file_if_missing(legacy_dir / "spell-user-words.txt", ide_spell_words_path())
    migrate_file_if_missing(legacy_dir / "github-token.json", ide_github_token_fallback_path())
    if not migrate_tree_if_missing(legacy_dir / "templates", ide_templates_dir()):
        merge_tree_missing(legacy_dir / "templates", ide_templates_dir())
