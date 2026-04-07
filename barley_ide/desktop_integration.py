from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DesktopEntrySpec:
    desktop_id: str
    command: str
    display_name: str
    comment: str
    categories: tuple[str, ...]
    icon_candidates: tuple[str, ...]
    startup_wm_class: str = ""

    @property
    def icon_name(self) -> str:
        return self.desktop_id.removesuffix(".desktop")


APP_SPEC = DesktopEntrySpec(
    desktop_id="barley-ide.desktop",
    command="barley-ide",
    display_name="Barley",
    comment="Barley IDE for Python, C/C++, Rust, and more",
    categories=("Development", "IDE"),
    icon_candidates=("barley_ide/icon.png",),
    startup_wm_class="barley-ide",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def xdg_data_home() -> Path:
    raw = str(os.environ.get("XDG_DATA_HOME", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share"


def applications_dir() -> Path:
    return xdg_data_home() / "applications"


def icon_dir() -> Path:
    return xdg_data_home() / "icons" / "hicolor" / "256x256" / "apps"


def desktop_file_path() -> Path:
    return applications_dir() / APP_SPEC.desktop_id


def installed_icon_path() -> Path:
    return icon_dir() / f"{APP_SPEC.icon_name}.png"


def legacy_artifact_paths() -> tuple[Path, ...]:
    return (
        applications_dir() / "pytpo.desktop",
        icon_dir() / "pytpo.png",
    )


def _candidate_icon_path(relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return candidate
    return repo_root() / candidate


def resolve_icon_source() -> Path:
    for candidate in APP_SPEC.icon_candidates:
        path = _candidate_icon_path(candidate)
        if path.is_file():
            return path
    raise FileNotFoundError(f"No icon asset found for {APP_SPEC.desktop_id}")


def render_desktop_file() -> str:
    categories = ";".join(category for category in APP_SPEC.categories if str(category).strip())
    lines = [
        "[Desktop Entry]",
        "Version=1.0",
        "Type=Application",
        f"Name={APP_SPEC.display_name}",
        f"Comment={APP_SPEC.comment}",
        f"Exec={APP_SPEC.command}",
        f"TryExec={APP_SPEC.command}",
        f"Icon={APP_SPEC.icon_name}",
        "Terminal=false",
        "StartupNotify=true",
        f"Categories={categories};",
    ]
    startup_wm_class = str(APP_SPEC.startup_wm_class or "").strip()
    if startup_wm_class:
        lines.append(f"StartupWMClass={startup_wm_class}")
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


def install_desktop_entry() -> list[str]:
    warnings: list[str] = []
    applications_dir().mkdir(parents=True, exist_ok=True)
    icon_dir().mkdir(parents=True, exist_ok=True)

    for legacy_path in legacy_artifact_paths():
        try:
            if legacy_path.exists():
                legacy_path.unlink()
        except OSError as exc:
            warnings.append(f"Could not remove legacy artifact {legacy_path}: {exc}")

    shutil.copy2(resolve_icon_source(), installed_icon_path())
    desktop_file_path().write_text(render_desktop_file(), encoding="utf-8")
    _run_command(["update-desktop-database", str(applications_dir())], warnings)
    return warnings


def uninstall_desktop_entry() -> list[str]:
    warnings: list[str] = []
    for path in (desktop_file_path(), installed_icon_path(), *legacy_artifact_paths()):
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            warnings.append(f"Could not remove {path}: {exc}")

    _run_command(["update-desktop-database", str(applications_dir())], warnings)
    return warnings


def installation_status() -> tuple[bool, bool]:
    return desktop_file_path().is_file(), installed_icon_path().is_file()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manage the Barley IDE desktop entry for user-local installs.")
    subparsers = p.add_subparsers(dest="command", required=True)
    subparsers.add_parser("install", help="Install the Barley desktop entry.")
    subparsers.add_parser("uninstall", help="Remove the Barley desktop entry.")
    subparsers.add_parser("status", help="Show installation status.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "install":
        warnings = install_desktop_entry()
        print(f"installed {desktop_file_path()}")
        print(f"icon {installed_icon_path()}")
    elif args.command == "uninstall":
        warnings = uninstall_desktop_entry()
        print(f"removed {desktop_file_path()}")
        print(f"icon {installed_icon_path()}")
    else:
        desktop_installed, icon_installed = installation_status()
        print(
            f"{APP_SPEC.desktop_id}: "
            f"desktop={'yes' if desktop_installed else 'no'} "
            f"icon={'yes' if icon_installed else 'no'}"
        )
        warnings = []

    if warnings:
        for warning in warnings:
            print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
