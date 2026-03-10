from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings

from src.services.file_type_catalog import DesktopAssociationType, desktop_association_types

APP_NAME = "PyTPO Text Editor"
APP_ID = "pytpo-text-editor.desktop"
WRAPPER_NAME = "pytpo-text-editor"
SETTINGS_ORG = "TwoPintOhh"
SETTINGS_APP = "TextEditor"
SETTINGS_KEY_ASKED = "desktop_integration/onboarding_seen"
SETTINGS_KEY_TYPES = "desktop_integration/selected_type_keys"


FILE_TYPE_ASSOCIATIONS: tuple[DesktopAssociationType, ...] = desktop_association_types()


def editor_settings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def file_type_by_key() -> dict[str, DesktopAssociationType]:
    return {item.key: item for item in FILE_TYPE_ASSOCIATIONS}


def default_type_keys() -> list[str]:
    return [item.key for item in FILE_TYPE_ASSOCIATIONS]


def selected_type_keys_from_settings() -> list[str]:
    settings = editor_settings()
    raw = settings.value(SETTINGS_KEY_TYPES, default_type_keys())
    if isinstance(raw, str):
        return [part for part in raw.split(",") if part]
    if isinstance(raw, (list, tuple)):
        clean = [str(part).strip() for part in raw if str(part).strip()]
        if clean:
            return clean
    return default_type_keys()


def save_selected_type_keys(keys: list[str]) -> None:
    settings = editor_settings()
    settings.setValue(SETTINGS_KEY_TYPES, keys)
    settings.sync()


def mark_onboarding_seen() -> None:
    settings = editor_settings()
    settings.setValue(SETTINGS_KEY_ASKED, True)
    settings.sync()


def should_offer_onboarding() -> bool:
    if not is_linux_desktop():
        return False
    settings = editor_settings()
    seen = bool(settings.value(SETTINGS_KEY_ASKED, False, type=bool))
    return not seen and not is_installed()


def is_linux_desktop() -> bool:
    return sys.platform.startswith("linux")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def icon_path() -> Path:
    candidate = repo_root() / "src" / "icons" / "txt.png"
    if candidate.is_file():
        return candidate
    return repo_root() / "src" / "icons" / "app_icon.png"


def xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share"


def local_bin_dir() -> Path:
    raw = os.environ.get("XDG_BIN_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "bin"


def applications_dir() -> Path:
    return xdg_data_home() / "applications"


def mime_packages_dir() -> Path:
    return xdg_data_home() / "mime" / "packages"


def desktop_file_path() -> Path:
    return applications_dir() / APP_ID


def wrapper_script_path() -> Path:
    return local_bin_dir() / WRAPPER_NAME


def mime_xml_path() -> Path:
    return mime_packages_dir() / "pytpo-text-editor.xml"


def normalize_type_keys(keys: list[str] | tuple[str, ...] | None) -> list[str]:
    available = file_type_by_key()
    if not keys:
        return default_type_keys()
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        name = str(key).strip().lower()
        if not name or name not in available or name in seen:
            continue
        ordered.append(name)
        seen.add(name)
    return ordered or default_type_keys()


def selected_associations(keys: list[str] | tuple[str, ...] | None) -> list[DesktopAssociationType]:
    available = file_type_by_key()
    return [available[key] for key in normalize_type_keys(keys)]


def _render_wrapper_script() -> str:
    root = repo_root()
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'cd "{root}"\n'
        f'exec uv run python "{root / "text_editor_main.py"}" "$@"\n'
    )


def _render_desktop_file(associations: list[DesktopAssociationType]) -> str:
    mime_types = sorted({mime for item in associations for mime in item.mime_types})
    icon = icon_path()
    lines = [
        "[Desktop Entry]",
        "Version=1.0",
        "Type=Application",
        f"Name={APP_NAME}",
        "Comment=Standalone text editor from PyTPO",
        f"Exec={wrapper_script_path()} %F",
        f"Icon={icon}",
        "Terminal=false",
        "StartupNotify=true",
        "Categories=Utility;TextEditor;Development;",
        f"MimeType={';'.join(mime_types)};",
    ]
    return "\n".join(lines) + "\n"


