from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from pytpo.services.app_icons import shared_app_icon_relative_paths
from pytpo.services.asset_paths import preferred_shared_asset_path
from pytpo_dock.autostart import DockAutostartManager


@dataclass(frozen=True, slots=True)
class DesktopAppSpec:
    key: str
    desktop_id: str
    command: str
    display_name: str
    comment: str
    categories: tuple[str, ...]
    icon_candidates: tuple[str, ...]
    exec_arg: str = ""

    @property
    def icon_name(self) -> str:
        return self.desktop_id.removesuffix(".desktop")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def desktop_app_specs() -> tuple[DesktopAppSpec, ...]:
    return (
        DesktopAppSpec(
            key="pytpo",
            desktop_id="pytpo.desktop",
            command="pytpo",
            display_name="PyTPO",
            comment="Multi-language IDE for Python, C/C++, Rust, and more",
            categories=("Development", "IDE"),
            icon_candidates=("pytpo/icon.png", *shared_app_icon_relative_paths("pytpo")),
        ),
        DesktopAppSpec(
            key="terminal",
            desktop_id="pytpo-terminal.desktop",
            command="pytpo-terminal",
            display_name="PyTPO Terminal",
            comment="Standalone terminal from the PyTPO suite",
            categories=("System", "TerminalEmulator", "Utility"),
            icon_candidates=("pytpo_terminal/icon.png", *shared_app_icon_relative_paths("terminal")),
        ),
        DesktopAppSpec(
            key="text-editor",
            desktop_id="pytpo-text-editor.desktop",
            command="pytpo-text-editor",
            display_name="PyTPO Text Editor",
            comment="Standalone text editor from the PyTPO suite",
            categories=("Utility", "TextEditor", "Development"),
            icon_candidates=("pytpo_text_editor/icon.png", *shared_app_icon_relative_paths("text-editor")),
            exec_arg="%F",
        ),
        DesktopAppSpec(
            key="dock",
            desktop_id="pytpo-dock.desktop",
            command="pytpo-dock",
            display_name="PyTPO Dock",
            comment="Standalone desktop dock from the PyTPO suite",
            categories=("Utility",),
            icon_candidates=("pytpo_dock/icon.png", *shared_app_icon_relative_paths("dock")),
        ),
        DesktopAppSpec(
            key="appgrid",
            desktop_id="pytpo-appgrid.desktop",
            command="pytpo-appgrid",
            display_name="PyTPO App Grid",
            comment="Standalone application launcher grid from the PyTPO suite",
            categories=("Utility",),
            icon_candidates=("pytpo_appgrid/icon.png", *shared_app_icon_relative_paths("appgrid")),
        ),
    )


def app_alias_map() -> dict[str, DesktopAppSpec]:
    mapping: dict[str, DesktopAppSpec] = {}
    for spec in desktop_app_specs():
        for alias in (spec.key, spec.command, spec.desktop_id, spec.icon_name):
            mapping[alias] = spec
    return mapping


def select_app_specs(names: list[str] | tuple[str, ...] | None = None) -> list[DesktopAppSpec]:
    if not names:
        return list(desktop_app_specs())

    mapping = app_alias_map()
    selected: list[DesktopAppSpec] = []
    seen: set[str] = set()
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        spec = mapping.get(name)
        if spec is None:
            raise KeyError(name)
        if spec.desktop_id in seen:
            continue
        selected.append(spec)
        seen.add(spec.desktop_id)
    return selected


