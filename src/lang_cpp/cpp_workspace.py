"""Project-scoped clangd workspace management."""

from __future__ import annotations

import os
import re
import shlex
import time
from collections import deque
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from src.lsp.lsp_client import LspClient


_CPP_EXTENSIONS = {".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hh", ".hxx"}
_SKIP_WALK_DIRS = {".git", ".hg", ".svn", ".venv", ".tide", "__pycache__", "node_modules"}

_DEFAULT_CPP_SETTINGS: dict[str, Any] = {
    "enable_cpp": True,
    "clangd_path": "clangd",
    "query_driver": "",
    "compile_commands_mode": "auto",  # auto | manual
    "compile_commands_path": "",
    "did_change_debounce_ms": 320,
    "log_lsp_traffic": False,
    "fallback": {
        "c_standard": "",
        "cpp_standard": "",
        "include_paths": [],
        "defines": [],
        "extra_flags": [],
    },
}


class CppWorkspace(QObject):
    """Owns one clangd process for a single project workspace."""

    diagnosticsUpdated = Signal(str, object)  # file_path, diagnostics[list[dict]]
    statusMessage = Signal(str)
    lspTraffic = Signal(str, str)

    def __init__(self, project_root: str, canonicalize, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._canonicalize = canonicalize
        self.project_root = self._canonicalize(project_root)

        self._settings: dict[str, Any] = _normalize_cpp_settings({})
        self._client = LspClient(self)
        self._client.notificationReceived.connect(self._on_notification)
        self._client.statusMessage.connect(self.statusMessage.emit)
        self._client.trafficLogged.connect(self.lspTraffic.emit)
        self._client.ready.connect(self._on_client_ready)

        self._editor_to_path: dict[str, str] = {}
        self._path_refcount: dict[str, int] = {}
        self._path_language_id: dict[str, str] = {}
        self._change_timers: dict[str, QTimer] = {}
        self._pending_text_by_path: dict[str, str] = {}
        self._last_text_by_path: dict[str, str] = {}

        self._compile_commands_dir: str = ""
        self._fallback_flags: list[str] = []
        self._active_command_signature: (
            tuple[str, tuple[str, ...], str, tuple[str, ...], bool] | None
        ) = None
        self._config_restart_pending = False
        self._next_command_probe_at = 0.0

    def shutdown(self) -> None:
        for timer in self._change_timers.values():
            timer.stop()
            timer.deleteLater()
        self._change_timers.clear()
        self._pending_text_by_path.clear()
        self._last_text_by_path.clear()
        self._editor_to_path.clear()
        self._path_refcount.clear()
        self._path_language_id.clear()
        self._active_command_signature = None
        self._config_restart_pending = False
        self._next_command_probe_at = 0.0
        self._client.stop()

    def supports_file(self, file_path: str) -> bool:
        suffix = os.path.splitext(str(file_path or ""))[1].lower()
        return suffix in _CPP_EXTENSIONS

    def is_enabled(self) -> bool:
        return bool(self._settings.get("enable_cpp", True))

    def update_settings(self, cpp_settings: dict[str, Any]) -> None:
        old_sig = self._active_command_signature
        self._settings = _normalize_cpp_settings(cpp_settings)
        self._client.set_log_traffic(bool(self._settings.get("log_lsp_traffic", False)))

        if not self.is_enabled():
            self._clear_all_tracked_diagnostics()
            self._client.stop()
            self._active_command_signature = None
            return

        # Recompute launch choices; restart if command/environment changed.
        program, args, compile_dir, fallback_flags = self._build_clangd_command()
        new_sig = (
            program,
            tuple(args),
            compile_dir,
            tuple(fallback_flags),
            bool(self._settings.get("log_lsp_traffic", False)),
        )
        self._compile_commands_dir = compile_dir
        self._fallback_flags = list(fallback_flags)
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
        self, *, editor_id: str, file_path: str, source_text: str, language_id: str
    ) -> None:
        editor_key = str(editor_id or "").strip()
        if not editor_key:
            return
        cpath = self._canonicalize(file_path)

        prev_path = self._editor_to_path.get(editor_key, "")
        if prev_path and prev_path == cpath and self.is_enabled() and self.supports_file(cpath):
            self._path_language_id[cpath] = str(
                language_id or self._path_language_id.get(cpath, "cpp")
            )
            self._last_text_by_path[cpath] = str(source_text or "")
            return
        if prev_path and prev_path != cpath:
            self.detach_editor(editor_key)

        if not self.is_enabled() or not self.supports_file(cpath):
            if prev_path:
                self.detach_editor(editor_key)
            return

        self._maybe_refresh_command_signature()
        self._editor_to_path[editor_key] = cpath
        self._path_language_id[cpath] = str(language_id or "cpp")
        self._last_text_by_path[cpath] = str(source_text or "")
        prev_count = int(self._path_refcount.get(cpath, 0))
        self._path_refcount[cpath] = prev_count + 1

        self._ensure_client_started()
        uri = self._client.path_to_uri(cpath)
        if prev_count <= 0:
            self._client.did_open(
                uri=uri, language_id=self._path_language_id[cpath], text=source_text or ""
            )
        elif source_text is not None:
            self._client.did_change(
                uri=uri,
                text=source_text or "",
                language_id=self._path_language_id.get(cpath, _language_id_for_cpp_path(cpath)),
            )

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
        self._path_language_id.pop(cpath, None)
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
        if not self.supports_file(cpath):
            return
        if cpath not in self._path_refcount:
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
        timer.start(int(self._settings.get("did_change_debounce_ms", 320)))

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
        cpath = self._canonicalize(file_path)
        self.diagnosticsUpdated.emit(cpath, [])

    def clear_all_diagnostics(self) -> None:
        self._clear_all_tracked_diagnostics()

    def request_completion(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        callback,
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "cpp_support_disabled"})
            return 0

        completion_context = _completion_context_for_position(
            source_text=str(source_text or ""),
            line=int(line),
            column=int(column),
        )
        params = {
            "textDocument": {"uri": self._client.path_to_uri(cpath)},
            "position": {"line": max(0, int(line) - 1), "character": max(0, int(column))},
            "context": completion_context,
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
        callback,
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "cpp_support_disabled"})
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

    def request_definition(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        callback,
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "cpp_support_disabled"})
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
        callback,
    ) -> int:
        cpath = self._canonicalize(file_path)
        if not self._prepare_request_document(cpath, source_text):
            callback(None, {"code": -32000, "message": "cpp_support_disabled"})
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

    def cancel_request(self, request_id: int) -> None:
        self._client.cancel_request(int(request_id or 0))

    def _prepare_request_document(self, cpath: str, source_text: str) -> bool:
        if not self.is_enabled() or not self.supports_file(cpath):
            return False
        if not self._maybe_refresh_command_signature():
            return False
        self._ensure_client_started()
        uri = self._client.path_to_uri(cpath)
        if cpath not in self._path_refcount:
            language_id = _language_id_for_cpp_path(cpath)
            self._path_refcount[cpath] = 1
            self._path_language_id[cpath] = language_id
            self._client.did_open(uri=uri, language_id=language_id, text=source_text or "")
        self._pending_text_by_path.pop(cpath, None)
        timer = self._change_timers.get(cpath)
        if timer is not None:
            timer.stop()
        self._last_text_by_path[cpath] = str(source_text or "")
        self._client.did_change(
            uri=uri,
            text=source_text or "",
            language_id=self._path_language_id.get(cpath, _language_id_for_cpp_path(cpath)),
        )
        return True

    def _flush_debounced_change(self, file_path: str) -> None:
        text = self._pending_text_by_path.pop(file_path, None)
        if text is None:
            return
        if file_path not in self._path_refcount:
            return
        self._last_text_by_path[file_path] = text
        uri = self._client.path_to_uri(file_path)
        self._client.did_change(
            uri=uri,
            text=text,
            language_id=self._path_language_id.get(file_path, _language_id_for_cpp_path(file_path)),
        )

    def _maybe_refresh_command_signature(self, *, force: bool = False) -> bool:
        if not self.is_enabled():
            return False
        if self._config_restart_pending:
            return False
        now = time.monotonic()
        if not force and now < float(self._next_command_probe_at):
            return True
        self._next_command_probe_at = now + 2.0

        program, args, compile_dir, fallback_flags = self._build_clangd_command()
        new_sig = (
            program,
            tuple(args),
            compile_dir,
            tuple(fallback_flags),
            bool(self._settings.get("log_lsp_traffic", False)),
        )
        old_sig = self._active_command_signature
        self._compile_commands_dir = compile_dir
        self._fallback_flags = list(fallback_flags)
        if old_sig == new_sig:
            return True

        self._active_command_signature = new_sig
        if self._client.is_running():
            self._config_restart_pending = True

            def _restart_once() -> None:
                try:
                    self._client.stopped.disconnect(_restart_once)
                except Exception:
                    pass
                self._config_restart_pending = False
                self._ensure_client_started()

            self._client.stopped.connect(_restart_once)
            self._client.stop()
            return False
        return True

    def _ensure_client_started(self) -> None:
        if not self.is_enabled():
            return
        if self._client.is_running():
            return

        if self._active_command_signature is None:
            self._maybe_refresh_command_signature(force=True)
        active = self._active_command_signature
        if not active:
            return
        program = str(active[0] or "clangd")
        args = list(active[1] or ())
        compile_dir = str(active[2] or "")
        fallback_flags = list(active[3] or ())

        init_options: dict[str, Any] = {}
        if fallback_flags:
            init_options["fallbackFlags"] = list(fallback_flags)
        root_uri = self._client.path_to_uri(self.project_root)
        self._client.start(
            program=program,
            args=args,
            cwd=self.project_root,
            root_uri=root_uri,
            workspace_name=os.path.basename(self.project_root) or self.project_root,
            initialization_options=init_options,
            trace="off",
        )
        mode_label = f"compile_commands={compile_dir}" if compile_dir else "fallback_flags"
        self.statusMessage.emit(f"clangd start: {mode_label}")

    def _build_clangd_command(self) -> tuple[str, list[str], str, list[str]]:
        clangd_path = str(self._settings.get("clangd_path") or "clangd").strip() or "clangd"

        compile_dir = _discover_compile_commands_dir(
            project_root=self.project_root,
            mode=str(self._settings.get("compile_commands_mode") or "auto"),
            manual_path=str(self._settings.get("compile_commands_path") or ""),
        )
        fallback_flags: list[str] = []
        if not compile_dir:
            fallback_flags = _build_fallback_flags(
                project_root=self.project_root,
                fallback_cfg=self._settings.get("fallback", {}),
            )

        args = [
            "--background-index",
            "--clang-tidy",
            "--header-insertion=never",
            "--completion-style=detailed",
        ]
        query_driver = _effective_query_driver_globs(self._settings.get("query_driver"))
        if query_driver:
            args.append(f"--query-driver={query_driver}")
        if compile_dir:
            args.append(f"--compile-commands-dir={compile_dir}")
        return clangd_path, args, compile_dir, fallback_flags

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
        # Re-open tracked documents after process restart.
        for path, refcount in list(self._path_refcount.items()):
            if int(refcount) <= 0:
                continue
            language_id = str(self._path_language_id.get(path) or _language_id_for_cpp_path(path))
            text = str(self._last_text_by_path.get(path, ""))
            uri = self._client.path_to_uri(path)
            self._client.did_open(uri=uri, language_id=language_id, text=text)

    def _clear_all_tracked_diagnostics(self) -> None:
        emitted: set[str] = set()
        for path in list(self._path_refcount.keys()):
            cpath = self._canonicalize(path)
            if cpath in emitted:
                continue
            emitted.add(cpath)
            self.diagnosticsUpdated.emit(cpath, [])