def _render_mime_xml(associations: list[DesktopAssociationType]) -> str:
    custom = [item for item in associations if item.custom_mime]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">',
    ]
    for item in custom:
        lines.append(f'  <mime-type type="{item.mime_types[0]}">')
        lines.append(f"    <comment>{item.label}</comment>")
        for ext in item.extensions:
            lines.append(f'    <glob pattern="*{ext}"/>')
        for name in item.filenames:
            lines.append(f'    <glob pattern="{name}"/>')
        lines.append("  </mime-type>")
    lines.append("</mime-info>")
    return "\n".join(lines) + "\n"


def _run_command(args: list[str], warnings: list[str]) -> None:
    try:
        proc = subprocess.run(args, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        warnings.append(f"Command not found: {args[0]}")
        return

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail:
            warnings.append(f"{' '.join(args)}: {detail}")
        else:
            warnings.append(f"{' '.join(args)} exited with status {proc.returncode}")


def install_desktop_integration(type_keys: list[str] | tuple[str, ...] | None = None) -> list[str]:
    warnings: list[str] = []
    associations = selected_associations(type_keys)

    wrapper_script_path().parent.mkdir(parents=True, exist_ok=True)
    applications_dir().mkdir(parents=True, exist_ok=True)
    mime_packages_dir().mkdir(parents=True, exist_ok=True)

    wrapper_script_path().write_text(_render_wrapper_script(), encoding="utf-8")
    wrapper_script_path().chmod(
        wrapper_script_path().stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    desktop_file_path().write_text(_render_desktop_file(associations), encoding="utf-8")
    mime_xml_path().write_text(_render_mime_xml(associations), encoding="utf-8")

    _run_command(["update-mime-database", str(xdg_data_home() / "mime")], warnings)
    _run_command(["update-desktop-database", str(applications_dir())], warnings)

    for mime_type in sorted({mime for item in associations for mime in item.mime_types}):
        _run_command(["xdg-mime", "default", APP_ID, mime_type], warnings)

    save_selected_type_keys([item.key for item in associations])
    mark_onboarding_seen()
    return warnings


def uninstall_desktop_integration() -> list[str]:
    warnings: list[str] = []
    for path in (desktop_file_path(), wrapper_script_path(), mime_xml_path()):
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            warnings.append(f"Could not remove {path}: {exc}")

    _run_command(["update-mime-database", str(xdg_data_home() / "mime")], warnings)
    _run_command(["update-desktop-database", str(applications_dir())], warnings)
    mark_onboarding_seen()
    return warnings


def is_installed() -> bool:
    return desktop_file_path().is_file() and wrapper_script_path().is_file()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manage PyTPO text editor desktop integration.")
    subparsers = p.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install local desktop integration.")
    install_parser.add_argument(
        "--types",
        nargs="*",
        default=None,
        help="Optional file type keys to associate, e.g. txt md py tdoc.",
    )

    subparsers.add_parser("uninstall", help="Remove local desktop integration.")
    subparsers.add_parser("status", help="Show current desktop integration status.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "install":
        warnings = install_desktop_integration(args.types)
        print(f"Installed desktop integration at {desktop_file_path()}")
        if warnings:
            for warning in warnings:
                print(f"warning: {warning}")
        return 0

    if args.command == "uninstall":
        warnings = uninstall_desktop_integration()
        print("Removed desktop integration files.")
        if warnings:
            for warning in warnings:
                print(f"warning: {warning}")
        return 0

    print(f"installed={is_installed()}")
    print(f"desktop_file={desktop_file_path()}")
    print(f"wrapper={wrapper_script_path()}")
    print(f"mime_xml={mime_xml_path()}")
    print(f"selected_types={','.join(selected_type_keys_from_settings())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
