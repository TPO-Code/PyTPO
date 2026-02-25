import sys
import os
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication



from src.ui.python_ide import PythonIDE, request_project_activation
from src.settings_manager import SettingsManager
def _split_startup_args(argv: list[str]) -> tuple[list[str], bool]:
    filtered: list[str] = []
    force_no_project = False
    for arg in argv:
        if arg == PythonIDE.FORCE_NO_PROJECT_ARG:
            force_no_project = True
            continue
        filtered.append(arg)
    return filtered, force_no_project


def _canonical_existing_dir(path_value: str | Path | None) -> str | None:
    text = str(path_value or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_dir():
        return None
    try:
        return str(candidate.resolve())
    except Exception:
        return str(candidate)


def _load_startup_settings_manager(project_root: Path) -> SettingsManager | None:
    """Load startup settings using the same path/migration logic as the IDE."""
    ide_app_dir = PythonIDE._default_ide_app_dir()
    try:
        PythonIDE._migrate_legacy_ide_settings_file(ide_app_dir)
    except Exception:
        pass

    manager = SettingsManager(project_root=project_root, ide_app_dir=ide_app_dir)
    try:
        manager.load_all()
    except Exception:
        return None
    return manager


def _startup_project_from_cli_or_settings(argv: list[str]) -> str | None:
    if argv:
        resolved_explicit = _canonical_existing_dir(argv[0])
        if resolved_explicit is not None:
            return resolved_explicit

    manager = _load_startup_settings_manager(Path.cwd())
    if manager is None:
        return None

    if bool(manager.get("projects.open_last_project", scope_preference="ide", default=False)):
        recent = manager.get("projects.recent_projects", scope_preference="ide", default=[])
        if isinstance(recent, list):
            for item in recent:
                resolved_recent = _canonical_existing_dir(item)
                if resolved_recent is not None:
                    return resolved_recent
    return None


if __name__ == "__main__":
    cli_args, force_no_project = _split_startup_args(sys.argv[1:])
    startup_project = None if force_no_project else _startup_project_from_cli_or_settings(cli_args)
    start_no_project_mode = startup_project is None
    if start_no_project_mode:
        os.environ[PythonIDE.NO_PROJECT_MODE_ENV] = "1"
    else:
        os.environ.pop(PythonIDE.NO_PROJECT_MODE_ENV, None)
    if startup_project:
        try:
            os.chdir(startup_project)
        except Exception:
            pass
    target_project = startup_project or PythonIDE.no_project_instance_key()
    if request_project_activation(target_project):
        sys.exit(0)

    app = QApplication([sys.argv[0], *cli_args])
    app.setStyle("Fusion")
    app.setApplicationName(PythonIDE.APP_NAME)
    initial_display = f"{PythonIDE.APP_NAME} [{'Welcome' if start_no_project_mode else Path(startup_project or '').name}]"
    app.setApplicationDisplayName(initial_display)
    icon_path = PythonIDE.app_icon_path()
    if icon_path.is_file():
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            app.setWindowIcon(icon)
    ide = PythonIDE()
    ide.apply_selected_theme()
    ide.show()
    sys.exit(app.exec())
