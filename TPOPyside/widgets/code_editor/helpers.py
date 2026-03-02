from __future__ import annotations

import ast
import builtins as py_builtins
import html
import inspect
import re
import textwrap

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

_COLOR_PATTERN = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6})\b")

_EDITOR_DEFAULT_KEYBINDINGS: dict[str, dict[str, list[str]]] = {
    "general": {
        "action.find": ["Ctrl+F"],
        "action.replace": ["Ctrl+H"],
        "action.go_to_definition": ["F12"],
        "action.find_usages": ["Shift+F12"],
        "action.rename_symbol": ["F2"],
        "action.extract_variable": ["Ctrl+Alt+V"],
        "action.extract_method": ["Ctrl+Alt+M"],
        "action.trigger_completion": ["Ctrl+Space"],
        "action.ai_inline_assist": ["Alt+\\"],
        "action.ai_inline_assist_alt_space": ["Alt+Space"],
        "action.ai_inline_assist_ctrl_alt_space": ["Ctrl+Alt+Space"],
    },
    "python": {
        "action.python_comment_toggle": ["Ctrl+/"],
    },
    "cpp": {
        "action.cpp_comment_toggle": ["Shift+/"],
    },
}


_COMPLETION_ITEM_ROLE = int(Qt.UserRole)
_COMPLETION_ROW_META_ROLE = int(Qt.UserRole) + 1
_COMPLETION_DOC_MISSING = object()

_COMPLETION_UI_DEFAULTS = {
    "show_signatures": True,
    "show_right_label": True,
    "show_doc_tooltip": True,
    "doc_tooltip_delay_ms": 180,
}
_LINT_VISUAL_DEFAULTS = {
    "mode": "squiggle",
    "error_color": "#E35D6A",
    "warning_color": "#D6A54A",
    "info_color": "#6AA1FF",
    "hint_color": "#8F9AA5",
    "squiggle_thickness": 2,
    "line_alpha": 64,
}
_OVERVIEW_MARKER_DEFAULTS = {
    "enabled": True,
    "width": 10,
    "search_color": "#4A8FD8",
    "search_active_color": "#D6A853",
    "occurrence_color": "#66A86A",
    "max_occurrence_matches": 12000,
    "max_occurrence_highlights": 3000,
    "occurrence_highlight_alpha": 88,
}
_HOVER_SIGNATURE_DELAY_MS = 180
_SIGNATURE_WRAP_WIDTH = 88
_TOOLTIP_STYLE_MARKER = "/* pytpo-dark-tooltip */"
_TOOLTIP_QSS = f"""
{_TOOLTIP_STYLE_MARKER}
QToolTip {{
    background-color: #2f2f2f;
    color: #e8e8e8;
    border: 1px solid #4a4a4a;
    padding: 6px;
}}
"""

_COMPLETION_KIND_COLOR_FALLBACKS = {
    "ai": QColor("#FFB86C"),
    "class": QColor("#4FC1FF"),
    "function": QColor("#DCDCAA"),
    "variable": QColor("#9CDCFE"),
    "module": QColor("#4EC9B0"),
    "keyword": QColor("#C586C0"),
    "default": QColor("#D4D4D4"),
}



def _first_nonempty_line(text: str) -> str:
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line:
            return line
    return ""


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n", ""}:
        return False
    return bool(default)


def _is_signature_like(text: str, label: str) -> bool:
    line = str(text or "").strip()
    if not line:
        return False
    if line.startswith("def ") or line.startswith("class "):
        return True
    label = str(label or "").strip()
    if label and re.search(rf"\b{re.escape(label)}\s*\(", line):
        return True
    return bool(re.match(r"^[A-Za-z_]\w*\s*\([^)]*\)\s*(->.*)?$", line))


def _clean_signature_whitespace(sig: str) -> str:
    out = re.sub(r"\s+", " ", str(sig or "")).strip()
    out = out.replace("( ", "(").replace(" )", ")")
    out = out.replace(" ,", ",")
    return out


def _normalize_signature_text(signature: str, label: str = "") -> str:
    text = _clean_signature_whitespace(signature)
    if not text:
        return ""

    m = re.match(r"^<\s*Signature\s*:?\s*(.*?)\s*(?:\?>|>)\s*$", text, re.IGNORECASE)
    if m:
        text = _clean_signature_whitespace(m.group(1))

    if text.lower().startswith("signature:"):
        text = _clean_signature_whitespace(text.split(":", 1)[1])

    if label:
        raw = text.strip()
        if raw.startswith("("):
            text = f"{label}{raw}"

    return _clean_signature_whitespace(text)


