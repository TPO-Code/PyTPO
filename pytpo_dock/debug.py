from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

from .storage_paths import dock_debug_log_path

_LOG_LOCK = Lock()
_QT_MESSAGE_HANDLER = None
_QT_MESSAGE_HANDLER_INSTALLED = False
_QT_MESSAGE_TYPE_NAMES = {
    QtMsgType.QtDebugMsg: "debug",
    QtMsgType.QtInfoMsg: "info",
    QtMsgType.QtWarningMsg: "warning",
    QtMsgType.QtCriticalMsg: "critical",
    QtMsgType.QtFatalMsg: "fatal",
}


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _normalize_value(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def reset_dock_debug_log(*, reason: str = "dock-startup") -> Path:
    path = dock_debug_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_LOCK:
        path.write_text("", encoding="utf-8")
    log_dock_debug("debug-log-reset", reason=reason, pid=os.getpid(), path=path)
    return path


def log_dock_debug(event: str, /, **fields: Any) -> None:
    path = dock_debug_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    details = " ".join(f"{key}={_normalize_value(value)}" for key, value in fields.items())
    line = f"{_timestamp()} {event}"
    if details:
        line = f"{line} {details}"
    with _LOG_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")


def install_qt_debug_message_logger() -> None:
    global _QT_MESSAGE_HANDLER, _QT_MESSAGE_HANDLER_INSTALLED
    if _QT_MESSAGE_HANDLER_INSTALLED:
        return

    def _handler(message_type, context, message):
        context_parts = []
        if context is not None:
            file_name = getattr(context, "file", None)
            if file_name:
                context_parts.append(str(file_name))
            line_number = getattr(context, "line", None)
            if line_number:
                context_parts.append(f"line={line_number}")
            function_name = getattr(context, "function", None)
            if function_name:
                context_parts.append(f"function={function_name}")
        log_dock_debug(
            "qt-message",
            level=_QT_MESSAGE_TYPE_NAMES.get(message_type, str(message_type)),
            message=message,
            context=" ".join(context_parts) if context_parts else "",
        )

    _QT_MESSAGE_HANDLER = _handler
    qInstallMessageHandler(_QT_MESSAGE_HANDLER)
    _QT_MESSAGE_HANDLER_INSTALLED = True
    log_dock_debug("qt-message-handler-installed")
