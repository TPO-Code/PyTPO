from __future__ import annotations

import ast
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
from src.services.language_id import language_id_for_path


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
    analysis_sys_path: list[str]
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
    analysis_sys_path: list[str]
    project_root: str


@dataclass
class _DefinitionPayload:
    file_path: str
    source_text: str
    line: int
    column: int
    token: int
    interpreter: str
    analysis_sys_path: list[str]
    project_root: str


@dataclass
class _ReferencesPayload:
    file_path: str
    source_text: str
    line: int
    column: int
    token: int
    interpreter: str
    analysis_sys_path: list[str]
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
    from jedi.api.environment import create_environment
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

def _normalize_sys_path(paths, project_root):
    out = []
    seen = set()
    if project_root:
        root = os.path.abspath(project_root)
        if os.path.isdir(root):
            seen.add(root)
            out.append(root)
    for raw in paths or []:
        value = str(raw or "").strip()
        if not value:
            continue
        if value == "":
            continue
        abs_value = os.path.abspath(value)
        if abs_value in seen:
            continue
        seen.add(abs_value)
        out.append(abs_value)
    return out

def _environment_for_interpreter(path):
    value = str(path or "").strip()
    if not value:
        return None
    if not os.path.exists(value):
        return None
    try:
        return create_environment(path=value, safe=True)
    except Exception:
        return None

def _project_for_root(project_root, analysis_interpreter, analysis_sys_path):
    root = os.path.abspath(project_root) if project_root else ""
    sys_path = _normalize_sys_path(analysis_sys_path, root)
    kwargs = {}
    if analysis_interpreter and os.path.exists(analysis_interpreter):
        kwargs["environment_path"] = analysis_interpreter
    if sys_path:
        kwargs["sys_path"] = sys_path
        kwargs["smart_sys_path"] = False
    if not root or not os.path.isdir(root):
        return None, sys_path
    try:
        return jedi.Project(path=root, **kwargs), sys_path
    except Exception:
        return None, sys_path

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
    analysis_interpreter = str(req.get("analysis_interpreter") or "").strip()
    analysis_sys_path = req.get("analysis_sys_path")
    if not isinstance(analysis_sys_path, list):
        analysis_sys_path = []
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
        analysis_interpreter,
        tuple(str(p or "") for p in analysis_sys_path),
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

    project, normalized_sys_path = _project_for_root(
        project_root,
        analysis_interpreter,
        analysis_sys_path,
    )
    environment = _environment_for_interpreter(analysis_interpreter)

    try:
        if project is not None:
            script = jedi.Script(code=source, path=path, project=project)
        elif environment is not None:
            script = jedi.Script(code=source, path=path, environment=environment)
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

