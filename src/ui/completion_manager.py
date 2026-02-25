from __future__ import annotations

import builtins
import concurrent.futures
import importlib
import json
import keyword
import os
import pkgutil
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal


@dataclass
class _CompletionPayload:
    file_path: str
    source_text: str
    line: int
    column: int
    prefix: str
    token: int
    reason: str
    completion_cfg: dict
    interpreter: str
    project_root: str
    recency: dict[str, int]


@dataclass
class _SignaturePayload:
    file_path: str
    source_text: str
    line: int
    column: int
    token: int
    interpreter: str
    project_root: str


@dataclass
class _DefinitionPayload:
    file_path: str
    source_text: str
    line: int
    column: int
    token: int
    interpreter: str
    project_root: str


@dataclass
class _ReferencesPayload:
    file_path: str
    source_text: str
    line: int
    column: int
    token: int
    interpreter: str
    project_root: str


# =========================================================
# Persistent Jedi worker process
# =========================================================

JEDI_SERVER_SCRIPT = r"""
import json
import os
import sys

try:
    import jedi
except Exception as exc:
    print(json.dumps({"state": "missing", "error": str(exc)}), flush=True)
    raise SystemExit(0)

# Small in-process cache for repeated identical requests
_CACHE = {}
_CACHE_ORDER = []
_CACHE_MAX = 64

def _cache_get(key):
    item = _CACHE.get(key)
    if not item:
        return None
    return item

def _cache_put(key, value):
    _CACHE[key] = value
    _CACHE_ORDER.append(key)
    if len(_CACHE_ORDER) > _CACHE_MAX:
        old = _CACHE_ORDER.pop(0)
        _CACHE.pop(old, None)

def _clamp_line_col(source, line, col):
    lines = source.splitlines()
    if not lines:
        return 1, 0
    line = max(1, min(int(line), len(lines)))
    line_text = lines[line - 1]
    col = max(0, min(int(col), len(line_text)))
    return line, col

def _project_for_root(project_root):
    if not project_root:
        return None
    root = os.path.abspath(project_root)
    if not os.path.isdir(root):
        return None
    try:
        # added_sys_path is key for local imports
        return jedi.Project(path=root, added_sys_path=[root])
    except Exception:
        return None

while True:
    raw = sys.stdin.readline()
    if not raw:
        break

    raw = raw.strip()
    if not raw:
        continue

    try:
        req = json.loads(raw)
    except Exception:
        print(json.dumps({"state": "failed", "error": "bad_json"}), flush=True)
        continue

    request_type = str(req.get("request") or "complete").strip().lower()
    source = str(req.get("source") or "")
    path = str(req.get("path") or "") or None
    project_root = str(req.get("project_root") or "").strip() or None
    line = req.get("line", 1)
    col = req.get("column", 0)
    max_items = max(5, min(2000, int(req.get("max_items") or 200)))
    include_signatures = bool(req.get("include_signatures", True))

    line, col = _clamp_line_col(source, line, col)

    cache_key = (
        request_type,
        path or "",
        line,
        col,
        max_items,
        project_root or "",
        hash(source),
        include_signatures,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        if request_type in {"signature", "definition", "references"}:
            print(json.dumps({"state": "ok", **cached}), flush=True)
        else:
            print(json.dumps({"state": "ok", "items": cached}), flush=True)
        continue

    # Ensure project root is import-visible inside worker process only
    if project_root:
        root = os.path.abspath(project_root)
        if root in sys.path:
            try:
                sys.path.remove(root)
            except Exception:
                pass
        sys.path.insert(0, root)

    project = _project_for_root(project_root)

    try:
        if project is not None:
            script = jedi.Script(code=source, path=path, project=project)
        else:
            script = jedi.Script(code=source, path=path)

        if request_type == "signature":
            probes = [(line, col)]
            if col > 0:
                probes.append((line, col - 1))

            sig_text = ""
            sig_doc = ""
            sig_name = ""
            sig_full_name = ""
            sig_module_name = ""
            for pline, pcol in probes:
                try:
                    sigs = script.get_signatures(pline, pcol)
                except Exception:
                    sigs = []
                if not sigs:
                    continue
                sig = sigs[0]
                try:
                    sig_text = str(sig.to_string() or "")
                except Exception:
                    sig_text = str(sig or "")
                try:
                    sig_doc = str(sig.docstring(raw=False) or "")
                except Exception:
                    sig_doc = ""
                sig_name = str(getattr(sig, "name", "") or "")
                sig_full_name = str(getattr(sig, "full_name", "") or "")
                sig_module_name = str(getattr(sig, "module_name", "") or "")
                try:
                    inferred = script.infer(pline, pcol)
                except Exception:
                    inferred = []
                if inferred:
                    d0 = inferred[0]
                    if not sig_doc:
                        try:
                            sig_doc = str(d0.docstring(raw=False) or "")
                        except Exception:
                            pass
                    if not sig_full_name:
                        sig_full_name = str(getattr(d0, "full_name", "") or "")
                    if not sig_module_name:
                        sig_module_name = str(getattr(d0, "module_name", "") or "")
                if sig_text:
                    break

            payload = {
                "signature": sig_text,
                "documentation": sig_doc,
                "label": sig_name,
                "full_name": sig_full_name,
                "module_name": sig_module_name,
                "source": "jedi",
            }
            _cache_put(cache_key, payload)
            print(json.dumps({"state": "ok", **payload}), flush=True)
        elif request_type == "definition":
            probes = [(line, col)]
            if col > 0:
                probes.append((line, col - 1))

            defs = []
            for pline, pcol in probes:
                try:
                    defs = script.goto(pline, pcol, follow_imports=True, follow_builtin_imports=True)
                except Exception:
                    defs = []
                if defs:
                    break

            results = []
            for d in defs:
                try:
                    module_path = str(getattr(d, "module_path", "") or "")
                except Exception:
                    module_path = ""
                if module_path:
                    module_path = os.path.abspath(module_path)
                results.append(
                    {
                        "name": str(getattr(d, "name", "") or ""),
                        "type": str(getattr(d, "type", "") or ""),
                        "module_path": module_path,
                        "module_name": str(getattr(d, "module_name", "") or ""),
                        "full_name": str(getattr(d, "full_name", "") or ""),
                        "line": int(getattr(d, "line", 0) or 0),
                        "column": int(getattr(d, "column", 0) or 0),
                        "description": str(getattr(d, "description", "") or ""),
                    }
                )

            payload = {"results": results}
            _cache_put(cache_key, payload)
            print(json.dumps({"state": "ok", **payload}), flush=True)
        elif request_type == "references":
            probes = [(line, col)]
            if col > 0:
                probes.append((line, col - 1))

            refs = []
            defs = []
            for pline, pcol in probes:
                try:
                    defs = script.goto(pline, pcol, follow_imports=True, follow_builtin_imports=True)
                except Exception:
                    defs = []
                try:
                    refs = script.get_references(pline, pcol, include_builtins=False)
                except Exception:
                    refs = []
                if refs or defs:
                    break

            def _node_to_item(node):
                try:
                    module_path = str(getattr(node, "module_path", "") or "")
                except Exception:
                    module_path = ""
                if module_path:
                    module_path = os.path.abspath(module_path)
                return {
                    "name": str(getattr(node, "name", "") or ""),
                    "type": str(getattr(node, "type", "") or ""),
                    "module_path": module_path,
                    "module_name": str(getattr(node, "module_name", "") or ""),
                    "full_name": str(getattr(node, "full_name", "") or ""),
                    "line": int(getattr(node, "line", 0) or 0),
                    "column": int(getattr(node, "column", 0) or 0),
                    "description": str(getattr(node, "description", "") or ""),
                }

            payload = {
                "definitions": [_node_to_item(d) for d in defs],
                "references": [_node_to_item(r) for r in refs],
            }
            _cache_put(cache_key, payload)
            print(json.dumps({"state": "ok", **payload}), flush=True)
        else:
            comps = script.complete(line, col)
            out = []
            for c in comps:
                name = str(getattr(c, "name", "") or "")
                if not name:
                    continue

                detail = ""
                try:
                    if include_signatures and hasattr(c, "get_signatures"):
                        sigs = c.get_signatures()
                        if sigs:
                            sig_obj = sigs[0]
                            try:
                                detail = str(sig_obj.to_string())
                            except Exception:
                                detail = str(sig_obj)
                        else:
                            detail = str(getattr(c, "description", "") or "")
                    else:
                        detail = str(getattr(c, "description", "") or "")
                except Exception:
                    detail = str(getattr(c, "description", "") or "")

                out.append(
                    {
                        "label": name,
                        "insert_text": str(getattr(c, "complete", "") or name),
                        "kind": str(getattr(c, "type", "") or ""),
                        "detail": detail,
                        "source": "jedi",
                        "source_scope": "unknown",
                    }
                )
                if len(out) >= max_items:
                    break

            _cache_put(cache_key, out)
            print(json.dumps({"state": "ok", "items": out}), flush=True)

    except Exception as exc:
        print(json.dumps({"state": "failed", "error": str(exc)}), flush=True)
"""


