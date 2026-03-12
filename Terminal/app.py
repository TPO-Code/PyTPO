from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from .main_window import APP_NAME, TerminalMainWindow


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--cwd",
        dest="cwd",
        default="",
        help="Working directory to use for new terminal tabs.",
    )
    return parser


def _normalize_cli_cwd(raw: object) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    resolved = Path(text).expanduser()
    if resolved.is_file():
        resolved = resolved.parent
    if resolved.is_dir():
        return str(resolved)
    return None


def _resolve_startup_cwd_and_qt_args(argv: list[str]) -> tuple[str | None, list[str]]:
    parser = _build_parser()
    parsed, unknown = parser.parse_known_args(argv[1:])
    return _normalize_cli_cwd(parsed.cwd), [argv[0], *unknown]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    runtime_startup_cwd, qt_args = _resolve_startup_cwd_and_qt_args(args)

    app = QApplication(qt_args)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)

    window = TerminalMainWindow(startup_cwd_override=runtime_startup_cwd)
    window.show()
    window.raise_()
    window.activateWindow()

    return app.exec()
