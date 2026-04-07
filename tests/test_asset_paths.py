from __future__ import annotations

from pathlib import Path

from TPOPyside.asset_paths import (
    preferred_shared_asset_dir,
    shared_asset_search_dirs,
)
from TPOPyside.shared_assets import (
    resolve_shared_theme_path,
    shared_icon_path,
    shared_theme_candidates,
    shared_theme_dir,
)


def test_shared_theme_dir_resolves_tpopyside_assets() -> None:
    theme_dir = shared_theme_dir()
    assert theme_dir.is_dir()
    assert theme_dir.name == "themes"
    assert (theme_dir / "Default.qsst").is_file()
    assert theme_dir.parent.name == "assets"
    assert theme_dir.parent.parent.name == "TPOPyside"


def test_shared_icon_path_resolves_tpopyside_assets() -> None:
    icon_path = shared_icon_path("folder.png")
    assert icon_path.is_file()
    assert icon_path.name == "folder.png"
    assert icon_path.parent.name == "icons"
    assert icon_path.parent.parent.name == "assets"
    assert icon_path.parent.parent.parent.name == "TPOPyside"


def test_shared_theme_candidates_resolve_default_theme() -> None:
    candidates = shared_theme_candidates()
    assert any(name == "Default" for name, _path in candidates)

    resolved = resolve_shared_theme_path("Default")
    assert resolved is not None
    resolved_name, resolved_path = resolved
    assert resolved_name == "Default"
    assert resolved_path.name == "Default.qsst"


def test_suite_app_icons_are_package_local() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    expected_icons = (
        repo_root / "barley_ide" / "icon.png",
        repo_root / "stout" / "icon.png",
        repo_root / "pytpo_text_editor" / "icon.png",
        repo_root / "pytpo_dock" / "icon.png",
        repo_root / "grist" / "icon.png",
    )
    for icon_path in expected_icons:
        assert icon_path.is_file(), icon_path


def test_shared_search_dirs_include_preferred_dirs() -> None:
    icon_dir = preferred_shared_asset_dir("icons")
    theme_dir = preferred_shared_asset_dir("themes")

    assert icon_dir in shared_asset_search_dirs("icons")
    assert theme_dir in shared_asset_search_dirs("themes")