def _normalize_cpp_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(_DEFAULT_CPP_SETTINGS)
    fallback = dict(_DEFAULT_CPP_SETTINGS["fallback"])
    if isinstance(raw, dict):
        data.update(raw)
        if isinstance(raw.get("fallback"), dict):
            fallback.update(raw["fallback"])
    data["fallback"] = fallback
    data["enable_cpp"] = bool(data.get("enable_cpp", True))
    data["clangd_path"] = str(data.get("clangd_path") or "clangd").strip() or "clangd"
    data["query_driver"] = _normalize_query_driver_value(data.get("query_driver"))
    mode = str(data.get("compile_commands_mode") or "auto").strip().lower()
    data["compile_commands_mode"] = mode if mode in {"auto", "manual"} else "auto"
    data["compile_commands_path"] = str(data.get("compile_commands_path") or "").strip()
    data["did_change_debounce_ms"] = max(
        150, min(3000, int(data.get("did_change_debounce_ms", 320)))
    )
    data["log_lsp_traffic"] = bool(data.get("log_lsp_traffic", False))

    fallback["c_standard"] = str(fallback.get("c_standard") or "").strip()
    fallback["cpp_standard"] = str(fallback.get("cpp_standard") or "").strip()
    fallback["include_paths"] = _to_str_list(fallback.get("include_paths"))
    fallback["defines"] = _to_str_list(fallback.get("defines"))
    fallback["extra_flags"] = _normalize_extra_flags(fallback.get("extra_flags"))
    return data


