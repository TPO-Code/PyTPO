from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import APP_NAME, TerminalMainWindow


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)

    app = QApplication(args)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)

    window = TerminalMainWindow()
    window.show()
    window.raise_()
    window.activateWindow()

    return app.exec()