ANALYSIS_PROBE_SCRIPT = r"""
import ast
import importlib
import inspect
import json
import pkgutil
import re
import sys


def _normalize_sys_path(values):
    out = []
    seen = set()
    for raw in values or []:
        value = str(raw or "").strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _module_candidates(prefix):
    out = []
    seen = set()
    head = str(prefix or "").split(".", 1)[0]
    try:
        for item in pkgutil.iter_modules():
            name = str(item.name or "")
            if head and not name.startswith(head):
                continue
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    except Exception:
        pass

    if "." in str(prefix or ""):
        pkg_name = str(prefix).rsplit(".", 1)[0]
        try:
            pkg = importlib.import_module(pkg_name)
            pkg_path = getattr(pkg, "__path__", None)
            if pkg_path:
                for item in pkgutil.iter_modules(pkg_path):
                    full = f"{pkg_name}.{item.name}"
                    if prefix and not full.startswith(prefix):
                        continue
                    if full not in seen:
                        seen.add(full)
                        out.append(full)
        except Exception:
            pass
    return out


def _module_members(module_name, prefix):
    try:
        mod = importlib.import_module(str(module_name or ""))
    except Exception:
        return []

    out = []
    for name in dir(mod):
        if prefix and not str(name).startswith(prefix):
            continue
        kind = "name"
        try:
            value = getattr(mod, name)
            if inspect.isclass(value):
                kind = "class"
            elif callable(value):
                kind = "function"
        except Exception:
            pass
        out.append({"label": str(name), "kind": kind})
    return out


def _builtins_list():
    try:
        names = dir(__builtins__)
    except Exception:
        names = []
    return [str(name) for name in names if str(name or "").strip()]


def _resolve_dotted_object(target):
    parts = [part for part in str(target or "").split(".") if part]
    if not parts:
        return None, None
    for idx in range(len(parts), 0, -1):
        module_name = ".".join(parts[:idx])
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        value = module
        ok = True
        for attr in parts[idx:]:
            if not hasattr(value, attr):
                ok = False
                break
            try:
                value = getattr(value, attr)
            except Exception:
                ok = False
                break
        if ok:
            return module, value
    return None, None


def _resolve_type_candidates(type_name, module_name):
    resolved = []
    seen = set()
    label = str(type_name or "").strip()
    if not label:
        return []

    module, value = _resolve_dotted_object(label)
    if inspect.isclass(value):
        ident = (getattr(value, "__module__", ""), getattr(value, "__name__", ""))
        seen.add(ident)
        resolved.append(value)

    probe_modules = []
    if module is not None:
        probe_modules.append(module)
    if module_name:
        mod, _ = _resolve_dotted_object(module_name)
        if mod is not None:
            probe_modules.append(mod)
        root_name = str(module_name).split(".", 1)[0]
        root_mod, _ = _resolve_dotted_object(root_name)
        if root_mod is not None:
            probe_modules.append(root_mod)

    for holder in probe_modules:
        if not hasattr(holder, label):
            continue
        try:
            value = getattr(holder, label)
        except Exception:
            continue
        if not inspect.isclass(value):
            continue
        ident = (getattr(value, "__module__", ""), getattr(value, "__name__", ""))
        if ident in seen:
            continue
        seen.add(ident)
        resolved.append(value)
    return resolved


def _candidate_return_type_names(callable_obj):
    names = []
    seen = set()

    def _add(raw):
        value = str(raw or "").strip()
        if not value or value in {"None", "NoneType"}:
            return
        if value in seen:
            return
        seen.add(value)
        names.append(value)

    try:
        ann = inspect.signature(callable_obj).return_annotation
    except Exception:
        ann = inspect._empty
    if ann is not inspect._empty:
        if inspect.isclass(ann):
            _add(getattr(ann, "__name__", ""))
            full_name = f"{getattr(ann, '__module__', '')}.{getattr(ann, '__name__', '')}".strip(".")
            _add(full_name)
        else:
            _add(getattr(ann, "__forward_arg__", ""))
            _add(ann)

    try:
        annotations = getattr(callable_obj, "__annotations__", {}) or {}
    except Exception:
        annotations = {}
    if isinstance(annotations, dict):
        ret = annotations.get("return")
        if inspect.isclass(ret):
            _add(getattr(ret, "__name__", ""))
            full_name = f"{getattr(ret, '__module__', '')}.{getattr(ret, '__name__', '')}".strip(".")
            _add(full_name)
        else:
            _add(getattr(ret, "__forward_arg__", ""))
            _add(ret)

    try:
        doc = inspect.getdoc(callable_obj) or ""
    except Exception:
        doc = ""
    doc_patterns = [
        r"Returns:\s*(?:\n\s*)?([A-Za-z_][A-Za-z0-9_\.]*)",
        r"->\s*([A-Za-z_][A-Za-z0-9_\.]*)",
    ]
    for pattern in doc_patterns:
        for match in re.finditer(pattern, doc):
            _add(match.group(1))

    return names


def _iter_subclasses_in_package(base_class, module_name, *, limit=96):
    if not inspect.isclass(base_class):
        return []
    package_name = str(module_name or "").split(".", 1)[0]
    if not package_name:
        return []
    try:
        package = importlib.import_module(package_name)
    except Exception:
        return []
    package_path = getattr(package, "__path__", None)
    if not package_path:
        return []

    out = []
    seen = set()
    for idx, item in enumerate(pkgutil.walk_packages(package_path, package.__name__ + ".")):
        if idx >= limit:
            break
        try:
            mod = importlib.import_module(item.name)
        except Exception:
            continue
        for value in vars(mod).values():
            if not inspect.isclass(value) or value is base_class:
                continue
            try:
                if not issubclass(value, base_class):
                    continue
            except Exception:
                continue
            ident = (getattr(value, "__module__", ""), getattr(value, "__name__", ""))
            if ident in seen:
                continue
            seen.add(ident)
            out.append(value)
    return out


def _member_items_for_return_types(target, prefix):
    module, callable_obj = _resolve_dotted_object(target)
    if callable_obj is None:
        return []

    module_name = getattr(module, "__name__", "")
    type_candidates = []
    seen_types = set()
    for type_name in _candidate_return_type_names(callable_obj):
        for cls in _resolve_type_candidates(type_name, module_name):
            ident = (getattr(cls, "__module__", ""), getattr(cls, "__name__", ""))
            if ident in seen_types:
                continue
            seen_types.add(ident)
            type_candidates.append(cls)

    if inspect.isclass(callable_obj):
        ident = (getattr(callable_obj, "__module__", ""), getattr(callable_obj, "__name__", ""))
        if ident not in seen_types:
            seen_types.add(ident)
            type_candidates.append(callable_obj)

    for cls in list(type_candidates):
        for subcls in _iter_subclasses_in_package(cls, module_name):
            ident = (getattr(subcls, "__module__", ""), getattr(subcls, "__name__", ""))
            if ident in seen_types:
                continue
            seen_types.add(ident)
            type_candidates.append(subcls)

    items = []
    seen_members = set()
    for cls in type_candidates:
        owner = getattr(cls, "__name__", "")
        for name in dir(cls):
            label = str(name or "")
            if not label:
                continue
            if prefix and not label.startswith(prefix):
                continue
            if label in seen_members:
                continue
            seen_members.add(label)
            kind = "name"
            try:
                value = getattr(cls, label)
                if isinstance(value, property):
                    kind = "property"
                elif callable(value):
                    kind = "function"
            except Exception:
                pass
            items.append(
                {
                    "label": label,
                    "kind": kind,
                    "owner": owner,
                }
            )
    return items


try:
    req = json.loads(sys.stdin.read() or "{}")
except Exception:
    print(json.dumps({"ok": False, "error": "bad_json"}))
    raise SystemExit(0)

sys_path = _normalize_sys_path(req.get("sys_path"))
if sys_path:
    sys.path[:] = sys_path

mode = str(req.get("mode") or "").strip().lower()
target = str(req.get("target") or "")
prefix = str(req.get("prefix") or "")

result = {"ok": True}
if mode == "module_candidates":
    result["items"] = _module_candidates(prefix)
elif mode == "module_members":
    result["items"] = _module_members(target, prefix)
elif mode == "builtins":
    result["items"] = _builtins_list()
elif mode == "callable_return_members":
    result["items"] = _member_items_for_return_types(target, prefix)
else:
    result = {"ok": False, "error": "bad_mode"}

print(json.dumps(result))
"""


