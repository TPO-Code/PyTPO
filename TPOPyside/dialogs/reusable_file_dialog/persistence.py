from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QSettings

_STARRED_PATHS_KEY = "file_dialog/starred_paths"

StarredPathsSettingsFactory = Callable[[], QSettings | None]

_default_starred_paths_settings_factory: StarredPathsSettingsFactory | None = None


def normalize_starred_paths(paths: Iterable[str | Path]) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for item in paths:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            normalized = str(Path(text).expanduser().resolve())
        except Exception:
            normalized = str(Path(text).expanduser())
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(normalized)
    return clean


def set_default_starred_paths_settings_factory(
    factory: StarredPathsSettingsFactory | None,
) -> None:
    global _default_starred_paths_settings_factory
    _default_starred_paths_settings_factory = factory


def get_default_starred_paths_settings() -> QSettings | None:
    factory = _default_starred_paths_settings_factory
    if factory is None:
        return None
    try:
        return factory()
    except Exception:
        return None


def load_starred_paths(settings: QSettings | None) -> list[str]:
    if settings is None:
        return []
    try:
        raw = settings.value(_STARRED_PATHS_KEY, [])
    except Exception:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return normalize_starred_paths(raw)


def save_starred_paths(settings: QSettings | None, paths: Iterable[str | Path]) -> None:
    if settings is None:
        return
    clean = normalize_starred_paths(paths)
    try:
        settings.setValue(_STARRED_PATHS_KEY, clean)
        settings.sync()
    except Exception:
        return
