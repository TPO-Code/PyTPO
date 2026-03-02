"""File classification helpers for IDE open-file routing."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from PySide6.QtGui import QImageReader


IMAGE_SUFFIXES: frozenset[str] = frozenset({
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".ico",
    ".webp",
})


class FileOpenKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    BINARY = "binary"


def _canonical_suffix(path: str) -> str:
    return str(Path(path).suffix or "").strip().lower()


def is_image_extension(path: str) -> bool:
    return _canonical_suffix(path) in IMAGE_SUFFIXES


def is_image_file(path: str) -> bool:
    target = str(path or "").strip()
    if not target:
        return False

    reader = QImageReader(target)
    try:
        if reader.canRead():
            return True
    except Exception:
        pass

    return is_image_extension(target)


def _sample_file_bytes(path: str, *, sample_size: int = 8192) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read(max(1, int(sample_size)))
    except Exception:
        return None


def is_probably_text_file(path: str) -> bool:
    sample = _sample_file_bytes(path)
    if sample is None:
        return False
    if not sample:
        return True
    if b"\x00" in sample:
        return False

    control_count = 0
    for value in sample:
        if value in (9, 10, 13):
            continue
        if value < 32 or value == 127:
            control_count += 1
    if control_count > max(1, len(sample) // 10):
        return False

    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def classify_file_for_open(path: str) -> FileOpenKind:
    if is_image_file(path):
        return FileOpenKind.IMAGE
    if is_probably_text_file(path):
        return FileOpenKind.TEXT
    return FileOpenKind.BINARY
