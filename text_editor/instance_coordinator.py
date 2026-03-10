from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "pytpo-text-editor"


def _serialize_payload(paths: list[Path]) -> bytes:
    payload = {
        "command": "open",
        "paths": [str(path.expanduser()) for path in paths],
    }
    return json.dumps(payload).encode("utf-8", errors="replace")


def request_editor_activation(paths: list[Path], timeout_ms: int = 500) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(timeout_ms):
        return False

    try:
        socket.write(_serialize_payload(paths))
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
    finally:
        socket.disconnectFromServer()
    return True


class TextEditorInstanceServer(QObject):
    openPathsRequested = Signal(object)
    activateRequested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)

    def listen(self) -> bool:
        if self.server.listen(SERVER_NAME):
            return True
        QLocalServer.removeServer(SERVER_NAME)
        return self.server.listen(SERVER_NAME)

    def close(self) -> None:
        try:
            self.server.close()
        finally:
            QLocalServer.removeServer(SERVER_NAME)

    def _on_new_connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            socket.readyRead.connect(lambda s=socket: self._handle_ready_read(s))
            socket.disconnected.connect(socket.deleteLater)

    def _handle_ready_read(self, socket: QLocalSocket) -> None:
        raw = bytes(socket.readAll())
        paths: list[str] = []
        try:
            payload = json.loads(raw.decode("utf-8", errors="ignore"))
        except Exception:
            payload = {}

        if isinstance(payload, dict):
            raw_paths = payload.get("paths")
            if isinstance(raw_paths, list):
                paths = [str(item).strip() for item in raw_paths if str(item).strip()]

        if paths:
            self.openPathsRequested.emit(paths)
        self.activateRequested.emit()
        socket.write(b"ok\n")
        socket.flush()
        socket.disconnectFromServer()
