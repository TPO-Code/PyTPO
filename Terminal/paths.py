from __future__ import annotations

from pathlib import Path

from TPOPyside.storage import migrate_file_if_missing, suite_storage_namespace

def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _terminal_namespace():
    return suite_storage_namespace("pytpo").app("terminal")


def migrate_legacy_terminal_storage() -> None:
    legacy_dir = repo_root() / ".terminal"
    if not legacy_dir.is_dir():
        return
    migrate_file_if_missing(legacy_dir / "settings.json", terminal_settings_path())
    migrate_file_if_missing(legacy_dir / "prompt-editor-state.json", terminal_prompt_editor_state_path())


def terminal_config_dir() -> Path:
    path = _terminal_namespace().config_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def terminal_state_dir() -> Path:
    path = _terminal_namespace().state_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def terminal_cache_dir() -> Path:
    path = _terminal_namespace().cache_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def terminal_settings_path() -> Path:
    return terminal_config_dir() / "settings.json"


def terminal_prompt_editor_state_path() -> Path:
    return terminal_state_dir() / "prompt-editor-state.json"
