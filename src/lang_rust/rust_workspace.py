"""Workspace-scoped rust-analyzer management."""

from __future__ import annotations

import os
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from src.lsp.lsp_client import LspClient


_RUST_EXTENSIONS = {".rs"}

_DEFAULT_RUST_SETTINGS: dict[str, Any] = {
    "enable_rust": True,
    "rust_analyzer_path": "rust-analyzer",
    "rust_analyzer_args": [],
    "did_change_debounce_ms": 260,
    "log_lsp_traffic": False,
    "initialization_options": {},
}


class RustWorkspace(QObject):
    diagnosticsUpdated = Signal(str, object)  # file_path, diagnostics[list[dict]]
    statusMessage = Signal(str)
    lspTraffic = Signal(str, str)

    def __init__(self, workspace_root: str, canonicalize, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._canonicalize = canonicalize
        self.workspace_root = self._canonicalize(workspace_root)

        self._settings: dict[str, Any] = _normalize_rust_settings({})
        self._client = LspClient(self)
        self._client.notificationReceived.connect(self._on_notification)
        self._client.statusMessage.connect(self.statusMessage.emit)
        self._client.trafficLogged.connect(self.lspTraffic.emit)
        self._client.ready.connect(self._on_client_ready)

        self._editor_to_path: dict[str, str] = {}
        self._path_refcount: dict[str, int] = {}
        self._change_timers: dict[str, QTimer] = {}
        self._pending_text_by_path: dict[str, str] = {}
        self._last_text_by_path: dict[str, str] = {}

        self._active_command_signature: tuple[str, tuple[str, ...], bool, str] | None = None
        self._config_restart_pending = False

    def shutdown(self) -> None:
        for timer in self._change_timers.values():
            timer.stop()
            timer.deleteLater()
        self._change_timers.clear()
        self._pending_text_by_path.clear()
        self._last_text_by_path.clear()
        self._editor_to_path.clear()
        self._path_refcount.clear()
        self._active_command_signature = None
        self._config_restart_pending = False
        self._client.stop()

    def supports_file(self, file_path: str) -> bool:
        suffix = os.path.splitext(str(file_path or ""))[1].lower()
        return suffix in _RUST_EXTENSIONS

    def is_enabled(self) -> bool:
        return bool(self._settings.get("enable_rust", True))

    def update_settings(self, rust_settings: dict[str, Any]) -> None:
        old_sig = self._active_command_signature
        self._settings = _normalize_rust_settings(rust_settings)
        self._client.set_log_traffic(bool(self._settings.get("log_lsp_traffic", False)))

        if not self.is_enabled():
            self._clear_all_tracked_diagnostics()
            self._client.stop()
            self._active_command_signature = None
            return

        program, args = self._build_server_command()
        init_key = _stable_json_key(self._settings.get("initialization_options", {}))
        new_sig = (
            program,
            tuple(args),
            bool(self._settings.get("log_lsp_traffic", False)),
            init_key,
        )
        self._active_command_signature = new_sig

        if old_sig is not None and new_sig != old_sig and self._client.is_running():

            def _restart_once() -> None:
                try:
                    self._client.stopped.disconnect(_restart_once)
                except Exception:
                    pass
                self._ensure_client_started()

            self._client.stopped.connect(_restart_once)
            self._client.stop()

    def attach_editor(
        self,
        *,
        editor_id: str,
        file_path: str,
        source_text: str,
        language_id: str,
    ) -> None:
        _ = language_id
        editor_key = str(editor_id or "").strip()
        if not editor_key:
            return
        cpath = self._canonicalize(file_path)
        if not self.is_enabled() or not self.supports_file(cpath):
            prev = self._editor_to_path.get(editor_key, "")
            if prev:
                self.detach_editor(editor_key)
            return

        prev_path = self._editor_to_path.get(editor_key, "")
        if prev_path and prev_path == cpath:
            self._last_text_by_path[cpath] = str(source_text or "")
            return
        if prev_path and prev_path != cpath:
            self.detach_editor(editor_key)

        self._editor_to_path[editor_key] = cpath
        self._last_text_by_path[cpath] = str(source_text or "")
        prev_count = int(self._path_refcount.get(cpath, 0))
        self._path_refcount[cpath] = prev_count + 1

        self._ensure_client_started()
        uri = self._client.path_to_uri(cpath)
        if prev_count <= 0:
            self._client.did_open(uri=uri, language_id="rust", text=source_text or "")
        elif source_text is not None:
            self._client.did_change(uri=uri, text=source_text or "", language_id="rust")

    def detach_editor(self, editor_id: str) -> None:
        editor_key = str(editor_id or "").strip()
        if not editor_key:
            return
        cpath = self._editor_to_path.pop(editor_key, None)
        if not cpath:
            return

        current = max(0, int(self._path_refcount.get(cpath, 0)) - 1)
        if current > 0:
            self._path_refcount[cpath] = current
            return

        self._path_refcount.pop(cpath, None)
        self._pending_text_by_path.pop(cpath, None)
        self._last_text_by_path.pop(cpath, None)
        timer = self._change_timers.pop(cpath, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

        uri = self._client.path_to_uri(cpath)
        self._client.did_close(uri=uri)
        self.diagnosticsUpdated.emit(cpath, [])

    def document_changed(self, *, file_path: str, source_text: str) -> None:
        if not self.is_enabled():
            return
        cpath = self._canonicalize(file_path)
        if not self.supports_file(cpath) or cpath not in self._path_refcount:
            return

        self._ensure_client_started()
        self._pending_text_by_path[cpath] = str(source_text or "")
        self._last_text_by_path[cpath] = str(source_text or "")
        timer = self._change_timers.get(cpath)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda p=cpath: self._flush_debounced_change(p))
            self._change_timers[cpath] = timer
        timer.start(int(self._settings.get("did_change_debounce_ms", 260)))

    def document_saved(self, *, file_path: str, source_text: str | None = None) -> None:
        cpath = self._canonicalize(file_path)
        if cpath not in self._path_refcount:
            return
        if source_text is not None:
            self._last_text_by_path[cpath] = str(source_text)
        self._flush_debounced_change(cpath)
        uri = self._client.path_to_uri(cpath)
        self._client.did_save(uri=uri, text=source_text)

    def clear_file_diagnostics(self, file_path: str) -> None:
        self.diagnosticsUpdated.emit(self._canonicalize(file_path), [])

    def clear_all_diagnostics(self) -> None:
        self._clear_all_tracked_diagnostics()

    def request_completion(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        callback: Callable[[object, object], None],
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return 0
        params = {
            "textDocument": {"uri": self._client.path_to_uri(cpath)},
            "position": {"line": max(0, int(line) - 1), "character": max(0, int(column))},
            "context": {"triggerKind": 1},
        }
        return self._client.request(
            "textDocument/completion",
            params,
            on_result=lambda result: callback(result, None),
            on_error=lambda err: callback(None, err),
        )

    def request_hover(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        callback: Callable[[object, object], None],
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return 0
        params = {
            "textDocument": {"uri": self._client.path_to_uri(cpath)},
            "position": {"line": max(0, int(line) - 1), "character": max(0, int(column))},
        }
        return self._client.request(
            "textDocument/hover",
            params,
            on_result=lambda result: callback(result, None),
            on_error=lambda err: callback(None, err),
        )

    def request_signature_help(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        callback: Callable[[object, object], None],
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return 0
        params = {
            "textDocument": {"uri": self._client.path_to_uri(cpath)},
            "position": {"line": max(0, int(line) - 1), "character": max(0, int(column))},
            "context": {"triggerKind": 1},
        }
        return self._client.request(
            "textDocument/signatureHelp",
            params,
            on_result=lambda result: callback(result, None),
            on_error=lambda err: callback(None, err),
        )

    def request_definition(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        callback: Callable[[object, object], None],
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return 0
        params = {
            "textDocument": {"uri": self._client.path_to_uri(cpath)},
            "position": {"line": max(0, int(line) - 1), "character": max(0, int(column))},
        }
        return self._client.request(
            "textDocument/definition",
            params,
            on_result=lambda result: callback(result, None),
            on_error=lambda err: callback(None, err),
        )

    def request_references(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        callback: Callable[[object, object], None],
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return 0
        params = {
            "textDocument": {"uri": self._client.path_to_uri(cpath)},
            "position": {"line": max(0, int(line) - 1), "character": max(0, int(column))},
            "context": {"includeDeclaration": True},
        }
        return self._client.request(
            "textDocument/references",
            params,
            on_result=lambda result: callback(result, None),
            on_error=lambda err: callback(None, err),
        )

    def request_rename(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        new_name: str,
        callback: Callable[[object, object], None],
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return 0
        params = {
            "textDocument": {"uri": self._client.path_to_uri(cpath)},
            "position": {"line": max(0, int(line) - 1), "character": max(0, int(column))},
            "newName": str(new_name or "").strip(),
        }
        return self._client.request(
            "textDocument/rename",
            params,
            on_result=lambda result: callback(result, None),
            on_error=lambda err: callback(None, err),
        )

    def request_code_actions(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        diagnostics: list[dict[str, Any]],
        callback: Callable[[object, object], None],
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return 0

        lsp_diags = _diagnostics_to_lsp_context(diagnostics)
        params = {
            "textDocument": {"uri": self._client.path_to_uri(cpath)},
            "range": {
                "start": {"line": max(0, int(line) - 1), "character": max(0, int(column))},
                "end": {"line": max(0, int(line) - 1), "character": max(0, int(column) + 1)},
            },
            "context": {"diagnostics": lsp_diags},
        }
        return self._client.request(
            "textDocument/codeAction",
            params,
            on_result=lambda result: callback(result, None),
            on_error=lambda err: callback(None, err),
        )

    def request_document_symbols(
        self,
        *,
        file_path: str,
        source_text: str,
        callback: Callable[[object, object], None],
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return 0
        params = {"textDocument": {"uri": self._client.path_to_uri(cpath)}}
        return self._client.request(
            "textDocument/documentSymbol",
            params,
            on_result=lambda result: callback(result, None),
            on_error=lambda err: callback(None, err),
        )

    def request_folding_ranges(
        self,
        *,
        file_path: str,
        source_text: str,
        callback: Callable[[object, object], None],
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return 0
        params = {"textDocument": {"uri": self._client.path_to_uri(cpath)}}
        return self._client.request(
            "textDocument/foldingRange",
            params,
            on_result=lambda result: callback(result, None),
            on_error=lambda err: callback(None, err),
        )

    def cancel_request(self, request_id: int) -> None:
        self._client.cancel_request(int(request_id or 0))

    def _prepare_request_document(self, cpath: str, source_text: str) -> bool:
        if not self.is_enabled() or not self.supports_file(cpath):
            return False
        if self._config_restart_pending:
            return False

        self._ensure_client_started()
        uri = self._client.path_to_uri(cpath)
        if cpath not in self._path_refcount:
            self._path_refcount[cpath] = 1
            self._client.did_open(uri=uri, language_id="rust", text=source_text or "")

        self._pending_text_by_path.pop(cpath, None)
        timer = self._change_timers.get(cpath)
        if timer is not None:
            timer.stop()
        self._last_text_by_path[cpath] = str(source_text or "")
        self._client.did_change(uri=uri, text=source_text or "", language_id="rust")
        return True

    def _flush_debounced_change(self, file_path: str) -> None:
        text = self._pending_text_by_path.pop(file_path, None)
        if text is None or file_path not in self._path_refcount:
            return
        self._last_text_by_path[file_path] = text
        uri = self._client.path_to_uri(file_path)
        self._client.did_change(uri=uri, text=text, language_id="rust")

    def _ensure_client_started(self) -> None:
        if not self.is_enabled():
            return
        if self._client.is_running():
            return

        if self._active_command_signature is None:
            program, args = self._build_server_command()
            init_key = _stable_json_key(self._settings.get("initialization_options", {}))
            self._active_command_signature = (
                program,
                tuple(args),
                bool(self._settings.get("log_lsp_traffic", False)),
                init_key,
            )

        active = self._active_command_signature
        if not active:
            return
        program = str(active[0] or "rust-analyzer")
        args = list(active[1] or ())
        init_options = self._settings.get("initialization_options", {})
        if not isinstance(init_options, dict):
            init_options = {}

        root_uri = self._client.path_to_uri(self.workspace_root)
        self._client.start(
            program=program,
            args=args,
            cwd=self.workspace_root,
            root_uri=root_uri,
            workspace_name=os.path.basename(self.workspace_root) or self.workspace_root,
            initialization_options=init_options,
            trace="off",
        )
        self.statusMessage.emit("rust-analyzer start")

    def _build_server_command(self) -> tuple[str, list[str]]:
        rust_analyzer_path = str(self._settings.get("rust_analyzer_path") or "rust-analyzer").strip() or "rust-analyzer"
        args = list(self._settings.get("rust_analyzer_args") or [])
        return rust_analyzer_path, [str(arg) for arg in args if str(arg).strip()]

    def _on_notification(self, method: str, params_obj: object) -> None:
        params = params_obj if isinstance(params_obj, dict) else {}
        if method != "textDocument/publishDiagnostics":
            return
        uri = str(params.get("uri") or "").strip()
        if not uri:
            return
        file_path = self._canonicalize(self._client.uri_to_path(uri))
        diagnostics = _normalize_lsp_diagnostics(file_path, params.get("diagnostics"))
        self.diagnosticsUpdated.emit(file_path, diagnostics)

    def _on_client_ready(self) -> None:
        for path, refcount in list(self._path_refcount.items()):
            if int(refcount) <= 0:
                continue
            text = str(self._last_text_by_path.get(path, ""))
            uri = self._client.path_to_uri(path)
            self._client.did_open(uri=uri, language_id="rust", text=text)

    def _clear_all_tracked_diagnostics(self) -> None:
        emitted: set[str] = set()
        for path in list(self._path_refcount.keys()):
            cpath = self._canonicalize(path)
            if cpath in emitted:
                continue
            emitted.add(cpath)
            self.diagnosticsUpdated.emit(cpath, [])


def _normalize_rust_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(_DEFAULT_RUST_SETTINGS)
    if isinstance(raw, dict):
        data.update(raw)
    data["enable_rust"] = bool(data.get("enable_rust", True))
    data["rust_analyzer_path"] = (
        str(data.get("rust_analyzer_path") or "rust-analyzer").strip() or "rust-analyzer"
    )
    args = data.get("rust_analyzer_args")
    if isinstance(args, list):
        data["rust_analyzer_args"] = [str(item).strip() for item in args if str(item).strip()]
    elif isinstance(args, str):
        text = str(args).strip()
        data["rust_analyzer_args"] = [text] if text else []
    else:
        data["rust_analyzer_args"] = []

    data["did_change_debounce_ms"] = max(
        120,
        min(3000, int(data.get("did_change_debounce_ms", 260))),
    )
    data["log_lsp_traffic"] = bool(data.get("log_lsp_traffic", False))
    init_opts = data.get("initialization_options")
    data["initialization_options"] = init_opts if isinstance(init_opts, dict) else {}
    return data


def _normalize_lsp_diagnostics(file_path: str, diagnostics_obj: object) -> list[dict[str, Any]]:
    diagnostics = diagnostics_obj if isinstance(diagnostics_obj, list) else []
    out: list[dict[str, Any]] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        rng = item.get("range")
        if not isinstance(rng, dict):
            continue
        start = rng.get("start") if isinstance(rng.get("start"), dict) else {}
        end = rng.get("end") if isinstance(rng.get("end"), dict) else {}

        line = max(1, int(start.get("line", 0)) + 1)
        col = max(1, int(start.get("character", 0)) + 1)
        end_line = max(1, int(end.get("line", 0)) + 1)
        end_col = max(1, int(end.get("character", 0)) + 1)
        severity = _diagnostic_severity_name(int(item.get("severity", 2)))
        code_raw = item.get("code")
        if isinstance(code_raw, dict):
            code = str(code_raw.get("value") or "")
        else:
            code = str(code_raw or "")
        out.append(
            {
                "file_path": str(file_path or ""),
                "line": line,
                "column": col,
                "end_line": end_line,
                "end_column": end_col,
                "severity": severity,
                "code": code,
                "message": str(item.get("message") or "").strip(),
                "source": str(item.get("source") or "rust-analyzer").strip() or "rust-analyzer",
            }
        )
    return out


def _diagnostic_severity_name(severity: int) -> str:
    if severity == 1:
        return "error"
    if severity == 2:
        return "warning"
    if severity == 3:
        return "info"
    return "hint"


def _diagnostics_to_lsp_context(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        try:
            line = max(1, int(item.get("line") or 1))
            col = max(1, int(item.get("column") or 1))
            end_line = max(line, int(item.get("end_line") or line))
            end_col = max(1, int(item.get("end_column") or col))
        except Exception:
            continue
        out.append(
            {
                "range": {
                    "start": {"line": line - 1, "character": max(0, col - 1)},
                    "end": {"line": end_line - 1, "character": max(0, end_col - 1)},
                },
                "severity": _diagnostic_severity_to_lsp(item.get("severity")),
                "code": str(item.get("code") or ""),
                "source": str(item.get("source") or "rust-analyzer"),
                "message": str(item.get("message") or ""),
            }
        )
    return out


def _diagnostic_severity_to_lsp(value: object) -> int:
    text = str(value or "").strip().lower()
    if text == "error":
        return 1
    if text == "warning":
        return 2
    if text == "info":
        return 3
    return 4


def _stable_json_key(value: object) -> str:
    if isinstance(value, dict):
        parts = []
        for key in sorted(value.keys()):
            parts.append(f"{key}:{_stable_json_key(value[key])}")
        return "{" + ",".join(parts) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_stable_json_key(item) for item in value) + "]"
    return str(value)

