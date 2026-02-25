"""Language-id resolution helpers for editor files.

Pure utility functions that map filenames/extensions to a language id that can
be routed to a completion/definition/signature provider.
"""

from __future__ import annotations

from pathlib import Path

# Keep ids stable and simple; they can be mapped to provider backends later.
_EXTENSION_LANGUAGE_IDS: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".pyi": "python",
    ".pyx": "python",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cxx": "cpp",
    ".hxx": "cpp",
    ".cc": "cpp",
    ".hh": "cpp",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascriptreact",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".json": "json",
    ".jsonc": "jsonc",
    ".geojson": "json",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".svg": "xml",
    ".xml": "xml",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".qss": "css",
    ".php": "php",
    ".phtml": "php",
    ".php3": "php",
    ".php4": "php",
    ".php5": "php",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ksh": "shell",
    ".md": "markdown",
    ".todo": "todo",
    ".task": "todo",
}

_FILENAME_LANGUAGE_IDS: dict[str, str] = {
    "makefile": "make",
    ".bashrc": "shell",
    ".zshrc": "shell",
}


def language_id_for_path(file_path: str | None, *, default: str = "plaintext") -> str:
    """Return a normalized language id for a file path."""
    path_text = str(file_path or "").strip()
    if not path_text:
        return str(default or "plaintext").strip().lower() or "plaintext"

    name = Path(path_text).name.lower()
    if name in _FILENAME_LANGUAGE_IDS:
        return _FILENAME_LANGUAGE_IDS[name]

    suffix = Path(path_text).suffix.lower()
    if suffix in _EXTENSION_LANGUAGE_IDS:
        return _EXTENSION_LANGUAGE_IDS[suffix]

    return str(default or "plaintext").strip().lower() or "plaintext"