def _wrap_signature_text(signature: str, width: int = _SIGNATURE_WRAP_WIDTH) -> str:
    sig = _clean_signature_whitespace(signature)
    if not sig:
        return ""
    sig = re.sub(r",\s*", ", ", sig)
    if len(sig) <= width:
        return sig
    wrapped = textwrap.wrap(
        sig,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
        subsequent_indent="    ",
    )
    return "\n".join(wrapped) if wrapped else sig


def _build_signature_tooltip_html(
        *,
        signature: str,
        label: str,
        documentation: str = "",
        owner: str = "",
) -> str:
    sig = _normalize_signature_text(signature, label)
    if not sig:
        return ""

    if not sig.startswith("def "):
        sig = f"def {sig}"
    sig = _wrap_signature_text(sig)

    owner_line = str(owner or "").strip()
    doc = _build_doc_preview_text(documentation, label)

    parts: list[str] = [
        "<div style='max-width:620px; white-space:pre-wrap; line-height:1.35;'>",
    ]
    if owner_line:
        parts.append(
            f"<div style='color:#7faeff; margin-bottom:5px;'>{html.escape(owner_line)}</div>"
        )
    parts.append(
        f"<div style='font-family:\"Cascadia Code\",\"Consolas\",monospace; color:#e6e6e6;'>{html.escape(sig)}</div>"
    )
    if doc:
        parts.append(
            f"<div style='margin-top:6px; color:#cfd7e6;'>{html.escape(doc)}</div>"
        )
    parts.append("</div>")
    return "".join(parts)


def _extract_compact_signature(label: str, detail: str) -> str:
    line = _first_nonempty_line(detail)
    if not line:
        return ""
    probe = _normalize_signature_text(line, label).strip()

    for prefix in ("def ", "async def ", "function ", "class "):
        if probe.startswith(prefix):
            probe = probe[len(prefix):].strip()
            break

    label = str(label or "").strip()
    if label:
        m = re.search(rf"\b{re.escape(label)}\s*\((?:[^()]|\([^)]*\))*\)", probe)
        if m:
            return _clean_signature_whitespace(m.group(0))

    m = re.search(r"[A-Za-z_]\w*\s*\((?:[^()]|\([^)]*\))*\)", probe)
    if not m:
        return ""
    return _clean_signature_whitespace(m.group(0))


def _kind_group(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k in {"ai"}:
        return "ai"
    if k in {"class", "type"}:
        return "class"
    if k in {"function", "method"}:
        return "function"
    if k in {"param", "parameter", "statement", "name", "instance", "attribute", "property", "variable"}:
        return "variable"
    if k in {"module", "path", "package"}:
        return "module"
    if k in {"keyword"}:
        return "keyword"
    return "default"


def _build_doc_preview_text(raw_doc: str, label: str = "") -> str:
    text = str(raw_doc or "").strip()
    if not text:
        return ""

    meaningful: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"[-=~`#*]{3,}", line):
            continue
        meaningful.append(line)

    if not meaningful:
        return ""

    first = meaningful[0]
    if _is_signature_like(first, label):
        meaningful = meaningful[1:]
        if not meaningful:
            return ""
        first = meaningful[0]

    first_wrapped = textwrap.wrap(first, width=90) or [first]
    first_line = first_wrapped[0]

    second_line = ""
    if len(first_wrapped) > 1:
        second_line = first_wrapped[1]
    elif len(meaningful) > 1:
        wrapped_second = textwrap.wrap(meaningful[1], width=90)
        if wrapped_second:
            second_line = wrapped_second[0]

    if second_line:
        return f"{first_line}\n{second_line}"
    return first_line


