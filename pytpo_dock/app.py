from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .debug import install_qt_debug_message_logger, log_dock_debug, reset_dock_debug_log
from .ui.main_window import CustomDock


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv if argv is None else argv)
    log_path = reset_dock_debug_log()
    install_qt_debug_message_logger()
    log_dock_debug("dock-main-start", argv=effective_argv, log_path=log_path)

    app = QApplication(effective_argv)

    if QIcon.themeName() == "":
        QIcon.setThemeName("Adwaita")
    log_dock_debug("dock-theme-ready", theme_name=QIcon.themeName())

    dock = CustomDock()
    log_dock_debug(
        "dock-created",
        geometry=dock.geometry().getRect(),
        size=(dock.width(), dock.height()),
        visible=dock.isVisible(),
    )

    screen = QApplication.primaryScreen()
    if screen is None:
        log_dock_debug("dock-no-primary-screen")
    else:
        screen_geo = screen.geometry()
        target_x = (screen_geo.width() - dock.width()) // 2
        target_y = screen_geo.height()
        dock.move(target_x, target_y)
        log_dock_debug(
            "dock-initial-move",
            screen_geometry=screen_geo.getRect(),
            target_pos=(target_x, target_y),
            geometry=dock.geometry().getRect(),
            visible=dock.isVisible(),
        )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
