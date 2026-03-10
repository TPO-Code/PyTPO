from __future__ import annotations

from pathlib import Path

DEBUGGER_BREAKPOINT_SUFFIXES: frozenset[str] = frozenset({".py", ".rs"})


def debugger_breakpoints_supported_for_path(file_path: str | None) -> bool:
    path = str(file_path or "").strip()
    if not path:
        return False
    return Path(path).suffix.lower() in DEBUGGER_BREAKPOINT_SUFFIXES


def debugger_breakpoints_supported_for_editor(editor) -> bool:
    if editor is None:
        return False
    resolver = getattr(editor, "file_path", "")
    return debugger_breakpoints_supported_for_path(str(resolver or ""))
