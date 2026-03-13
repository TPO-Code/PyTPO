"""Small LSP dataclasses/helpers for positions and edits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    line: int
    character: int


@dataclass(frozen=True)
class Range:
    start: Position
    end: Position


@dataclass(frozen=True)
class TextEdit:
    range: Range
    new_text: str


def utf16_code_units(text: str) -> int:
    if not text:
        return 0
    return len(text.encode("utf-16-le")) // 2


def utf16_units_for_prefix(text: str, codepoint_index: int) -> int:
    if not text:
        return 0
    idx = max(0, min(len(text), int(codepoint_index)))
    return utf16_code_units(text[:idx])


def codepoint_index_from_utf16_units(text: str, utf16_units: int) -> int:
    if not text:
        return 0
    remaining = max(0, int(utf16_units))
    idx = 0
    while idx < len(text):
        ch = text[idx]
        units = 1 if ord(ch) <= 0xFFFF else 2
        if remaining < units:
            break
        remaining -= units
        idx += 1
    return idx

