from __future__ import annotations

import configparser
import json
import locale
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_EXTRA_APPLICATION_DIRS = (
    Path("/var/lib/flatpak/exports/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
)
DESKTOP_APP_DRAG_MIME_TYPE = "application/x-pytpo-desktop-app"


@dataclass(frozen=True, slots=True)
class DesktopApplication:
    path: str
    desktop_id: str
    name: str
    exec: str
    icon: str
    startup_wm_class: str
    comment: str
    generic_name: str
    categories: tuple[str, ...]
    keywords: tuple[str, ...]

    def to_legacy_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "desktop_id": self.desktop_id,
            "Name": self.name,
            "Exec": self.exec,
            "Icon": self.icon,
            "StartupWMClass": self.startup_wm_class,
            "Comment": self.comment,
            "GenericName": self.generic_name,
            "Categories": ";".join(self.categories),
            "Keywords": ";".join(self.keywords),
            "Type": "Application",
        }


def _split_semicolon_list(value: str) -> tuple[str, ...]:
    items = []
    for item in str(value or "").split(";"):
        cleaned = item.strip()
        if cleaned:
            items.append(cleaned)
    return tuple(items)


def _desktop_languages() -> tuple[str, ...]:
    candidates: list[str] = []
    current_locale = locale.getlocale(locale.LC_MESSAGES)[0] or locale.getlocale()[0] or ""
    normalized = current_locale.replace("-", "_").strip()
    if normalized:
        candidates.append(normalized)
        language = normalized.split("_", 1)[0]
        if language and language != normalized:
            candidates.append(language)
    return tuple(dict.fromkeys(candidates))


def _localized_value(entry: configparser.SectionProxy, key: str) -> str:
    for language in _desktop_languages():
        localized_key = f"{key}[{language}]"
        value = str(entry.get(localized_key, "") or "").strip()
        if value:
            return value
    direct_value = str(entry.get(key, "") or "").strip()
    if direct_value:
        return direct_value
    for entry_key in entry:
        if entry_key.startswith(f"{key}["):
            value = str(entry.get(entry_key, "") or "").strip()
            if value:
                return value
    return ""


def _is_truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def desktop_application_dirs() -> tuple[Path, ...]:
    ordered_dirs: list[Path] = []
    seen: set[Path] = set()

    data_home = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    candidates = [data_home / "applications"]

    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    for raw_dir in data_dirs.split(":"):
        raw_dir = raw_dir.strip()
        if raw_dir:
            candidates.append(Path(raw_dir).expanduser() / "applications")

    candidates.append(data_home / "flatpak" / "exports" / "share" / "applications")
    candidates.extend(_EXTRA_APPLICATION_DIRS)

    for directory in candidates:
        resolved = directory.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_dir():
            ordered_dirs.append(resolved)
    return tuple(ordered_dirs)


def parse_desktop_file(path: str | os.PathLike[str]) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        with Path(path).open("r", encoding="utf-8") as file_handle:
            parser.read_file(file_handle)
    except Exception:
        return {}

    if not parser.has_section("Desktop Entry"):
        return {}

    entry = parser["Desktop Entry"]
    return {
        "path": str(path),
        "desktop_id": Path(path).name,
        "Name": _localized_value(entry, "Name"),
        "Exec": str(entry.get("Exec", "") or "").strip(),
        "Icon": str(entry.get("Icon", "") or "").strip(),
        "StartupWMClass": str(entry.get("StartupWMClass", "") or "").strip(),
        "Comment": _localized_value(entry, "Comment"),
        "GenericName": _localized_value(entry, "GenericName"),
        "Categories": str(entry.get("Categories", "") or "").strip(),
        "Keywords": str(entry.get("Keywords", "") or "").strip(),
        "Type": str(entry.get("Type", "") or "").strip(),
        "NoDisplay": str(entry.get("NoDisplay", "") or "").strip(),
        "Hidden": str(entry.get("Hidden", "") or "").strip(),
    }