def _to_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _normalize_extra_flags(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            return [str(item).strip() for item in shlex.split(text) if str(item).strip()]
        except Exception:
            return [part for part in text.split() if part]
    return []


def _normalize_query_driver_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return ",".join(cleaned)
    return ""


def _effective_query_driver_globs(value: object) -> str:
    explicit = _normalize_query_driver_value(value)
    if explicit.lower() in {"off", "none", "disabled"}:
        return ""
    if explicit:
        return explicit

    auto_candidates = [
        "/usr/bin/c++",
        "/usr/bin/g++",
        "/usr/bin/clang++",
        "/usr/bin/clang",
        "/usr/bin/x86_64-linux-gnu-g++",
        "/usr/bin/x86_64-linux-gnu-c++",
    ]
    found: list[str] = []
    for path in auto_candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            found.append(path)
    return ",".join(found)


def _build_fallback_flags(project_root: str, fallback_cfg: object) -> list[str]:
    cfg = fallback_cfg if isinstance(fallback_cfg, dict) else {}
    flags: list[str] = []
    cpp_std = str(cfg.get("cpp_standard") or "").strip()
    c_std = str(cfg.get("c_standard") or "").strip()
    if cpp_std:
        flags.append(f"-std={cpp_std}")
    elif c_std:
        flags.append(f"-std={c_std}")

    for raw in _to_str_list(cfg.get("include_paths")):
        include_path = raw
        if not os.path.isabs(include_path):
            include_path = os.path.join(project_root, include_path)
        flags.append(f"-I{include_path}")

    for raw in _to_str_list(cfg.get("defines")):
        if raw.startswith("-D"):
            flags.append(raw)
        else:
            flags.append(f"-D{raw}")

    flags.extend(_normalize_extra_flags(cfg.get("extra_flags")))
    return flags


def _discover_compile_commands_dir(*, project_root: str, mode: str, manual_path: str) -> str:
    mode_norm = str(mode or "auto").strip().lower()
    if mode_norm == "manual":
        resolved = _resolve_manual_compile_commands(project_root, manual_path)
        return resolved or ""
    return _discover_compile_commands_auto(project_root)


def _resolve_manual_compile_commands(project_root: str, manual_path: str) -> str:
    path = str(manual_path or "").strip()
    if not path:
        return ""
    if not os.path.isabs(path):
        path = os.path.join(project_root, path)
    path = os.path.abspath(path)
    if os.path.isdir(path):
        candidate = os.path.join(path, "compile_commands.json")
        return path if os.path.isfile(candidate) else ""
    if os.path.isfile(path) and os.path.basename(path) == "compile_commands.json":
        return os.path.dirname(path)
    return ""


def _discover_compile_commands_auto(project_root: str) -> str:
    root = os.path.abspath(project_root)
    direct_candidates = [
        root,
        os.path.join(root, "build"),
        os.path.join(root, "build-debug"),
        os.path.join(root, "build-release"),
        os.path.join(root, "out", "build"),
    ]
    for directory in direct_candidates:
        if os.path.isfile(os.path.join(directory, "compile_commands.json")):
            return directory

    child_dirs = _list_child_dirs(root)
    for child in child_dirs:
        base = os.path.basename(child).lower()
        if base.startswith("cmake-build-") and os.path.isfile(
            os.path.join(child, "compile_commands.json")
        ):
            return child

    walk_hits = _search_compile_commands(root, max_depth=3)
    if not walk_hits:
        return ""
    walk_hits.sort(key=lambda item: _compile_dir_rank(root, item))
    return walk_hits[0]


def _list_child_dirs(root: str) -> list[str]:
    out: list[str] = []
    try:
        for entry in os.scandir(root):
            if entry.is_dir(follow_symlinks=False):
                out.append(entry.path)
    except Exception:
        return []
    return out


def _search_compile_commands(root: str, *, max_depth: int) -> list[str]:
    found: list[str] = []
    queue: deque[tuple[str, int]] = deque([(root, 0)])
    seen: set[str] = set()
    while queue:
        directory, depth = queue.popleft()
        if directory in seen:
            continue
        seen.add(directory)

        candidate = os.path.join(directory, "compile_commands.json")
        if os.path.isfile(candidate):
            found.append(directory)

        if depth >= max_depth:
            continue
        try:
            entries = list(os.scandir(directory))
        except Exception:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            name = entry.name
            if name in _SKIP_WALK_DIRS or name.startswith("."):
                continue
            queue.append((entry.path, depth + 1))
    return found


def _compile_dir_rank(root: str, directory: str) -> tuple[int, int, str]:
    abs_dir = os.path.abspath(directory)
    rel = os.path.relpath(abs_dir, root)
    depth = len([part for part in rel.split(os.sep) if part and part != "."])
    base = os.path.basename(abs_dir).lower()
    if abs_dir == root:
        score = 0
    elif base == "build":
        score = 1
    elif base in {"build-debug", "build-release"}:
        score = 2
    elif rel.replace("\\", "/").startswith("out/build"):
        score = 3
    elif base.startswith("cmake-build-"):
        score = 4
    else:
        score = 10
    return score, depth, rel.lower()


def _completion_context_for_position(*, source_text: str, line: int, column: int) -> dict[str, Any]:
    """
    Prefer trigger-character completion in contexts where clangd is more precise
    (`std::`, `obj.`, `ptr->`, `#include <...>`), otherwise use invocation mode.
    """
    line_text = ""
    lines = str(source_text or "").splitlines()
    line_idx = max(0, int(line) - 1)
    if line_idx < len(lines):
        line_text = str(lines[line_idx] or "")
    col = max(0, min(int(column), len(line_text)))

    prev_char = line_text[col - 1] if col > 0 else ""
    prev_two = line_text[col - 2 : col] if col >= 2 else ""

    trigger_char = ""
    if prev_two in {"::", "->"}:
        trigger_char = prev_two[-1]
    elif prev_char in {".", ":", "<", '"', "/"}:
        trigger_char = prev_char

    # Header completion support even when cursor has already moved past `<` / `"`.
    before_cursor = line_text[:col]
    if not trigger_char and re.match(r'^\s*#\s*include\s*[<"][^>"]*$', before_cursor):
        if "<" in before_cursor and before_cursor.rfind("<") >= before_cursor.rfind('"'):
            trigger_char = "<"
        elif '"' in before_cursor:
            trigger_char = '"'

    if trigger_char:
        return {"triggerKind": 2, "triggerCharacter": trigger_char}
    return {"triggerKind": 1}


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
                "source": str(item.get("source") or "clangd").strip() or "clangd",
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


def _language_id_for_cpp_path(file_path: str) -> str:
    suffix = os.path.splitext(str(file_path or ""))[1].lower()
    if suffix in {".c", ".h"}:
        return "c"
    return "cpp"
