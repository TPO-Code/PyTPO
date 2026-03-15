from __future__ import annotations

import argparse
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .dbus import SingleInstanceGuard, topbar_instance_name
from .log_utils import configure_logging
from .ui import TopBar


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    parser = argparse.ArgumentParser(description="PyTPO standalone X11 topbar prototype")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose topbar debug logging.",
    )
    args, qt_args = parser.parse_known_args(argv[1:])

    logger, log_path = configure_logging(debug=args.debug)
    instance_guard = SingleInstanceGuard(topbar_instance_name(argv[0]))
    if not instance_guard.acquire():
        logger.info("another topbar instance is already running; exiting")
        return 0

    app = QApplication([argv[0], *qt_args])
    app.aboutToQuit.connect(instance_guard.close)
    QIcon.setThemeName("Pop")

    logger.info("Persistent log file: %s", log_path)
    logger.info("Starting topbar with Qt args: %s", qt_args)
    bar = TopBar()
    bar.show()
    return app.exec()
