from __future__ import annotations

import ast
import concurrent.futures
import json
import os
import queue
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal


PYTHON_SUFFIXES = (".py", ".pyw", ".pyi")
SEVERITY_ORDER = {"error": 3, "warning": 2, "info": 1}


@dataclass
class _LintPayload:
    file_path: str
    token: int
    reason: str
    source_text: Optional[str]
    lint_cfg: dict
    backend_order: list[str]
    interpreter: str
    project_root: str


class LintManager(QObject):
    fileDiagnosticsUpdated = Signal(str, object)   # file_path, diagnostics(list[dict])
    fileDiagnosticsCleared = Signal(str)           # file_path
    allDiagnosticsCleared = Signal()
    statusMessage = Signal(str)
    problemCountChanged = Signal(int)

    DEFAULTS = {
        "enabled": True,
        "respect_excludes": True,
        "debounce_ms": 600,
        "run_on_save": True,
        "run_on_idle": True,
        "max_problems_per_file": 200,
        "backend": "ruff",
        "fallback_backend": "ast",
        "args": {
            "ruff": ["check", "--output-format", "json"],
            "pyflakes": [],
        },
        "severity_overrides": {},
    }
    BACKENDS = {"ruff", "pyflakes", "ast"}
    FALLBACK_BACKENDS = {"none", "ruff", "pyflakes", "ast"}

    def __init__(
        self,
        project_root: str,
        canonicalize: Callable[[str], str],
        resolve_interpreter: Callable[[str], str],
        is_path_excluded: Callable[[str, str], bool],
        follow_symlinks_provider: Callable[[], bool],
        parent=None,
    ):
        super().__init__(parent)
        self._project_root = canonicalize(project_root)
        self._canonicalize = canonicalize
        self._resolve_interpreter = resolve_interpreter
        self._is_path_excluded = is_path_excluded
        self._follow_symlinks_provider = follow_symlinks_provider

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="pytpo-lint",
        )
        self._active_futures: set[concurrent.futures.Future] = set()
        self._result_queue: queue.Queue[object] = queue.Queue()
        self._result_pump = QTimer(self)
        self._result_pump.setInterval(35)
        self._result_pump.timeout.connect(self._drain_result_queue)
        self._result_pump.start()

        self._lint_cfg: dict = {}
        self._latest_token_by_file: dict[str, int] = {}
        self._diagnostics_by_file: dict[str, list[dict]] = {}
        self._debounce_timers: dict[str, QTimer] = {}
        self._pending_requests: dict[str, tuple[Optional[str], str, int]] = {}
        self._missing_warned: set[tuple[str, str]] = set()

        self.update_settings({})

    # ---------- Public API ----------

    def update_settings(self, lint_cfg: dict):
        before_backend = str(self._lint_cfg.get("backend", ""))
        before_fallback = str(self._lint_cfg.get("fallback_backend", ""))

        merged = {}
        self._merge_defaults(merged, self.DEFAULTS)
        if isinstance(lint_cfg, dict):
            self._merge_defaults(merged, lint_cfg)
        merged = self._normalize_cfg(merged)
        self._lint_cfg = merged

        if (
            before_backend != self._lint_cfg["backend"]
            or before_fallback != self._lint_cfg["fallback_backend"]
        ):
            self._missing_warned.clear()

        if not self._lint_cfg.get("enabled", True):
            self._stop_all_timers()
            self.clear_all()

    def request_lint_file(self, file_path: str, source_text: str | None = None, reason: str = "idle"):
        cpath = self._canonicalize(file_path)
        if not self._is_python_path(cpath):
            self.clear_file(cpath)
            return
        if not self._lint_cfg.get("enabled", True):
            return
        if self._lint_cfg.get("respect_excludes", True) and self._is_path_excluded(cpath, "lint"):
            self.clear_file(cpath)
            return

        if reason == "idle" and not self._lint_cfg.get("run_on_idle", True):
            return
        if reason == "save" and not self._lint_cfg.get("run_on_save", True):
            return

        token = self._next_token(cpath)
        if reason == "idle":
            self._pending_requests[cpath] = (source_text, reason, token)
            timer = self._debounce_timers.get(cpath)
            if timer is None:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda p=cpath: self._flush_debounced_request(p))
                self._debounce_timers[cpath] = timer
            timer.start(int(self._lint_cfg["debounce_ms"]))
            return

        self._start_worker(cpath, source_text, reason, token)

    def request_lint_project(self):
        if not self._lint_cfg.get("enabled", True):
            self.statusMessage.emit("Lint is disabled.")
            return

        follow_links = bool(self._follow_symlinks_provider())
        files: list[str] = []

        for root, dirs, names in os.walk(self._project_root, followlinks=follow_links):
            pruned_dirs = []
            for d in dirs:
                dpath = self._canonicalize(os.path.join(root, d))
                if self._lint_cfg.get("respect_excludes", True) and self._is_path_excluded(dpath, "lint"):
                    continue
                pruned_dirs.append(d)
            dirs[:] = pruned_dirs

            for name in names:
                if not name.lower().endswith(PYTHON_SUFFIXES):
                    continue
                p = self._canonicalize(os.path.join(root, name))
                if self._lint_cfg.get("respect_excludes", True) and self._is_path_excluded(p, "lint"):
                    continue
                files.append(p)

        for fpath in files:
            self.request_lint_file(fpath, source_text=None, reason="project")

        self.statusMessage.emit(f"Lint queued for {len(files)} file(s).")

    def clear_all(self):
        if not self._diagnostics_by_file:
            return
        self._diagnostics_by_file.clear()
        self.allDiagnosticsCleared.emit()
        self.problemCountChanged.emit(0)

    def clear_file(self, file_path: str):
        cpath = self._canonicalize(file_path)
        self._invalidate_file_token(cpath)
        self._cancel_file_timer(cpath)
        self._pending_requests.pop(cpath, None)
        if cpath in self._diagnostics_by_file:
            self._diagnostics_by_file.pop(cpath, None)
            self.fileDiagnosticsCleared.emit(cpath)
            self.problemCountChanged.emit(self._total_problem_count())

    def clear_paths_under(self, path_prefix: str):
        prefix = self._canonicalize(path_prefix)
        candidates = set(self._diagnostics_by_file.keys())
        candidates.update(self._latest_token_by_file.keys())
        candidates.update(self._pending_requests.keys())
        candidates.update(self._debounce_timers.keys())
        to_clear = [path for path in candidates if self._is_prefix(prefix, path)]
        for path in to_clear:
            self.clear_file(path)

    def diagnostics_snapshot(self) -> dict[str, list[dict]]:
        return {k: list(v) for k, v in self._diagnostics_by_file.items()}

    def shutdown(self):
        self._stop_all_timers()
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

    # ---------- Scheduling ----------

    def _start_worker(self, file_path: str, source_text: str | None, reason: str, token: int):
        if source_text is None and not os.path.exists(file_path):
            self.clear_file(file_path)
            return

        payload = _LintPayload(
            file_path=file_path,
            token=token,
            reason=reason,
            source_text=source_text,
            lint_cfg=dict(self._lint_cfg),
            backend_order=self._backend_order(),
            interpreter=self._resolve_interpreter(file_path),
            project_root=self._project_root,
        )
        try:
            future = self._executor.submit(_run_lint_payload, payload)
        except Exception:
            return
        self._active_futures.add(future)
        future.add_done_callback(self._queue_future_result)

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
            try:
                self._on_worker_finished(result_obj)
            except Exception:
                # Lint failures should never crash the IDE UI.
                continue

    def _flush_debounced_request(self, file_path: str):
        pending = self._pending_requests.pop(file_path, None)
        if pending is None:
            return
        source_text, reason, token = pending
        self._start_worker(file_path, source_text, reason, token)

    def _on_worker_finished(self, result_obj: object):
        if not isinstance(result_obj, dict):
            return
        file_path = str(result_obj.get("file_path") or "")
        token = int(result_obj.get("token") or 0)
        if not file_path:
            return

        # Drop stale worker results; only the latest token per file is allowed to update UI state.
        latest = self._latest_token_by_file.get(file_path, 0)
        if token != latest:
            return

        for backend in result_obj.get("missing_backends", []):
            if not isinstance(backend, str):
                continue
            self._emit_missing_backend_warning(
                interpreter=str(result_obj.get("interpreter") or "python"),
                backend=backend,
            )

        diagnostics = result_obj.get("diagnostics") or []
        if not isinstance(diagnostics, list):
            diagnostics = []

        if diagnostics:
            self._diagnostics_by_file[file_path] = diagnostics
        else:
            self._diagnostics_by_file.pop(file_path, None)

        self.fileDiagnosticsUpdated.emit(file_path, diagnostics)
        self.problemCountChanged.emit(self._total_problem_count())

    # ---------- Helpers ----------

    def _next_token(self, file_path: str) -> int:
        token = self._latest_token_by_file.get(file_path, 0) + 1
        self._latest_token_by_file[file_path] = token
        return token

    def _invalidate_file_token(self, file_path: str):
        self._next_token(file_path)

    def _backend_order(self) -> list[str]:
        primary = self._lint_cfg["backend"]
        fallback = self._lint_cfg["fallback_backend"]
        order: list[str] = []
        if primary in self.BACKENDS:
            order.append(primary)
        if fallback in self.BACKENDS and fallback not in order:
            order.append(fallback)
        if "ast" not in order:
            order.append("ast")
        return order

    def _emit_missing_backend_warning(self, interpreter: str, backend: str):
        key = (interpreter, backend)
        if key in self._missing_warned:
            return
        self._missing_warned.add(key)
        self.statusMessage.emit(f"{backend} not available in interpreter {interpreter}, falling back to ast.")

    def _cancel_file_timer(self, file_path: str):
        timer = self._debounce_timers.pop(file_path, None)
        if timer is None:
            return
        timer.stop()
        timer.deleteLater()

    def _stop_all_timers(self):
        for key in list(self._debounce_timers.keys()):
            self._cancel_file_timer(key)
        self._pending_requests.clear()

    def _total_problem_count(self) -> int:
        return sum(len(v) for v in self._diagnostics_by_file.values())

    def _is_prefix(self, prefix: str, path: str) -> bool:
        try:
            return os.path.commonpath([prefix, path]) == prefix
        except Exception:
            return False

    @staticmethod
    def _is_python_path(file_path: str) -> bool:
        return str(file_path or "").lower().endswith(PYTHON_SUFFIXES)

    def _merge_defaults(self, target: dict, source: dict):
        for key, value in source.items():
            if isinstance(value, dict):
                current = target.get(key)
                if not isinstance(current, dict):
                    current = {}
                self._merge_defaults(current, value)
                target[key] = current
            else:
                target[key] = value

    def _normalize_cfg(self, cfg: dict) -> dict:
        out = dict(cfg)
        out["enabled"] = bool(out.get("enabled", True))
        out["respect_excludes"] = bool(out.get("respect_excludes", True))
        out["run_on_idle"] = bool(out.get("run_on_idle", True))
        out["run_on_save"] = bool(out.get("run_on_save", True))
        out["debounce_ms"] = max(100, min(5000, int(out.get("debounce_ms", 600))))
        out["max_problems_per_file"] = max(20, min(5000, int(out.get("max_problems_per_file", 200))))

        backend = str(out.get("backend", "ruff")).strip().lower()
        if backend not in self.BACKENDS:
            backend = "ruff"
        out["backend"] = backend

        fallback = str(out.get("fallback_backend", "ast")).strip().lower()
        if fallback not in self.FALLBACK_BACKENDS:
            fallback = "ast"
        out["fallback_backend"] = fallback

        args_cfg = out.get("args")
        if not isinstance(args_cfg, dict):
            args_cfg = {}
        ruff_args = args_cfg.get("ruff", ["check", "--output-format", "json"])
        pyflakes_args = args_cfg.get("pyflakes", [])
        args_cfg["ruff"] = [str(v) for v in ruff_args] if isinstance(ruff_args, list) and ruff_args else ["check", "--output-format", "json"]
        args_cfg["pyflakes"] = [str(v) for v in pyflakes_args] if isinstance(pyflakes_args, list) else []
        out["args"] = args_cfg

        severity_overrides_raw = out.get("severity_overrides")
        severity_overrides: dict[str, str] = {}
        if isinstance(severity_overrides_raw, dict):
            for raw_key, raw_value in severity_overrides_raw.items():
                key = str(raw_key or "").strip().upper()
                if not key:
                    continue
                value = str(raw_value or "").strip().lower()
                if value not in {"error", "warning", "info", "hint"}:
                    continue
                severity_overrides[key] = value
        out["severity_overrides"] = severity_overrides
        return out


