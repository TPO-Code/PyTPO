from __future__ import annotations

import logging
import subprocess

LOGGER = logging.getLogger("topbar.system_menu")


def run_command(cmd: list[str], *, timeout: float = 2.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def start_detached(cmd: list[str]) -> bool:
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as exc:
        LOGGER.warning("Failed to start detached command %s: %r", cmd, exc)
        return False


def launch_gnome_settings_panel(panel: str) -> bool:
    for cmd in (["gnome-control-center", panel], ["gnome-control-center"]):
        if start_detached(cmd):
            return True
    return False