class JediServer:
    def __init__(self, interpreter: str, project_root: str):
        self.interpreter = str(interpreter).strip() or "python"
        self.project_root = os.path.abspath(project_root) if project_root else ""
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._start()

    def _start(self):
        env = dict(os.environ)

        # Keep worker cache local to project under .tide.
        cache_home = os.path.join(
            self.project_root if self.project_root else os.getcwd(),
            ".tide",
            "cache",
        )
        try:
            os.makedirs(cache_home, exist_ok=True)
            env["XDG_CACHE_HOME"] = cache_home
        except Exception:
            pass

        # Help worker resolve local imports
        if self.project_root:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{self.project_root}{os.pathsep}{existing}" if existing else self.project_root
            )

        try:
            self._proc = subprocess.Popen(
                [self.interpreter, "-u", "-c", JEDI_SERVER_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                cwd=self.project_root if os.path.isdir(self.project_root) else None,
            )
        except Exception:
            self._proc = None

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def request(self, payload: dict, timeout_s: float = 1.2) -> dict:
        with self._lock:
            if not self._alive():
                self.shutdown()
                self._start()

            if not self._alive():
                return {"state": "failed", "error": "server_not_running"}

            assert self._proc is not None
            try:
                assert self._proc.stdin is not None
                assert self._proc.stdout is not None

                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()

                # simple bounded wait loop for one line
                deadline = time.time() + timeout_s
                while time.time() < deadline:
                    line = self._proc.stdout.readline()
                    if line:
                        try:
                            return json.loads(line.strip())
                        except Exception:
                            return {"state": "failed", "error": "bad_worker_json"}
                    time.sleep(0.005)

                return {"state": "failed", "error": "timeout"}
            except Exception as exc:
                self.shutdown()
                return {"state": "failed", "error": str(exc)}

    def shutdown(self):
        p = self._proc
        self._proc = None
        if not p:
            return
        try:
            p.terminate()
            p.wait(timeout=0.5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


# =========================================================
# Completion manager
# =========================================================

class CompletionManager(QObject):
    completionReady = Signal(object)
    signatureReady = Signal(object)
    definitionReady = Signal(object)
    referencesProgress = Signal(object)
    referencesReady = Signal(object)
    statusMessage = Signal(str)

    DEFAULTS = {
        "enabled": True,
        "respect_excludes": True,
        "auto_trigger": True,
        "auto_trigger_after_dot": True,
        "auto_trigger_min_chars": 1,
        "debounce_ms": 120,
        "max_items": 500,
        "case_sensitive": False,
        "backend": "jedi",
    }

    def __init__(
            self,
            project_root: str,
            canonicalize: Callable[[str], str],
            resolve_interpreter: Callable[[str], str],
            is_path_excluded: Callable[[str, str], bool],
            parent=None,
    ):
        super().__init__(parent)
        self._project_root = canonicalize(project_root)
        self._canonicalize = canonicalize
        self._resolve_interpreter = resolve_interpreter
        self._is_path_excluded = is_path_excluded

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="pytpo-complete")
        self._active_futures: set[concurrent.futures.Future] = set()
        self._result_queue: queue.Queue[object] = queue.Queue()

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(16)
        self._result_pump.timeout.connect(self._drain_result_queue)
        self._result_pump.start()

        self._completion_cfg: dict = {}
        self._latest_token_by_file: dict[str, int] = {}
        self._latest_signature_token_by_file: dict[str, int] = {}
        self._latest_definition_token_by_file: dict[str, int] = {}
        self._debounce_timers: dict[str, QTimer] = {}
        self._pending_requests: dict[str, _CompletionPayload] = {}
        self._jedi_missing_warned: set[str] = set()
        self._accepted_recency: dict[str, int] = {}
        self._cancelled_reference_tokens: set[int] = set()

        self._servers: dict[str, JediServer] = {}
        self.update_settings({})

    # ---------- Public API ----------

    def update_settings(self, completion_cfg: dict):
        merged: dict = {}
        self._merge_defaults(merged, self.DEFAULTS)
        if isinstance(completion_cfg, dict):
            merged.update(completion_cfg)
        self._completion_cfg = self._normalize_cfg(merged)
        self._jedi_missing_warned.clear()
        if not self._completion_cfg.get("enabled", True):
            self._stop_all_timers()
            self._shutdown_servers()

    def register_accepted(self, text: str):
        key = str(text or "").strip()
        if not key:
            return
        self._accepted_recency[key] = self._accepted_recency.get(key, 0) + 1
        if len(self._accepted_recency) > 2000:
            for k in list(self._accepted_recency.keys())[:300]:
                self._accepted_recency.pop(k, None)

    def request_completion(
            self,
            *,
            file_path: str,
            source_text: str,
            line: int,
            column: int,
            prefix: str,
            token: int,
            reason: str = "auto",
    ):
        if not self._completion_cfg.get("enabled", True):
            return

        cpath = self._canonicalize(file_path or self._project_root)
        if self._completion_cfg.get("respect_excludes", True) and self._is_path_excluded(cpath, "completion"):
            self.completionReady.emit({"file_path": cpath, "token": token, "items": [], "reason": reason})
            return

        payload = _CompletionPayload(
            file_path=cpath,
            source_text=source_text or "",
            line=max(1, int(line)),
            column=max(0, int(column)),
            prefix=str(prefix or ""),
            token=max(1, int(token)),
            reason=str(reason or "auto"),
            completion_cfg=dict(self._completion_cfg),
            interpreter=self._resolve_interpreter(cpath),
            project_root=self._project_root,
            recency=dict(self._accepted_recency),
        )
        self._latest_token_by_file[cpath] = max(payload.token, self._latest_token_by_file.get(cpath, 0))

        if payload.reason == "auto":
            self._pending_requests[cpath] = payload
            timer = self._debounce_timers.get(cpath)
            if timer is None:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda p=cpath: self._flush_debounced_request(p))
                self._debounce_timers[cpath] = timer
            timer.start(int(self._completion_cfg.get("debounce_ms", 120)))
            return

        self._start_worker(payload)

    def request_signature(
            self,
            *,
            file_path: str,
            source_text: str,
            line: int,
            column: int,
            token: int,
    ):
        if not self._completion_cfg.get("enabled", True):
            return

        cpath = self._canonicalize(file_path or self._project_root)
        if self._completion_cfg.get("respect_excludes", True) and self._is_path_excluded(cpath, "completion"):
            self.signatureReady.emit(
                {
                    "result_type": "signature",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "signature": "",
                    "documentation": "",
                    "source": "excluded",
                }
            )
            return

        payload = _SignaturePayload(
            file_path=cpath,
            source_text=source_text or "",
            line=max(1, int(line)),
            column=max(0, int(column)),
            token=max(1, int(token)),
            interpreter=self._resolve_interpreter(cpath),
            project_root=self._project_root,
        )
        self._latest_signature_token_by_file[cpath] = max(
            payload.token,
            self._latest_signature_token_by_file.get(cpath, 0),
        )
        self._start_signature_worker(payload)

    def get_definitions(
            self,
            *,
            file_path: str,
            source_text: str,
            line: int,
            column: int,
            token: int = 1,
            interpreter: str | None = None,
            project_root: str | None = None,
    ):
        cpath = self._canonicalize(file_path or self._project_root)
        tok = max(1, int(token))
        if not self._completion_cfg.get("enabled", True):
            self.definitionReady.emit(
                {
                    "result_type": "definition",
                    "file_path": cpath,
                    "token": tok,
                    "results": [],
                    "source": "disabled",
                }
            )
            return

        if self._completion_cfg.get("respect_excludes", True) and self._is_path_excluded(cpath, "completion"):
            self.definitionReady.emit(
                {
                    "result_type": "definition",
                    "file_path": cpath,
                    "token": tok,
                    "results": [],
                    "source": "excluded",
                }
            )
            return

        payload = _DefinitionPayload(
            file_path=cpath,
            source_text=source_text or "",
            line=max(1, int(line)),
            column=max(0, int(column)),
            token=tok,
            interpreter=str(interpreter or self._resolve_interpreter(cpath)),
            project_root=str(project_root or self._project_root),
        )
        self._latest_definition_token_by_file[cpath] = max(
            payload.token,
            self._latest_definition_token_by_file.get(cpath, 0),
        )
        self._start_definition_worker(payload)

    def request_definition(self, **kwargs):
        self.get_definitions(**kwargs)

    def find_references(
            self,
            *,
            file_path: str,
            source_text: str,
            line: int,
            column: int,
            token: int = 1,
            interpreter: str | None = None,
            project_root: str | None = None,
    ):
        cpath = self._canonicalize(file_path or self._project_root)
        tok = max(1, int(token))
        self._cancelled_reference_tokens.discard(tok)
        if not self._completion_cfg.get("enabled", True):
            self.referencesReady.emit(
                {
                    "result_type": "references_done",
                    "file_path": cpath,
                    "token": tok,
                    "results": [],
                    "source": "disabled",
                    "canceled": False,
                }
            )
            return

        if self._completion_cfg.get("respect_excludes", True) and self._is_path_excluded(cpath, "completion"):
            self.referencesReady.emit(
                {
                    "result_type": "references_done",
                    "file_path": cpath,
                    "token": tok,
                    "results": [],
                    "source": "excluded",
                    "canceled": False,
                }
            )
            return

        payload = _ReferencesPayload(
            file_path=cpath,
            source_text=source_text or "",
            line=max(1, int(line)),
            column=max(0, int(column)),
            token=tok,
            interpreter=str(interpreter or self._resolve_interpreter(cpath)),
            project_root=str(project_root or self._project_root),
        )
        self._start_references_worker(payload)

    def cancel_references(self, token: int):
        tok = max(0, int(token))
        if tok > 0:
            self._cancelled_reference_tokens.add(tok)

    def shutdown(self):
        self._stop_all_timers()
        self._shutdown_servers()
        self._cancelled_reference_tokens.clear()
        try:
            self._result_pump.stop()
        except Exception:
            pass
        for fut in list(self._active_futures):
            try:
                fut.cancel()
            except Exception:
                pass
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass

    # ---------- Internals ----------

    def _server_key(self, interpreter: str, project_root: str) -> str:
        return f"{interpreter}::{project_root}"

    def _get_server(self, interpreter: str, project_root: str) -> JediServer:
        key = self._server_key(interpreter, project_root)
        srv = self._servers.get(key)
        if srv is None:
            srv = JediServer(interpreter=interpreter, project_root=project_root)
            self._servers[key] = srv
        return srv

    def _shutdown_servers(self):
        for s in self._servers.values():
            s.shutdown()
        self._servers.clear()

    def _start_worker(self, payload: _CompletionPayload):
        try:
            fut = self._executor.submit(self._run_completion_payload_fast, payload)
        except Exception:
            return
        self._active_futures.add(fut)
        fut.add_done_callback(self._queue_future_result)

    def _start_signature_worker(self, payload: _SignaturePayload):
        try:
            fut = self._executor.submit(self._run_signature_payload_fast, payload)
        except Exception:
            return
        self._active_futures.add(fut)
        fut.add_done_callback(self._queue_future_result)

    def _start_definition_worker(self, payload: _DefinitionPayload):
        try:
            fut = self._executor.submit(self._run_definition_payload_fast, payload)
        except Exception:
            return
        self._active_futures.add(fut)
        fut.add_done_callback(self._queue_future_result)

    def _start_references_worker(self, payload: _ReferencesPayload):
        def _emit_progress(progress_obj: dict):
            self._result_queue.put(progress_obj)

        def _is_cancelled(token: int) -> bool:
            return int(token) in self._cancelled_reference_tokens

        try:
            fut = self._executor.submit(
                self._run_references_payload_fast,
                payload,
                _emit_progress,
                _is_cancelled,
            )
        except Exception:
            return
        self._active_futures.add(fut)
        fut.add_done_callback(self._queue_future_result)

    def _queue_future_result(self, future: concurrent.futures.Future):
        try:
            result_obj: object = future.result()
        except Exception:
            result_obj = {}
        self._result_queue.put(result_obj)
        self._active_futures.discard(future)

    def _drain_result_queue(self):
        while True:
            try:
                result_obj = self._result_queue.get_nowait()
            except queue.Empty:
                return
            if not isinstance(result_obj, dict):
                continue
            try:
                self._on_worker_result(result_obj)
            except Exception:
                continue

    def _flush_debounced_request(self, file_path: str):
        payload = self._pending_requests.pop(file_path, None)
        if payload is None:
            return
        self._start_worker(payload)

    def _on_worker_result(self, result_obj: dict):
        result_type = str(result_obj.get("result_type") or "completion").strip().lower()
        cpath = str(result_obj.get("file_path") or "")
        token = int(result_obj.get("token") or 0)
        if not cpath or token <= 0:
            return
        if result_type == "references_progress":
            if token in self._cancelled_reference_tokens:
                return
            self.referencesProgress.emit(result_obj)
            return
        if result_type == "references_done":
            self._cancelled_reference_tokens.discard(token)
            self.referencesReady.emit(result_obj)
            return
        if result_type == "definition":
            if token != self._latest_definition_token_by_file.get(cpath, 0):
                return
            missing_backend = str(result_obj.get("missing_backend") or "")
            interpreter = str(result_obj.get("interpreter") or "python")
            if missing_backend == "jedi" and interpreter not in self._jedi_missing_warned:
                self._jedi_missing_warned.add(interpreter)
                self.statusMessage.emit(
                    f"jedi not available in interpreter {interpreter}, using keywords/builtins fallback."
                )
            self.definitionReady.emit(result_obj)
            return
        if result_type == "signature":
            if token != self._latest_signature_token_by_file.get(cpath, 0):
                return
            missing_backend = str(result_obj.get("missing_backend") or "")
            interpreter = str(result_obj.get("interpreter") or "python")
            if missing_backend == "jedi" and interpreter not in self._jedi_missing_warned:
                self._jedi_missing_warned.add(interpreter)
                self.statusMessage.emit(
                    f"jedi not available in interpreter {interpreter}, using keywords/builtins fallback."
                )
            self.signatureReady.emit(result_obj)
            return
        if token != self._latest_token_by_file.get(cpath, 0):
            return

        missing_backend = str(result_obj.get("missing_backend") or "")
        interpreter = str(result_obj.get("interpreter") or "python")
        if missing_backend == "jedi" and interpreter not in self._jedi_missing_warned:
            self._jedi_missing_warned.add(interpreter)
            self.statusMessage.emit(
                f"jedi not available in interpreter {interpreter}, using keywords/builtins fallback."
            )

        self.completionReady.emit(result_obj)

    def _cancel_timer(self, file_path: str):
        timer = self._debounce_timers.pop(file_path, None)
        if timer is None:
            return
        timer.stop()
        timer.deleteLater()

    def _stop_all_timers(self):
        for key in list(self._debounce_timers.keys()):
            self._cancel_timer(key)
        self._pending_requests.clear()

    def _merge_defaults(self, target: dict, defaults: dict):
        for key, default in defaults.items():
            if key not in target:
                target[key] = default

    def _normalize_cfg(self, cfg: dict) -> dict:
        out = dict(cfg)
        out["enabled"] = bool(out.get("enabled", True))
        out["respect_excludes"] = bool(out.get("respect_excludes", True))
        out["auto_trigger"] = bool(out.get("auto_trigger", True))
        out["auto_trigger_after_dot"] = bool(out.get("auto_trigger_after_dot", True))
        out["auto_trigger_min_chars"] = max(1, min(10, int(out.get("auto_trigger_min_chars", 1))))
        out["debounce_ms"] = max(20, min(3000, int(out.get("debounce_ms", 120))))
        out["max_items"] = max(5, min(1000, int(out.get("max_items", 500))))
        out["case_sensitive"] = bool(out.get("case_sensitive", False))
        backend = str(out.get("backend", "jedi")).strip().lower()
        out["backend"] = "jedi" if backend != "jedi" else backend
        return out

    # ---------- Fast worker path ----------

    def _run_completion_payload_fast(self, payload: _CompletionPayload) -> dict:
        cfg = payload.completion_cfg
        max_items = int(cfg.get("max_items", 500))
        case_sensitive = bool(cfg.get("case_sensitive", False))

        context = _detect_context(
            source_text=payload.source_text,
            line=payload.line,
            column=payload.column,
            prefix=payload.prefix,
        )

        items: list[dict] = []
        used_backend = "fallback"
        missing_backend = ""

        if str(cfg.get("backend", "jedi")).strip().lower() == "jedi":
            server = self._get_server(payload.interpreter, payload.project_root)
            req = {
                "path": payload.file_path,
                "source": payload.source_text,
                "line": payload.line,
                "column": payload.column,
                "max_items": max(max_items * 6, 300),
                "project_root": payload.project_root,
                "include_signatures": True,
            }
            jedi_res = server.request(req, timeout_s=1.2)
            state = str(jedi_res.get("state") or "failed")

            if state == "ok":
                used_backend = "jedi"
                items = _annotate_jedi_items(jedi_res.get("items", []))
                items = _context_filter_items(items, context)
            elif state == "missing":
                missing_backend = "jedi"

        if not items:
            items = _fallback_candidates(payload=payload, context=context, max_items=max(max_items * 4, 120))

        ranked = _rank_items(
            items=items,
            prefix=payload.prefix,
            context=context,
            case_sensitive=case_sensitive,
            recency=payload.recency,
            max_items=max_items,
        )
        return {
            "result_type": "completion",
            "file_path": payload.file_path,
            "token": payload.token,
            "items": ranked,
            "backend": used_backend,
            "missing_backend": missing_backend,
            "interpreter": payload.interpreter,
            "reason": payload.reason,
        }

    def _run_signature_payload_fast(self, payload: _SignaturePayload) -> dict:
        signature = ""
        documentation = ""
        full_name = ""
        module_name = ""
        source = "fallback"
        missing_backend = ""

        if str(self._completion_cfg.get("backend", "jedi")).strip().lower() == "jedi":
            server = self._get_server(payload.interpreter, payload.project_root)
            req = {
                "request": "signature",
                "path": payload.file_path,
                "source": payload.source_text,
                "line": payload.line,
                "column": payload.column,
                "project_root": payload.project_root,
            }
            jedi_res = server.request(req, timeout_s=1.2)
            state = str(jedi_res.get("state") or "failed")
            if state == "ok":
                source = "jedi"
                signature = str(jedi_res.get("signature") or "")
                documentation = str(jedi_res.get("documentation") or "")
                full_name = str(jedi_res.get("full_name") or "")
                module_name = str(jedi_res.get("module_name") or "")
            elif state == "missing":
                missing_backend = "jedi"

        return {
            "result_type": "signature",
            "file_path": payload.file_path,
            "token": payload.token,
            "signature": signature,
            "documentation": documentation,
            "full_name": full_name,
            "module_name": module_name,
            "source": source,
            "missing_backend": missing_backend,
            "interpreter": payload.interpreter,
        }

    def _run_definition_payload_fast(self, payload: _DefinitionPayload) -> dict:
        results: list[dict] = []
        source = "fallback"
        missing_backend = ""
        backend = str(self._completion_cfg.get("backend", "jedi")).strip().lower()

        if backend == "jedi":
            server = self._get_server(payload.interpreter, payload.project_root)
            req = {
                "request": "definition",
                "path": payload.file_path,
                "source": payload.source_text,
                "line": payload.line,
                "column": payload.column,
                "project_root": payload.project_root,
            }
            jedi_res = server.request(req, timeout_s=4.0)
            state = str(jedi_res.get("state") or "failed")
            if state == "ok":
                source = "jedi"
                raw_results = jedi_res.get("results")
                if isinstance(raw_results, list):
                    results = [self._normalize_symbol_location(item, payload.file_path) for item in raw_results]
            elif state == "missing":
                missing_backend = "jedi"

        if not results:
            fallback = self._fallback_definitions_for_payload(payload)
            if fallback:
                results = fallback

        return {
            "result_type": "definition",
            "file_path": payload.file_path,
            "token": payload.token,
            "results": results,
            "source": source,
            "missing_backend": missing_backend,
            "interpreter": payload.interpreter,
        }

    def _run_references_payload_fast(
            self,
            payload: _ReferencesPayload,
            emit_progress: Callable[[dict], None],
            is_cancelled: Callable[[int], bool],
    ) -> dict:
        def _emit_chunk(chunk_results: list[dict], processed: int):
            if not chunk_results:
                return
            emit_progress(
                {
                    "result_type": "references_progress",
                    "file_path": payload.file_path,
                    "token": payload.token,
                    "results": list(chunk_results),
                    "processed": int(processed),
                }
            )

        if is_cancelled(payload.token):
            return {
                "result_type": "references_done",
                "file_path": payload.file_path,
                "token": payload.token,
                "results": [],
                "processed": 0,
                "canceled": True,
                "source": "cancelled",
            }

        missing_backend = ""
        source = "fallback"
        backend = str(self._completion_cfg.get("backend", "jedi")).strip().lower()
        respect_excludes = bool(self._completion_cfg.get("respect_excludes", True))
        references: list[dict] = []
        definitions: list[dict] = []
        processed = 0
        chunk: list[dict] = []

        if backend == "jedi":
            server = self._get_server(payload.interpreter, payload.project_root)
            req = {
                "request": "references",
                "path": payload.file_path,
                "source": payload.source_text,
                "line": payload.line,
                "column": payload.column,
                "project_root": payload.project_root,
            }
            jedi_res = server.request(req, timeout_s=20.0)
            state = str(jedi_res.get("state") or "failed")
            if state == "ok":
                source = "jedi"
                raw_defs = jedi_res.get("definitions")
                if isinstance(raw_defs, list):
                    definitions = [self._normalize_symbol_location(item, payload.file_path) for item in raw_defs]

                raw_refs = jedi_res.get("references")
                if isinstance(raw_refs, list):
                    normalized_refs: list[dict] = []
                    for item in raw_refs:
                        if is_cancelled(payload.token):
                            return {
                                "result_type": "references_done",
                                "file_path": payload.file_path,
                                "token": payload.token,
                                "results": [],
                                "processed": processed,
                                "canceled": True,
                                "source": source,
                                "missing_backend": missing_backend,
                            }
                        loc = self._normalize_symbol_location(item, payload.file_path)
                        target_path = str(loc.get("file_path") or "")
                        if (
                            target_path
                            and respect_excludes
                            and self._is_path_excluded(target_path, "completion")
                        ):
                            continue
                        normalized_refs.append(loc)
                    references = normalized_refs
            elif state == "missing":
                missing_backend = "jedi"

        if not references:
            fallback_refs = self._fallback_references_for_payload(payload, is_cancelled=is_cancelled)
            if fallback_refs:
                references = fallback_refs

        dedupe: dict[tuple[str, int, int], dict] = {}
        for item in references:
            path = str(item.get("file_path") or "")
            line = max(1, int(item.get("line") or 1))
            col = max(1, int(item.get("column") or 1))
            key = (path, line, col)
            if key not in dedupe:
                dedupe[key] = item
        ordered = sorted(
            dedupe.values(),
            key=lambda d: (
                str(d.get("file_path") or ""),
                int(d.get("line") or 1),
                int(d.get("column") or 1),
            ),
        )

        for hit in ordered:
            if is_cancelled(payload.token):
                return {
                    "result_type": "references_done",
                    "file_path": payload.file_path,
                    "token": payload.token,
                    "results": [],
                    "processed": processed,
                    "canceled": True,
                    "source": source,
                    "missing_backend": missing_backend,
                }
            chunk.append(hit)
            processed += 1
            if len(chunk) >= 80:
                _emit_chunk(chunk, processed)
                chunk = []

        return {
            "result_type": "references_done",
            "file_path": payload.file_path,
            "token": payload.token,
            "results": chunk,
            "processed": processed,
            "definitions": definitions,
            "canceled": False,
            "source": source,
            "missing_backend": missing_backend,
            "interpreter": payload.interpreter,
        }

    def _normalize_symbol_location(self, item: object, fallback_file_path: str) -> dict:
        if not isinstance(item, dict):
            return {}
        raw_path = str(item.get("module_path") or "").strip()
        file_path = self._canonicalize(raw_path if raw_path else fallback_file_path)
        line = max(1, int(item.get("line") or 1))
        col_raw = int(item.get("column") or 0)
        column = max(1, col_raw + 1)
        preview = self._line_preview(file_path, line, fallback_text=None).strip()
        return {
            "name": str(item.get("name") or ""),
            "type": str(item.get("type") or ""),
            "file_path": file_path,
            "line": line,
            "column": column,
            "module_name": str(item.get("module_name") or ""),
            "full_name": str(item.get("full_name") or ""),
            "description": str(item.get("description") or ""),
            "preview": preview,
        }

    def _line_preview(self, file_path: str, line: int, fallback_text: str | None = None) -> str:
        if fallback_text:
            lines = fallback_text.splitlines()
            if 1 <= line <= len(lines):
                return str(lines[line - 1]).strip()
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                for idx, raw in enumerate(fh, start=1):
                    if idx == line:
                        return str(raw).rstrip("\n").strip()
        except Exception:
            return ""
        return ""

    def _symbol_at_source_position(self, source_text: str, line: int, column: int) -> str:
        lines = (source_text or "").splitlines()
        if not (1 <= line <= len(lines)):
            return ""
        text = lines[line - 1]
        if not text:
            return ""
        col = max(0, min(int(column), len(text)))
        if col >= len(text) and col > 0:
            col -= 1
        if col < 0 or col >= len(text):
            return ""
        if not (text[col].isalnum() or text[col] == "_"):
            if col > 0 and (text[col - 1].isalnum() or text[col - 1] == "_"):
                col -= 1
            else:
                return ""
        start = col
        while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
            start -= 1
        end = col + 1
        while end < len(text) and (text[end].isalnum() or text[end] == "_"):
            end += 1
        return str(text[start:end]).strip()

    def _fallback_definitions_for_payload(self, payload: _DefinitionPayload) -> list[dict]:
        symbol = self._symbol_at_source_position(payload.source_text, payload.line, payload.column)
        if not symbol:
            return []
        lines = payload.source_text.splitlines()
        out: list[dict] = []
        pats = (
            re.compile(rf"^\s*def\s+{re.escape(symbol)}\b"),
            re.compile(rf"^\s*class\s+{re.escape(symbol)}\b"),
            re.compile(rf"^\s*{re.escape(symbol)}\s*="),
        )
        for idx, line_text in enumerate(lines, start=1):
            if any(p.match(line_text) for p in pats):
                col = max(1, line_text.find(symbol) + 1)
                out.append(
                    {
                        "name": symbol,
                        "type": "name",
                        "file_path": payload.file_path,
                        "line": idx,
                        "column": col,
                        "module_name": "",
                        "full_name": "",
                        "description": "fallback definition",
                        "preview": line_text.strip(),
                    }
                )
        return out

    def _iter_reference_candidate_files(self, project_root: str):
        root = self._canonicalize(project_root or self._project_root)
        if not os.path.isdir(root):
            return
        for dirpath, dirnames, filenames in os.walk(root):
            keep_dirs = []
            for name in dirnames:
                full = self._canonicalize(os.path.join(dirpath, name))
                if self._completion_cfg.get("respect_excludes", True) and self._is_path_excluded(full, "completion"):
                    continue
                keep_dirs.append(name)
            dirnames[:] = keep_dirs
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                fpath = self._canonicalize(os.path.join(dirpath, fname))
                if self._completion_cfg.get("respect_excludes", True) and self._is_path_excluded(fpath, "completion"):
                    continue
                yield fpath

    def _fallback_references_for_payload(
            self,
            payload: _ReferencesPayload,
            *,
            is_cancelled: Callable[[int], bool],
    ) -> list[dict]:
        symbol = self._symbol_at_source_position(payload.source_text, payload.line, payload.column)
        if not symbol:
            return []
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        results: list[dict] = []
        for fpath in self._iter_reference_candidate_files(payload.project_root):
            if is_cancelled(payload.token):
                return results
            if fpath == payload.file_path:
                source = payload.source_text
            else:
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        source = fh.read()
                except Exception:
                    continue

            for line_no, text in enumerate(source.splitlines(), start=1):
                if is_cancelled(payload.token):
                    return results
                for match in pattern.finditer(text):
                    results.append(
                        {
                            "name": symbol,
                            "type": "name",
                            "file_path": fpath,
                            "line": int(line_no),
                            "column": int(match.start()) + 1,
                            "module_name": "",
                            "full_name": "",
                            "description": "fallback reference",
                            "preview": str(text).strip(),
                        }
                    )
                    if len(results) >= 8000:
                        return results
        return results


