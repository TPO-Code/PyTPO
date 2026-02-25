from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtGui import QColor

from src.ui.dialogs.reusable_file_dialog import BackgroundOptions, FileDialog


def _get_setting(manager: Any | None, key: str, default: Any) -> Any:
    if manager is None:
        return default
    try:
        return manager.get(key, scope_preference="ide", default=default)
    except Exception:
        return default


def _dialog_background(manager: Any | None) -> BackgroundOptions | None:
    image_path = str(_get_setting(manager, "file_dialog.background_image_path", "") or "").strip()
    scale_mode = str(_get_setting(manager, "file_dialog.background_scale_mode", "stretch") or "stretch").strip().lower()
    if scale_mode not in {"stretch", "fit_width", "fit_height", "tile"}:
        scale_mode = "stretch"
    try:
        brightness = int(_get_setting(manager, "file_dialog.background_brightness", 100))
    except Exception:
        brightness = 100
    brightness = max(0, min(200, brightness))

    tint_text = str(_get_setting(manager, "file_dialog.tint_color", "#000000") or "").strip() or "#000000"
    tint = QColor(tint_text)
    if not tint.isValid():
        tint = QColor("#000000")
    try:
        tint_strength = int(_get_setting(manager, "file_dialog.tint_strength", 0))
    except Exception:
        tint_strength = 0
    tint_strength = max(0, min(100, tint_strength))

    if not image_path and tint_strength == 0 and brightness == 100:
        return None

    return BackgroundOptions(
        image_path=image_path or None,
        brightness=float(brightness) / 100.0,
        scale_mode=scale_mode,
        tint_color=tint.name(QColor.HexRgb),
        tint_strength=float(tint_strength) / 100.0,
    )


def _dialog_starred_paths(manager: Any | None) -> list[str]:
    raw = _get_setting(manager, "file_dialog.starred_paths", [])
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
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
        result.append(normalized)
    return result


def _save_dialog_starred_paths(manager: Any | None, starred_paths: list[str]) -> None:
    if manager is None:
        return
    clean: list[str] = []
    seen: set[str] = set()
    for item in starred_paths:
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
    try:
        manager.set("file_dialog.starred_paths", clean, "ide")
        manager.save_all(scopes={"ide"}, only_dirty=True)
    except Exception:
        return


def get_open_file_name(
    *,
    parent: Any | None,
    manager: Any | None,
    caption: str,
    directory: str = "",
    file_filter: str = "",
) -> tuple[str, str]:
    path, selected_filter, _starred = FileDialog.getOpenFileName(
        parent=parent,
        caption=caption,
        directory=directory,
        filter=file_filter,
        starred_paths=_dialog_starred_paths(manager),
        background=_dialog_background(manager),
    )
    _save_dialog_starred_paths(manager, _starred)
    return path, selected_filter


def get_save_file_name(
    *,
    parent: Any | None,
    manager: Any | None,
    caption: str,
    directory: str = "",
    file_filter: str = "",
) -> tuple[str, str]:
    path, selected_filter, _starred = FileDialog.getSaveFileName(
        parent=parent,
        caption=caption,
        directory=directory,
        filter=file_filter,
        starred_paths=_dialog_starred_paths(manager),
        background=_dialog_background(manager),
    )
    _save_dialog_starred_paths(manager, _starred)
    return path, selected_filter


def get_existing_directory(
    *,
    parent: Any | None,
    manager: Any | None,
    caption: str,
    directory: str = "",
) -> str:
    selected, _starred = FileDialog.getExistingDirectory(
        parent=parent,
        caption=caption,
        directory=directory,
        starred_paths=_dialog_starred_paths(manager),
        background=_dialog_background(manager),
    )
    _save_dialog_starred_paths(manager, _starred)
    return selected
