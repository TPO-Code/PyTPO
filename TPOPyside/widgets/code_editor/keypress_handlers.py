"""Language id resolution and language-specific key/mouse handler dispatch."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

if TYPE_CHECKING:
    from .editor import CodeEditor

# Language-id registry used by language-specific action dispatch.
# Add entries here to enable language-level behavior without changing event code.
EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".pyi": "python",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".xhtml": "html",
    ".svg": "xml",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascriptreact",
    ".php": "php",
    ".phtml": "php",
    ".php3": "php",
    ".php4": "php",
    ".php5": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cxx": "cpp",
    ".hxx": "cpp",
    ".cc": "cpp",
    ".hh": "cpp",
    ".json": "json",
    ".jsonc": "jsonc",
    ".geojson": "json",
    ".rs": "rust",
    ".css": "css",
    ".scss": "scss",
    ".qss": "css",
    ".less": "less",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ksh": "shell",
    ".md": "markdown",
    ".todo": "todo",
    ".task": "todo",
    ".tasks": "todo",
    ".lst": "todo",
}

NAME_TO_LANG: dict[str, str] = {
    ".zshrc": "shell",
    ".bashrc": "shell",
    "makefile": "cpp",
}


def get_language_id(file_path: str | Path | None, fallback: str = "plaintext") -> str:
    text = str(file_path or "").strip()
    if not text:
        return str(fallback or "plaintext").strip().lower() or "plaintext"
    name = Path(text).name.lower()
    if name in NAME_TO_LANG:
        return NAME_TO_LANG[name]
    ext = Path(text).suffix.lower()
    if ext in EXT_TO_LANG:
        return EXT_TO_LANG[ext]
    return str(fallback or "plaintext").strip().lower() or "plaintext"


# Supports:
#   [ ] task
#   [x] task
#   [✔] task
#   - [ ] task
#   * [x] task
TODO_BOX_RE = re.compile(
    r'^(?P<prefix>[ \t]*(?:[-*+]\s+)?)\[(?P<state>[ xX✔])\](?P<suffix>.*)$'
)


def is_todo_checkbox_at_pos(editor: QPlainTextEdit, pos: QPoint, hit_slop_chars: int = 0) -> bool:
    cursor = editor.cursorForPosition(pos)
    block = cursor.block()
    if not block.isValid():
        return False

    line_text = block.text()
    m = TODO_BOX_RE.match(line_text)
    if not m:
        return False

    box_start = m.start("state") - 1
    box_end = m.end("state") + 1
    col = cursor.positionInBlock()
    return (box_start - hit_slop_chars) <= col < (box_end + hit_slop_chars)


def _toggle_checkbox_in_block(editor: QPlainTextEdit, block, match: re.Match) -> bool:
    state = match.group("state")
    new_state = "✔" if state == " " else " "

    block_pos = block.position()
    state_col = match.start("state")
    state_doc_pos = block_pos + state_col

    original = editor.textCursor()
    orig_anchor = int(original.anchor())
    orig_pos = int(original.position())
    vbar = editor.verticalScrollBar()
    hbar = editor.horizontalScrollBar()
    v_value = int(vbar.value()) if vbar is not None else 0
    h_value = int(hbar.value()) if hbar is not None else 0

    doc = editor.document()
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        c.setPosition(state_doc_pos)
        c.setPosition(state_doc_pos + 1, QTextCursor.KeepAnchor)
        c.insertText(new_state)
    finally:
        c.endEditBlock()

    restore = QTextCursor(doc)
    char_count = max(0, int(doc.characterCount()) - 1)
    restore_anchor = max(0, min(orig_anchor, char_count))
    restore_pos = max(0, min(orig_pos, char_count))
    restore.setPosition(restore_anchor)
    restore.setPosition(restore_pos, QTextCursor.KeepAnchor)
    editor.setTextCursor(restore)

    if vbar is not None:
        vbar.setValue(v_value)
    if hbar is not None:
        hbar.setValue(h_value)
    return True


def _toggle_todo_checkbox_at_pos(editor: QPlainTextEdit, pos: QPoint, hit_slop_chars: int = 0) -> bool:
    cursor = editor.cursorForPosition(pos)
    block = cursor.block()
    if not block.isValid():
        return False

    line_text = block.text()
    m = TODO_BOX_RE.match(line_text)
    if not m:
        return False

    box_start = m.start("state") - 1
    box_end = m.end("state") + 1
    clicked_col = cursor.positionInBlock()

    if not (box_start - hit_slop_chars <= clicked_col < box_end + hit_slop_chars):
        return False

    return _toggle_checkbox_in_block(editor, block, m)


def _toggle_todo_checkbox_at_cursor(editor: QPlainTextEdit, hit_slop_chars: int = 0) -> bool:
    cursor = editor.textCursor()
    if cursor.hasSelection():
        return False
    block = cursor.block()
    if not block.isValid():
        return False
    line_text = block.text()
    m = TODO_BOX_RE.match(line_text)
    if not m:
        return False
    box_start = m.start("state") - 1
    box_end = m.end("state") + 1
    col = cursor.positionInBlock()
    if not (box_start - hit_slop_chars <= col < box_end + hit_slop_chars):
        return False
    return _toggle_checkbox_in_block(editor, block, m)


def _todo_mouse_press_handler(editor: "CodeEditor", event: QMouseEvent) -> bool:
    if event.button() != Qt.LeftButton:
        return False
    pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
    return _toggle_todo_checkbox_at_pos(editor, pos)


def _todo_key_press_handler(editor: "CodeEditor", event: QKeyEvent) -> bool:
    key = event.key()
    mods = event.modifiers()
    if bool(mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
        return False
    if key not in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
        return False
    return _toggle_todo_checkbox_at_cursor(editor)


def _toggle_python_comment_selection(editor: "CodeEditor") -> bool:
    return _toggle_line_comment_selection(editor, marker="#")


def _toggle_line_comment_selection(editor: "CodeEditor", *, marker: str) -> bool:
    cursor = editor.textCursor()
    old_anchor = int(cursor.anchor())
    old_pos = int(cursor.position())
    had_selection = cursor.hasSelection()
    doc = editor.document()
    token = str(marker or "").strip()
    if not token:
        return False

    if had_selection:
        first_bn, last_bn = editor._selected_block_range(cursor)
    else:
        bn = cursor.block().blockNumber()
        first_bn = bn
        last_bn = bn

    block_numbers: list[int] = []
    lines: list[str] = []
    for bn in range(first_bn, last_bn + 1):
        block = doc.findBlockByNumber(bn)
        if not block.isValid():
            continue
        block_numbers.append(bn)
        lines.append(block.text())

    if not block_numbers:
        return False

    nonempty = [line for line in lines if line.strip()]
    all_commented = bool(nonempty) and all(line.lstrip().startswith(token) for line in nonempty)
    uncomment = all_commented

    line_starts: list[int] = []
    deltas: list[int] = []
    changed = False

    cursor.beginEditBlock()
    try:
        for bn in block_numbers:
            block = doc.findBlockByNumber(bn)
            if not block.isValid():
                continue
            line = block.text()
            indent_len = len(line) - len(line.lstrip(" \t"))
            start = int(block.position())

            c = QTextCursor(doc)
            c.setPosition(start + indent_len)

            if uncomment:
                stripped = line[indent_len:]
                if not stripped.startswith(token):
                    continue
                remove_len = len(token)
                if len(stripped) > remove_len and stripped[remove_len] == " ":
                    remove_len += 1
                c.setPosition(start + indent_len + remove_len, QTextCursor.KeepAnchor)
                c.removeSelectedText()
                line_starts.append(start)
                deltas.append(-remove_len)
                changed = True
                continue

            c.insertText(token + " ")
            line_starts.append(start)
            deltas.append(len(token) + 1)
            changed = True
    finally:
        cursor.endEditBlock()

    if not changed:
        return False

    new_anchor = editor._remap_pos_by_line_deltas(old_anchor, line_starts, deltas)
    new_pos = editor._remap_pos_by_line_deltas(old_pos, line_starts, deltas)
    if had_selection:
        editor._set_selection_preserve_tip(new_anchor, new_pos)
    else:
        restore = editor.textCursor()
        restore.setPosition(new_pos)
        editor.setTextCursor(restore)
        editor.ensureCursorVisible()
    return True


def toggle_python_comment_selection(editor: "CodeEditor") -> bool:
    return _toggle_python_comment_selection(editor)


def _toggle_cpp_comment_selection(editor: "CodeEditor") -> bool:
    return _toggle_line_comment_selection(editor, marker="//")


def toggle_cpp_comment_selection(editor: "CodeEditor") -> bool:
    return _toggle_cpp_comment_selection(editor)


def _python_key_press_handler(editor: "CodeEditor", event: QKeyEvent) -> bool:
    if bool(getattr(editor, "_python_comment_shortcut_managed", False)):
        return False
    mods = event.modifiers()
    if not bool(mods & Qt.ControlModifier):
        return False
    if bool(mods & (Qt.AltModifier | Qt.MetaModifier)):
        return False

    key = event.key()
    text = event.text()
    question_key = getattr(Qt, "Key_Question", Qt.Key_Slash)
    is_slash_shortcut = key in {Qt.Key_Slash, question_key} or text in {"/", "?"}
    if not is_slash_shortcut:
        return False

    # Ctrl+/ (and layouts where Ctrl+Shift+/ emits '?') toggles comments.
    _toggle_python_comment_selection(editor)
    return True


def _cpp_key_press_handler(editor: "CodeEditor", event: QKeyEvent) -> bool:
    if bool(getattr(editor, "_cpp_comment_shortcut_managed", False)):
        return False

    mods = event.modifiers()
    if bool(mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
        return False
    if not bool(mods & Qt.ShiftModifier):
        return False

    key = event.key()
    text = event.text()
    question_key = getattr(Qt, "Key_Question", Qt.Key_Slash)
    is_slash_shortcut = key in {Qt.Key_Slash, question_key} or text in {"/", "?"}
    if not is_slash_shortcut:
        return False

    # Shift+/ toggles line comments for C/C++ files.
    _toggle_cpp_comment_selection(editor)
    return True


# Dispatch contract:
# - Return True when the language handler fully handled the event.
# - Return False to continue generic editor behavior.
KEY_PRESS_HANDLERS: dict[str, Callable[["CodeEditor", QKeyEvent], bool]] = {
    "todo": _todo_key_press_handler,
    "python": _python_key_press_handler,
    "c": _cpp_key_press_handler,
    "cpp": _cpp_key_press_handler,
}
MOUSE_PRESS_HANDLERS: dict[str, Callable[["CodeEditor", QMouseEvent], bool]] = {
    "todo": _todo_mouse_press_handler,
}


def dispatch_key_press(editor: "CodeEditor", event: QKeyEvent) -> bool:
    handler = KEY_PRESS_HANDLERS.get(editor.language_id())
    if not callable(handler):
        return False
    # True means handled: stop generic key processing.
    return bool(handler(editor, event))


def dispatch_mouse_press(editor: "CodeEditor", event: QMouseEvent) -> bool:
    handler = MOUSE_PRESS_HANDLERS.get(editor.language_id())
    if not callable(handler):
        return False
    # True means handled: stop generic mouse press processing.
    return bool(handler(editor, event))


__all__ = [
    "EXT_TO_LANG",
    "NAME_TO_LANG",
    "KEY_PRESS_HANDLERS",
    "MOUSE_PRESS_HANDLERS",
    "get_language_id",
    "dispatch_key_press",
    "dispatch_mouse_press",
    "is_todo_checkbox_at_pos",
    "toggle_cpp_comment_selection",
    "toggle_python_comment_selection",
]
