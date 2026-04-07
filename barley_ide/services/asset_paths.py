from __future__ import annotations

from pathlib import Path

from TPOPyside.asset_paths import preferred_shared_asset_dir as _preferred_shared_asset_dir
from TPOPyside.asset_paths import preferred_shared_asset_path as _preferred_shared_asset_path
from TPOPyside.asset_paths import shared_asset_search_dirs as _shared_asset_search_dirs

_INSTALLED_ASSET_NAMESPACE = "barley_ide"


def preferred_shared_asset_dir(relative_dir: str | Path) -> Path:
    return _preferred_shared_asset_dir(
        relative_dir,
        installed_namespace=_INSTALLED_ASSET_NAMESPACE,
    )


def shared_asset_search_dirs(relative_dir: str | Path) -> list[Path]:
    return _shared_asset_search_dirs(
        relative_dir,
        installed_namespace=_INSTALLED_ASSET_NAMESPACE,
    )


def preferred_shared_asset_path(relative_path: str | Path) -> Path:
    return _preferred_shared_asset_path(
        relative_path,
        installed_namespace=_INSTALLED_ASSET_NAMESPACE,
    )
