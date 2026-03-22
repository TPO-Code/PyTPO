from __future__ import annotations

from pathlib import Path

from pytpo.services.asset_paths import (
    preferred_shared_asset_dir,
    preferred_shared_asset_path,
    shared_asset_search_dirs,
)


def test_shared_theme_dir_resolves_root_assets() -> None:
    theme_dir = preferred_shared_asset_dir("themes")
    assert theme_dir.is_dir()
    assert theme_dir.name == "themes"
    assert (theme_dir / "Default.qsst").is_file()


def test_shared_icon_path_resolves_root_assets() -> None:
    icon_path = preferred_shared_asset_path("icons/folder.png")
    assert icon_path.is_file()
    assert icon_path.name == "folder.png"
    assert icon_path.parent.name == "icons"


def test_suite_app_icons_are_package_local() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    expected_icons = (
        repo_root / "pytpo" / "icon.png",
        repo_root / "pytpo_terminal" / "icon.png",
        repo_root / "pytpo_text_editor" / "icon.png",
        repo_root / "pytpo_dock" / "icon.png",
        repo_root / "pytpo_appgrid" / "icon.png",
    )
    for icon_path in expected_icons:
        assert icon_path.is_file(), icon_path


def test_shared_search_dirs_include_preferred_dirs() -> None:
    icon_dir = preferred_shared_asset_dir("icons")
    theme_dir = preferred_shared_asset_dir("themes")

    assert icon_dir in shared_asset_search_dirs("icons")
    assert theme_dir in shared_asset_search_dirs("themes")