def xdg_data_home() -> Path:
    raw = str(os.environ.get("XDG_DATA_HOME", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share"


def applications_dir() -> Path:
    return xdg_data_home() / "applications"


def icon_dir() -> Path:
    return xdg_data_home() / "icons" / "hicolor" / "256x256" / "apps"


def desktop_file_path(spec: DesktopAppSpec) -> Path:
    return applications_dir() / spec.desktop_id


def installed_icon_path(spec: DesktopAppSpec) -> Path:
    return icon_dir() / f"{spec.icon_name}.png"


def _candidate_icon_path(relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return candidate
    if candidate.parts[:1] == ("icons",):
        return preferred_shared_asset_path(candidate)
    return repo_root() / candidate


def resolve_icon_source(spec: DesktopAppSpec) -> Path:
    for candidate in spec.icon_candidates:
        path = _candidate_icon_path(candidate)
        if path.is_file():
            return path
    fallback = preferred_shared_asset_path("icons/pytpo.png")
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(f"No icon asset found for {spec.desktop_id}")


def render_desktop_file(spec: DesktopAppSpec) -> str:
    categories = ";".join(category for category in spec.categories if str(category).strip())
    exec_parts = [spec.command]
    if spec.exec_arg:
        exec_parts.append(spec.exec_arg)
    lines = [
        "[Desktop Entry]",
        "Version=1.0",
        "Type=Application",
        f"Name={spec.display_name}",
        f"Comment={spec.comment}",
        f"Exec={' '.join(exec_parts)}",
        f"TryExec={spec.command}",
        f"Icon={spec.icon_name}",
        "Terminal=false",
        "StartupNotify=true",
        f"Categories={categories};",
    ]
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


def install_desktop_entries(names: list[str] | tuple[str, ...] | None = None) -> list[str]:
    warnings: list[str] = []
    specs = select_app_specs(names)

    applications_dir().mkdir(parents=True, exist_ok=True)
    icon_dir().mkdir(parents=True, exist_ok=True)

    for spec in specs:
        shutil.copy2(resolve_icon_source(spec), installed_icon_path(spec))
        desktop_file_path(spec).write_text(render_desktop_file(spec), encoding="utf-8")

    _run_command(["update-desktop-database", str(applications_dir())], warnings)
    return warnings


def uninstall_desktop_entries(names: list[str] | tuple[str, ...] | None = None) -> list[str]:
    warnings: list[str] = []
    specs = select_app_specs(names)

    for spec in specs:
        for path in (desktop_file_path(spec), installed_icon_path(spec)):
            try:
                if path.exists():
                    path.unlink()
            except OSError as exc:
                warnings.append(f"Could not remove {path}: {exc}")

    if _selected_specs_include_dock(specs):
        manager = DockAutostartManager()
        try:
            manager.cleanup()
        except OSError as exc:
            warnings.append(f"Could not remove {manager.entry_path()}: {exc}")

    _run_command(["update-desktop-database", str(applications_dir())], warnings)
    return warnings


def installation_status(names: list[str] | tuple[str, ...] | None = None) -> list[tuple[DesktopAppSpec, bool, bool]]:
    specs = select_app_specs(names)
    return [
        (
            spec,
            desktop_file_path(spec).is_file(),
            installed_icon_path(spec).is_file(),
        )
        for spec in specs
    ]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manage PyTPO desktop entries for pipx-style installs.")
    subparsers = p.add_subparsers(dest="command", required=True)

    app_help = (
        "Optional app selector. Accepts short keys such as 'terminal' or full names like "
        "'pytpo-terminal'. Defaults to all apps."
    )

    install_parser = subparsers.add_parser("install", help="Install user-local desktop entries.")
    install_parser.add_argument("--app", action="append", default=None, help=app_help)

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove user-local desktop entries.")
    uninstall_parser.add_argument("--app", action="append", default=None, help=app_help)

    status_parser = subparsers.add_parser("status", help="Show installation status.")
    status_parser.add_argument("--app", action="append", default=None, help=app_help)

    return p


def _format_unknown_app_error(exc: KeyError) -> str:
    available = ", ".join(spec.key for spec in desktop_app_specs())
    return f"Unknown app selector: {exc.args[0]}. Available selectors include: {available}"


def _selected_specs_include_dock(specs: list[DesktopAppSpec]) -> bool:
    return any(spec.key == "dock" for spec in specs)


def _prompt_enable_dock_on_startup() -> bool:
    if not sys.stdin.isatty():
        print("dock startup prompt skipped: stdin is not interactive; leaving dock autostart disabled")
        return False
    try:
        response = input("Do you wish to enable the PyTPO Dock on startup? [y/N] ")
    except EOFError:
        return False
    return response.strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "install":
            selected_specs = select_app_specs(args.app)
            warnings = install_desktop_entries(args.app)
            for spec in selected_specs:
                print(f"installed {desktop_file_path(spec)}")
                print(f"icon {installed_icon_path(spec)}")
            if _selected_specs_include_dock(selected_specs) and _prompt_enable_dock_on_startup():
                try:
                    autostart_path = DockAutostartManager().enable()
                except OSError as exc:
                    print(f"error: could not create dock autostart entry: {exc}", file=sys.stderr)
                    return 1
                print(f"autostart {autostart_path}")
            for warning in warnings:
                print(f"warning: {warning}")
            return 0

        if args.command == "uninstall":
            selected_specs = select_app_specs(args.app)
            warnings = uninstall_desktop_entries(args.app)
            for spec in selected_specs:
                print(f"removed {desktop_file_path(spec)}")
                print(f"removed {installed_icon_path(spec)}")
            if _selected_specs_include_dock(selected_specs):
                print(f"removed {DockAutostartManager().entry_path()}")
            for warning in warnings:
                print(f"warning: {warning}")
            return 0

        for spec, desktop_installed, icon_installed in installation_status(args.app):
            print(
                f"{spec.desktop_id}: desktop_installed={desktop_installed} "
                f"icon_installed={icon_installed} command={spec.command}"
            )
        return 0
    except KeyError as exc:
        print(_format_unknown_app_error(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
