"""Code folding providers and update helpers for CodeEditor."""

from __future__ import annotations

import ast
import io
import re
import token
import tokenize
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .editor import CodeEditor

FoldRegion = tuple[int, int]
FoldProvider = Callable[[str], list[FoldRegion]]


def _normalize_fold_ranges(ranges: list[tuple[int, int]], line_count: int) -> list[tuple[int, int]]:
    merged: dict[int, int] = {}
    max_line = max(0, int(line_count))
    for start_raw, end_raw in ranges:
        try:
            start = int(start_raw)
            end = int(end_raw)
        except Exception:
            continue
        if start < 1:
            start = 1
        if end > max_line:
            end = max_line
        if end <= start:
            continue
        prev = merged.get(start)
        if prev is None or end > prev:
            merged[start] = end
    return sorted(merged.items(), key=lambda item: (item[0], item[1]))


def normalize_fold_ranges(ranges: list[tuple[int, int]], line_count: int) -> list[tuple[int, int]]:
    return _normalize_fold_ranges(ranges, line_count)


def _python_fold_ranges_from_ast(source_text: str) -> list[tuple[int, int]]:
    tree = ast.parse(source_text or "")
    fold_nodes = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.Match,
    )
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, fold_nodes):
            continue
        start = int(getattr(node, "lineno", 0) or 0)
        end = int(getattr(node, "end_lineno", start) or start)
        if end > start:
            ranges.append((start, end))
    return ranges


def _python_fold_ranges_from_tokens(source_text: str) -> list[tuple[int, int]]:
    text = str(source_text or "")
    if not text:
        return []

    open_for_close = {
        ")": "(",
        "]": "[",
        "}": "{",
    }
    bracket_stack: list[tuple[str, int]] = []
    ranges: list[tuple[int, int]] = []

    stream = io.StringIO(text).readline
    for tok in tokenize.generate_tokens(stream):
        tok_type = int(tok.type)
        tok_text = str(tok.string or "")
        start_line = int(tok.start[0] or 0)
        end_line = int(tok.end[0] or start_line)

        if tok_type == token.STRING:
            if end_line > start_line:
                ranges.append((start_line, end_line))
            continue

        if tok_type != token.OP or not tok_text:
            continue

        if tok_text in "([{" :
            bracket_stack.append((tok_text, start_line))
            continue

        expected_open = open_for_close.get(tok_text)
        if expected_open is None:
            continue

        matched_index = -1
        for i in range(len(bracket_stack) - 1, -1, -1):
            if bracket_stack[i][0] == expected_open:
                matched_index = i
                break
        if matched_index < 0:
            continue

        _, open_line = bracket_stack[matched_index]
        del bracket_stack[matched_index:]
        if end_line > open_line:
            ranges.append((open_line, end_line))

    return ranges


def _python_fold_ranges_from_indent(source_text: str) -> list[tuple[int, int]]:
    lines = str(source_text or "").splitlines()
    stack: list[tuple[int, int]] = []
    ranges: list[tuple[int, int]] = []
    for idx, raw in enumerate(lines, start=1):
        line = str(raw or "")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        while stack and indent <= stack[-1][0]:
            _, start_line = stack.pop()
            end_line = idx - 1
            if end_line > start_line:
                ranges.append((start_line, end_line))
        if stripped.endswith(":"):
            stack.append((indent, idx))

    last_line = len(lines)
    while stack:
        _, start_line = stack.pop()
        if last_line > start_line:
            ranges.append((start_line, last_line))
    return ranges


def python_fold_ranges(source_text: str) -> list[tuple[int, int]]:
    line_count = len(str(source_text or "").splitlines())
    ranges: list[tuple[int, int]] = []

    try:
        ranges.extend(_python_fold_ranges_from_ast(source_text))
    except Exception:
        ranges.extend(_python_fold_ranges_from_indent(source_text))

    try:
        ranges.extend(_python_fold_ranges_from_tokens(source_text))
    except Exception:
        pass

    if not ranges:
        ranges.extend(_python_fold_ranges_from_indent(source_text))

    return _normalize_fold_ranges(ranges, line_count)


def json_fold_ranges(source_text: str) -> list[tuple[int, int]]:
    text = str(source_text or "")
    line_count = len(text.splitlines())
    ranges: list[tuple[int, int]] = []

    stack: list[tuple[str, int]] = []
    line = 1
    in_string = False
    escaped = False

    for ch in text:
        if ch == "\n":
            line += 1

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            escaped = False
            continue

        if ch in "{[":
            stack.append((ch, line))
            continue

        if ch not in "}]":
            continue

        expected_open = "{" if ch == "}" else "["
        matched_index = -1
        for i in range(len(stack) - 1, -1, -1):
            if stack[i][0] == expected_open:
                matched_index = i
                break
        if matched_index < 0:
            continue

        _, start_line = stack[matched_index]
        del stack[matched_index:]
        if line > start_line:
            ranges.append((start_line, line))

    return _normalize_fold_ranges(ranges, line_count)


