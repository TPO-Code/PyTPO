from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from pytpo.services.app_icons import shared_app_icon_path

from .main_window import build_window
from .settings_dialog import AppGridSettingsDialog

_PREVIOUS_QT_MESSAGE_HANDLER = None
_QT_MESSAGE_HANDLER = None


def _should_suppress_qt_message(category: str, message: str) -> bool:
    category_text = str(category or "").strip()
    message_text = str(message or "").strip()
    return category_text == "qt.svg" and "Could not resolve property" in message_text


def _install_qt_message_filter() -> None:
    global _PREVIOUS_QT_MESSAGE_HANDLER, _QT_MESSAGE_HANDLER

    if _QT_MESSAGE_HANDLER is not None:
        return

    def _handler(message_type, context, message):
        if _should_suppress_qt_message(getattr(context, "category", ""), message):
            return
        if _PREVIOUS_QT_MESSAGE_HANDLER is not None:
            _PREVIOUS_QT_MESSAGE_HANDLER(message_type, context, message)
            return
        level_name = {
            QtMsgType.QtDebugMsg: "qt",
            QtMsgType.QtInfoMsg: "qt",
            QtMsgType.QtWarningMsg: "qt",
            QtMsgType.QtCriticalMsg: "qt",
            QtMsgType.QtFatalMsg: "qt",
        }.get(message_type, "qt")
        print(f"{level_name}: {message}", file=sys.stderr)

    _QT_MESSAGE_HANDLER = _handler
    _PREVIOUS_QT_MESSAGE_HANDLER = qInstallMessageHandler(_QT_MESSAGE_HANDLER)


def _build_argument_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="pytpo-appgrid")
    parser.add_argument(
        "--settings",
        action="store_true",
        help="Open the app grid settings dialog instead of the launcher grid.",
    )
    return parser


def _parse_args(argv: list[str]) -> Namespace:
    return _build_argument_parser().parse_args(argv[1:])


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv if argv is None else argv)
    args = _parse_args(effective_argv)
    _install_qt_message_filter()
    app = QApplication(effective_argv)
    if hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName("pytpo-appgrid")
    app.setApplicationName("pytpo-appgrid")
    icon_path = shared_app_icon_path("appgrid")
    app_icon = QIcon(str(icon_path)) if icon_path.is_file() else QIcon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    if QIcon.themeName() == "":
        QIcon.setThemeName("Adwaita")

    if args.settings:
        dialog = AppGridSettingsDialog(parent=None)
        if not app_icon.isNull():
            dialog.setWindowIcon(app_icon)
        dialog.exec()
        return 0

    window = build_window()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.show()
    return app.exec()