def _run_lint_payload(payload: _LintPayload) -> dict:
    file_path = payload.file_path
    lint_cfg = payload.lint_cfg
    diagnostics: list[dict] = []
    missing_backends: list[str] = []
    selected_source = "ast"

    is_python = file_path.lower().endswith(PYTHON_SUFFIXES)
    if not is_python:
        return {
            "file_path": file_path,
            "token": payload.token,
            "reason": payload.reason,
            "diagnostics": [],
            "backend": selected_source,
            "interpreter": payload.interpreter,
            "missing_backends": [],
        }

    for backend in payload.backend_order:
        if backend == "ast":
            selected_source = "ast"
            diagnostics = _lint_with_ast(
                file_path=file_path,
                source_text=payload.source_text,
            )
            break

        run_res = _run_external_backend(
            backend=backend,
            interpreter=payload.interpreter,
            file_path=file_path,
            source_text=payload.source_text,
            args_cfg=lint_cfg.get("args", {}),
            severity_overrides=lint_cfg.get("severity_overrides", {}),
            project_root=payload.project_root,
        )
        if run_res["state"] == "ok":
            selected_source = backend
            diagnostics = run_res["diagnostics"]
            break
        if run_res["state"] == "missing":
            missing_backends.append(backend)
            continue

    diagnostics = _dedupe_and_cap(
        diagnostics=diagnostics,
        max_items=int(lint_cfg.get("max_problems_per_file", 200)),
    )

    return {
        "file_path": file_path,
        "token": payload.token,
        "reason": payload.reason,
        "diagnostics": diagnostics,
        "backend": selected_source,
        "interpreter": payload.interpreter,
        "missing_backends": missing_backends,
    }


