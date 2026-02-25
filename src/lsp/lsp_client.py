"""Async LSP client over stdio using QProcess."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal

from .json_rpc import LspMessageParser, encode_lsp_message


ResultCallback = Callable[[object], None]
ErrorCallback = Callable[[object], None]


@dataclass
class _PendingRequest:
    method: str
    on_result: ResultCallback | None
    on_error: ErrorCallback | None


class LspClient(QObject):
    """JSON-RPC LSP client with request correlation and doc version tracking."""

    started = Signal()
    stopped = Signal()
    ready = Signal()
    notificationReceived = Signal(str, object)
    statusMessage = Signal(str)
    trafficLogged = Signal(str, str)  # direction, payload

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc = QProcess(self)
        self._proc.readyReadStandardOutput.connect(self._on_stdout_ready)
        self._proc.readyReadStandardError.connect(self._on_stderr_ready)
        self._proc.started.connect(self._on_process_started)
        self._proc.finished.connect(self._on_process_finished)
        self._proc.errorOccurred.connect(self._on_process_error)

        self._parser = LspMessageParser()
        self._next_request_id = 1
        self._pending: dict[int, _PendingRequest] = {}
        self._queued_messages: list[dict[str, Any]] = []
        self._doc_versions: dict[str, int] = {}

        self._running = False
        self._ready = False
        self._log_traffic = False
        self._shutting_down = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._force_terminate_if_running)

        self._startup: dict[str, Any] = {
            "program": "clangd",
            "args": [],
            "cwd": "",
            "root_uri": "",
            "trace": "off",
            "initialization_options": {},
            "workspace_name": "",
        }
        self.server_capabilities: dict[str, Any] = {}

    @staticmethod
    def path_to_uri(path: str) -> str:
        return QUrl.fromLocalFile(os.path.abspath(path)).toString()

    @staticmethod
    def uri_to_path(uri: str) -> str:
        url = QUrl(uri)
        if url.isLocalFile():
            return str(url.toLocalFile())
        return str(uri or "")

    def set_log_traffic(self, enabled: bool) -> None:
        self._log_traffic = bool(enabled)

    def is_running(self) -> bool:
        return self._proc.state() != QProcess.NotRunning

    def is_ready(self) -> bool:
        return self._ready and self._running

    def start(
        self,
        *,
        program: str,
        args: list[str] | None = None,
        cwd: str = "",
        root_uri: str = "",
        workspace_name: str = "",
        initialization_options: dict[str, Any] | None = None,
        trace: str = "off",
    ) -> None:
        self.stop()

        self._startup = {
            "program": str(program or "clangd").strip() or "clangd",
            "args": [str(item) for item in (args or [])],
            "cwd": str(cwd or "").strip(),
            "root_uri": str(root_uri or "").strip(),
            "workspace_name": str(workspace_name or "").strip(),
            "trace": str(trace or "off").strip() or "off",
            "initialization_options": initialization_options if isinstance(initialization_options, dict) else {},
        }

        self._running = False
        self._ready = False
        self._shutting_down = False
        self._next_request_id = 1
        self._pending.clear()
        self._queued_messages.clear()
        self._doc_versions.clear()
        self._parser.reset()
        self.server_capabilities = {}

        self._proc.setProgram(self._startup["program"])
        self._proc.setArguments(self._startup["args"])
        if self._startup["cwd"] and os.path.isdir(self._startup["cwd"]):
            self._proc.setWorkingDirectory(self._startup["cwd"])
        self._proc.start()

    def stop(self) -> None:
        state = self._proc.state()
        if state == QProcess.NotRunning:
            self._running = False
            self._ready = False
            self._pending.clear()
            self._queued_messages.clear()
            self._doc_versions.clear()
            self._parser.reset()
            return

        self._shutting_down = True
        if state == QProcess.Starting:
            # Process pipes may not be writable yet; skip graceful shutdown handshake.
            self._force_terminate_if_running()
            return

        if self.is_ready():
            self.request(
                "shutdown",
                {},
                on_result=lambda _res: self._send_exit(),
                on_error=lambda _err: self._send_exit(),
            )
            self._shutdown_timer.start(1200)
            return

        self._send_exit()
        self._force_terminate_if_running()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_result: ResultCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._pending[request_id] = _PendingRequest(
            method=str(method or ""),
            on_result=on_result,
            on_error=on_error,
        )
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": str(method or ""),
            "params": params if isinstance(params, dict) else {},
        }
        self._send_or_queue(payload, requires_ready=str(method or "").strip().lower() != "initialize")
        return request_id

    def notify(self, method: str, params: dict[str, Any] | None = None, *, requires_ready: bool = True) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": str(method or ""),
            "params": params if isinstance(params, dict) else {},
        }
        self._send_or_queue(payload, requires_ready=requires_ready)

    def cancel_request(self, request_id: int) -> None:
        rid = int(request_id or 0)
        if rid <= 0:
            return
        self.notify("$/cancelRequest", {"id": rid})

    def did_open(self, *, uri: str, language_id: str, text: str) -> int:
        clean_uri = str(uri or "").strip()
        if not clean_uri:
            return 0
        if clean_uri in self._doc_versions:
            return self.did_change(uri=clean_uri, text=text, language_id=language_id)
        version = 1
        self._doc_versions[clean_uri] = version
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": clean_uri,
                    "languageId": str(language_id or "plaintext"),
                    "version": version,
                    "text": str(text or ""),
                }
            },
        )
        return version

    def did_change(self, *, uri: str, text: str, language_id: str = "plaintext") -> int:
        clean_uri = str(uri or "").strip()
        if not clean_uri:
            return 0
        if clean_uri not in self._doc_versions:
            return self.did_open(uri=clean_uri, language_id=language_id, text=text)
        version = int(self._doc_versions.get(clean_uri, 0)) + 1
        self._doc_versions[clean_uri] = version
        self.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": clean_uri, "version": version},
                "contentChanges": [{"text": str(text or "")}],
            },
        )
        return version

    def did_save(self, *, uri: str, text: str | None = None) -> None:
        clean_uri = str(uri or "").strip()
        if not clean_uri or clean_uri not in self._doc_versions:
            return
        payload: dict[str, Any] = {"textDocument": {"uri": clean_uri}}
        if text is not None:
            payload["text"] = str(text)
        self.notify("textDocument/didSave", payload)

    def did_close(self, *, uri: str) -> None:
        clean_uri = str(uri or "").strip()
        if not clean_uri:
            return
        if clean_uri in self._doc_versions:
            self.notify("textDocument/didClose", {"textDocument": {"uri": clean_uri}})
            self._doc_versions.pop(clean_uri, None)

    def _send_or_queue(self, payload: dict[str, Any], *, requires_ready: bool) -> None:
        if self._proc.state() == QProcess.NotRunning:
            return
        if requires_ready and not self.is_ready():
            self._queued_messages.append(payload)
            return
        self._send_now(payload)

    def _send_now(self, payload: dict[str, Any]) -> None:
        if self._proc.state() == QProcess.NotRunning:
            return
        raw = encode_lsp_message(payload)
        try:
            written = int(self._proc.write(raw))
        except Exception:
            return
        if written < 0:
            if not self._shutting_down:
                self.statusMessage.emit(f"LSP write failed: {self._proc.errorString()}")
            return
        self._log_payload("out", payload)

    def _flush_queued_messages(self) -> None:
        if not self.is_ready():
            return
        queued = list(self._queued_messages)
        self._queued_messages.clear()
        for payload in queued:
            self._send_now(payload)

    def _send_exit(self) -> None:
        if self._proc.state() == QProcess.NotRunning:
            return
        self.notify("exit", {}, requires_ready=False)

    def _force_terminate_if_running(self) -> None:
        if self._proc.state() == QProcess.NotRunning:
            return
        try:
            self._proc.terminate()
        except Exception:
            pass
        if self._proc.state() != QProcess.NotRunning:
            try:
                self._proc.kill()
            except Exception:
                pass

    def _on_process_started(self) -> None:
        self._running = True
        self.started.emit()

        root_uri = str(self._startup.get("root_uri") or "")
        workspace_name = str(self._startup.get("workspace_name") or "").strip()
        if not workspace_name:
            workspace_name = self.uri_to_path(root_uri) or "workspace"

        initialize_params: dict[str, Any] = {
            "processId": int(os.getpid()),
            "clientInfo": {"name": "PyTPO"},
            "rootUri": root_uri or None,
            "trace": str(self._startup.get("trace") or "off"),
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {
                        "relatedInformation": True,
                        "tagSupport": {"valueSet": [1, 2]},
                    },
                    "completion": {
                        "completionItem": {
                            "snippetSupport": False,
                            "documentationFormat": ["markdown", "plaintext"],
                            "resolveSupport": {"properties": ["documentation", "detail"]},
                        }
                    },
                    "signatureHelp": {
                        "signatureInformation": {
                            "documentationFormat": ["markdown", "plaintext"],
                            "parameterInformation": {"labelOffsetSupport": True},
                        }
                    },
                    "definition": {"linkSupport": True},
                    "references": {},
                    "rename": {"prepareSupport": False},
                    "codeAction": {
                        "codeActionLiteralSupport": {
                            "codeActionKind": {
                                "valueSet": [
                                    "",
                                    "quickfix",
                                    "refactor",
                                    "refactor.extract",
                                    "refactor.inline",
                                    "refactor.rewrite",
                                    "source",
                                ]
                            }
                        }
                    },
                    "documentSymbol": {
                        "hierarchicalDocumentSymbolSupport": True,
                        "symbolKind": {
                            "valueSet": list(range(1, 27)),
                        },
                    },
                    "foldingRange": {"lineFoldingOnly": True},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "synchronization": {
                        "didSave": True,
                        "willSave": False,
                        "didSaveTextDocument": True,
                    },
                },
                "workspace": {
                    "workspaceFolders": True,
                    "applyEdit": True,
                    "workspaceEdit": {
                        "documentChanges": True,
                    },
                },
            },
            "initializationOptions": self._startup.get("initialization_options", {}),
        }
        if root_uri:
            initialize_params["workspaceFolders"] = [{"uri": root_uri, "name": workspace_name}]

        self.request(
            "initialize",
            initialize_params,
            on_result=self._on_initialize_result,
            on_error=self._on_initialize_error,
        )

    def _on_process_finished(self, _exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._shutdown_timer.stop()
        was_running = self._running
        self._running = False
        self._ready = False
        self._pending.clear()
        self._queued_messages.clear()
        self._doc_versions.clear()
        self._parser.reset()
        self.server_capabilities = {}
        self._shutting_down = False
        if was_running:
            self.stopped.emit()

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if self._shutting_down and error in {
            QProcess.ProcessError.Crashed,
            QProcess.ProcessError.ReadError,
            QProcess.ProcessError.WriteError,
        }:
            return
        self.statusMessage.emit(f"LSP process error: {self._proc.errorString()}")

    def _on_stdout_ready(self) -> None:
        raw = bytes(self._proc.readAllStandardOutput())
        if not raw:
            return
        for message in self._parser.feed(raw):
            self._log_payload("in", message)
            self._handle_message(message)

    def _on_stderr_ready(self) -> None:
        raw = bytes(self._proc.readAllStandardError())
        if not raw:
            return
        text = raw.decode("utf-8", errors="replace").strip()
        if text:
            self.statusMessage.emit(text)

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and "method" not in message:
            self._handle_response(message)
            return

        method = str(message.get("method") or "").strip()
        if not method:
            return

        # Server request: respond "method not found" to keep protocol healthy.
        if "id" in message:
            self._send_now(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32601, "message": f"Method not supported: {method}"},
                }
            )
            return

        params = message.get("params")
        if method == "window/logMessage" and isinstance(params, dict):
            text = str(params.get("message") or "").strip()
            if text:
                self.statusMessage.emit(text)
        elif method == "window/showMessage" and isinstance(params, dict):
            text = str(params.get("message") or "").strip()
            if text:
                self.statusMessage.emit(text)
        self.notificationReceived.emit(method, params)

    def _handle_response(self, message: dict[str, Any]) -> None:
        raw_id = message.get("id")
        try:
            request_id = int(raw_id)
        except Exception:
            return
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return

        if "error" in message:
            error_obj = message.get("error")
            if callable(pending.on_error):
                pending.on_error(error_obj)
            return
        if callable(pending.on_result):
            pending.on_result(message.get("result"))

    def _on_initialize_result(self, result_obj: object) -> None:
        result = result_obj if isinstance(result_obj, dict) else {}
        caps = result.get("capabilities")
        self.server_capabilities = caps if isinstance(caps, dict) else {}
        self._ready = True
        self.notify("initialized", {}, requires_ready=False)
        self.ready.emit()
        self._flush_queued_messages()

    def _on_initialize_error(self, error_obj: object) -> None:
        self._ready = False
        self.statusMessage.emit(f"LSP initialize failed: {error_obj}")

    def _log_payload(self, direction: str, payload: dict[str, Any]) -> None:
        if not self._log_traffic:
            return
        try:
            text = str(payload)
        except Exception:
            text = "<unprintable>"
        self.trafficLogged.emit(str(direction), text)
