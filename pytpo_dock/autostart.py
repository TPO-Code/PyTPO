from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DOCK_AUTOSTART_DESKTOP_ID = "pytpo-dock.desktop"


def xdg_config_home() -> Path:
    raw = str(os.environ.get("XDG_CONFIG_HOME", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".config"


def xdg_autostart_dir() -> Path:
    return xdg_config_home() / "autostart"


@dataclass(frozen=True, slots=True)
class DockStartupState:
    backend: str
    entry_path: Path
    enabled: bool


class DockAutostartManager:
    """Manage the dock's XDG autostart entry.

    The interface is intentionally small so a future systemd user service
    backend can slot in without changing callers that only care about
    status, enable, disable, and cleanup.
    """

    backend_name = "xdg-autostart"
    desktop_id = DOCK_AUTOSTART_DESKTOP_ID
    command = "pytpo-dock"
    display_name = "PyTPO Dock"
    comment = "Start the PyTPO Dock with your desktop session"
    icon_name = "pytpo-dock"

    def entry_path(self) -> Path:
        return xdg_autostart_dir() / self.desktop_id

    def state(self) -> DockStartupState:
        path = self.entry_path()
        return DockStartupState(
            backend=self.backend_name,
            entry_path=path,
            enabled=path.is_file(),
        )

    def is_enabled(self) -> bool:
        return self.state().enabled

    def render_entry(self) -> str:
        lines = [
            "[Desktop Entry]",
            "Version=1.0",
            "Type=Application",
            f"Name={self.display_name}",
            f"Comment={self.comment}",
            f"Exec={self.command}",
            f"TryExec={self.command}",
            f"Icon={self.icon_name}",
            "Terminal=false",
            "StartupNotify=false",
            "NoDisplay=true",
            "X-GNOME-Autostart-enabled=true",
        ]
        return "\n".join(lines) + "\n"

    def enable(self) -> Path:
        path = self.entry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render_entry(), encoding="utf-8")
        return path

    def disable(self) -> bool:
        path = self.entry_path()
        if not path.exists():
            return False
        if not path.is_file():
            raise OSError(f"Autostart entry path is not a file: {path}")
        path.unlink()
        return True

    def cleanup(self) -> list[Path]:
        removed: list[Path] = []
        path = self.entry_path()
        if self.disable():
            removed.append(path)
        return removed