def rust_fold_ranges(source_text: str) -> list[tuple[int, int]]:
    lines = str(source_text or "").splitlines()
    ranges: list[tuple[int, int]] = []
    stack: list[int] = []
    in_block_comment = False

    for line_no, raw in enumerate(lines, start=1):
        cleaned, in_block_comment = _strip_rust_line_for_braces(raw, in_block_comment)
        for ch in cleaned:
            if ch == "{":
                stack.append(line_no)
                continue
            if ch != "}":
                continue
            if not stack:
                continue
            start = stack.pop()
            if line_no > start:
                ranges.append((start, line_no))
    return _normalize_fold_ranges(ranges, len(lines))


def cpp_fold_ranges(source_text: str) -> list[tuple[int, int]]:
    lines = str(source_text or "").splitlines()
    ranges: list[tuple[int, int]] = []
    stack: list[int] = []
    in_block_comment = False

    for line_no, raw in enumerate(lines, start=1):
        cleaned, in_block_comment = _strip_cpp_line_for_braces(raw, in_block_comment)
        for ch in cleaned:
            if ch == "{":
                stack.append(line_no)
                continue
            if ch != "}":
                continue
            if not stack:
                continue
            start = stack.pop()
            if line_no > start:
                ranges.append((start, line_no))
    return _normalize_fold_ranges(ranges, len(lines))


def _strip_cpp_line_for_braces(line: str, in_block_comment: bool) -> tuple[str, bool]:
    src = str(line or "")
    if not src:
        return "", in_block_comment

    out: list[str] = []
    i = 0
    n = len(src)
    block = bool(in_block_comment)
    in_string = False
    quote = ""
    escaped = False

    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if block:
            if ch == "*" and nxt == "/":
                block = False
                i += 2
                continue
            i += 1
            continue

        if in_string:
            if escaped:
                escaped = False
                i += 1
                continue
            if ch == "\\":
                escaped = True
                i += 1
                continue
            if ch == quote:
                in_string = False
                quote = ""
            i += 1
            continue

        if ch == "/" and nxt == "/":
            break
        if ch == "/" and nxt == "*":
            block = True
            i += 2
            continue
        if ch in {'"', "'"}:
            in_string = True
            quote = ch
            escaped = False
            i += 1
            continue

        out.append(ch)
        i += 1
    return "".join(out), block


def _strip_rust_line_for_braces(line: str, in_block_comment: bool) -> tuple[str, bool]:
    src = str(line or "")
    if not src:
        return "", in_block_comment

    out: list[str] = []
    i = 0
    n = len(src)
    block = bool(in_block_comment)
    in_string = False
    quote = ""
    escaped = False

    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if block:
            if ch == "*" and nxt == "/":
                block = False
                i += 2
                continue
            i += 1
            continue

        if in_string:
            if escaped:
                escaped = False
                i += 1
                continue
            if ch == "\\":
                escaped = True
                i += 1
                continue
            if ch == quote:
                in_string = False
                quote = ""
            i += 1
            continue

        if ch == "/" and nxt == "/":
            break
        if ch == "/" and nxt == "*":
            block = True
            i += 2
            continue
        if ch in {'"', "'"}:
            in_string = True
            quote = ch
            escaped = False
            i += 1
            continue

        out.append(ch)
        i += 1
    return "".join(out), block


def todo_fold_ranges(source_text: str) -> list[tuple[int, int]]:
    """
    Folding strategy:
      - Indent-based folds for task/comment/plain nested blocks
      - Header lines ending with ':' become fold starters too
      - Blank lines are skipped for structure decisions
    """
    lines = str(source_text or "").splitlines()
    ranges: list[tuple[int, int]] = []
    stack: list[tuple[int, int]] = []

    def is_blank(s: str) -> bool:
        return not s.strip()

    def is_header(s: str) -> bool:
        stripped = s.strip()
        if not stripped:
            return False
        if stripped.startswith("#"):
            return False
        if re.match(r'^(?:[-*+]\s+)?\[(?: |x|X)\]\s+', stripped):
            return False
        return stripped.endswith(":")

    for idx, raw in enumerate(lines, start=1):
        line = raw or ""
        if is_blank(line):
            continue

        indent = len(line) - len(line.lstrip(" \t"))

        while stack and indent <= stack[-1][0]:
            _, start_line = stack.pop()
            end_line = idx - 1
            if end_line > start_line:
                ranges.append((start_line, end_line))

        if is_header(line):
            stack.append((indent, idx))
            continue

        j = idx
        next_indent = None
        while j < len(lines):
            j += 1
            if j > len(lines):
                break
            candidate = lines[j - 1]
            if is_blank(candidate):
                continue
            next_indent = len(candidate) - len(candidate.lstrip(" \t"))
            break

        if next_indent is not None and next_indent > indent:
            stack.append((indent, idx))

    last_line = len(lines)
    while stack:
        _, start_line = stack.pop()
        if last_line > start_line:
            ranges.append((start_line, last_line))

    return _normalize_fold_ranges(ranges, len(lines))

