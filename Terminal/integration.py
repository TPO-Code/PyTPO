from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from .paths import repo_root


@dataclass(slots=True)
class IntegrationScriptResult:
    ok: bool
    returncode: int
    command: list[str]
    stdout: str
    stderr: str
    script_path: Path


def scripts_dir() -> Path:
    return Path(__file__).resolve().parent / "scripts"


def install_default_terminal(
    *,
    launcher_path: str | None = None,
    desktop_file: str | None = None,
) -> IntegrationScriptResult:
    return _run_integration_script(
        "install_default_terminal.sh",
        launcher_path=launcher_path,
        desktop_file=desktop_file,
    )


def uninstall_default_terminal(
    *,
    launcher_path: str | None = None,
    desktop_file: str | None = None,
) -> IntegrationScriptResult:
    return _run_integration_script(
        "uninstall_default_terminal.sh",
        launcher_path=launcher_path,
        desktop_file=desktop_file,
    )


def _run_integration_script(
    script_name: str,
    *,
    launcher_path: str | None = None,
    desktop_file: str | None = None,
) -> IntegrationScriptResult:
    script_path = scripts_dir() / str(script_name or "").strip()
    if not script_path.is_file():
        return IntegrationScriptResult(
            ok=False,
            returncode=127,
            command=[],
            stdout="",
            stderr=f"Script was not found: {script_path}",
            script_path=script_path,
        )

    command = ["bash", str(script_path), "--repo-root", str(repo_root())]

    normalized_launcher = str(Path(str(launcher_path or "").strip()).expanduser()) if launcher_path else ""
    if normalized_launcher:
        command.extend(["--launcher-path", normalized_launcher])

    normalized_desktop = str(Path(str(desktop_file or "").strip()).expanduser()) if desktop_file else ""
    if normalized_desktop:
        command.extend(["--desktop-file", normalized_desktop])

    try:
        completed = subprocess.run(
            command,
            cwd=str(repo_root()),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return IntegrationScriptResult(
            ok=False,
            returncode=1,
            command=command,
            stdout="",
            stderr=str(exc),
            script_path=script_path,
        )

    return IntegrationScriptResult(
        ok=(completed.returncode == 0),
        returncode=int(completed.returncode),
        command=command,
        stdout=str(completed.stdout or "").strip(),
        stderr=str(completed.stderr or "").strip(),
        script_path=script_path,
    )