def _run_external_backend(
    backend: str,
    interpreter: str,
    file_path: str,
    source_text: Optional[str],
    args_cfg: dict,
    severity_overrides: dict | None,
    project_root: str,
) -> dict:
    target = file_path
    temp_path = None

    try:
        if source_text is not None:
            suffix = Path(file_path).suffix or ".py"
            with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as tmp:
                tmp.write(source_text)
                temp_path = tmp.name
                target = temp_path

        cmd = _build_backend_command(
            backend=backend,
            interpreter=interpreter,
            target=target,
            args_cfg=args_cfg,
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=25,
            env=_backend_environment(project_root),
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined = (stdout + "\n" + stderr).strip()

        if _is_backend_missing(backend, combined):
            return {"state": "missing", "diagnostics": []}

        if backend == "ruff":
            parsed = _parse_ruff_json(stdout, file_path, target, severity_overrides=severity_overrides)
        else:
            parsed = _parse_pyflakes_text(combined, file_path, target)

        if proc.returncode == 0:
            return {"state": "ok", "diagnostics": parsed}
        if proc.returncode == 1 and parsed:
            return {"state": "ok", "diagnostics": parsed}
        if parsed:
            return {"state": "ok", "diagnostics": parsed}
        return {"state": "failed", "diagnostics": []}
    except Exception:
        return {"state": "failed", "diagnostics": []}
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def _backend_environment(project_root: str) -> dict:
    env = dict(os.environ)
    root = str(project_root or "").strip()
    if not root:
        return env
    tide_dir = os.path.join(root, ".tide")
    cache_home = os.path.join(tide_dir, "cache")
    ruff_cache = os.path.join(tide_dir, "ruff_cache")
    try:
        os.makedirs(cache_home, exist_ok=True)
        env["XDG_CACHE_HOME"] = cache_home
    except Exception:
        pass
    try:
        os.makedirs(ruff_cache, exist_ok=True)
        env["RUFF_CACHE_DIR"] = ruff_cache
    except Exception:
        pass
    return env


def _build_backend_command(backend: str, interpreter: str, target: str, args_cfg: dict) -> list[str]:
    if backend == "ruff":
        configured = args_cfg.get("ruff", [])
        args = [str(v) for v in configured] if isinstance(configured, list) and configured else ["check", "--output-format", "json"]
        if "--output-format" not in args:
            args.extend(["--output-format", "json"])
        return [interpreter, "-m", "ruff"] + args + [target]

    configured = args_cfg.get("pyflakes", [])
    args = [str(v) for v in configured] if isinstance(configured, list) else []
    return [interpreter, "-m", "pyflakes"] + args + [target]


def _is_backend_missing(backend: str, text: str) -> bool:
    lower = text.lower()
    if "no module named" in lower and backend in lower:
        return True
    if f"module named {backend}" in lower:
        return True
    if "modulenotfounderror" in lower and backend in lower:
        return True
    if backend == "ruff" and ("python -m ruff" in lower and "not found" in lower):
        return True
    return False


def _parse_ruff_json(
    stdout: str,
    real_file: str,
    target_file: str,
    *,
    severity_overrides: dict | None = None,
) -> list[dict]:
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []

    out: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        location = item.get("location", {})
        if not isinstance(location, dict):
            location = {}
        end_location = item.get("end_location", {})
        if not isinstance(end_location, dict):
            end_location = {}
        line = int(location.get("row") or 1)
        col = int(location.get("column") or 1)
        end_line = int(end_location.get("row") or line)
        end_col = int(end_location.get("column") or (col + 1))
        code = str(item.get("code") or "").strip() or None
        message = str(item.get("message") or "").strip() or "Lint error"
        severity = _severity_from_code(
            code,
            message=message,
            source="ruff",
            severity_overrides=severity_overrides,
        )
        fname = str(item.get("filename") or target_file).strip() or target_file
        if os.path.samefile(fname, target_file) if os.path.exists(fname) and os.path.exists(target_file) else fname == target_file:
            fname = real_file

        out.append(
            {
                "file_path": real_file,
                "line": max(1, line),
                "column": max(1, col),
                "end_line": max(1, end_line),
                "end_column": max(1, end_col),
                "severity": severity,
                "code": code,
                "message": message,
                "source": "ruff",
            }
        )
    return out


def _parse_pyflakes_text(text: str, real_file: str, target_file: str) -> list[dict]:
    line_re = re.compile(r"^(.*?):(\d+):(?:(\d+):)?\s*(.*)$")
    out: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = line_re.match(line)
        if not m:
            continue
        fname, row, col, message = m.groups()
        _ = fname, target_file
        out.append(
            {
                "file_path": real_file,
                "line": max(1, int(row)),
                "column": max(1, int(col) if col else 1),
                "end_line": max(1, int(row)),
                "end_column": max(1, (int(col) if col else 1) + 1),
                "severity": "error",
                "code": None,
                "message": message.strip() or "Lint error",
                "source": "pyflakes",
            }
        )
    return out


def _lint_with_ast(file_path: str, source_text: Optional[str]) -> list[dict]:
    if not file_path.lower().endswith(PYTHON_SUFFIXES):
        return []

    try:
        if source_text is None:
            with open(file_path, "r", encoding="utf-8") as f:
                source_text = f.read()
        ast.parse(source_text, filename=file_path)
        return []
    except SyntaxError as exc:
        line = max(1, int(exc.lineno or 1))
        col = max(1, int(exc.offset or 1))
        end_line = max(line, int(getattr(exc, "end_lineno", 0) or line))
        end_col_raw = int(getattr(exc, "end_offset", 0) or (col + 1))
        end_col = max(1, end_col_raw)
        return [
            {
                "file_path": file_path,
                "line": line,
                "column": col,
                "end_line": end_line,
                "end_column": end_col,
                "severity": "error",
                "code": "E999",
                "message": str(exc.msg or "Syntax error"),
                "source": "ast",
            }
        ]
    except Exception:
        return []


def _severity_from_code(
    code: Optional[str],
    *,
    message: str = "",
    source: str = "",
    severity_overrides: dict | None = None,
) -> str:
    c = str(code or "").strip().upper()
    msg = str(message or "").strip().lower()
    src = str(source or "").strip().lower()

    overridden = _resolve_severity_override(c, severity_overrides)
    if overridden:
        return overridden

    # Hard overrides for syntax-level issues.
    if c in {"INVALID-SYNTAX", "SYNTAX-ERROR", "E999"}:
        return "error"
    if "invalid syntax" in msg or "syntax error" in msg:
        return "error"
    if src == "ast":
        return "error"

    # Rule-specific tuning for common quality diagnostics.
    if c == "F401":
        return "warning"

    # Family defaults.
    if c.startswith("E"):
        return "error"
    if c.startswith("W"):
        return "warning"
    if c.startswith("F"):
        return "warning"
    if not c:
        return "warning"
    return "info"


def _resolve_severity_override(code: str, severity_overrides: dict | None) -> str:
    if not code:
        return ""
    if not isinstance(severity_overrides, dict) or not severity_overrides:
        return ""

    c = str(code or "").strip().upper()
    exact = severity_overrides.get(c)
    if isinstance(exact, str) and exact in {"error", "warning", "info", "hint"}:
        return exact

    best_match = ""
    best_prefix_len = -1
    for raw_key, raw_value in severity_overrides.items():
        key = str(raw_key or "").strip().upper()
        value = str(raw_value or "").strip().lower()
        if value not in {"error", "warning", "info", "hint"}:
            continue
        if not key.endswith("*"):
            continue
        prefix = key[:-1]
        if not prefix or not c.startswith(prefix):
            continue
        if len(prefix) > best_prefix_len:
            best_prefix_len = len(prefix)
            best_match = value
    return best_match


def _dedupe_and_cap(diagnostics: list[dict], max_items: int) -> list[dict]:
    unique: dict[tuple, dict] = {}
    for d in diagnostics:
        if not isinstance(d, dict):
            continue
        key = (
            str(d.get("file_path") or ""),
            int(d.get("line") or 1),
            int(d.get("column") or 1),
            int(d.get("end_line") or int(d.get("line") or 1)),
            int(d.get("end_column") or (int(d.get("column") or 1) + 1)),
            str(d.get("severity") or "warning"),
            str(d.get("code") or ""),
            str(d.get("message") or ""),
            str(d.get("source") or ""),
        )
        unique[key] = {
            "file_path": key[0],
            "line": key[1],
            "column": key[2],
            "end_line": key[3],
            "end_column": key[4],
            "severity": key[5],
            "code": key[6] or None,
            "message": key[7],
            "source": key[8],
        }

    ordered = sorted(
        unique.values(),
        key=lambda x: (
            x.get("file_path", ""),
            -SEVERITY_ORDER.get(str(x.get("severity", "warning")), 1),
            int(x.get("line", 1)),
            int(x.get("column", 1)),
            str(x.get("message", "")),
        ),
    )
    return ordered[: max(1, int(max_items))]
