from __future__ import annotations

import os
from pathlib import Path


def _absolute_path(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().absolute()
    except Exception:
        return Path(path).expanduser().absolute()


def _is_path_like(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return (
        text.startswith(".")
        or text.startswith("/")
        or text.startswith("~")
        or os.sep in text
        or (os.altsep and os.altsep in text)
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except Exception:
        return False


def _looks_like_python_executable(path: Path) -> bool:
    name = path.name.lower()
    return name == "python" or name == "python3" or name.startswith("python")


def normalize_interpreter_for_project(value: str, project_root: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not _is_path_like(text):
        return text

    root = _absolute_path(project_root)
    raw = Path(text).expanduser()
    absolute = raw if raw.is_absolute() else (root / raw)
    absolute = _absolute_path(absolute)
    if _is_within(absolute, root):
        return absolute.relative_to(root).as_posix()
    return str(absolute)


def interpreter_browse_directory_hint(current_value: str, project_root: str) -> str:
    root = _absolute_path(project_root)
    text = str(current_value or "").strip()
    if not text or not _is_path_like(text):
        return str(root)

    raw = Path(text).expanduser()
    absolute = raw if raw.is_absolute() else (root / raw)
    absolute = _absolute_path(absolute)
    if absolute.is_file():
        return str(absolute.parent)
    if absolute.is_dir():
        return str(absolute)
    parent = absolute.parent
    if parent.exists() and parent.is_dir():
        return str(parent)
    return str(root)


def discover_project_interpreters(project_root: str, *, max_results: int = 40) -> list[str]:
    root = _absolute_path(project_root)
    if not root.exists() or not root.is_dir():
        return []

    discovered: list[str] = []
    seen: set[str] = set()

    def add_candidate(path: Path) -> None:
        if len(discovered) >= max_results:
            return
        absolute = _absolute_path(path)
        if not absolute.exists() or not absolute.is_file():
            return
        if not _is_within(absolute, root):
            return
        if not _looks_like_python_executable(absolute):
            return
        if os.name != "nt" and not os.access(absolute, os.X_OK):
            return
        rel = absolute.relative_to(root).as_posix()
        key = rel.lower()
        if key in seen:
            return
        seen.add(key)
        discovered.append(rel)

    for rel in (
        ".venv/bin/python",
        ".venv/bin/python3",
        ".venv/Scripts/python.exe",
        "venv/bin/python",
        "venv/bin/python3",
        "venv/Scripts/python.exe",
        "env/bin/python",
        "env/bin/python3",
        "env/Scripts/python.exe",
        ".env/bin/python",
        ".env/bin/python3",
        ".env/Scripts/python.exe",
    ):
        add_candidate(root / rel)

    for cfg in root.rglob("pyvenv.cfg"):
        try:
            rel_cfg = cfg.relative_to(root)
        except Exception:
            continue
        if len(rel_cfg.parts) > 6:
            continue
        venv_root = cfg.parent
        add_candidate(venv_root / "bin" / "python")
        add_candidate(venv_root / "bin" / "python3")
        add_candidate(venv_root / "Scripts" / "python.exe")
        add_candidate(venv_root / "Scripts" / "python")
        if len(discovered) >= max_results:
            break

    if len(discovered) < max_results:
        for pattern in ("**/bin/python", "**/bin/python3", "**/Scripts/python.exe"):
            for candidate in root.glob(pattern):
                try:
                    rel = candidate.relative_to(root)
                except Exception:
                    continue
                if len(rel.parts) > 7:
                    continue
                add_candidate(candidate)
                if len(discovered) >= max_results:
                    break
            if len(discovered) >= max_results:
                break

    def rank(item: str) -> tuple[int, str]:
        lower = item.lower()
        if lower.startswith(".venv/"):
            return (0, lower)
        if lower.startswith("venv/"):
            return (1, lower)
        if lower.startswith(".env/"):
            return (2, lower)
        if lower.startswith("env/"):
            return (3, lower)
        if "/.venv/" in lower or "/venv/" in lower:
            return (4, lower)
        return (10, lower)

    discovered.sort(key=rank)
    return discovered
