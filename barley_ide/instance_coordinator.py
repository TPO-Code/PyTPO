from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _canonical_project_path(project_path: str) -> str:
    try:
        return str(Path(project_path).expanduser().resolve())
    except Exception:
        return str(Path(project_path).expanduser())


def project_instance_name(project_path: str) -> str:
    canonical = _canonical_project_path(project_path).lower().encode("utf-8", errors="replace")
    digest = hashlib.sha1(canonical).hexdigest()
    return f"pytpo-project-{digest}"


def request_project_activation(project_path: str, timeout_ms: int = 300) -> bool:
    """
    Try to notify an already-running instance for this project to activate.
    Returns True when an instance accepted the activation request.
    """
    socket = QLocalSocket()
    socket.connectToServer(project_instance_name(project_path))
    if not socket.waitForConnected(timeout_ms):
        return False

    try:
        socket.write(b"activate\n")
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
    finally:
        socket.disconnectFromServer()
    return True


class ProjectInstanceServer(QObject):
    activateRequested = Signal()

    def __init__(self, project_path: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server_name = project_instance_name(project_path)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)

    def listen(self) -> bool:
        if self.server.listen(self.server_name):
            return True

        # Recover from stale socket endpoints after abrupt shutdown.
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
        payload = bytes(socket.readAll()).decode("utf-8", errors="ignore").strip().lower()
        if payload.startswith("activate"):
            self.activateRequested.emit()
            socket.write(b"ok\n")
            socket.flush()
        socket.disconnectFromServer()