def _compute_completion_doc_preview(item: dict, source_text: str) -> str:
    label = str(item.get("label") or item.get("insert_text") or "")

    for key in ("doc", "docstring", "documentation"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            preview = _build_doc_preview_text(value, label)
            if preview:
                return preview

    detail = str(item.get("detail") or "")
    if "\n" in detail:
        preview = _build_doc_preview_text(detail, label)
        if preview:
            return preview

    if source_text and label:
        try:
            tree = ast.parse(source_text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == label:
                    doc = ast.get_docstring(node, clean=True) or ""
                    preview = _build_doc_preview_text(doc, label)
                    if preview:
                        return preview
                    break
        except Exception:
            pass

    scope = str(item.get("source_scope") or "").strip().lower()
    if scope == "builtins" and hasattr(py_builtins, label):
        try:
            doc = inspect.getdoc(getattr(py_builtins, label)) or ""
            preview = _build_doc_preview_text(doc, label)
            if preview:
                return preview
        except Exception:
            pass

    return ""


def _expr_to_text(expr: ast.AST | None) -> str:
    if expr is None:
        return ""
    try:
        return str(ast.unparse(expr)).strip()
    except Exception:
        return "..."


def _format_ast_callable_signature(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        omit_first_self: bool = False,
) -> str:
    parts: list[str] = []

    posonly = list(node.args.posonlyargs or [])
    regular = list(node.args.args or [])
    if omit_first_self:
        if regular and regular[0].arg in {"self", "cls"}:
            regular = regular[1:]
        elif posonly and posonly[0].arg in {"self", "cls"}:
            posonly = posonly[1:]

    positional = posonly + regular
    defaults = list(node.args.defaults or [])
    default_start = len(positional) - len(defaults)

    for idx, arg in enumerate(positional):
        token = arg.arg
        ann = _expr_to_text(arg.annotation)
        if ann:
            token = f"{token}: {ann}"
        if idx >= default_start:
            token = f"{token}={_expr_to_text(defaults[idx - default_start])}"
        parts.append(token)
        if posonly and idx == len(posonly) - 1:
            parts.append("/")

    if node.args.vararg is not None:
        var_token = f"*{node.args.vararg.arg}"
        var_ann = _expr_to_text(node.args.vararg.annotation)
        if var_ann:
            var_token = f"{var_token}: {var_ann}"
        parts.append(var_token)
    elif node.args.kwonlyargs:
        parts.append("*")

    kw_defaults = list(node.args.kw_defaults or [])
    for idx, arg in enumerate(node.args.kwonlyargs or []):
        token = arg.arg
        ann = _expr_to_text(arg.annotation)
        if ann:
            token = f"{token}: {ann}"
        if idx < len(kw_defaults) and kw_defaults[idx] is not None:
            token = f"{token}={_expr_to_text(kw_defaults[idx])}"
        parts.append(token)

    if node.args.kwarg is not None:
        kw_token = f"**{node.args.kwarg.arg}"
        kw_ann = _expr_to_text(node.args.kwarg.annotation)
        if kw_ann:
            kw_token = f"{kw_token}: {kw_ann}"
        parts.append(kw_token)

    return f"({', '.join(parts)})"


def _collect_source_signatures(source_text: str) -> dict[str, str]:
    if not source_text:
        return {}
    try:
        tree = ast.parse(source_text)
    except Exception:
        return {}

    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, f"{node.name}{_format_ast_callable_signature(node)}")
        elif isinstance(node, ast.ClassDef):
            init_fn: ast.FunctionDef | ast.AsyncFunctionDef | None = None
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_sig = f"{sub.name}{_format_ast_callable_signature(sub, omit_first_self=True)}"
                    out.setdefault(sub.name, method_sig)
                    if sub.name == "__init__":
                        init_fn = sub
            if init_fn is not None:
                out.setdefault(node.name, f"{node.name}{_format_ast_callable_signature(init_fn, omit_first_self=True)}")
            else:
                out.setdefault(node.name, f"{node.name}()")
    return out


def _signature_for_label(label: str, source_text: str) -> str:
    name = str(label or "").strip()
    if not name:
        return ""

    source_index = _collect_source_signatures(source_text)
    sig = source_index.get(name)
    if sig:
        return _normalize_signature_text(sig, name)

    if hasattr(py_builtins, name):
        try:
            obj = getattr(py_builtins, name)
            if callable(obj):
                return _normalize_signature_text(f"{name}{inspect.signature(obj)}", name)
        except Exception:
            pass
    return ""




# Preserve historical star-import behavior, including underscore-prefixed helpers.
__all__ = [name for name in globals() if not name.startswith("__")]
