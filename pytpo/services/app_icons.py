from __future__ import annotations

from pathlib import Path

from .asset_paths import preferred_shared_asset_path

_APP_ICON_FILES: dict[str, str] = {
    "pytpo": "pytpo.png",
    "terminal": "terminal.png",
    "text-editor": "txt.png",
    "dock": "dock.png",
    "appgrid": "appgrid.png",
}

_APP_ICON_ALIASES: dict[str, str] = {
    "pytpo": "pytpo",
    "pytpo.desktop": "pytpo",
    "pytpo-terminal": "terminal",
    "pytpo-terminal.desktop": "terminal",
    "terminal": "terminal",
    "pytpo-text-editor": "text-editor",
    "pytpo-text-editor.desktop": "text-editor",
    "text-editor": "text-editor",
    "pytpo-dock": "dock",
    "pytpo-dock.desktop": "dock",
    "dock": "dock",
    "pytpo-appgrid": "appgrid",
    "pytpo-appgrid.desktop": "appgrid",
    "appgrid": "appgrid",
}


def canonical_app_icon_key(name: str) -> str:
    key = str(name or "").strip().lower()
    return _APP_ICON_ALIASES.get(key, key)


def shared_app_icon_relative_paths(name: str) -> tuple[str, ...]:
    canonical = canonical_app_icon_key(name)
    icon_file = _APP_ICON_FILES.get(canonical)
    if not icon_file:
        return ("icons/pytpo.png",)
    if canonical == "pytpo":
        return (f"icons/{icon_file}",)
    return (f"icons/{icon_file}", "icons/pytpo.png")


def shared_app_icon_path(name: str) -> Path:
    candidates = shared_app_icon_relative_paths(name)
    for relative_path in candidates:
        icon_path = preferred_shared_asset_path(relative_path)
        if icon_path.is_file():
            return icon_path
    return preferred_shared_asset_path(candidates[0])
