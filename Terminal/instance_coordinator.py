from __future__ import annotations

import hashlib
import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .paths import repo_root


def terminal_instance_name() -> str:
    canonical = str(repo_root().resolve()).lower().encode("utf-8", errors="replace")
    digest = hashlib.sha1(canonical).hexdigest()
    return f"pytpo-terminal-{digest}"


def request_open_tab(cwd: str | None, timeout_ms: int = 350) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(terminal_instance_name())
    if not socket.waitForConnected(timeout_ms):
        return False

    payload = json.dumps(
        {
            "action": "open_tab",
            "cwd": str(cwd or "").strip(),
        },
        ensure_ascii=True,
    ).encode("utf-8")
    try:
        socket.write(payload)
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
    finally:
        socket.disconnectFromServer()
    return True


class TerminalInstanceServer(QObject):
    openTabRequested = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server_name = terminal_instance_name()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)

    def listen(self) -> bool:
        if self.server.listen(self.server_name):
            return True
        QLocalServer.removeServer(self.server_name)
        return self.server.listen(self.server_name)

    def close(self) -> None:
        try:
            self.server.close()
        finally:
            QLocalServer.removeServer(self.server_name)

    def _on_new_connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            socket.readyRead.connect(lambda s=socket: self._handle_ready_read(s))
            socket.disconnected.connect(socket.deleteLater)

    def _handle_ready_read(self, socket: QLocalSocket) -> None:
        raw = bytes(socket.readAll()).decode("utf-8", errors="ignore").strip()
        requested_cwd = ""
        if raw:
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
            if isinstance(data, dict) and str(data.get("action") or "").strip().lower() == "open_tab":
                requested_cwd = str(data.get("cwd") or "").strip()
        self.openTabRequested.emit(requested_cwd)
        socket.write(b"ok\n")
        socket.flush()
        socket.disconnectFromServer()
