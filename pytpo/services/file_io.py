"""Safe file read/write helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def read_text(path: str, *, encoding: str = "utf-8", errors: str = "strict") -> str:
    return Path(path).read_text(encoding=encoding, errors=errors)


def write_text(path: str, text: str, *, encoding: str = "utf-8") -> None:
    Path(path).write_text(text, encoding=encoding)


def atomic_write_text(
    path: str,
    text: str,
    *,
    encoding: str = "utf-8",
    create_backup: bool = False,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if create_backup and target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        backup.write_bytes(target.read_bytes())

    fd, tmp_path = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
        os.replace(tmp_path, target)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
