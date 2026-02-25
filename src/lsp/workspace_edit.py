"""Utilities for extracting and applying LSP WorkspaceEdit payloads."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl

from .types import codepoint_index_from_utf16_units


def uri_to_path(uri: str) -> str:
    text = str(uri or "").strip()
    if not text:
        return ""
    url = QUrl(text)
    if url.isLocalFile():
        return str(url.toLocalFile())
    return ""


def collect_workspace_text_edits(workspace_edit_obj: object) -> dict[str, list[dict[str, Any]]]:
    edit = workspace_edit_obj if isinstance(workspace_edit_obj, dict) else {}
    out: dict[str, list[dict[str, Any]]] = {}

    changes = edit.get("changes")
    if isinstance(changes, dict):
        for uri, edits_obj in changes.items():
            path = uri_to_path(str(uri or ""))
            if not path:
                continue
            normalized = _normalize_text_edits(edits_obj)
            if normalized:
                out.setdefault(path, []).extend(normalized)

    document_changes = edit.get("documentChanges")
    if isinstance(document_changes, list):
        for item in document_changes:
            if not isinstance(item, dict):
                continue
            text_document = item.get("textDocument")
            edits_obj = item.get("edits")
            if not isinstance(text_document, dict) or not isinstance(edits_obj, list):
                # Ignore file operations for now (CreateFile/RenameFile/DeleteFile).
                continue
            path = uri_to_path(str(text_document.get("uri") or ""))
            if not path:
                continue
            normalized = _normalize_text_edits(edits_obj)
            if normalized:
                out.setdefault(path, []).extend(normalized)

    return out


def apply_workspace_edits_to_text(
    source_text: str,
    edits: list[dict[str, Any]],
) -> str:
    text = str(source_text or "")
    normalized = _normalize_text_edits(edits)
    if not normalized:
        return text

    offsets: list[tuple[int, int, str]] = []
    for item in normalized:
        start_line = int(item["start_line"])
        start_char = int(item["start_char"])
        end_line = int(item["end_line"])
        end_char = int(item["end_char"])
        new_text = str(item["new_text"])

        start = _offset_from_utf16_position(text, line0=start_line, utf16_char=start_char)
        end = _offset_from_utf16_position(text, line0=end_line, utf16_char=end_char)
        if end < start:
            start, end = end, start
        offsets.append((start, end, new_text))

    updated = text
    for start, end, replacement in sorted(offsets, key=lambda row: (row[0], row[1]), reverse=True):
        start = max(0, min(len(updated), int(start)))
        end = max(start, min(len(updated), int(end)))
        updated = f"{updated[:start]}{replacement}{updated[end:]}"
    return updated


def _normalize_text_edits(edits_obj: object) -> list[dict[str, Any]]:
    edits = edits_obj if isinstance(edits_obj, list) else []
    out: list[dict[str, Any]] = []
    for item in edits:
        if not isinstance(item, dict):
            continue
        range_obj = item.get("range")
        if not isinstance(range_obj, dict):
            continue
        start = range_obj.get("start")
        end = range_obj.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            continue
        try:
            start_line = max(0, int(start.get("line", 0)))
            start_char = max(0, int(start.get("character", 0)))
            end_line = max(0, int(end.get("line", 0)))
            end_char = max(0, int(end.get("character", 0)))
        except Exception:
            continue
        out.append(
            {
                "start_line": start_line,
                "start_char": start_char,
                "end_line": end_line,
                "end_char": end_char,
                "new_text": str(item.get("newText") or ""),
            }
        )
    return out


def _offset_from_utf16_position(text: str, *, line0: int, utf16_char: int) -> int:
    if not text:
        return 0

    lines = text.splitlines(keepends=True)
    if not lines:
        return 0

    line_index = max(0, int(line0))
    if line_index >= len(lines):
        return len(text)

    start_offset = sum(len(part) for part in lines[:line_index])
    line_full = str(lines[line_index] or "")
    line_text = line_full.rstrip("\r\n")
    char_index = codepoint_index_from_utf16_units(line_text, utf16_char)
    return start_offset + min(len(line_text), char_index)