def _desktop_app_from_info(info: dict[str, str]) -> DesktopApplication | None:
    if info.get("Type") != "Application":
        return None
    if _is_truthy(info.get("NoDisplay", "")) or _is_truthy(info.get("Hidden", "")):
        return None

    name = str(info.get("Name", "") or "").strip()
    if not name:
        return None

    return DesktopApplication(
        path=str(info.get("path", "") or ""),
        desktop_id=str(info.get("desktop_id", "") or Path(str(info.get("path", "") or "")).name),
        name=name,
        exec=str(info.get("Exec", "") or "").strip(),
        icon=str(info.get("Icon", "") or "").strip(),
        startup_wm_class=str(info.get("StartupWMClass", "") or "").strip(),
        comment=str(info.get("Comment", "") or "").strip(),
        generic_name=str(info.get("GenericName", "") or "").strip(),
        categories=_split_semicolon_list(str(info.get("Categories", "") or "")),
        keywords=_split_semicolon_list(str(info.get("Keywords", "") or "")),
    )


def load_desktop_applications(paths: Iterable[Path] | None = None) -> list[DesktopApplication]:
    directories = tuple(paths) if paths is not None else desktop_application_dirs()
    applications: list[DesktopApplication] = []
    seen_ids: set[str] = set()

    for directory in directories:
        if not Path(directory).is_dir():
            continue
        for desktop_path in sorted(Path(directory).glob("*.desktop")):
            info = parse_desktop_file(desktop_path)
            app = _desktop_app_from_info(info)
            if app is None:
                continue
            desktop_id = app.desktop_id.lower()
            if desktop_id in seen_ids:
                continue
            seen_ids.add(desktop_id)
            applications.append(app)

    applications.sort(key=lambda app: app.name.casefold())
    return applications


def build_app_registry() -> dict[str, dict[str, str]]:
    registry: dict[str, dict[str, str]] = {}
    for app in load_desktop_applications():
        app_info = app.to_legacy_dict()
        wm_class = app.startup_wm_class.lower()
        if wm_class:
            registry[wm_class] = app_info
        registry[app.desktop_id.lower().removesuffix(".desktop")] = app_info
    return registry


def build_desktop_app_drag_payload(app: DesktopApplication | dict[str, str] | str) -> bytes:
    if isinstance(app, DesktopApplication):
        path = app.path
    elif isinstance(app, dict):
        path = str(app.get("path", "") or "").strip()
    else:
        path = str(app or "").strip()
    return json.dumps({"path": path}, ensure_ascii=True).encode("utf-8")


def parse_desktop_app_drag_payload(raw_payload: bytes | bytearray | str) -> str:
    if isinstance(raw_payload, (bytes, bytearray)):
        payload_text = bytes(raw_payload).decode("utf-8", errors="ignore")
    else:
        payload_text = str(raw_payload or "")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("path", "") or "").strip()


def _clean_exec(exec_str: str) -> list[str]:
    raw_exec = str(exec_str or "").strip()
    if not raw_exec:
        return []
    cleaned_exec = re.sub(r"%[fFuUdDnNvmikc]", "", raw_exec).strip()
    if not cleaned_exec:
        return []
    try:
        return shlex.split(cleaned_exec)
    except Exception:
        return []


def launch_app(app_data: DesktopApplication | dict[str, str]) -> bool:
    if isinstance(app_data, DesktopApplication):
        desktop_id = app_data.desktop_id
        desktop_path = app_data.path
        exec_str = app_data.exec
    else:
        desktop_id = str(app_data.get("desktop_id", "") or Path(str(app_data.get("path", "") or "")).name).strip()
        desktop_path = str(app_data.get("path", "") or "").strip()
        exec_str = str(app_data.get("Exec", "") or "").strip()

    if desktop_id and shutil.which("gtk-launch"):
        try:
            subprocess.Popen(
                ["gtk-launch", desktop_id.removesuffix(".desktop")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            pass

    if desktop_path and shutil.which("gio"):
        try:
            subprocess.Popen(
                ["gio", "launch", desktop_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            pass

    args = _clean_exec(exec_str)
    if not args:
        return False
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return True
