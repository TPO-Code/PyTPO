from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def terminal_state_dir() -> Path:
    return repo_root() / ".terminal"


def terminal_settings_path() -> Path:
    return terminal_state_dir() / "settings.json"
