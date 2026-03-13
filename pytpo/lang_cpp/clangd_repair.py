"""Helpers for repairing clangd stdlib include resolution in project scope."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


_STD_HEADER_NAMES: set[str] = {
    "algorithm",
    "any",
    "array",
    "atomic",
    "bit",
    "bitset",
    "cassert",
    "cctype",
    "cerrno",
    "cfenv",
    "cfloat",
    "charconv",
    "chrono",
    "cinttypes",
    "climits",
    "clocale",
    "cmath",
    "codecvt",
    "compare",
    "complex",
    "concepts",
    "condition_variable",
    "coroutine",
    "csetjmp",
    "csignal",
    "cstdarg",
    "cstddef",
    "cstdint",
    "cstdio",
    "cstdlib",
    "cstring",
    "ctime",
    "cwchar",
    "cwctype",
    "deque",
    "exception",
    "execution",
    "filesystem",
    "format",
    "forward_list",
    "fstream",
    "functional",
    "future",
    "initializer_list",
    "iomanip",
    "ios",
    "iosfwd",
    "iostream",
    "istream",
    "iterator",
    "latch",
    "limits",
    "list",
    "locale",
    "map",
    "memory",
    "memory_resource",
    "mutex",
    "new",
    "numbers",
    "numeric",
    "optional",
    "ostream",
    "queue",
    "random",
    "ranges",
    "ratio",
    "regex",
    "scoped_allocator",
    "semaphore",
    "set",
    "shared_mutex",
    "source_location",
    "span",
    "sstream",
    "stack",
    "stdexcept",
    "stop_token",
    "streambuf",
    "string",
    "string_view",
    "strstream",
    "syncstream",
    "system_error",
    "thread",
    "tuple",
    "type_traits",
    "typeindex",
    "typeinfo",
    "unordered_map",
    "unordered_set",
    "utility",
    "valarray",
    "variant",
    "vector",
}

_STD_C_HEADER_NAMES: set[str] = {
    "assert.h",
    "ctype.h",
    "errno.h",
    "float.h",
    "inttypes.h",
    "limits.h",
    "locale.h",
    "math.h",
    "setjmp.h",
    "signal.h",
    "stdarg.h",
    "stddef.h",
    "stdint.h",
    "stdio.h",
    "stdlib.h",
    "string.h",
    "time.h",
    "uchar.h",
    "wchar.h",
    "wctype.h",
}

_CPP_FILE_SUFFIXES = {".h", ".hpp", ".hh", ".hxx", ".ipp", ".tpp", ".inl", ".c", ".cc", ".cpp", ".cxx"}
_SKIP_SCAN_DIRS = {".git", ".hg", ".svn", ".tide", ".venv", "node_modules", "__pycache__"}
_MANAGED_BEGIN = "# >>> PYTPO clangd include repair >>>"
_MANAGED_END = "# <<< PYTPO clangd include repair <<<"


@dataclass(slots=True)
class ClangdRepairResult:
    ok: bool
    query_driver: str
    query_driver_changed: bool
    fixed_by_query_driver: bool
    wrote_clangd_file: bool
    clangd_file_path: str
    probe_file_path: str
    compiler_path: str
    include_paths: list[str]
    missing_header: str
    message: str


def normalize_query_driver_input(value: object) -> str:
    if isinstance(value, list):
        parts = [str(item or "").strip() for item in value]
        return _normalize_query_driver_from_text(",".join(parts))
    return _normalize_query_driver_from_text(str(value or ""))


def _normalize_query_driver_from_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.lower() in {"off", "none", "disabled"}:
        return raw.lower()

    parts: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,]+", raw):
        piece = str(token or "").strip()
        if not piece:
            continue
        lower = piece.lower()
        if lower.startswith("--query-driver="):
            piece = piece.split("=", 1)[1].strip()
            if not piece:
                continue
        if piece.startswith("="):
            piece = piece[1:].strip()
            if not piece:
                continue
        dedupe = piece.lower()
        if dedupe in seen:
            continue
        seen.add(dedupe)
        parts.append(piece)
    return ",".join(parts)


def query_driver_candidates(value: object) -> list[str]:
    normalized = normalize_query_driver_input(value)
    if not normalized or normalized in {"off", "none", "disabled"}:
        return []
    out: list[str] = []
    for item in normalized.split(","):
        candidate = str(item or "").strip()
        if not candidate:
            continue
        out.append(candidate)
    return out


def auto_query_driver_candidates() -> list[str]:
    base = [
        "/usr/bin/c++",
        "/usr/bin/g++",
        "/usr/bin/clang++",
        "/usr/bin/clang",
        "/usr/bin/x86_64-linux-gnu-g++",
        "/usr/bin/x86_64-linux-gnu-c++",
    ]
    out: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        cpath = str(path or "").strip()
        if not cpath:
            return
        if not os.path.isfile(cpath) or not os.access(cpath, os.X_OK):
            return
        key = cpath.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(cpath)

    for path in base:
        add(path)

    for pattern in ("/usr/bin/g++-*", "/usr/bin/c++-*", "/usr/bin/x86_64-linux-gnu-g++-*"):
        for hit in sorted(Path("/").glob(pattern.lstrip("/"))):
            add(str(hit))
    return out


def missing_std_header_from_diagnostic(diag: dict | None) -> str:
    if not isinstance(diag, dict):
        return ""
    source = str(diag.get("source") or "").strip().lower()
    if source != "clangd":
        return ""
    code_obj = diag.get("code")
    if isinstance(code_obj, dict):
        code = str(code_obj.get("value") or "").strip().lower()
    else:
        code = str(code_obj or "").strip().lower()
    message = str(diag.get("message") or "").strip()
    if "file not found" not in message.lower():
        return ""
    if code and code != "pp_file_not_found":
        return ""
    return _extract_std_header_name(message)


def _extract_std_header_name(message: str) -> str:
    text = str(message or "")
    match = re.search(r"'([^']+)' file not found", text)
    if not match:
        return ""
    header = str(match.group(1) or "").strip()
    if not header:
        return ""
    low = header.lower()
    if low in _STD_HEADER_NAMES or low in _STD_C_HEADER_NAMES:
        return header
    if low.startswith("bits/") or low.startswith("ext/") or low.startswith("__"):
        return header
    if "/" in header or "\\" in header:
        return ""
    if low.startswith("c") and re.fullmatch(r"c[a-z0-9_]+", low):
        return header
    if "." not in header:
        return header
    return ""


def repair_clangd_includes(
    *,
    project_root: str,
    clangd_path: str,
    query_driver: str,
    compile_commands_mode: str,
    compile_commands_path: str,
    target_file_path: str = "",
) -> ClangdRepairResult:
    root = os.path.abspath(str(project_root or ""))
    if not os.path.isdir(root):
        return ClangdRepairResult(
            ok=False,
            query_driver="",
            query_driver_changed=False,
            fixed_by_query_driver=False,
            wrote_clangd_file=False,
            clangd_file_path=os.path.join(root, ".clangd"),
            probe_file_path="",
            compiler_path="",
            include_paths=[],
            missing_header="",
            message="Project path is not available.",
        )

    clangd_cmd = str(clangd_path or "clangd").strip() or "clangd"
    existing_query = normalize_query_driver_input(query_driver)
    auto_query = ",".join(auto_query_driver_candidates())
    preferred_query = existing_query
    if preferred_query in {"off", "none", "disabled"}:
        preferred_query = ""
    if not preferred_query:
        preferred_query = auto_query
    query_changed = preferred_query != existing_query and bool(preferred_query)

    probe_file = _pick_probe_file(root, preferred_path=target_file_path)
    if not probe_file:
        return ClangdRepairResult(
            ok=False,
            query_driver=preferred_query,
            query_driver_changed=query_changed,
            fixed_by_query_driver=False,
            wrote_clangd_file=False,
            clangd_file_path=os.path.join(root, ".clangd"),
            probe_file_path="",
            compiler_path="",
            include_paths=[],
            missing_header="",
            message="No C/C++ file found to verify clangd include resolution.",
        )

    compile_dir = _discover_compile_commands_dir(
        project_root=root,
        mode=compile_commands_mode,
        manual_path=compile_commands_path,
    )
    first_probe = _probe_std_header_missing(
        clangd_path=clangd_cmd,
        project_root=root,
        file_path=probe_file,
        compile_commands_dir=compile_dir,
        query_driver=preferred_query,
    )
    if not first_probe["missing_header"]:
        return ClangdRepairResult(
            ok=True,
            query_driver=preferred_query,
            query_driver_changed=query_changed,
            fixed_by_query_driver=True,
            wrote_clangd_file=False,
            clangd_file_path=os.path.join(root, ".clangd"),
            probe_file_path=probe_file,
            compiler_path="",
            include_paths=[],
            missing_header="",
            message="Query-driver configuration resolves standard headers.",
        )

    auto_probe_missing = first_probe["missing_header"]
    if auto_query and auto_query != preferred_query:
        auto_probe = _probe_std_header_missing(
            clangd_path=clangd_cmd,
            project_root=root,
            file_path=probe_file,
            compile_commands_dir=compile_dir,
            query_driver=auto_query,
        )
        auto_probe_missing = auto_probe["missing_header"]
        if not auto_probe_missing:
            return ClangdRepairResult(
                ok=True,
                query_driver=auto_query,
                query_driver_changed=auto_query != existing_query,
                fixed_by_query_driver=True,
                wrote_clangd_file=False,
                clangd_file_path=os.path.join(root, ".clangd"),
                probe_file_path=probe_file,
                compiler_path="",
                include_paths=[],
                missing_header="",
                message="Auto-detected query-driver resolves standard headers.",
            )

    missing_after_query_probe = str(auto_probe_missing or first_probe["missing_header"] or "").strip()

    compiler_path = _select_compiler_for_include_probe(
        preferred_query_driver=auto_query or preferred_query,
        compile_commands_dir=compile_dir,
    )
    if not compiler_path:
        return ClangdRepairResult(
            ok=False,
            query_driver=preferred_query,
            query_driver_changed=query_changed,
            fixed_by_query_driver=False,
            wrote_clangd_file=False,
            clangd_file_path=os.path.join(root, ".clangd"),
            probe_file_path=probe_file,
            compiler_path="",
            include_paths=[],
            missing_header=missing_after_query_probe,
            message="Could not detect a compiler to extract standard include paths.",
        )

    include_paths = _extract_include_search_paths(compiler_path)
    if not include_paths:
        return ClangdRepairResult(
            ok=False,
            query_driver=preferred_query,
            query_driver_changed=query_changed,
            fixed_by_query_driver=False,
            wrote_clangd_file=False,
            clangd_file_path=os.path.join(root, ".clangd"),
            probe_file_path=probe_file,
            compiler_path=compiler_path,
            include_paths=[],
            missing_header=missing_after_query_probe,
            message=f"Could not extract include search paths from {compiler_path}.",
        )

    clangd_file = _write_or_update_managed_clangd(root, include_paths)
    second_probe = _probe_std_header_missing(
        clangd_path=clangd_cmd,
        project_root=root,
        file_path=probe_file,
        compile_commands_dir=compile_dir,
        query_driver=preferred_query,
    )
    fixed = not bool(second_probe["missing_header"])
    if fixed:
        message = "Applied .clangd include repair and verified standard headers."
    else:
        missing = str(second_probe["missing_header"] or missing_after_query_probe or "").strip()
        if missing:
            message = f"Applied .clangd include repair, but clangd still reports missing '{missing}'."
        else:
            message = "Applied .clangd include repair, but clangd still reports missing standard headers."
    return ClangdRepairResult(
        ok=fixed,
        query_driver=preferred_query,
        query_driver_changed=query_changed,
        fixed_by_query_driver=False,
        wrote_clangd_file=True,
        clangd_file_path=clangd_file,
        probe_file_path=probe_file,
        compiler_path=compiler_path,
        include_paths=include_paths,
        missing_header=str(second_probe["missing_header"] or ""),
        message=message,
    )


def _pick_probe_file(project_root: str, *, preferred_path: str) -> str:
    preferred = str(preferred_path or "").strip()
    if preferred:
        cpath = preferred if os.path.isabs(preferred) else os.path.join(project_root, preferred)
        cpath = os.path.abspath(cpath)
        if os.path.isfile(cpath) and os.path.splitext(cpath)[1].lower() in _CPP_FILE_SUFFIXES:
            return cpath

    priority: dict[str, int] = {
        ".hpp": 0,
        ".hxx": 1,
        ".hh": 2,
        ".h": 3,
        ".cpp": 4,
        ".cc": 5,
        ".cxx": 6,
        ".c": 7,
    }
    best_path = ""
    best_rank = 999
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_SCAN_DIRS and not d.startswith(".")]
        for name in files:
            suffix = os.path.splitext(name)[1].lower()
            rank = priority.get(suffix)
            if rank is None:
                continue
            if rank < best_rank:
                best_rank = rank
                best_path = os.path.join(root, name)
                if best_rank == 0:
                    return best_path
    if best_path:
        return best_path
    return ""


def _probe_std_header_missing(
    *,
    clangd_path: str,
    project_root: str,
    file_path: str,
    compile_commands_dir: str,
    query_driver: str,
) -> dict[str, str]:
    args = [str(clangd_path or "clangd"), f"--check={file_path}", "--log=error"]
    compile_dir = str(compile_commands_dir or "").strip()
    if compile_dir:
        args.append(f"--compile-commands-dir={compile_dir}")
    query = normalize_query_driver_input(query_driver)
    if query and query not in {"off", "none", "disabled"}:
        args.append(f"--query-driver={query}")

    try:
        proc = subprocess.run(
            args,
            cwd=project_root,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return {"missing_header": "", "raw_output": str(exc)}

    output = "\n".join(
        part for part in [str(proc.stdout or "").strip(), str(proc.stderr or "").strip()] if part
    )
    header = ""
    for line in output.splitlines():
        if "file not found" not in line.lower():
            continue
        header = _extract_std_header_name(line)
        if header:
            break
    return {"missing_header": header, "raw_output": output}


def _select_compiler_for_include_probe(
    *,
    preferred_query_driver: str,
    compile_commands_dir: str,
) -> str:
    for candidate in query_driver_candidates(preferred_query_driver):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    command = _compiler_from_compile_commands(compile_commands_dir)
    if command:
        return command

    candidates = auto_query_driver_candidates()
    return candidates[0] if candidates else ""


def _compiler_from_compile_commands(compile_commands_dir: str) -> str:
    cdir = str(compile_commands_dir or "").strip()
    if not cdir:
        return ""
    path = os.path.join(cdir, "compile_commands.json")
    if not os.path.isfile(path):
        return ""
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(entries, list):
        return ""
    for item in entries:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if not command:
            continue
        try:
            argv = shlex.split(command)
        except Exception:
            argv = command.split()
        if not argv:
            continue
        exe = shutil.which(argv[0]) if not os.path.isabs(argv[0]) else argv[0]
        if exe and os.path.isfile(exe) and os.access(exe, os.X_OK):
            return exe
    return ""


def _extract_include_search_paths(compiler_path: str) -> list[str]:
    compiler = str(compiler_path or "").strip()
    if not compiler:
        return []
    try:
        proc = subprocess.run(
            [compiler, "-E", "-x", "c++", "-", "-v"],
            input="",
            text=True,
            capture_output=True,
            timeout=12,
            check=False,
        )
    except Exception:
        return []

    output = "\n".join([str(proc.stderr or ""), str(proc.stdout or "")])
    lines = output.splitlines()
    in_block = False
    include_paths: list[str] = []
    seen: set[str] = set()

    for raw in lines:
        line = str(raw or "").rstrip()
        if "#include <...> search starts here:" in line:
            in_block = True
            continue
        if not in_block:
            continue
        if "End of search list." in line:
            break
        candidate = line.strip()
        if not candidate:
            continue
        if "(framework directory)" in candidate:
            candidate = candidate.replace("(framework directory)", "").strip()
        if not candidate.startswith("/"):
            continue
        cpath = os.path.abspath(candidate)
        if not os.path.isdir(cpath):
            continue
        key = cpath.lower()
        if key in seen:
            continue
        seen.add(key)
        include_paths.append(cpath)
    return include_paths


def _write_or_update_managed_clangd(project_root: str, include_paths: list[str]) -> str:
    file_path = os.path.join(project_root, ".clangd")
    fragment_lines = [_MANAGED_BEGIN, "CompileFlags:", "  Add:"]
    for path in include_paths:
        quoted = _yaml_single_quote(path)
        fragment_lines.append("    - -isystem")
        fragment_lines.append(f"    - '{quoted}'")
    fragment_lines.append(_MANAGED_END)
    fragment = "\n".join(fragment_lines).strip() + "\n"

    existing = ""
    if os.path.isfile(file_path):
        try:
            existing = Path(file_path).read_text(encoding="utf-8")
        except Exception:
            existing = ""

    if _MANAGED_BEGIN in existing and _MANAGED_END in existing:
        pattern = re.compile(
            rf"{re.escape(_MANAGED_BEGIN)}[\s\S]*?{re.escape(_MANAGED_END)}\n?",
            re.MULTILINE,
        )
        updated = re.sub(pattern, fragment, existing)
    elif existing.strip():
        tail = existing if existing.endswith("\n") else existing + "\n"
        updated = f"{tail}\n---\n{fragment}"
    else:
        updated = fragment

    Path(file_path).write_text(updated, encoding="utf-8")
    return file_path


def _yaml_single_quote(text: str) -> str:
    return str(text or "").replace("'", "''")


def _discover_compile_commands_dir(*, project_root: str, mode: str, manual_path: str) -> str:
    mode_norm = str(mode or "auto").strip().lower()
    if mode_norm == "manual":
        return _resolve_manual_compile_commands(project_root, manual_path)
    return _discover_compile_commands_auto(project_root)


def _resolve_manual_compile_commands(project_root: str, manual_path: str) -> str:
    path = str(manual_path or "").strip()
    if not path:
        return ""
    if not os.path.isabs(path):
        path = os.path.join(project_root, path)
    path = os.path.abspath(path)
    if os.path.isdir(path):
        return path if os.path.isfile(os.path.join(path, "compile_commands.json")) else ""
    if os.path.isfile(path) and os.path.basename(path) == "compile_commands.json":
        return os.path.dirname(path)
    return ""


def _discover_compile_commands_auto(project_root: str) -> str:
    root = os.path.abspath(project_root)
    for path in (
        root,
        os.path.join(root, "build"),
        os.path.join(root, "build-debug"),
        os.path.join(root, "build-release"),
        os.path.join(root, "out", "build"),
    ):
        if os.path.isfile(os.path.join(path, "compile_commands.json")):
            return path

    queue: list[tuple[str, int]] = [(root, 0)]
    seen: set[str] = set()
    hits: list[str] = []
    while queue:
        directory, depth = queue.pop(0)
        if directory in seen:
            continue
        seen.add(directory)
        if os.path.isfile(os.path.join(directory, "compile_commands.json")):
            hits.append(directory)
        if depth >= 3:
            continue
        try:
            entries = list(os.scandir(directory))
        except Exception:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name in _SKIP_SCAN_DIRS or entry.name.startswith("."):
                continue
            queue.append((entry.path, depth + 1))
    if not hits:
        return ""
    hits.sort(key=lambda path: (len(path), path.lower()))
    return hits[0]
