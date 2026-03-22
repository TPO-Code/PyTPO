from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from .debug import install_qt_debug_message_logger, log_dock_debug, reset_dock_debug_log
from .xlib_window_source import ensure_xlib_available
from .ui.main_window import CustomDock


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv if argv is None else argv)
    log_path = reset_dock_debug_log()
    install_qt_debug_message_logger()
    log_dock_debug("dock-main-start", argv=effective_argv, log_path=log_path)
    try:
        ensure_xlib_available()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    app = QApplication(effective_argv)
    if hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName("pytpo-dock")
    app.setQuitOnLastWindowClosed(False)
    icon_path = Path(__file__).with_name("icon.png")
    app_icon = QIcon(str(icon_path)) if icon_path.is_file() else QIcon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    log_dock_debug("dock-quit-on-last-window-closed-disabled")

    if QIcon.themeName() == "":
        QIcon.setThemeName("Adwaita")
    log_dock_debug("dock-theme-ready", theme_name=QIcon.themeName())

    dock = CustomDock()
    if not app_icon.isNull():
        dock.setWindowIcon(app_icon)
    log_dock_debug(
        "dock-created",
        geometry=dock.geometry().getRect(),
        size=(dock.width(), dock.height()),
        visible=dock.isVisible(),
    )

    screen = QGuiApplication.screenAt(QCursor.pos())
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:
        log_dock_debug("dock-no-primary-screen")
    else:
        initializer = getattr(dock, "ensure_hidden_window_mapped", None)
        if callable(initializer):
            initializer()
        else:
            screen_geo = screen.geometry()
            target_x = screen_geo.x() + (screen_geo.width() - dock.width()) // 2
            target_y = screen_geo.y() + screen_geo.height()
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