class JediServer:
    def __init__(self, worker_interpreter: str, project_root: str):
        self.worker_interpreter = str(worker_interpreter).strip() or sys.executable or "python"
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
                [self.worker_interpreter, "-u", "-c", JEDI_SERVER_SCRIPT],
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
        self._worker_interpreter = str(sys.executable or "python")

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
        self._analysis_sys_path_cache: dict[str, list[str]] = {}
        self._analysis_probe_cache: dict[tuple[str, tuple[str, ...], str, str, str], object] = {}

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

        resolved_interpreter = self._resolve_interpreter(cpath)
        payload = _CompletionPayload(
            file_path=cpath,
            source_text=source_text or "",
            line=max(1, int(line)),
            column=max(0, int(column)),
            prefix=str(prefix or ""),
            token=max(1, int(token)),
            reason=str(reason or "auto"),
            completion_cfg=dict(self._completion_cfg),
            interpreter=resolved_interpreter,
            analysis_sys_path=self._resolve_analysis_sys_path(resolved_interpreter),
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

        resolved_interpreter = self._resolve_interpreter(cpath)
        payload = _SignaturePayload(
            file_path=cpath,
            source_text=source_text or "",
            line=max(1, int(line)),
            column=max(0, int(column)),
            token=max(1, int(token)),
            interpreter=resolved_interpreter,
            analysis_sys_path=self._resolve_analysis_sys_path(resolved_interpreter),
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

        resolved_interpreter = str(interpreter or self._resolve_interpreter(cpath))
        payload = _DefinitionPayload(
            file_path=cpath,
            source_text=source_text or "",
            line=max(1, int(line)),
            column=max(0, int(column)),
            token=tok,
            interpreter=resolved_interpreter,
            analysis_sys_path=self._resolve_analysis_sys_path(resolved_interpreter),
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

        resolved_interpreter = str(interpreter or self._resolve_interpreter(cpath))
        payload = _ReferencesPayload(
            file_path=cpath,
            source_text=source_text or "",
            line=max(1, int(line)),
            column=max(0, int(column)),
            token=tok,
            interpreter=resolved_interpreter,
            analysis_sys_path=self._resolve_analysis_sys_path(resolved_interpreter),
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

    def _server_key(self, worker_interpreter: str, project_root: str) -> str:
        return f"{worker_interpreter}::{project_root}"

    def _get_server(self, worker_interpreter: str, project_root: str) -> JediServer:
        key = self._server_key(worker_interpreter, project_root)
        srv = self._servers.get(key)
        if srv is None:
            srv = JediServer(worker_interpreter=worker_interpreter, project_root=project_root)
            self._servers[key] = srv
        return srv

    def _shutdown_servers(self):
        for s in self._servers.values():
            s.shutdown()
        self._servers.clear()

    def _resolve_analysis_sys_path(self, interpreter: str) -> list[str]:
        key = str(interpreter or "").strip()
        if not key:
            return []
        cached = self._analysis_sys_path_cache.get(key)
        if cached is not None:
            return list(cached)
        try:
            proc = subprocess.run(
                [
                    key,
                    "-c",
                    (
                        "import json, sys; "
                        "print(json.dumps([str(p) for p in sys.path if isinstance(p, str) and p]))"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=3.0,
                cwd=self._project_root if os.path.isdir(self._project_root) else None,
            )
        except Exception:
            self._analysis_sys_path_cache[key] = []
            return []
        if proc.returncode != 0:
            self._analysis_sys_path_cache[key] = []
            return []
        try:
            raw_paths = json.loads(str(proc.stdout or "").strip() or "[]")
        except Exception:
            raw_paths = []
        if not isinstance(raw_paths, list):
            raw_paths = []
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in raw_paths:
            value = str(raw or "").strip()
            if not value:
                continue
            norm = os.path.abspath(value)
            if norm in seen:
                continue
            seen.add(norm)
            normalized.append(norm)
        self._analysis_sys_path_cache[key] = list(normalized)
        return list(normalized)

    def _run_analysis_probe(
        self,
        *,
        interpreter: str,
        analysis_sys_path: list[str],
        project_root: str,
        mode: str,
        target: str = "",
        prefix: str = "",
    ) -> object:
        interp = str(interpreter or "").strip()
        mode_key = str(mode or "").strip().lower()
        path_key = tuple(str(p or "").strip() for p in analysis_sys_path or [] if str(p or "").strip())
        cache_key = (interp, path_key, str(project_root or ""), mode_key, f"{target}\0{prefix}")
        if cache_key in self._analysis_probe_cache:
            return self._analysis_probe_cache[cache_key]
        if not interp or mode_key not in {"module_candidates", "module_members", "builtins", "callable_return_members"}:
            return []

        payload = {
            "mode": mode_key,
            "target": str(target or ""),
            "prefix": str(prefix or ""),
            "sys_path": list(path_key),
        }
        try:
            proc = subprocess.run(
                [interp, "-c", ANALYSIS_PROBE_SCRIPT],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=3.0,
                cwd=project_root if os.path.isdir(project_root) else None,
            )
        except Exception:
            return []
        if proc.returncode != 0:
            return []
        try:
            result_obj = json.loads(str(proc.stdout or "").strip() or "{}")
        except Exception:
            return []
        if not isinstance(result_obj, dict) or not bool(result_obj.get("ok", False)):
            return []
        items = result_obj.get("items", [])
        self._analysis_probe_cache[cache_key] = items
        return items

    def _analysis_module_candidates(
        self,
        *,
        interpreter: str,
        analysis_sys_path: list[str],
        project_root: str,
        prefix: str,
    ) -> list[str]:
        result = self._run_analysis_probe(
            interpreter=interpreter,
            analysis_sys_path=analysis_sys_path,
            project_root=project_root,
            mode="module_candidates",
            prefix=prefix,
        )
        if not isinstance(result, list):
            return []
        return [str(item or "") for item in result if str(item or "").strip()]

    def _analysis_module_members(
        self,
        *,
        interpreter: str,
        analysis_sys_path: list[str],
        project_root: str,
        module_name: str,
        prefix: str,
    ) -> list[dict]:
        result = self._run_analysis_probe(
            interpreter=interpreter,
            analysis_sys_path=analysis_sys_path,
            project_root=project_root,
            mode="module_members",
            target=module_name,
            prefix=prefix,
        )
        if not isinstance(result, list):
            return []
        out: list[dict] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            out.append(
                {
                    "label": label,
                    "insert_text": label,
                    "kind": str(item.get("kind") or "name").strip().lower() or "name",
                    "detail": "",
                    "source": "fallback",
                    "source_scope": "project",
                }
            )
        return out

    def _analysis_builtins(self, *, interpreter: str, analysis_sys_path: list[str], project_root: str) -> list[str]:
        result = self._run_analysis_probe(
            interpreter=interpreter,
            analysis_sys_path=analysis_sys_path,
            project_root=project_root,
            mode="builtins",
        )
        if not isinstance(result, list):
            return []
        return [str(item or "") for item in result if str(item or "").strip()]

    def _analysis_callable_return_members(
        self,
        *,
        interpreter: str,
        analysis_sys_path: list[str],
        project_root: str,
        target: str,
        prefix: str,
    ) -> list[dict]:
        result = self._run_analysis_probe(
            interpreter=interpreter,
            analysis_sys_path=analysis_sys_path,
            project_root=project_root,
            mode="callable_return_members",
            target=target,
            prefix=prefix,
        )
        if not isinstance(result, list):
            return []
        out: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for item in result:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            key = (label, str(item.get("owner") or ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "label": label,
                    "insert_text": label,
                    "kind": str(item.get("kind") or "name").strip().lower() or "name",
                    "detail": "runtime inferred return member",
                    "owner": str(item.get("owner") or "").strip(),
                    "source": "runtime",
                    "source_scope": "runtime_inference",
                }
            )
        return out

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
            worker_interpreter = str(result_obj.get("worker_interpreter") or result_obj.get("interpreter") or "python")
            if missing_backend == "jedi" and worker_interpreter not in self._jedi_missing_warned:
                self._jedi_missing_warned.add(worker_interpreter)
                self.statusMessage.emit(
                    f"jedi not available in interpreter {worker_interpreter}, using keywords/builtins fallback."
                )
            self.definitionReady.emit(result_obj)
            return
        if result_type == "signature":
            if token != self._latest_signature_token_by_file.get(cpath, 0):
                return
            missing_backend = str(result_obj.get("missing_backend") or "")
            worker_interpreter = str(result_obj.get("worker_interpreter") or result_obj.get("interpreter") or "python")
            if missing_backend == "jedi" and worker_interpreter not in self._jedi_missing_warned:
                self._jedi_missing_warned.add(worker_interpreter)
                self.statusMessage.emit(
                    f"jedi not available in interpreter {worker_interpreter}, using keywords/builtins fallback."
                )
            self.signatureReady.emit(result_obj)
            return
        if token != self._latest_token_by_file.get(cpath, 0):
            return

        missing_backend = str(result_obj.get("missing_backend") or "")
        worker_interpreter = str(result_obj.get("worker_interpreter") or result_obj.get("interpreter") or "python")
        if missing_backend == "jedi" and worker_interpreter not in self._jedi_missing_warned:
            self._jedi_missing_warned.add(worker_interpreter)
            self.statusMessage.emit(
                f"jedi not available in interpreter {worker_interpreter}, using keywords/builtins fallback."
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
        language_id = language_id_for_path(payload.file_path, default="plaintext")

        if language_id == "tdocproject":
            context = _detect_tdocproject_context(
                source_text=payload.source_text,
                line=payload.line,
                column=payload.column,
                prefix=payload.prefix,
            )
            items = _fallback_tdocproject_candidates(
                payload=payload,
                context=context,
                max_items=max(max_items * 4, 120),
            )
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
                "backend": "tdocproject",
                "missing_backend": "",
                "interpreter": payload.interpreter,
                "worker_interpreter": self._worker_interpreter,
                "reason": payload.reason,
            }

        context = _detect_context(
            source_text=payload.source_text,
            line=payload.line,
            column=payload.column,
            prefix=payload.prefix,
        )
        resolved_callable_target = _resolve_attribute_callable_target(payload.source_text, context)
        if resolved_callable_target:
            context = {**context, "resolved_callable_target": resolved_callable_target}

        items: list[dict] = []
        used_backend = "fallback"
        missing_backend = ""

        if str(cfg.get("backend", "jedi")).strip().lower() == "jedi":
            server = self._get_server(self._worker_interpreter, payload.project_root)
            req = {
                "path": payload.file_path,
                "source": payload.source_text,
                "line": payload.line,
                "column": payload.column,
                "max_items": max(max_items * 6, 300),
                "project_root": payload.project_root,
                "analysis_interpreter": payload.interpreter,
                "analysis_sys_path": list(payload.analysis_sys_path),
                "include_signatures": True,
            }
            jedi_res = server.request(req, timeout_s=1.2)
            state = str(jedi_res.get("state") or "failed")

            if state == "ok":
                used_backend = "jedi"
                items = _annotate_jedi_items(jedi_res.get("items", []))
                items = _context_filter_items(items, context)
                if _should_augment_dynamic_attribute_items(items, context):
                    runtime_items = self._analysis_callable_return_members(
                        interpreter=payload.interpreter,
                        analysis_sys_path=payload.analysis_sys_path,
                        project_root=payload.project_root,
                        target=str(
                            context.get("resolved_callable_target")
                            or context.get("callable_target")
                            or ""
                        ),
                        prefix=payload.prefix,
                    )
                    if runtime_items:
                        items = _merge_completion_item_lists(runtime_items, items)
            elif state == "missing":
                missing_backend = "jedi"

        if not items:
            items = _fallback_candidates(
                payload=payload,
                context=context,
                max_items=max(max_items * 4, 120),
                module_candidates_provider=lambda prefix: self._analysis_module_candidates(
                    interpreter=payload.interpreter,
                    analysis_sys_path=payload.analysis_sys_path,
                    project_root=payload.project_root,
                    prefix=prefix,
                ),
                module_members_provider=lambda module_name, prefix: self._analysis_module_members(
                    interpreter=payload.interpreter,
                    analysis_sys_path=payload.analysis_sys_path,
                    project_root=payload.project_root,
                    module_name=module_name,
                    prefix=prefix,
                ),
                builtins_provider=lambda: self._analysis_builtins(
                    interpreter=payload.interpreter,
                    analysis_sys_path=payload.analysis_sys_path,
                    project_root=payload.project_root,
                ),
            )

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
            "worker_interpreter": self._worker_interpreter,
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
            server = self._get_server(self._worker_interpreter, payload.project_root)
            req = {
                "request": "signature",
                "path": payload.file_path,
                "source": payload.source_text,
                "line": payload.line,
                "column": payload.column,
                "project_root": payload.project_root,
                "analysis_interpreter": payload.interpreter,
                "analysis_sys_path": list(payload.analysis_sys_path),
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
            "worker_interpreter": self._worker_interpreter,
        }

    def _run_definition_payload_fast(self, payload: _DefinitionPayload) -> dict:
        results: list[dict] = []
        source = "fallback"
        missing_backend = ""
        backend = str(self._completion_cfg.get("backend", "jedi")).strip().lower()

        if backend == "jedi":
            server = self._get_server(self._worker_interpreter, payload.project_root)
            req = {
                "request": "definition",
                "path": payload.file_path,
                "source": payload.source_text,
                "line": payload.line,
                "column": payload.column,
                "project_root": payload.project_root,
                "analysis_interpreter": payload.interpreter,
                "analysis_sys_path": list(payload.analysis_sys_path),
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
            "worker_interpreter": self._worker_interpreter,
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
            server = self._get_server(self._worker_interpreter, payload.project_root)
            req = {
                "request": "references",
                "path": payload.file_path,
                "source": payload.source_text,
                "line": payload.line,
                "column": payload.column,
                "project_root": payload.project_root,
                "analysis_interpreter": payload.interpreter,
                "analysis_sys_path": list(payload.analysis_sys_path),
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
                                "interpreter": payload.interpreter,
                                "worker_interpreter": self._worker_interpreter,
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
                    "interpreter": payload.interpreter,
                    "worker_interpreter": self._worker_interpreter,
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
            "worker_interpreter": self._worker_interpreter,
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
            attr_ctx = _attribute_context(left[: idx - 1], pfx)
            return {"mode": "attribute", "import_module": "", "prefix": pfx, **attr_ctx}
    if left.endswith("."):
        attr_ctx = _attribute_context(left[:-1], "")
        return {"mode": "attribute", "import_module": "", "prefix": "", **attr_ctx}

    return {"mode": "normal", "import_module": "", "prefix": prefix}


def _attribute_context(base_text: str, prefix: str) -> dict:
    expr = _extract_attribute_base_expression(base_text)
    callable_target = _callable_target_for_expression(expr)
    return {
        "attribute_expr": expr,
        "callable_target": callable_target,
        "prefix": prefix,
    }


def _extract_attribute_base_expression(base_text: str) -> str:
    text = str(base_text or "")
    end = len(text)
    while end > 0 and text[end - 1].isspace():
        end -= 1
    if end <= 0:
        return ""

    depth = 0
    idx = end - 1
    stop_chars = set("=,+-*/%&|^~<>[]{}:;\n\r\t ")
    while idx >= 0:
        ch = text[idx]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth <= 0:
                break
            depth -= 1
        elif depth == 0 and ch in stop_chars:
            break
        idx -= 1
    return text[idx + 1:end].strip()


def _callable_target_for_expression(expr: str) -> str:
    text = str(expr or "").strip()
    if not text:
        return ""
    match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_\.]*)\s*\(", text)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _resolve_attribute_callable_target(source_text: str, context: dict) -> str:
    direct = str(context.get("callable_target") or "").strip()
    if direct:
        return direct

    expr = str(context.get("attribute_expr") or "").strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", expr):
        return ""
    return _infer_symbol_callable_target(source_text, expr)


def _infer_symbol_callable_target(source_text: str, symbol_name: str) -> str:
    target = str(symbol_name or "").strip()
    if not target:
        return ""
    try:
        tree = ast.parse(source_text or "")
    except Exception:
        tree = None

    resolved = ""

    if tree is not None:
        class _AssignmentVisitor(ast.NodeVisitor):
            def visit_Assign(self, node):
                nonlocal resolved
                if resolved:
                    return
                for assign_target in node.targets:
                    if isinstance(assign_target, ast.Name) and assign_target.id == target:
                        resolved = _callable_name_from_ast(node.value)
                        if resolved:
                            return
                self.generic_visit(node)

            def visit_AnnAssign(self, node):
                nonlocal resolved
                if resolved:
                    return
                assign_target = node.target
                if isinstance(assign_target, ast.Name) and assign_target.id == target:
                    resolved = _callable_name_from_ast(node.value)
                    if resolved:
                        return
                self.generic_visit(node)

        _AssignmentVisitor().visit(tree)
        if resolved:
            return resolved

    assign_re = re.compile(
        rf"^\s*{re.escape(target)}\s*=\s*([A-Za-z_][A-Za-z0-9_\.]*)\s*\(",
    )
    for raw_line in reversed((source_text or "").splitlines()):
        match = assign_re.match(str(raw_line or ""))
        if not match:
            continue
        return str(match.group(1) or "").strip()
    return ""


def _callable_name_from_ast(node: ast.AST | None) -> str:
    value = node
    while isinstance(value, ast.IfExp):
        value = value.body
    if not isinstance(value, ast.Call):
        return ""

    func = value.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(str(func.attr or ""))
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(str(func.id or ""))
    else:
        return ""
    parts.reverse()
    if not parts:
        return ""
    return ".".join(part for part in parts if part)


_TDOCPROJECT_RULE_VALUE_RE = re.compile(r"^\s*(?P<rule>include|ignore)\s*:\s*(?P<value>[^#]*)$")
_TDOCPROJECT_FRONTMATTER_RULE_RE = re.compile(r"^\s*frontmatter_schema\s*:\s*(?P<value>[^#]*)$")
_TDOCPROJECT_INDEX_GROUP_RULE_RE = re.compile(r"^\s*index_group_by\s*:\s*(?P<value>[^#]*)$")
_TDOCPROJECT_SECTION_HEADER_RE = re.compile(r"^\s*(?P<section>[^=#:\n][^=#\n]*?)\s*:\s*$")
_TDOCPROJECT_METADATA_KEY_RE = re.compile(r"(?:^|;)\s*(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*=")
_TDOCPROJECT_DEFAULT_SECTION_SUGGESTIONS = [
    "Characters",
    "Locations",
    "Concepts",
    "Guides",
    "References",
]
_TDOCPROJECT_DEFAULT_METADATA_KEYS = [
    "doc",
    "role",
    "tags",
    "status",
    "owner",
]


def _detect_tdocproject_context(source_text: str, line: int, column: int, prefix: str) -> dict:
    lines = source_text.splitlines()
    line_text = lines[line - 1] if 1 <= line <= len(lines) else ""
    col = max(0, min(len(line_text), int(column)))
    left = line_text[:col]
    stripped = left.lstrip()

    if stripped.startswith("#"):
        return {"mode": "comment", "prefix": prefix}
    frontmatter_m = _TDOCPROJECT_FRONTMATTER_RULE_RE.match(left)
    if frontmatter_m:
        raw_value = str(frontmatter_m.group("value") or "").lstrip()
        if raw_value.startswith('"') or raw_value.startswith("'"):
            raw_value = raw_value[1:]
        raw_value = raw_value.replace("\\", "/")
        if "/" in raw_value:
            path_prefix, leaf_prefix = raw_value.rsplit("/", 1)
            path_prefix = f"{path_prefix}/"
        else:
            path_prefix, leaf_prefix = "", raw_value
        return {
            "mode": "frontmatter_schema_value",
            "prefix": leaf_prefix,
            "path_prefix": path_prefix,
        }
    index_group_m = _TDOCPROJECT_INDEX_GROUP_RULE_RE.match(left)
    if index_group_m:
        return {
            "mode": "index_group_value",
            "prefix": str(index_group_m.group("value") or "").strip().lower(),
        }
    rule_m = _TDOCPROJECT_RULE_VALUE_RE.match(left)
    if rule_m:
        return {
            "mode": "rule_value",
            "rule": str(rule_m.group("rule") or "").strip().lower(),
            "prefix": prefix,
        }
    if ";" in left:
        return {"mode": "metadata", "prefix": prefix}
    if stripped.endswith(":") and "=" not in stripped:
        return {"mode": "section_header", "prefix": prefix}
    return {"mode": "normal", "prefix": prefix}


def _extract_tdocproject_section_names(source_text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in (source_text or "").splitlines():
        line = str(raw or "").strip()
        if not line or line.startswith("#"):
            continue
        if (
            _TDOCPROJECT_RULE_VALUE_RE.match(line)
            or _TDOCPROJECT_FRONTMATTER_RULE_RE.match(line)
            or _TDOCPROJECT_INDEX_GROUP_RULE_RE.match(line)
        ):
            continue
        match = _TDOCPROJECT_SECTION_HEADER_RE.match(line)
        if not match:
            continue
        section = str(match.group("section") or "").strip()
        if not section:
            continue
        key = section.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(section)
    return out


def _extract_tdocproject_metadata_keys(source_text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in (source_text or "").splitlines():
        line = str(raw or "")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if (
            _TDOCPROJECT_RULE_VALUE_RE.match(stripped)
            or _TDOCPROJECT_FRONTMATTER_RULE_RE.match(stripped)
            or _TDOCPROJECT_INDEX_GROUP_RULE_RE.match(stripped)
        ):
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if ";" not in line and indent <= 0:
            continue
        for match in _TDOCPROJECT_METADATA_KEY_RE.finditer(line):
            key = str(match.group("key") or "").strip().lower()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _fallback_tdocproject_candidates(payload: _CompletionPayload, context: dict, max_items: int) -> list[dict]:
    mode = str(context.get("mode") or "normal")
    file_sections = _extract_tdocproject_section_names(payload.source_text)
    metadata_keys = _extract_tdocproject_metadata_keys(payload.source_text)

    sections = list(_TDOCPROJECT_DEFAULT_SECTION_SUGGESTIONS)
    for section in file_sections:
        if section.casefold() not in {item.casefold() for item in sections}:
            sections.append(section)

    keys = list(_TDOCPROJECT_DEFAULT_METADATA_KEYS)
    for key in metadata_keys:
        if key.casefold() not in {item.casefold() for item in keys}:
            keys.append(key)

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(
        label: str,
        *,
        insert_text: str | None = None,
        kind: str = "name",
        detail: str = "",
    ) -> None:
        shown = str(label or "").strip()
        text = str(insert_text if insert_text is not None else shown)
        if not shown or not text.strip():
            return
        key = (shown, text)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "label": shown,
                "insert_text": text,
                "kind": kind,
                "detail": detail,
                "source": "tdocproject",
                "source_scope": "current_file",
            }
        )

    if mode == "frontmatter_schema_value":
        path_prefix = str(context.get("path_prefix") or "").replace("\\", "/")
        if path_prefix.startswith("./"):
            path_prefix = path_prefix[2:]
        if path_prefix.startswith("/"):
            path_prefix = path_prefix.lstrip("/")
        search_rel = path_prefix.rstrip("/")
        base_dir = os.path.dirname(payload.file_path) if payload.file_path else payload.project_root
        search_dir = os.path.join(base_dir, search_rel) if search_rel else base_dir
        seen_labels: set[str] = set()
        try:
            if os.path.isdir(search_dir):
                for entry in sorted(os.listdir(search_dir), key=lambda item: str(item).casefold()):
                    name = str(entry or "").strip()
                    if not name:
                        continue
                    abs_candidate = os.path.join(search_dir, name)
                    rel_candidate = f"{search_rel}/{name}" if search_rel else name
                    if os.path.isdir(abs_candidate):
                        if name.casefold() in seen_labels:
                            continue
                        seen_labels.add(name.casefold())
                        _add(
                            name,
                            insert_text=f"{rel_candidate}/",
                            kind="folder",
                            detail="directory",
                        )
                        continue
                    if not name.lower().endswith(".json"):
                        continue
                    if name.casefold() in seen_labels:
                        continue
                    seen_labels.add(name.casefold())
                    _add(
                        name,
                        insert_text=rel_candidate,
                        kind="file",
                        detail="json schema",
                    )
        except Exception:
            pass
        if not out:
            _add("frontmatter.schema.json", kind="file", detail="json schema")
            _add("schemas", insert_text="schemas/", kind="folder", detail="directory")
        return out[: max_items * 2]

    if mode == "rule_value":
        _add("**/*.tdoc", kind="text", detail="glob pattern")
        _add("docs/**/*.tdoc", kind="text", detail="glob pattern")
        _add("guides/**/*.tdoc", kind="text", detail="glob pattern")
        _add("drafts/**/*.tdoc", kind="text", detail="glob pattern")
        _add("index.tdoc", kind="text", detail="single document")
        return out[: max_items * 2]

    if mode == "index_group_value":
        _add("none", kind="keyword", detail="group references by file path")
        _add("folder", kind="keyword", detail="group references/documents by folder")
        return out[: max_items * 2]

    if mode == "metadata":
        for key in keys:
            _add(f"{key}=", kind="property", detail="symbol metadata")
        _add("key=value", kind="snippet", detail="metadata template")
        return out[: max_items * 2]

    _add("include:", insert_text="include: ", kind="keyword", detail="index include rule")
    _add("ignore:", insert_text="ignore: ", kind="keyword", detail="index ignore rule")
    _add("frontmatter_schema:", insert_text="frontmatter_schema: ", kind="keyword", detail="frontmatter schema rule")
    _add("index_group_by:", insert_text="index_group_by: ", kind="keyword", detail="index grouping mode")
    for section in sections:
        _add(f"{section}:", kind="class", detail="section header")
    _add("Symbol Name", kind="snippet", detail="canonical symbol")
    _add("Symbol Name = Alias 1 | Alias 2", kind="snippet", detail="canonical + aliases")
    _add("Symbol Name ; key=value", kind="snippet", detail="symbol metadata")
    _add(
        "Symbol Name = Alias 1 | Alias 2 ; key=value",
        kind="snippet",
        detail="aliases + metadata",
    )
    _add("# Comment", kind="snippet", detail="comment line")
    return out[: max_items * 2]


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


def _merge_completion_item_lists(primary: list[dict], secondary: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for source in (primary, secondary):
        for item in source:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("insert_text") or "").strip()
            insert_text = str(item.get("insert_text") or label).strip()
            if not label or not insert_text:
                continue
            key = (label, insert_text)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _should_augment_dynamic_attribute_items(items: list[dict], context: dict) -> bool:
    if str(context.get("mode") or "") != "attribute":
        return False
    if not str(
        context.get("resolved_callable_target")
        or context.get("callable_target")
        or ""
    ).strip():
        return False
    if not items:
        return True

    public_labels: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("insert_text") or "").strip()
        if not label:
            continue
        if label.startswith("_"):
            continue
        public_labels.append(label)

    if not public_labels:
        return True
    return all(label in {"NoneType", "NoneType()"} for label in public_labels)


def _fallback_candidates(
    payload: _CompletionPayload,
    context: dict,
    max_items: int,
    *,
    module_candidates_provider: Callable[[str], list[str]] | None = None,
    module_members_provider: Callable[[str, str], list[dict]] | None = None,
    builtins_provider: Callable[[], list[str]] | None = None,
) -> list[dict]:
    mode = str(context.get("mode") or "normal")
    prefix = str(context.get("prefix") or payload.prefix or "")

    if mode in {"import_stmt", "from_module"}:
        return _fallback_module_candidates(
            prefix=prefix,
            project_root=payload.project_root,
            max_items=max_items,
            module_candidates_provider=module_candidates_provider,
        )

    if mode == "from_import":
        module_name = str(context.get("import_module") or "").strip()
        return _fallback_module_members(
            module_name=module_name,
            prefix=prefix,
            max_items=max_items,
            project_root=payload.project_root,
            module_members_provider=module_members_provider,
        )

    if mode == "attribute":
        return _fallback_attribute_candidates(
            payload.source_text,
            payload.line,
            payload.column,
            prefix,
            max_items,
            project_root=payload.project_root,
            module_members_provider=module_members_provider,
        )

    return _fallback_normal_candidates(
        source_text=payload.source_text,
        prefix=prefix,
        max_items=max_items,
        builtins_provider=builtins_provider,
    )


def _module_name_candidates_for_symbol(source_text: str, symbol_name: str) -> list[str]:
    target = str(symbol_name or "").strip()
    if not target:
        return []

    try:
        tree = ast.parse(source_text or "")
    except Exception:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        value = str(name or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        candidates.append(value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = str(getattr(alias, "name", "") or "").strip()
                if not module_name:
                    continue
                alias_name = str(getattr(alias, "asname", "") or "").strip()
                bound_name = alias_name or module_name.split(".", 1)[0]
                if bound_name == target:
                    _add(module_name if alias_name else bound_name)
        elif isinstance(node, ast.ImportFrom):
            module_name = str(getattr(node, "module", "") or "").strip()
            if not module_name:
                continue
            for alias in node.names:
                imported_name = str(getattr(alias, "name", "") or "").strip()
                if not imported_name or imported_name == "*":
                    continue
                alias_name = str(getattr(alias, "asname", "") or "").strip()
                bound_name = alias_name or imported_name
                if bound_name == target:
                    _add(f"{module_name}.{imported_name}")

    if not candidates and "." not in target:
        _add(target)
    return candidates


def _fallback_attribute_candidates(
    source_text: str,
    line: int,
    column: int,
    prefix: str,
    max_items: int,
    *,
    project_root: str = "",
    module_members_provider: Callable[[str, str], list[dict]] | None = None,
) -> list[dict]:
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
    module_names = _module_name_candidates_for_symbol(source_text, obj_name)
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

    inserted = False
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)
        inserted = True
    try:
        for module_name in module_names:
            if callable(module_members_provider):
                for item in module_members_provider(module_name, prefix):
                    label = str(item.get("label") or "")
                    if label:
                        names.add(label)
                continue
            try:
                mod = importlib.import_module(module_name)
            except Exception:
                continue
            for name in dir(mod):
                names.add(str(name))
    finally:
        if inserted:
            try:
                sys.path.pop(0)
            except Exception:
                pass

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


def _fallback_normal_candidates(
    source_text: str,
    prefix: str,
    max_items: int,
    *,
    builtins_provider: Callable[[], list[str]] | None = None,
) -> list[dict]:
    names: dict[str, str] = {}
    for token in re.findall(r"[A-Za-z_]\w*", source_text or ""):
        names[token] = "current_file"
    for k in keyword.kwlist:
        names.setdefault(k, "builtins")
    builtin_names = builtins_provider() if callable(builtins_provider) else dir(builtins)
    for b in builtin_names:
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


def _fallback_module_candidates(
    prefix: str,
    project_root: str,
    max_items: int,
    *,
    module_candidates_provider: Callable[[str], list[str]] | None = None,
) -> list[dict]:
    candidates: dict[str, str] = {}
    module_candidates = (
        module_candidates_provider(prefix)
        if callable(module_candidates_provider)
        else list(_iter_available_modules(prefix))
    )
    for mod in module_candidates:
        candidates[str(mod)] = "interpreter_modules"
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


def _fallback_module_members(
    module_name: str,
    prefix: str,
    max_items: int,
    project_root: str = "",
    *,
    module_members_provider: Callable[[str, str], list[dict]] | None = None,
) -> list[dict]:
    if not module_name:
        return []

    if callable(module_members_provider):
        provided = module_members_provider(module_name, prefix)
        if provided:
            return provided[: max_items * 2]

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
        "runtime_inference": 540,
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