def markdown_fold_ranges(source_text: str) -> list[tuple[int, int]]:
    lines = str(source_text or "").splitlines()
    line_count = len(lines)
    ranges: list[tuple[int, int]] = []

    heading_stack: list[tuple[int, int]] = []
    # (level, start_line)

    fence_start_line: int | None = None
    fence_delim: str | None = None

    def is_blank(s: str) -> bool:
        return not s.strip()

    def atx_heading_level(s: str) -> int | None:
        m = re.match(r"^\s*(#{1,6})\s+.*$", s)
        if not m:
            return None
        return len(m.group(1))

    def is_setext_underline(s: str) -> int | None:
        stripped = s.strip()
        if re.match(r"^=+\s*$", stripped):
            return 1
        if re.match(r"^-+\s*$", stripped):
            return 2
        return None

    def fence_info(s: str) -> tuple[str, str] | None:
        m = re.match(r"^\s*(```+|~~~+)\s*([\w#+.-]*)\s*$", s)
        if not m:
            return None
        return m.group(1), (m.group(2) or "").strip()

    def close_headings_for_level(new_level: int, end_line: int):
        nonlocal ranges, heading_stack
        while heading_stack and heading_stack[-1][0] >= new_level:
            _, start_line = heading_stack.pop()
            if end_line > start_line:
                ranges.append((start_line, end_line))

    idx = 1
    while idx <= line_count:
        line = lines[idx - 1]

        # ---------------------------------
        # fenced code block handling
        # ---------------------------------
        fi = fence_info(line)
        if fence_start_line is not None:
            if fi and fi[0].startswith(fence_delim[0]):
                if idx > fence_start_line:
                    ranges.append((fence_start_line, idx))
                fence_start_line = None
                fence_delim = None
            idx += 1
            continue

        if fi:
            fence_start_line = idx
            fence_delim = fi[0]
            idx += 1
            continue

        # ---------------------------------
        # setext heading handling
        # ---------------------------------
        if idx < line_count:
            next_line = lines[idx]
            setext_level = is_setext_underline(next_line)
            if setext_level is not None and not is_blank(line):
                close_headings_for_level(setext_level, idx - 1)
                heading_stack.append((setext_level, idx))
                idx += 2
                continue

        # ---------------------------------
        # atx heading handling
        # ---------------------------------
        level = atx_heading_level(line)
        if level is not None:
            close_headings_for_level(level, idx - 1)
            heading_stack.append((level, idx))
            idx += 1
            continue

        idx += 1

    # unclosed fence goes to EOF
    if fence_start_line is not None and line_count > fence_start_line:
        ranges.append((fence_start_line, line_count))

    # close remaining headings to EOF
    while heading_stack:
        _, start_line = heading_stack.pop()
        if line_count > start_line:
            ranges.append((start_line, line_count))

    return _normalize_fold_ranges(ranges, line_count)

LANGUAGE_FOLD_PROVIDERS: dict[str, FoldProvider] = {
    "c": cpp_fold_ranges,
    "cpp": cpp_fold_ranges,
    "python": python_fold_ranges,
    "json": json_fold_ranges,
    "jsonc": json_fold_ranges,
    "rust": rust_fold_ranges,
    "todo": todo_fold_ranges,
    "markdown": markdown_fold_ranges,
}


def get_fold_provider(language_id: str | None) -> FoldProvider | None:
    return LANGUAGE_FOLD_PROVIDERS.get(str(language_id or "").strip().lower())


def compute_folding_regions(editor: "CodeEditor", language_id: str | None) -> list[FoldRegion]:
    provider = get_fold_provider(language_id)
    if provider is None:
        return []
    text = editor.toPlainText()
    try:
        raw_ranges = provider(text)
    except Exception:
        raw_ranges = []
    line_count = max(1, editor.document().blockCount())
    return normalize_fold_ranges(list(raw_ranges or []), line_count)


def update_folding(editor: "CodeEditor") -> None:
    language_id = ""
    resolver = getattr(editor, "language_id", None)
    if callable(resolver):
        try:
            language_id = str(resolver() or "")
        except Exception:
            language_id = ""

    provider = get_fold_provider(language_id)
    editor._fold_provider = provider
    if provider is None:
        editor._clear_folding()
        editor.updateLineNumberAreaWidth(0)
        editor.lineNumberArea.update()
        return

    normalized = compute_folding_regions(editor, language_id)
    fold_ranges: dict[int, int] = {}
    for start_line, end_line in normalized:
        start_block = int(start_line) - 1
        end_block = int(end_line) - 1
        if end_block <= start_block:
            continue
        prev = fold_ranges.get(start_block)
        if prev is None or end_block > prev:
            fold_ranges[start_block] = end_block
    editor._fold_ranges = fold_ranges
    editor._folded_starts = {line for line in editor._folded_starts if line in editor._fold_ranges}
    editor._apply_fold_visibility()


__all__ = [
    "FoldRegion",
    "FoldProvider",
    "normalize_fold_ranges",
    "get_fold_provider",
    "compute_folding_regions",
    "update_folding",
    "cpp_fold_ranges",
    "python_fold_ranges",
    "json_fold_ranges",
    "rust_fold_ranges",
    "todo_fold_ranges",
    "markdown_fold_ranges",
]