# =========================================================
# Shared completion helpers
# =========================================================

def _detect_context(source_text: str, line: int, column: int, prefix: str) -> dict:
    lines = source_text.splitlines()
    if 1 <= line <= len(lines):
        line_text = lines[line - 1]
    else:
        line_text = ""
    col = max(0, min(len(line_text), int(column)))
    left = line_text[:col]

    m = re.match(r"^\s*import\s+([A-Za-z0-9_\.]*)$", left)
    if m:
        return {"mode": "import_stmt", "import_module": "", "prefix": m.group(1) or prefix}

    m = re.match(r"^\s*from\s+([A-Za-z0-9_\.]*)$", left)
    if m:
        return {"mode": "from_module", "import_module": "", "prefix": m.group(1) or prefix}

    m = re.match(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s+([A-Za-z0-9_\.\,\s]*)$", left)
    if m:
        suffix = (m.group(2) or "")
        token = suffix.split(",")[-1].strip()
        return {"mode": "from_import", "import_module": m.group(1), "prefix": token or prefix}

    pfx = str(prefix or "")
    if pfx and left.endswith(pfx):
        idx = len(left) - len(pfx)
        if idx > 0 and left[idx - 1] == ".":
            return {"mode": "attribute", "import_module": "", "prefix": pfx}
    if left.endswith("."):
        return {"mode": "attribute", "import_module": "", "prefix": ""}

    return {"mode": "normal", "import_module": "", "prefix": prefix}


def _annotate_jedi_items(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").lower()
        scope = "project"
        if kind == "param":
            scope = "locals"
        elif kind == "statement":
            scope = "current_file"
        elif kind in {"module", "path"}:
            scope = "interpreter_modules"
        elif kind == "keyword":
            scope = "builtins"

        label = str(item.get("label") or item.get("insert_text") or "").strip()
        insert_text = str(item.get("insert_text") or label).strip()
        if not label or not insert_text:
            continue

        out.append(
            {
                "label": label,
                "insert_text": insert_text,
                "kind": kind,
                "detail": str(item.get("detail") or ""),
                "source": "jedi",
                "source_scope": scope,
            }
        )
    return out


def _context_filter_items(items: list[dict], context: dict) -> list[dict]:
    mode = str(context.get("mode") or "normal")
    if mode in {"import_stmt", "from_module"}:
        allowed = {"module", "path"}
        return [it for it in items if str(it.get("kind") or "").lower() in allowed]
    if mode == "from_import":
        return [it for it in items if str(it.get("kind") or "").lower() != "keyword"]
    if mode == "attribute":
        # Keep attributes from Jedi, only strip hard-keywords
        return [it for it in items if str(it.get("kind") or "").lower() != "keyword"]
    return list(items)


def _fallback_candidates(payload: _CompletionPayload, context: dict, max_items: int) -> list[dict]:
    mode = str(context.get("mode") or "normal")
    prefix = str(context.get("prefix") or payload.prefix or "")

    if mode in {"import_stmt", "from_module"}:
        return _fallback_module_candidates(prefix=prefix, project_root=payload.project_root, max_items=max_items)

    if mode == "from_import":
        module_name = str(context.get("import_module") or "").strip()
        return _fallback_module_members(module_name=module_name, prefix=prefix, max_items=max_items, project_root=payload.project_root)

    if mode == "attribute":
        return _fallback_attribute_candidates(payload.source_text, payload.line, payload.column, prefix, max_items)

    return _fallback_normal_candidates(source_text=payload.source_text, prefix=prefix, max_items=max_items)


def _fallback_attribute_candidates(source_text: str, line: int, column: int, prefix: str, max_items: int) -> list[dict]:
    """
    Lightweight local fallback for `obj.`:
    - infer obj name left of dot
    - find `obj = ClassName(...)` in current buffer
    - add names from class body if present in same file
    """
    lines = source_text.splitlines()
    if not (1 <= line <= len(lines)):
        return []
    lt = lines[line - 1]
    col = max(0, min(len(lt), int(column)))
    left = lt[:col]

    # match "... obj." or "... obj.pre"
    m = re.search(r"([A-Za-z_]\w*)\.\w*$", left)
    if not m:
        return []
    obj_name = m.group(1)

    cls_name = None
    assign_pat = re.compile(rf"^\s*{re.escape(obj_name)}\s*=\s*([A-Za-z_]\w*)\s*\(")
    for ln in lines:
        mm = assign_pat.match(ln)
        if mm:
            cls_name = mm.group(1)
            break

    names: set[str] = set()
    if cls_name:
        # parse class body roughly in same file
        class_line = None
        class_pat = re.compile(rf"^\s*class\s+{re.escape(cls_name)}\b")
        for i, ln in enumerate(lines):
            if class_pat.match(ln):
                class_line = i
                break

        if class_line is not None:
            class_indent = len(lines[class_line]) - len(lines[class_line].lstrip(" "))
            for ln in lines[class_line + 1:]:
                if not ln.strip():
                    continue
                indent = len(ln) - len(ln.lstrip(" "))
                if indent <= class_indent:
                    break
                defm = re.match(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", ln)
                if defm:
                    names.add(defm.group(1))
                attrm = re.match(r"^\s*self\.([A-Za-z_]\w*)\s*=", ln)
                if attrm:
                    names.add(attrm.group(1))

    # Always include safe object attrs so menu not empty
    for n in ("__class__", "__doc__", "__repr__", "__str__"):
        names.add(n)

    out = []
    p = (prefix or "").lower()
    for n in sorted(names):
        if p and p not in n.lower():
            continue
        out.append(
            {
                "label": n,
                "insert_text": n,
                "kind": "function" if not n.startswith("__") else "name",
                "detail": "fallback attribute",
                "source": "fallback",
                "source_scope": "current_file",
            }
        )
        if len(out) >= max_items * 2:
            break
    return out


def _fallback_normal_candidates(source_text: str, prefix: str, max_items: int) -> list[dict]:
    names: dict[str, str] = {}
    for token in re.findall(r"[A-Za-z_]\w*", source_text or ""):
        names[token] = "current_file"
    for k in keyword.kwlist:
        names.setdefault(k, "builtins")
    for b in dir(builtins):
        names.setdefault(str(b), "builtins")

    out: list[dict] = []
    for name, scope in names.items():
        out.append(
            {
                "label": name,
                "insert_text": name,
                "kind": "name",
                "detail": "",
                "source": "fallback",
                "source_scope": scope,
            }
        )
    return out[: max_items * 2]


def _fallback_module_candidates(prefix: str, project_root: str, max_items: int) -> list[dict]:
    candidates: dict[str, str] = {}
    for mod in _iter_available_modules(prefix):
        candidates[mod] = "interpreter_modules"
    for mod in _iter_project_modules(project_root):
        if prefix and not mod.startswith(prefix):
            continue
        candidates[mod] = "project"

    module_head = ""
    if "." in str(prefix or ""):
        module_head = str(prefix).rsplit(".", 1)[0].strip()

    out: list[dict] = []
    seen_display: set[tuple[str, str]] = set()
    for name, scope in candidates.items():
        display = name
        detail = ""
        if module_head and name == module_head:
            continue
        if module_head and name.startswith(module_head + "."):
            display = name[len(module_head) + 1:]
            detail = name

        key = (display, scope)
        if key in seen_display:
            continue
        seen_display.add(key)
        out.append(
            {
                "label": display,
                "insert_text": display,
                "kind": "module",
                "detail": detail,
                "source": "fallback",
                "source_scope": scope,
            }
        )
    return out[: max_items * 2]


def _fallback_module_members(module_name: str, prefix: str, max_items: int, project_root: str = "") -> list[dict]:
    if not module_name:
        return []

    inserted = False
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)
        inserted = True

    try:
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            return []
        out: list[dict] = []
        for name in dir(mod):
            if prefix and not name.startswith(prefix):
                continue
            kind = "name"
            try:
                value = getattr(mod, name)
                if isinstance(value, type):
                    kind = "class"
                elif callable(value):
                    kind = "function"
            except Exception:
                pass
            out.append(
                {
                    "label": name,
                    "insert_text": name,
                    "kind": kind,
                    "detail": "",
                    "source": "fallback",
                    "source_scope": "project",
                }
            )
            if len(out) >= max_items * 2:
                break
        return out
    finally:
        if inserted:
            try:
                sys.path.pop(0)
            except Exception:
                pass


def _iter_available_modules(prefix: str):
    try:
        for item in pkgutil.iter_modules():
            name = str(item.name)
            if prefix and not name.startswith(prefix.split(".")[0]):
                continue
            yield name
    except Exception:
        pass

    if "." in prefix:
        head = prefix.rsplit(".", 1)[0]
        try:
            pkg = importlib.import_module(head)
            pkg_path = getattr(pkg, "__path__", None)
            if pkg_path:
                for item in pkgutil.iter_modules(pkg_path):
                    full = f"{head}.{item.name}"
                    if prefix and not full.startswith(prefix):
                        continue
                    yield full
        except Exception:
            pass


def _iter_project_modules(project_root: str):
    root = os.path.abspath(project_root)
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", ".venv", "__pycache__", ".idea", ".cache", ".ruff_cache", ".tide"}]
        rel_dir = os.path.relpath(dirpath, root)
        pkg_parts = [] if rel_dir in {".", ""} else rel_dir.split(os.sep)

        if "__init__.py" in filenames and pkg_parts:
            yield ".".join(pkg_parts)

        for fname in filenames:
            if not fname.endswith(".py") or fname == "__init__.py":
                continue
            stem = fname[:-3]
            if rel_dir in {".", ""}:
                yield stem
            else:
                yield ".".join(pkg_parts + [stem])


def _rank_items(
        *,
        items: list[dict],
        prefix: str,
        context: dict,
        case_sensitive: bool,
        recency: dict[str, int],
        max_items: int,
) -> list[dict]:
    pfx = str(context.get("prefix") or prefix or "")
    mode = str(context.get("mode") or "normal")
    if mode in {"import_stmt", "from_module"} and "." in pfx:
        # In dotted import contexts Jedi returns leaf candidates (e.g. `utils`)
        # while the typed prefix can be `package.sub`.
        pfx = pfx.rsplit(".", 1)[-1]
    pfx_cmp = pfx if case_sensitive else pfx.lower()
    allow_private = pfx.startswith("_")

    source_priority = {
        "locals": 600,
        "current_file": 500,
        "project": 420,
        "interpreter_modules": 330,
        "builtins": 240,
        "unknown": 100,
    }

    ranked: list[tuple[int, str, dict]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue

        label = str(item.get("label") or item.get("insert_text") or "").strip()
        insert_text = str(item.get("insert_text") or label).strip()
        if not label or not insert_text:
            continue

        dedupe_key = f"{insert_text}\0{label}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        probe = label if case_sensitive else label.lower()
        if pfx_cmp:
            if probe == pfx_cmp:
                match_score = 300
            elif probe.startswith(pfx_cmp):
                match_score = 220
            elif pfx_cmp in probe:
                match_score = 120
            else:
                continue
        else:
            match_score = 80

        scope = str(item.get("source_scope") or "unknown")
        scope_score = source_priority.get(scope, 100)
        recency_score = min(120, int(recency.get(insert_text, 0)) * 15)

        private_penalty = 0
        if not allow_private:
            if label.startswith("__") and label.endswith("__"):
                private_penalty -= 260
            elif label.startswith("_"):
                private_penalty -= 120

        kind = str(item.get("kind") or "").lower()
        mode_score = 0
        if mode in {"import_stmt", "from_module"}:
            mode_score += 120 if kind in {"module", "path"} else -120
        elif mode == "from_import":
            mode_score += -80 if kind == "keyword" else 0
        elif mode == "attribute":
            if kind in {"module", "path", "keyword"}:
                mode_score -= 120
            else:
                mode_score += 40
            if kind in {"function", "method"}:
                mode_score += 80

        score = match_score + scope_score + recency_score + private_penalty + mode_score
        ranked.append((score, label.lower(), {**item, "label": label, "insert_text": insert_text, "_score": int(score)}))

    ranked.sort(key=lambda t: (-t[0], t[1]))
    return [item for _, _, item in ranked[: max_items]]
