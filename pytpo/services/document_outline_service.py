from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class OutlineSymbol:
    name: str
    kind: str
    line: int
    column: int
    children: list["OutlineSymbol"] = field(default_factory=list)


def build_document_outline(
    *,
    file_path: str,
    source_text: str,
    language_id: str = "",
) -> tuple[list[OutlineSymbol], str]:
    lang = str(language_id or "").strip().lower()
    suffix = os.path.splitext(str(file_path or ""))[1].lower()

    if lang == "python" or suffix in {".py", ".pyw", ".pyi"}:
        return _build_python_outline(source_text)
    if lang in {"c", "cpp"} or suffix in {".c", ".h", ".hpp", ".cpp", ".cc", ".cxx", ".hh", ".hxx"}:
        return _build_cpp_outline(source_text)
    return [], ""


def _build_python_outline(source_text: str) -> tuple[list[OutlineSymbol], str]:
    text = str(source_text or "")
    if not text.strip():
        return [], ""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        line = int(getattr(exc, "lineno", 0) or 0)
        return [], f"Python parse error at line {max(1, line)}"
    except Exception:
        return [], "Python parse error"

    return _collect_python_nodes(list(tree.body), inside_class=False), ""


def _collect_python_nodes(nodes: list[ast.stmt], *, inside_class: bool) -> list[OutlineSymbol]:
    out: list[OutlineSymbol] = []
    for node in nodes:
        if isinstance(node, ast.ClassDef):
            child_symbols = _collect_python_nodes(list(node.body), inside_class=True)
            out.append(
                OutlineSymbol(
                    name=str(node.name or "").strip() or "<class>",
                    kind="class",
                    line=max(1, int(getattr(node, "lineno", 1) or 1)),
                    column=max(1, int(getattr(node, "col_offset", 0) or 0) + 1),
                    children=child_symbols,
                )
            )
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            child_symbols = _collect_python_nodes(list(getattr(node, "body", []) or []), inside_class=False)
            out.append(
                OutlineSymbol(
                    name=str(node.name or "").strip() or "<function>",
                    kind="method" if inside_class else "function",
                    line=max(1, int(getattr(node, "lineno", 1) or 1)),
                    column=max(1, int(getattr(node, "col_offset", 0) or 0) + 1),
                    children=child_symbols,
                )
            )
    return out


_CPP_CLASS_RE = re.compile(r"^\s*(class|struct)\s+([A-Za-z_]\w*)\b")
_CPP_ENUM_RE = re.compile(r"^\s*enum(?:\s+class)?\s+([A-Za-z_]\w*)\b")
_CPP_FUNCTION_RE = re.compile(
    r"(?P<name>[~A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\([^;{}()]*\)\s*"
    r"(?:(?:const|noexcept|override|final)\b\s*)*"
    r"(?:->\s*[^({;]+)?\s*"
    r"(?P<tail>[{;])"
)
_CPP_CONTROL_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "sizeof", "decltype"}


def _build_cpp_outline(source_text: str) -> tuple[list[OutlineSymbol], str]:
    text = str(source_text or "")
    if not text.strip():
        return [], ""

    symbols: list[OutlineSymbol] = []
    scope_stack: list[tuple[int, OutlineSymbol]] = []
    brace_depth = 0
    in_block_comment = False

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line, in_block_comment = _strip_cpp_comments(raw_line, in_block_comment)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            brace_depth += line.count("{") - line.count("}")
            while scope_stack and brace_depth < scope_stack[-1][0]:
                scope_stack.pop()
            continue

        class_match = _CPP_CLASS_RE.match(stripped)
        if class_match:
            kind = class_match.group(1)
            name = class_match.group(2)
            symbol = OutlineSymbol(name=name, kind=str(kind), line=line_no, column=max(1, line.find(name) + 1))
            _append_cpp_symbol(symbols, scope_stack, symbol)
            if "{" in stripped:
                scope_stack.append((brace_depth + 1, symbol))

        enum_match = _CPP_ENUM_RE.match(stripped)
        if enum_match:
            name = enum_match.group(1)
            symbol = OutlineSymbol(name=name, kind="enum", line=line_no, column=max(1, line.find(name) + 1))
            _append_cpp_symbol(symbols, scope_stack, symbol)

        fn_match = _CPP_FUNCTION_RE.search(stripped)
        if fn_match:
            full_name = str(fn_match.group("name") or "").strip()
            tail = str(fn_match.group("tail") or "").strip()
            simple_name = full_name.split("::")[-1].strip()
            if simple_name and simple_name not in _CPP_CONTROL_KEYWORDS:
                kind = "method" if scope_stack else "function"
                if tail == ";" and kind == "function":
                    kind = "declaration"
                symbol = OutlineSymbol(
                    name=full_name,
                    kind=kind,
                    line=line_no,
                    column=max(1, line.find(simple_name) + 1),
                )
                _append_cpp_symbol(symbols, scope_stack, symbol)

        brace_depth += line.count("{") - line.count("}")
        while scope_stack and brace_depth < scope_stack[-1][0]:
            scope_stack.pop()

    return symbols, ""


def _append_cpp_symbol(
    symbols: list[OutlineSymbol],
    scope_stack: list[tuple[int, OutlineSymbol]],
    symbol: OutlineSymbol,
) -> None:
    if scope_stack:
        scope_stack[-1][1].children.append(symbol)
        return
    symbols.append(symbol)


def _strip_cpp_comments(line: str, in_block_comment: bool) -> tuple[str, bool]:
    src = str(line or "")
    if not src:
        return "", in_block_comment

    out: list[str] = []
    i = 0
    n = len(src)
    block = bool(in_block_comment)
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
        if ch == "/" and nxt == "*":
            block = True
            i += 2
            continue
        if ch == "/" and nxt == "/":
            break
        out.append(ch)
        i += 1
    return "".join(out), block


__all__ = ["OutlineSymbol", "build_document_outline"]
