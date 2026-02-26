from __future__ import annotations

import ast
import builtins as py_builtins
from collections import OrderedDict
import concurrent.futures
import html
import inspect
import os
import re
import textwrap
from pathlib import Path
from typing import Callable, Mapping

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, Signal, QTimer, QRectF
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPolygon,
    QPainter,
    QPalette,
    QPixmap,
    QShortcut,
    QTextCursor,
    QTextFormat, QBrush, QFontMetricsF, QPen, QCursor, QPainterPath,
)
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QDialog, QLabel, QTextEdit, QHBoxLayout, QStyle, QStyleOptionViewItem, QStyledItemDelegate, QToolTip, QWidget

from TPOPyside.dialogs.color_picker_dialog import ColorPickerDialog
from TPOPyside.widgets.code_folding import get_fold_provider, update_folding as update_editor_folding
from TPOPyside.widgets.keypress_handlers import (
    dispatch_key_press as dispatch_language_key_press,
    dispatch_mouse_press as dispatch_language_mouse_press,
    get_language_id,
    is_todo_checkbox_at_pos,
    toggle_cpp_comment_selection,
    toggle_python_comment_selection,
)
from TPOPyside.widgets.syntax_highlighters import (
    ensure_highlighter as ensure_editor_highlighter,
    set_highlighter_for_file as set_editor_highlighter_for_file,
)

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


class _CompletionItemDelegate(QStyledItemDelegate):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        row_h = max(base.height(), self._editor.fontMetrics().height() + 8)
        return QSize(base.width(), row_h)

    def paint(self, painter, option, index):
        meta = index.data(_COMPLETION_ROW_META_ROLE)
        if not isinstance(meta, dict):
            super().paint(painter, option, index)
            return

        style = option.widget.style() if option.widget is not None else QApplication.style()
        style_opt = QStyleOptionViewItem(option)
        style_opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, style_opt, painter, option.widget)

        rect = option.rect.adjusted(8, 0, -8, 0)
        if rect.width() <= 0:
            return

        primary = str(meta.get("primary") or "")
        right = str(meta.get("right") or "")
        kind = str(meta.get("kind_group") or "default")

        fm = option.fontMetrics
        selected = bool(option.state & QStyle.State_Selected)

        right_width = 0
        if right:
            right_width = min(max(36, fm.horizontalAdvance(right) + 8), int(rect.width() * 0.42))

        right_rect = QRect(rect.right() - right_width + 1, rect.top(), right_width, rect.height())
        main_rect = QRect(rect.left(), rect.top(), max(0, rect.width() - right_width - 10), rect.height())

        painter.save()
        if right and right_rect.width() > 0:
            right_pen = (
                option.palette.color(QPalette.HighlightedText)
                if selected
                else option.palette.color(QPalette.PlaceholderText)
            )
            painter.setPen(right_pen)
            painter.drawText(
                right_rect.adjusted(0, 0, -2, 0),
                Qt.AlignRight | Qt.AlignVCenter,
                fm.elidedText(right, Qt.ElideRight, right_rect.width()),
            )

        painter.setPen(self._editor._completion_kind_color(kind, option.palette, selected))
        painter.drawText(
            main_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            fm.elidedText(primary, Qt.ElideRight, main_rect.width()),
        )
        painter.restore()


class _EditorSearchBar(QFrame):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor
        self._replace_visible = False
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("editorSearchBar")
        self.setStyleSheet(
            """
            QFrame#editorSearchBar {
                background: #252526;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
            }
            QLineEdit, QPushButton, QCheckBox {
                font-size: 10pt;
            }
            QLineEdit {
                min-height: 24px;
                padding: 2px 6px;
                border: 1px solid #4a4a4a;
                background: #1e1e1e;
            }
            QPushButton {
                min-height: 24px;
                padding: 0 8px;
            }
            QCheckBox {
                spacing: 4px;
            }
            """
        )

        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.installEventFilter(self)

        self.replace_edit = QLineEdit(self)
        self.replace_edit.setPlaceholderText("Replace")
        self.replace_edit.installEventFilter(self)

        self.prev_btn = QPushButton("Prev", self)
        self.next_btn = QPushButton("Next", self)
        self.replace_btn = QPushButton("Replace", self)
        self.replace_all_btn = QPushButton("Replace All", self)
        self.case_box = QCheckBox("Case", self)
        self.word_box = QCheckBox("Word", self)
        self.regex_box = QCheckBox("Regex", self)
        self.selection_box = QCheckBox("Selection", self)
        self.count_lbl = QLabel("0 / 0", self)
        self.close_btn = QPushButton("X", self)
        self.close_btn.setFixedWidth(24)

        self.prev_btn.clicked.connect(self._editor.search_previous)
        self.next_btn.clicked.connect(self._editor.search_next)
        self.replace_btn.clicked.connect(self._editor.replace_current_or_next)
        self.replace_all_btn.clicked.connect(self._editor.replace_all_matches)
        self.close_btn.clicked.connect(self._editor.hide_search_bar)

        self.find_edit.textChanged.connect(self._editor._on_search_query_changed)
        self.replace_edit.textChanged.connect(self._editor._on_replace_query_changed)
        self.case_box.toggled.connect(self._editor._on_search_option_changed)
        self.word_box.toggled.connect(self._editor._on_search_option_changed)
        self.regex_box.toggled.connect(self._editor._on_search_option_changed)
        self.selection_box.toggled.connect(self._editor._on_search_option_changed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)
        lay.addWidget(self.find_edit, 2)
        lay.addWidget(self.replace_edit, 2)
        lay.addWidget(self.prev_btn)
        lay.addWidget(self.next_btn)
        lay.addWidget(self.replace_btn)
        lay.addWidget(self.replace_all_btn)
        lay.addWidget(self.case_box)
        lay.addWidget(self.word_box)
        lay.addWidget(self.regex_box)
        lay.addWidget(self.selection_box)
        lay.addWidget(self.count_lbl)
        lay.addWidget(self.close_btn)
        self.set_replace_visible(False)

    def set_replace_visible(self, visible: bool):
        self._replace_visible = bool(visible)
        self.replace_edit.setVisible(self._replace_visible)
        self.replace_btn.setVisible(self._replace_visible)
        self.replace_all_btn.setVisible(self._replace_visible)
        self.adjustSize()

    def is_replace_visible(self) -> bool:
        return self._replace_visible

    def set_count_text(self, text: str):
        self.count_lbl.setText(str(text or "0 / 0"))

    def set_in_selection_checked(self, checked: bool):
        blocker = self.selection_box.blockSignals(True)
        self.selection_box.setChecked(bool(checked))
        self.selection_box.blockSignals(blocker)

    def focus_find(self):
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def eventFilter(self, watched, event):
        if watched in {self.find_edit, self.replace_edit} and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self._editor.search_previous()
                else:
                    self._editor.search_next()
                return True
            if key == Qt.Key_Escape:
                self._editor.hide_search_bar()
                return True
        return super().eventFilter(watched, event)


# ---------------- Code Editor with line numbers ----------------

class LineNumberArea(QWidget):
    def __init__(self, editor: 'CodeEditor'):
        super().__init__(editor)
        self.codeEditor = editor
    def sizeHint(self):
        return QSize(self.codeEditor.lineNumberAreaWidth(), 0)
    def paintEvent(self, event):
        self.codeEditor.lineNumberAreaPaintEvent(event)
    def mousePressEvent(self, event):
        self.codeEditor.lineNumberAreaMousePressEvent(event)
    def mouseDoubleClickEvent(self, event):
        self.codeEditor.lineNumberAreaMousePressEvent(event)


class OverviewMarkerArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.codeEditor = editor

    def sizeHint(self):
        return QSize(self.codeEditor.overviewMarkerAreaWidth(), 0)

    def paintEvent(self, event):
        self.codeEditor.overviewMarkerAreaPaintEvent(event)

    def mousePressEvent(self, event):
        self.codeEditor.overviewMarkerAreaMousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.codeEditor.overviewMarkerAreaMousePressEvent(event)


# -----------------
class CodeEditor(QPlainTextEdit):
    completionRequested = Signal(str)  # reason: manual | auto
    completionAccepted = Signal(str)   # insert_text
    aiAssistRequested = Signal(str)  # reason: manual
    inlineSuggestionAccepted = Signal(str)
    signatureRequested = Signal(object)  # hover request payload
    definitionRequested = Signal(object)
    usagesRequested = Signal(object)
    renameRequested = Signal(object)
    quickFixRequested = Signal(object)
    extractVariableRequested = Signal(object)
    extractMethodRequested = Signal(object)
    contextMenuAboutToShow = Signal(object, object)  # menu, payload
    wordWrapPreferenceChanged = Signal(object)  # {enabled, file_path, language_id}
    editorFontSizeStepRequested = Signal(int)  # +1 / -1
    _tooltip_style_installed = False
    _default_keybindings: dict[str, dict[str, list[str]]] = {
        "general": {
            key: list(value)
            for key, value in _EDITOR_DEFAULT_KEYBINDINGS.get("general", {}).items()
        },
        "python": {
            key: list(value)
            for key, value in _EDITOR_DEFAULT_KEYBINDINGS.get("python", {}).items()
        },
        "cpp": {
            key: list(value)
            for key, value in _EDITOR_DEFAULT_KEYBINDINGS.get("cpp", {}).items()
        },
    }


    def __init__(self, parent=None):
        super().__init__(parent)
        self._python_comment_shortcut_managed = True
        self._cpp_comment_shortcut_managed = False
        self._configured_keybindings = {
            "general": {
                key: list(value)
                for key, value in self._default_keybindings.get("general", {}).items()
            },
            "python": {
                key: list(value)
                for key, value in self._default_keybindings.get("python", {}).items()
            },
            "cpp": {
                key: list(value)
                for key, value in self._default_keybindings.get("cpp", {}).items()
            },
        }
        self._configured_shortcuts: list[QShortcut] = []
        self._file_path: str | None = None
        self._fold_provider: Callable[[str], list[tuple[int, int]]] | None = None
        self._fold_ranges: dict[int, int] = {}
        self._folded_starts: set[int] = set()
        self._fold_selection_adjusting = False
        self._fold_gutter_width = 14
        self.use_tabs = False
        self.indent_width = 4  # spaces per indent level
        self._lint_diagnostics: list[dict] = []
        self._lint_line_severity: dict[int, str] = {}
        self._lint_selections: list[QTextEdit.ExtraSelection] = []
        self._lint_visual_cfg = dict(_LINT_VISUAL_DEFAULTS)
        self._overview_cfg = dict(_OVERVIEW_MARKER_DEFAULTS)
        self._overview_search_lines: set[int] = set()
        self._overview_active_search_lines: set[int] = set()
        self._overview_occurrence_lines: set[int] = set()
        self._overview_occurrence_term = ""
        self._completion_items: list[dict] = []
        self._completion_filtered_items: list[dict] = []
        self._completion_recency: dict[str, int] = {}
        self._completion_max_visible_rows = 10
        self._completion_row_meta_cache: OrderedDict[tuple[str, str, int], dict] = OrderedDict()
        self._completion_doc_cache: OrderedDict[tuple[str, str, int], str] = OrderedDict()
        self._completion_doc_futures: dict[tuple[str, str, int], concurrent.futures.Future] = {}
        self._completion_cache_max = 512
        self._completion_result_file_path = ""
        self._completion_result_token = 0
        self._completion_result_revision = 0
        self._completion_result_source_text = ""
        self._completion_source_sig_index_key: tuple[str, int] | None = None
        self._completion_source_sig_index: dict[str, str] = {}
        self._completion_pending_doc_row = -1
        self._completion_pending_doc_pos = QPoint()
        self._completion_pending_doc_key: tuple[str, str, int] | None = None
        self._completion_ai_item: dict | None = None
        self._inline_suggestion_text = ""
        self._inline_suggestion_anchor_pos = -1
        self._inline_suggestion_anchor_revision = -1
        self._completion_ui_cfg = dict(_COMPLETION_UI_DEFAULTS)
        self._hover_signature_cache: OrderedDict[tuple[str, str, int], str] = OrderedDict()
        self._hover_signature_futures: dict[tuple[str, str, int], concurrent.futures.Future] = {}
        self._hover_signature_pending_key: tuple[str, str, int] | None = None
        self._hover_signature_pending_label = ""
        self._hover_signature_pending_pos = QPoint()
        self._hover_signature_pending_line = 0
        self._hover_signature_pending_column = 0
        self._hover_signature_request_seq = 0
        self._hover_signature_active_request_id = 0
        self._hover_signature_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="pytpo-hover-sig",
        )
        self._completion_doc_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="pytpo-doc",
        )

        self._completion_popup = QListWidget(self)
        self._completion_popup.hide()
        self._completion_popup.setFocusPolicy(Qt.NoFocus)
        self._completion_popup.setMouseTracking(True)
        self._completion_popup.viewport().setMouseTracking(True)
        self._completion_popup.viewport().installEventFilter(self)
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)
        self._completion_popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._completion_popup.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._completion_popup.setUniformItemSizes(True)
        self._completion_popup.setSelectionMode(QListWidget.SingleSelection)
        self._completion_popup.setItemDelegate(_CompletionItemDelegate(self))
        self._completion_popup.itemClicked.connect(self._on_completion_item_clicked)
        self._completion_popup.currentRowChanged.connect(self._on_completion_row_changed)
        self._completion_popup.setStyleSheet(
            """
            QListWidget {
                background: #1f1f1f;
                border: 1px solid #3a3a3a;
                padding: 2px;
            }
            QListWidget::item {
                padding: 3px 4px;
            }
            QListWidget::item:selected {
                background: #264f78;
            }
            """
        )
        self._completion_doc_tooltip_timer = QTimer(self)
        self._completion_doc_tooltip_timer.setSingleShot(True)
        self._completion_doc_tooltip_timer.timeout.connect(self._on_completion_doc_timer)
        self._completion_doc_future_pump = QTimer(self)
        self._completion_doc_future_pump.setInterval(24)
        self._completion_doc_future_pump.timeout.connect(self._drain_completion_doc_futures)
        self._hover_signature_timer = QTimer(self)
        self._hover_signature_timer.setSingleShot(True)
        self._hover_signature_timer.setInterval(_HOVER_SIGNATURE_DELAY_MS)
        self._hover_signature_timer.timeout.connect(self._on_hover_signature_timer)
        self._hover_signature_future_pump = QTimer(self)
        self._hover_signature_future_pump.setInterval(24)
        self._hover_signature_future_pump.timeout.connect(self._drain_hover_signature_futures)

        self._search_bar = _EditorSearchBar(self)
        self._search_bar.hide()
        self._search_selection_range: tuple[int, int] | None = None
        self._search_matches: list[tuple[int, int]] = []
        self._search_current_index = -1
        self._search_highlight_selections: list[QTextEdit.ExtraSelection] = []
        self._search_active_selection: QTextEdit.ExtraSelection | None = None
        self._search_last_error = ""
        self._search_refresh_timer = QTimer(self)
        self._search_refresh_timer.setSingleShot(True)
        self._search_refresh_timer.setInterval(110)
        self._search_refresh_timer.timeout.connect(self._refresh_search_matches)
        self._occurrence_refresh_timer = QTimer(self)
        self._occurrence_refresh_timer.setSingleShot(True)
        self._occurrence_refresh_timer.setInterval(90)
        self._occurrence_refresh_timer.timeout.connect(self._refresh_occurrence_markers)
        self.textChanged.connect(self._on_text_changed_search_refresh)
        self.textChanged.connect(self._schedule_occurrence_marker_refresh)
        self._fold_refresh_timer = QTimer(self)
        self._fold_refresh_timer.setSingleShot(True)
        self._fold_refresh_timer.setInterval(140)
        self._fold_refresh_timer.timeout.connect(self._refresh_fold_ranges)
        self.textChanged.connect(self._schedule_fold_refresh)

        self._editor_background_color = QColor("#252526")
        self._editor_background_image_path = ""
        self._editor_background_scale_mode = "stretch"
        self._editor_background_image_brightness = 100
        self._editor_background_tint_color = QColor("#000000")
        self._editor_background_tint_strength = 0
        self._editor_background_source_pixmap: QPixmap | None = None
        self._editor_background_cache_size = QSize()
        self._editor_background_cache_pixmap: QPixmap | None = None

        self._todo_hovering_box = False

        self.lineNumberArea = LineNumberArea(self)
        self.overviewMarkerArea = OverviewMarkerArea(self)
        self.blockCountChanged.connect(self.updateLineNumberAreaWidth)
        self.updateRequest.connect(self.updateLineNumberArea)
        self.verticalScrollBar().rangeChanged.connect(self._on_scrollbar_range_changed)
        self.horizontalScrollBar().rangeChanged.connect(self._on_scrollbar_range_changed)
        self.cursorPositionChanged.connect(self.highlightCurrentLine)
        self.cursorPositionChanged.connect(self._on_cursor_moved_inline_suggestion)
        self.cursorPositionChanged.connect(self._schedule_occurrence_marker_refresh)
        self.selectionChanged.connect(self._on_selection_changed_expand_collapsed_folds)
        self.selectionChanged.connect(self._schedule_occurrence_marker_refresh)

        self.updateLineNumberAreaWidth(0)
        self.highlightCurrentLine()
        self.setFont(QFont("Courier New", 11))
        self.set_editor_background()
        self._ensure_tooltip_style()
        self.update_completion_ui_settings({})
        self._rebuild_configured_shortcuts()
        self._schedule_occurrence_marker_refresh()
        self.destroyed.connect(lambda *_args: self._shutdown_completion_workers())

    @classmethod
    def set_default_keybindings(cls, keybindings: Mapping[str, Mapping[str, list[str]]] | None) -> None:
        merged = {
            "general": {
                key: list(value)
                for key, value in _EDITOR_DEFAULT_KEYBINDINGS.get("general", {}).items()
            },
            "python": {
                key: list(value)
                for key, value in _EDITOR_DEFAULT_KEYBINDINGS.get("python", {}).items()
            },
            "cpp": {
                key: list(value)
                for key, value in _EDITOR_DEFAULT_KEYBINDINGS.get("cpp", {}).items()
            },
        }
        payload = keybindings if isinstance(keybindings, Mapping) else {}
        for scope in ("general", "python", "cpp"):
            scoped = payload.get(scope)
            if not isinstance(scoped, Mapping):
                continue
            scope_map = merged.setdefault(scope, {})
            for action_id, sequence in scoped.items():
                if not isinstance(sequence, list):
                    continue
                normalized = [str(item).strip() for item in sequence if str(item).strip()]
                if normalized:
                    scope_map[str(action_id)] = normalized
        cls._default_keybindings = merged

    def configure_keybindings(self, keybindings: Mapping[str, Mapping[str, list[str]]] | None) -> None:
        self.__class__.set_default_keybindings(keybindings)
        self._configured_keybindings = {
            "general": {
                key: list(value)
                for key, value in self._default_keybindings.get("general", {}).items()
            },
            "python": {
                key: list(value)
                for key, value in self._default_keybindings.get("python", {}).items()
            },
            "cpp": {
                key: list(value)
                for key, value in self._default_keybindings.get("cpp", {}).items()
            },
        }
        self._rebuild_configured_shortcuts()

    def _action_sequence(self, scope: str, action_id: str) -> list[str]:
        scoped = self._configured_keybindings.get(str(scope or "").strip().lower(), {})
        if not isinstance(scoped, dict):
            return []
        sequence = scoped.get(str(action_id or "").strip())
        if not isinstance(sequence, list):
            return []
        return [str(item).strip() for item in sequence if str(item).strip()]

    @staticmethod
    def _sequence_to_qkeysequence(sequence: list[str]) -> QKeySequence:
        return QKeySequence(", ".join(str(item).strip() for item in sequence if str(item).strip()))

    @staticmethod
    def _sequence_to_text(sequence: list[str]) -> str:
        return ", ".join(str(item).strip() for item in sequence if str(item).strip())

    def _clear_configured_shortcuts(self) -> None:
        for shortcut in self._configured_shortcuts:
            try:
                shortcut.activated.disconnect()
            except Exception:
                pass
            try:
                shortcut.deleteLater()
            except Exception:
                pass
        self._configured_shortcuts.clear()

    def _install_shortcut(self, sequence: list[str], callback: Callable[[], None]) -> None:
        qseq = self._sequence_to_qkeysequence(sequence)
        if qseq.isEmpty():
            return
        shortcut = QShortcut(qseq, self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        self._configured_shortcuts.append(shortcut)

    def _rebuild_configured_shortcuts(self) -> None:
        self._clear_configured_shortcuts()
        self._install_shortcut(
            self._action_sequence("python", "action.python_comment_toggle"),
            self._on_python_comment_toggle_shortcut,
        )

    def _event_matches_action_shortcut(self, event: QKeyEvent, scope: str, action_id: str) -> bool:
        sequence = self._action_sequence(scope, action_id)
        if not sequence:
            return False
        chord = str(sequence[0] or "").strip()
        if not chord:
            return False
        target = QKeySequence(chord)
        if target.isEmpty():
            return False
        try:
            mods = event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.MetaModifier
            )
            pressed = QKeySequence(int(mods) | int(event.key()))
        except Exception:
            return False
        return bool(pressed.matches(target) == QKeySequence.SequenceMatch.ExactMatch)

    def _handle_editor_shortcut_fallback(self, event: QKeyEvent) -> bool:
        if (
            bool(event.modifiers() & Qt.AltModifier)
            and not bool(event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
        ):
            return bool(self.request_quick_fix("shortcut"))
        if self._event_matches_action_shortcut(event, "general", "action.find"):
            self.show_find_bar()
            return True
        if self._event_matches_action_shortcut(event, "general", "action.replace"):
            self.show_replace_bar()
            return True
        if self._event_matches_action_shortcut(event, "general", "action.trigger_completion"):
            self.request_manual_completion()
            return True
        if self._event_matches_action_shortcut(event, "general", "action.go_to_definition"):
            return bool(self.request_definition("shortcut"))
        if self._event_matches_action_shortcut(event, "general", "action.find_usages"):
            return bool(self.request_usages("shortcut"))
        return False

    def event(self, event):
        if event is not None and event.type() == QEvent.ShortcutOverride and isinstance(event, QKeyEvent):
            if self._handle_editor_shortcut_fallback(event):
                event.accept()
                return True
        return super().event(event)

    def _on_python_comment_toggle_shortcut(self) -> None:
        if self.language_id() != "python":
            return
        toggle_python_comment_selection(self)

    def set_editor_font_preferences(self, *, family: str | None = None, point_size: int | None = None) -> None:
        font = self.font()
        if isinstance(family, str) and family.strip():
            font.setFamily(family.strip())
        font.setStyleHint(QFont.StyleHint.Monospace)
        if point_size is not None:
            try:
                size = max(1, int(point_size))
            except Exception:
                size = int(font.pointSize()) if int(font.pointSize()) > 0 else 10
            font.setPointSize(size)
        self.setFont(font)

    def set_file_path(self, file_path: str | None):
        self._file_path = str(file_path) if file_path else None
        self._apply_highlighter()
        self._apply_fold_provider()

    def is_word_wrap_enabled(self) -> bool:
        return self.lineWrapMode() != QPlainTextEdit.LineWrapMode.NoWrap

    def set_word_wrap_enabled(self, enabled: bool) -> None:
        mode = (
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if bool(enabled)
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.setLineWrapMode(mode)

    def language_id(self) -> str:
        return get_language_id(self._file_path, fallback="plaintext")

    def _dispatch_language_key_press(self, event: QKeyEvent) -> bool:
        try:
            return bool(dispatch_language_key_press(self, event))
        except Exception:
            return False

    def _dispatch_language_mouse_press(self, event: QMouseEvent) -> bool:
        try:
            return bool(dispatch_language_mouse_press(self, event))
        except Exception:
            return False

    def setDocument(self, document):
        super().setDocument(document)
        self._apply_highlighter()
        self._apply_fold_provider()

    def _apply_highlighter(self):
        ensure_editor_highlighter(self)

    def _apply_fold_provider(self):
        provider = get_fold_provider(self.language_id())
        self._fold_provider = provider
        if provider is None:
            self._clear_folding()
            self.updateLineNumberAreaWidth(0)
            self.lineNumberArea.update()
            return
        self._schedule_fold_refresh(immediate=True)

    def _clear_folding(self):
        self._fold_refresh_timer.stop()
        self._fold_ranges = {}
        self._folded_starts = set()
        self._set_all_blocks_visible()
        self._refresh_fold_layout()

    def _schedule_fold_refresh(self, immediate: bool = False):
        if self._fold_provider is None:
            return
        if immediate:
            self._fold_refresh_timer.stop()
            self._refresh_fold_ranges()
            return
        self._fold_refresh_timer.start()

    def _refresh_fold_ranges(self):
        update_editor_folding(self)

    def _set_all_blocks_visible(self):
        block = self.document().firstBlock()
        while block.isValid():
            block.setVisible(True)
            block.setLineCount(1)
            block = block.next()

    def _apply_fold_visibility(self):
        self._set_all_blocks_visible()
        for start_block in sorted(self._folded_starts):
            end_block = self._fold_ranges.get(start_block)
            if end_block is None or end_block <= start_block:
                continue
            block = self.document().findBlockByNumber(start_block).next()
            while block.isValid() and block.blockNumber() <= end_block:
                block.setVisible(False)
                block.setLineCount(0)
                block = block.next()
        self._refresh_fold_layout()

    def _refresh_fold_layout(self):
        doc = self.document()
        try:
            doc.markContentsDirty(0, max(0, doc.characterCount()))
        except Exception:
            pass
        self.viewport().update()
        self.lineNumberArea.update()
        self._apply_viewport_margins()

    def _toggle_fold_at_block(self, block_number: int) -> bool:
        block_no = int(block_number)
        if block_no not in self._fold_ranges:
            return False
        if block_no in self._folded_starts:
            self._folded_starts.discard(block_no)
        else:
            self._folded_starts.add(block_no)
        self._apply_fold_visibility()
        return True

    def _fold_marker_rect(self, top: int, line_height: int) -> QRect:
        marker_size = max(8, min(11, int(line_height) - 3))
        x = 2
        y = int(top + max(0, (line_height - marker_size) // 2))
        return QRect(x, y, marker_size, marker_size)

    def _fold_region_pos_range(self, start_block_no: int) -> tuple[int, int] | None:
        if int(start_block_no) not in self._folded_starts:
            return None

        end_block_no = self._fold_ranges.get(int(start_block_no))
        if end_block_no is None or int(end_block_no) <= int(start_block_no):
            return None

        doc = self.document()
        start_block = doc.findBlockByNumber(int(start_block_no))
        end_block = doc.findBlockByNumber(int(end_block_no))
        if not start_block.isValid() or not end_block.isValid():
            return None

        start_pos = int(start_block.position())
        end_pos = int(end_block.position()) + len(end_block.text())
        if end_pos <= start_pos:
            return None
        return start_pos, end_pos

    def _expand_selection_over_collapsed_folds(self, sel_start: int, sel_end: int) -> tuple[int, int, bool]:
        start = int(sel_start)
        end = int(sel_end)
        if end <= start or not self._folded_starts:
            return start, end, False

        changed_any = False
        while True:
            changed = False
            for block_no in sorted(self._folded_starts):
                region = self._fold_region_pos_range(int(block_no))
                if region is None:
                    continue
                region_start, region_end = region
                header_block = self.document().findBlockByNumber(int(block_no))
                if not header_block.isValid():
                    continue
                header_visible_end = region_start + max(0, int(header_block.length()))
                # If selection touches the visible folded header line, expand to full folded region.
                touches_header = start < header_visible_end and end > region_start
                if not touches_header:
                    continue
                if start > region_start:
                    start = region_start
                    changed = True
                if end < region_end:
                    end = region_end
                    changed = True
            if not changed:
                break
            changed_any = True
        return start, end, changed_any

    def _on_selection_changed_expand_collapsed_folds(self) -> None:
        if self._fold_selection_adjusting:
            return
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return

        anchor = int(cursor.anchor())
        pos = int(cursor.position())
        if anchor == pos:
            return

        sel_start = min(anchor, pos)
        sel_end = max(anchor, pos)
        expanded_start, expanded_end, changed = self._expand_selection_over_collapsed_folds(sel_start, sel_end)
        if not changed:
            return
        if expanded_start == sel_start and expanded_end == sel_end:
            return

        self._fold_selection_adjusting = True
        try:
            adjusted = QTextCursor(self.document())
            if anchor <= pos:
                adjusted.setPosition(expanded_start)
                adjusted.setPosition(expanded_end, QTextCursor.KeepAnchor)
            else:
                adjusted.setPosition(expanded_end)
                adjusted.setPosition(expanded_start, QTextCursor.KeepAnchor)
            self.setTextCursor(adjusted)
        finally:
            self._fold_selection_adjusting = False

    def _block_number_at_y(self, y_pos: int) -> int:
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        while block.isValid() and top <= y_pos:
            if block.isVisible() and bottom >= y_pos:
                return int(block.blockNumber())
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
        return -1

    def update_completion_ui_settings(self, completion_cfg: dict):
        cfg = completion_cfg if isinstance(completion_cfg, dict) else {}
        merged = dict(_COMPLETION_UI_DEFAULTS)
        for key in ("show_signatures", "show_right_label", "show_doc_tooltip", "doc_tooltip_delay_ms"):
            if key in cfg:
                merged[key] = cfg.get(key)

        merged["show_signatures"] = bool(merged.get("show_signatures", True))
        merged["show_right_label"] = bool(merged.get("show_right_label", True))
        merged["show_doc_tooltip"] = bool(merged.get("show_doc_tooltip", True))
        merged["doc_tooltip_delay_ms"] = max(
            120,
            min(1200, int(merged.get("doc_tooltip_delay_ms", 180))),
        )

        self._completion_ui_cfg = merged
        self._completion_doc_tooltip_timer.setInterval(int(merged["doc_tooltip_delay_ms"]))

        if not merged["show_doc_tooltip"]:
            self._completion_doc_tooltip_timer.stop()
            self._completion_pending_doc_row = -1
            self._completion_pending_doc_key = None
            QToolTip.hideText()

        if self.is_completion_popup_visible():
            self._rebuild_completion_popup()
            self._position_completion_popup()

    def update_lint_visual_settings(self, lint_visual_cfg: dict):
        cfg = lint_visual_cfg if isinstance(lint_visual_cfg, dict) else {}
        merged = dict(_LINT_VISUAL_DEFAULTS)
        for key in (
            "mode",
            "error_color",
            "warning_color",
            "info_color",
            "hint_color",
            "squiggle_thickness",
            "line_alpha",
        ):
            if key in cfg:
                merged[key] = cfg.get(key)

        mode = str(merged.get("mode") or "squiggle").strip().lower()
        if mode not in {"squiggle", "line", "both"}:
            mode = "squiggle"
        merged["mode"] = mode
        merged["error_color"] = self._valid_lint_color_hex(merged.get("error_color"), _LINT_VISUAL_DEFAULTS["error_color"])
        merged["warning_color"] = self._valid_lint_color_hex(
            merged.get("warning_color"),
            _LINT_VISUAL_DEFAULTS["warning_color"],
        )
        merged["info_color"] = self._valid_lint_color_hex(merged.get("info_color"), _LINT_VISUAL_DEFAULTS["info_color"])
        merged["hint_color"] = self._valid_lint_color_hex(merged.get("hint_color"), _LINT_VISUAL_DEFAULTS["hint_color"])
        try:
            merged["squiggle_thickness"] = max(1, min(6, int(merged.get("squiggle_thickness", 2))))
        except Exception:
            merged["squiggle_thickness"] = 2
        try:
            merged["line_alpha"] = max(0, min(255, int(merged.get("line_alpha", 64))))
        except Exception:
            merged["line_alpha"] = 64

        if merged == self._lint_visual_cfg:
            return

        self._lint_visual_cfg = merged
        self._rebuild_lint_selections()
        self._rebuild_extra_selections()
        self.viewport().update()
        self._refresh_overview_marker_area()

    @staticmethod
    def _valid_lint_color_hex(value: object, fallback: str) -> str:
        text = str(value or "").strip()
        color = QColor(text)
        if color.isValid():
            return color.name(QColor.HexRgb)
        fb = QColor(str(fallback or "").strip())
        if fb.isValid():
            return fb.name(QColor.HexRgb)
        return "#ff0000"

    def _shutdown_completion_workers(self):
        self._clear_configured_shortcuts()
        self._completion_doc_tooltip_timer.stop()
        self._completion_doc_future_pump.stop()
        self._hover_signature_timer.stop()
        self._hover_signature_future_pump.stop()
        self._occurrence_refresh_timer.stop()
        for fut in list(self._completion_doc_futures.values()):
            try:
                fut.cancel()
            except Exception:
                pass
        self._completion_doc_futures.clear()
        for fut in list(self._hover_signature_futures.values()):
            try:
                fut.cancel()
            except Exception:
                pass
        self._hover_signature_futures.clear()
        try:
            self._completion_doc_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                self._completion_doc_executor.shutdown(wait=False)
            except Exception:
                pass
        try:
            self._hover_signature_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                self._hover_signature_executor.shutdown(wait=False)
            except Exception:
                pass

    def _ensure_tooltip_style(self):
        if CodeEditor._tooltip_style_installed:
            return
        app = QApplication.instance()
        if app is None:
            return
        current = str(app.styleSheet() or "")
        if _TOOLTIP_STYLE_MARKER in current:
            CodeEditor._tooltip_style_installed = True
            return
        app.setStyleSheet((current + "\n" + _TOOLTIP_QSS).strip())
        CodeEditor._tooltip_style_installed = True

    def set_editor_background(
        self,
        *,
        background_color: str | QColor = "#252526",
        background_image_path: str = "",
        background_image_scale_mode: str = "stretch",
        background_image_brightness: int = 100,
        background_tint_color: str | QColor = "#000000",
        background_tint_strength: int = 0,
    ) -> None:
        base = self._resolve_background_color(background_color, "#252526")
        tint = self._resolve_background_color(background_tint_color, "#000000")
        scale_mode = str(background_image_scale_mode or "stretch").strip().lower()
        if scale_mode not in {"stretch", "fit_width", "fit_height", "tile"}:
            scale_mode = "stretch"
        brightness = max(0, min(200, int(background_image_brightness)))
        tint_strength = max(0, min(100, int(background_tint_strength)))
        image_path = str(background_image_path or "").strip()

        source_pixmap: QPixmap | None = None
        if image_path:
            candidate = Path(image_path).expanduser()
            if candidate.exists():
                loaded = QPixmap(str(candidate))
                if not loaded.isNull():
                    source_pixmap = loaded
                    image_path = str(candidate)
                else:
                    image_path = ""
            else:
                image_path = ""

        changed = (
            base != self._editor_background_color
            or tint != self._editor_background_tint_color
            or scale_mode != self._editor_background_scale_mode
            or brightness != self._editor_background_image_brightness
            or tint_strength != self._editor_background_tint_strength
            or image_path != self._editor_background_image_path
            or source_pixmap is not self._editor_background_source_pixmap
        )
        if not changed:
            return

        self._editor_background_color = base
        self._editor_background_tint_color = tint
        self._editor_background_scale_mode = scale_mode
        self._editor_background_image_brightness = brightness
        self._editor_background_tint_strength = tint_strength
        self._editor_background_image_path = image_path
        self._editor_background_source_pixmap = source_pixmap
        self._editor_background_cache_size = QSize()
        self._editor_background_cache_pixmap = None
        self._apply_editor_background_palette()
        self._rebuild_extra_selections()
        self.lineNumberArea.update()
        self.viewport().update()

    @staticmethod
    def _resolve_background_color(value: str | QColor, fallback: str) -> QColor:
        if isinstance(value, QColor):
            color = QColor(value)
        else:
            color = QColor(str(value or "").strip())
        if not color.isValid():
            color = QColor(fallback)
        return color

    def _build_editor_background_pixmap(self, size: QSize) -> QPixmap | None:
        source = self._editor_background_source_pixmap
        if source is None or source.isNull() or size.width() <= 0 or size.height() <= 0:
            return None

        pixmap = QPixmap(size)
        pixmap.fill(self._editor_background_color)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        mode = self._editor_background_scale_mode
        if mode == "tile":
            painter.drawTiledPixmap(pixmap.rect(), source)
        elif mode == "fit_width":
            scaled = source.scaledToWidth(size.width(), Qt.TransformationMode.SmoothTransformation)
            x = int((size.width() - scaled.width()) / 2)
            y = int((size.height() - scaled.height()) / 2)
            painter.drawPixmap(x, y, scaled)
        elif mode == "fit_height":
            scaled = source.scaledToHeight(size.height(), Qt.TransformationMode.SmoothTransformation)
            x = int((size.width() - scaled.width()) / 2)
            y = int((size.height() - scaled.height()) / 2)
            painter.drawPixmap(x, y, scaled)
        else:
            scaled = source.scaled(
                size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(0, 0, scaled)

        brightness = self._editor_background_image_brightness
        if brightness < 100:
            alpha = int(((100 - brightness) / 100.0) * 220)
            if alpha > 0:
                painter.fillRect(pixmap.rect(), QColor(0, 0, 0, min(alpha, 220)))
        elif brightness > 100:
            alpha = int(((brightness - 100) / 100.0) * 180)
            if alpha > 0:
                painter.fillRect(pixmap.rect(), QColor(255, 255, 255, min(alpha, 180)))

        tint_alpha = int((self._editor_background_tint_strength / 100.0) * 255.0)
        if tint_alpha > 0:
            tint = QColor(self._editor_background_tint_color)
            tint.setAlpha(max(0, min(255, tint_alpha)))
            painter.fillRect(pixmap.rect(), tint)

        painter.end()
        return pixmap

    def _background_texture_for_viewport(self) -> QPixmap | None:
        viewport_size = self.viewport().size() if self.viewport() is not None else QSize()
        if viewport_size.width() <= 0 or viewport_size.height() <= 0:
            return None
        if (
            self._editor_background_cache_pixmap is not None
            and self._editor_background_cache_size == viewport_size
        ):
            return self._editor_background_cache_pixmap
        self._editor_background_cache_size = QSize(viewport_size)
        self._editor_background_cache_pixmap = self._build_editor_background_pixmap(viewport_size)
        return self._editor_background_cache_pixmap

    def _apply_editor_background_palette(self) -> None:
        transparent = QColor(0, 0, 0, 0)
        palette = self.palette()
        palette.setColor(QPalette.Base, transparent)
        palette.setColor(QPalette.Window, transparent)
        self.setPalette(palette)
        self.setAutoFillBackground(False)

        viewport = self.viewport()
        if viewport is not None:
            viewport_palette = viewport.palette()
            viewport_palette.setColor(QPalette.Base, transparent)
            viewport_palette.setColor(QPalette.Window, transparent)
            viewport.setPalette(viewport_palette)
            viewport.setAutoFillBackground(False)

    def _paint_editor_background_layer(self, painter: QPainter, rect: QRect) -> None:
        target = self.viewport().rect() if self.viewport() is not None else QRect()
        if target.isEmpty():
            return
        painter.setClipRect(rect)
        texture = self._background_texture_for_viewport()
        if texture is not None:
            painter.drawPixmap(target.topLeft(), texture)
            return
        painter.fillRect(target, self._editor_background_color)

    def _cursor_x(self, line, pos: int) -> float:
        v = line.cursorToX(pos)
        return float(v[0] if isinstance(v, tuple) else v)


    def _swatch_metrics(self):
        fm = QFontMetricsF(self.font())

        # Width of one character cell around '#'
        char_w = max(15.0, fm.horizontalAdvance("#"))

        # Make chip smaller and better centered visually
        chip_h = max(15.0, fm.height() * 0.52)

        # Horizontal fill of the '#' cell (slightly narrower looks cleaner)
        chip_w = max(6.0, char_w * 0.86)

        # Corner rounding
        radius = 0#max(1.5, chip_h * 0.20)

        # Optional subtle nudges for optical centering
        nudge_x = -6
        nudge_y = -2.5  # tiny downward nudge often looks better in code fonts

        return chip_w, chip_h, radius, nudge_x, nudge_y


    def _iter_visible_color_swatch_rects(self):
        """
        Yields tuples:
          (block, match, color_code, QColor, QRectF swatch_rect)
        for all visible color matches.
        """
        chip_w, chip_h, _, nudge_x, nudge_y = self._swatch_metrics()

        block = self.firstVisibleBlock()
        viewport_rect = self.viewport().rect()

        while block.isValid():
            block_geo = self.blockBoundingGeometry(block).translated(self.contentOffset())

            if block_geo.top() > viewport_rect.bottom():
                break

            if block.isVisible() and block_geo.bottom() >= viewport_rect.top():
                text = block.text()
                layout = block.layout()
                if layout is not None:
                    for m in _COLOR_PATTERN.finditer(text):
                        code = m.group(0)
                        c = QColor(code)
                        if not c.isValid():
                            continue

                        # '#' position in block
                        start = m.start()

                        line = layout.lineForTextPosition(start)
                        if not line.isValid():
                            continue

                        line_start = line.textStart()
                        rel = start - line_start

                        x_hash = self._cursor_x(line, rel)

                        # Base position in viewport coordinates
                        vx = block_geo.left() + line.x() + x_hash
                        vy = block_geo.top() + line.y()

                        # Center chip inside the line box
                        y = vy + (line.height() - chip_h) * 0.5 + nudge_y

                        # Center chip inside '#' cell horizontally
                        hash_cell_w = max(7.0, QFontMetricsF(self.font()).horizontalAdvance("#"))
                        x = vx + (hash_cell_w - chip_w) * 0.5 + nudge_x

                        rect = QRectF(x, y, chip_w, chip_h)

                        yield block, m, code, c, rect

            block = block.next()


    def _paint_color_swatches(self, event):
        painter = QPainter(self.viewport())
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            _, _, radius, _, _ = self._swatch_metrics()

            for _block, _match, _code, c, rect in self._iter_visible_color_swatch_rects():
                # fill
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(c))
                painter.drawRoundedRect(rect, radius, radius)

                # subtle outline for dark/light visibility
                lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
                outline = QColor(255, 255, 255, 110) if lum < 70 else QColor(0, 0, 0, 80)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(outline, 1.0))
                painter.drawRoundedRect(rect, radius, radius)

        finally:
            painter.end()


    def _handle_color_click(self, event) -> bool:
        """
        Open color picker only when click lands on a painted swatch rect.
        """
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        click_pt = pos  # viewport coords

        for block, match, old_hex, initial_color, rect in self._iter_visible_color_swatch_rects():
            if not rect.contains(click_pt):
                continue

            dlg = ColorPickerDialog(initial_color, self)
            if dlg.exec() != QDialog.Accepted:
                return True  # click was on swatch, consume it anyway

            new_color = dlg.get_color()

            has_alpha_input = len(old_hex) == 9
            is_transparent = new_color.alpha() < 255

            if has_alpha_input or is_transparent:
                # #RRGGBBAA
                new_hex = (
                    f"#{new_color.red():02X}"
                    f"{new_color.green():02X}"
                    f"{new_color.blue():02X}"
                    f"{new_color.alpha():02X}"
                )
            else:
                new_hex = new_color.name().upper()  # #RRGGBB

            cursor = self.textCursor()
            cursor.setPosition(block.position() + match.start())
            cursor.setPosition(block.position() + match.end(), QTextCursor.KeepAnchor)
            cursor.insertText(new_hex)

            return True

        return False

    # --------- find/replace ---------
    def _search_query(self) -> str:
        return str(self._search_bar.find_edit.text() or "")

    def _replace_query(self) -> str:
        return str(self._search_bar.replace_edit.text() or "")

    def _search_top_margin(self) -> int:
        if not self._search_bar.isVisible():
            return 0
        return max(30, int(self._search_bar.sizeHint().height()))

    def _apply_viewport_margins(self):
        top_margin = self._search_top_margin()
        right_margin = self.overviewMarkerAreaWidth()
        self.setViewportMargins(self.lineNumberAreaWidth(), top_margin, right_margin, 0)
        if hasattr(self, "lineNumberArea") and isinstance(self.lineNumberArea, QWidget):
            cr = self.contentsRect()
            self.lineNumberArea.setGeometry(
                QRect(
                    cr.left(),
                    cr.top() + top_margin,
                    self.lineNumberAreaWidth(),
                    max(0, cr.height() - top_margin),
                )
            )
        if hasattr(self, "overviewMarkerArea") and isinstance(self.overviewMarkerArea, QWidget):
            self._position_overview_marker_area()

    def _position_overview_marker_area(self):
        if not hasattr(self, "overviewMarkerArea") or not isinstance(self.overviewMarkerArea, QWidget):
            return
        width = self.overviewMarkerAreaWidth()
        if width <= 0:
            self.overviewMarkerArea.hide()
            return
        vp = self.viewport().geometry()
        self.overviewMarkerArea.setGeometry(
            QRect(
                vp.right() + 1,
                vp.top(),
                width,
                max(0, vp.height()),
            )
        )
        self.overviewMarkerArea.show()
        self.overviewMarkerArea.raise_()

    def _on_scrollbar_range_changed(self, *_args):
        self._position_overview_marker_area()

    def _position_search_bar(self):
        if not self._search_bar.isVisible():
            return
        cr = self.contentsRect()
        h = self._search_top_margin()
        self._search_bar.setGeometry(cr.left(), cr.top(), cr.width(), h)
        self._search_bar.raise_()

    def show_find_bar(self):
        self._search_bar.set_replace_visible(False)
        if not self._search_bar.isVisible():
            selected = str(self.textCursor().selectedText() or "").replace("\u2029", "\n")
            if selected and "\n" not in selected:
                self._search_bar.find_edit.setText(selected)
            self._search_bar.show()
        self._apply_viewport_margins()
        self._position_search_bar()
        self._search_bar.focus_find()
        self._schedule_search_refresh(immediate=True)

    def show_replace_bar(self):
        self._search_bar.set_replace_visible(True)
        if not self._search_bar.isVisible():
            selected = str(self.textCursor().selectedText() or "").replace("\u2029", "\n")
            if selected and "\n" not in selected:
                self._search_bar.find_edit.setText(selected)
            self._search_bar.show()
        self._apply_viewport_margins()
        self._position_search_bar()
        self._search_bar.focus_find()
        self._schedule_search_refresh(immediate=True)

    def hide_search_bar(self):
        if not self._search_bar.isVisible():
            return
        self._search_refresh_timer.stop()
        self._search_bar.hide()
        self._search_matches = []
        self._search_current_index = -1
        self._search_highlight_selections = []
        self._search_active_selection = None
        self._overview_search_lines = set()
        self._overview_active_search_lines = set()
        self._search_last_error = ""
        self._search_selection_range = None
        self._search_bar.set_count_text("0 / 0")
        self._search_bar.set_in_selection_checked(False)
        self._apply_viewport_margins()
        self._rebuild_extra_selections()
        self._refresh_overview_marker_area()

    def _on_search_query_changed(self, _text: str):
        self._schedule_search_refresh(immediate=False)

    def _on_replace_query_changed(self, _text: str):
        # Replace text does not affect match set; keep current status.
        pass

    def _on_search_option_changed(self, _value):
        if self._search_bar.selection_box.isChecked():
            cursor = self.textCursor()
            if cursor.hasSelection():
                self._search_selection_range = (cursor.selectionStart(), cursor.selectionEnd())
            else:
                self._search_selection_range = None
                self._search_bar.set_in_selection_checked(False)
        else:
            self._search_selection_range = None
        self._schedule_search_refresh(immediate=True)

    def _on_text_changed_search_refresh(self):
        if self._search_bar.isVisible():
            self._schedule_search_refresh(immediate=False)

    def _schedule_occurrence_marker_refresh(self):
        if not bool(self._overview_cfg.get("enabled", True)):
            return
        self._occurrence_refresh_timer.start()

    def _refresh_occurrence_markers(self):
        term, pattern, flags = self._occurrence_pattern_from_cursor()
        if not term or not pattern:
            self._overview_occurrence_term = ""
            self._overview_occurrence_lines = set()
            self._refresh_overview_marker_area()
            return

        source = self.toPlainText()
        if not source:
            self._overview_occurrence_term = ""
            self._overview_occurrence_lines = set()
            self._refresh_overview_marker_area()
            return

        try:
            max_matches = max(1000, int(self._overview_cfg.get("max_occurrence_matches", 12000)))
        except Exception:
            max_matches = 12000

        try:
            regex = re.compile(pattern, flags)
        except Exception:
            self._overview_occurrence_term = ""
            self._overview_occurrence_lines = set()
            self._refresh_overview_marker_area()
            return

        lines: set[int] = set()
        count = 0
        for match in regex.finditer(source):
            start = int(match.start())
            end = int(match.end())
            if end <= start:
                continue
            lines.update(self._line_numbers_for_span(start, end))
            count += 1
            if count >= max_matches:
                break

        self._overview_occurrence_term = term
        self._overview_occurrence_lines = lines
        self._refresh_overview_marker_area()

    def _occurrence_pattern_from_cursor(self) -> tuple[str, str, int]:
        cur = self.textCursor()
        selected = str(cur.selectedText() or "").replace("\u2029", "\n")
        if selected and "\n" not in selected:
            token = selected
            if len(token) < 2:
                return "", "", 0
            if re.match(r"^[A-Za-z_]\w*$", token):
                return token, rf"\b{re.escape(token)}\b", 0
            return token, re.escape(token), 0

        token = self._identifier_token_under_cursor(cur)
        if len(token) < 2:
            return "", "", 0
        return token, rf"\b{re.escape(token)}\b", 0

    def _identifier_token_under_cursor(self, cursor: QTextCursor | None = None) -> str:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        block_text = cur.block().text()
        if not block_text:
            return ""

        col = int(cur.positionInBlock())
        if col >= len(block_text) and col > 0:
            col -= 1
        if col < 0:
            return ""
        if col < len(block_text) and not self._is_identifier_char(block_text[col]):
            if col > 0 and self._is_identifier_char(block_text[col - 1]):
                col -= 1
            else:
                return ""

        start = max(0, min(col, len(block_text)))
        while start > 0 and self._is_identifier_char(block_text[start - 1]):
            start -= 1
        end = start
        while end < len(block_text) and self._is_identifier_char(block_text[end]):
            end += 1
        token = block_text[start:end].strip()
        if not token:
            return ""
        return token

    def _line_numbers_for_span(self, start: int, end: int) -> set[int]:
        out: set[int] = set()
        s = max(0, int(start))
        e = max(s, int(end))
        if e <= s:
            return out
        first = self.document().findBlock(s)
        last = self.document().findBlock(max(s, e - 1))
        if not first.isValid() or not last.isValid():
            return out
        first_line = int(first.blockNumber() + 1)
        last_line = int(last.blockNumber() + 1)
        for line_no in range(first_line, last_line + 1):
            out.add(line_no)
        return out

    def _schedule_search_refresh(self, *, immediate: bool):
        if not self._search_bar.isVisible():
            return
        self._search_refresh_timer.stop()
        if immediate:
            self._refresh_search_matches()
            return
        self._search_refresh_timer.start()

    def _search_range(self, text_length: int) -> tuple[int, int]:
        if not self._search_bar.selection_box.isChecked() or self._search_selection_range is None:
            return 0, text_length
        start, end = self._search_selection_range
        start = max(0, min(int(start), text_length))
        end = max(0, min(int(end), text_length))
        if end <= start:
            return 0, text_length
        return start, end

    def _compile_search_pattern(self):
        query = self._search_query()
        if not query:
            return None
        flags = 0 if self._search_bar.case_box.isChecked() else re.IGNORECASE
        if self._search_bar.regex_box.isChecked():
            pattern = query
        else:
            pattern = re.escape(query)
        if self._search_bar.word_box.isChecked():
            pattern = rf"\b(?:{pattern})\b"
        try:
            return re.compile(pattern, flags)
        except re.error as exc:
            self._search_last_error = str(exc)
            return None

    def _refresh_search_matches(self):
        if not self._search_bar.isVisible():
            return
        self._search_last_error = ""
        query = self._search_query()
        if not query:
            self._search_matches = []
            self._search_current_index = -1
            self._search_highlight_selections = []
            self._search_active_selection = None
            self._overview_search_lines = set()
            self._overview_active_search_lines = set()
            self._search_bar.set_count_text("0 / 0")
            self._rebuild_extra_selections()
            self._refresh_overview_marker_area()
            return

        pattern = self._compile_search_pattern()
        if pattern is None:
            self._search_matches = []
            self._search_current_index = -1
            self._search_highlight_selections = []
            self._search_active_selection = None
            self._overview_search_lines = set()
            self._overview_active_search_lines = set()
            self._search_bar.set_count_text("0 / 0")
            self._rebuild_extra_selections()
            self._refresh_overview_marker_area()
            return

        source = self.toPlainText()
        start, end = self._search_range(len(source))
        segment = source[start:end]

        matches: list[tuple[int, int]] = []
        for m in pattern.finditer(segment):
            s = start + int(m.start())
            e = start + int(m.end())
            if e <= s:
                continue
            matches.append((s, e))
            if len(matches) >= 10000:
                break
        self._search_matches = matches
        self._refresh_search_marker_lines()
        self._refresh_search_current_index()

    def _search_index_for_cursor(self) -> int:
        if not self._search_matches:
            return -1
        cur = self.textCursor()
        if cur.hasSelection():
            ss = cur.selectionStart()
            se = cur.selectionEnd()
            for idx, (start, end) in enumerate(self._search_matches):
                if start == ss and end == se:
                    return idx
        pos = cur.position()
        best_idx = -1
        for idx, (start, end) in enumerate(self._search_matches):
            if start <= pos <= end:
                return idx
            if start < pos:
                best_idx = idx
            else:
                break
        return best_idx

    def _refresh_search_current_index(self):
        if not self._search_bar.isVisible():
            return
        self._search_current_index = self._search_index_for_cursor()

        hl: list[QTextEdit.ExtraSelection] = []
        for start, end in self._search_matches[:3000]:
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(self.document())
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.KeepAnchor)
            sel.cursor = cur
            sel.format.setBackground(QColor(74, 92, 126, 110))
            hl.append(sel)
        self._search_highlight_selections = hl

        self._search_active_selection = None
        self._overview_active_search_lines = set()
        if 0 <= self._search_current_index < len(self._search_matches):
            start, end = self._search_matches[self._search_current_index]
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(self.document())
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.KeepAnchor)
            sel.cursor = cur
            sel.format.setBackground(QColor(214, 168, 83, 170))
            self._search_active_selection = sel
            self._overview_active_search_lines = self._line_numbers_for_span(start, end)
            self._search_bar.set_count_text(f"{self._search_current_index + 1} / {len(self._search_matches)}")
        elif self._search_last_error:
            self._search_bar.set_count_text("0 / 0")
        else:
            self._search_bar.set_count_text(f"0 / {len(self._search_matches)}")

        self._rebuild_extra_selections()
        self._refresh_overview_marker_area()

    def _refresh_search_marker_lines(self):
        lines: set[int] = set()
        for start, end in self._search_matches[:5000]:
            lines.update(self._line_numbers_for_span(start, end))
        self._overview_search_lines = lines

    def _goto_search_index(self, idx: int):
        if idx < 0 or idx >= len(self._search_matches):
            return
        start, end = self._search_matches[idx]
        cur = self.textCursor()
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        self.setTextCursor(cur)
        self.centerCursor()
        self._search_current_index = idx
        self._refresh_search_current_index()

    def search_next(self):
        if not self._search_bar.isVisible():
            self.show_find_bar()
            return
        if not self._search_matches:
            self._refresh_search_matches()
            if not self._search_matches:
                return

        if self._search_current_index >= 0:
            idx = (self._search_current_index + 1) % len(self._search_matches)
            self._goto_search_index(idx)
            return

        pos = self.textCursor().position()
        for idx, (start, _end) in enumerate(self._search_matches):
            if start >= pos:
                self._goto_search_index(idx)
                return
        self._goto_search_index(0)

    def search_previous(self):
        if not self._search_bar.isVisible():
            self.show_find_bar()
            return
        if not self._search_matches:
            self._refresh_search_matches()
            if not self._search_matches:
                return

        if self._search_current_index >= 0:
            idx = (self._search_current_index - 1) % len(self._search_matches)
            self._goto_search_index(idx)
            return

        pos = self.textCursor().position()
        for idx in range(len(self._search_matches) - 1, -1, -1):
            start, _end = self._search_matches[idx]
            if start <= pos:
                self._goto_search_index(idx)
                return
        self._goto_search_index(len(self._search_matches) - 1)

    def _replacement_text_for_span(self, start: int, end: int) -> str:
        source = self.toPlainText()
        selected = source[start:end]
        if self._search_bar.regex_box.isChecked():
            pattern = self._compile_search_pattern()
            if pattern is not None:
                m = pattern.match(selected)
                if m is not None:
                    return str(m.expand(self._replace_query()))
        return self._replace_query()

    def replace_current_or_next(self):
        if not self._search_bar.isVisible():
            self.show_replace_bar()
            return
        if not self._search_matches:
            self._refresh_search_matches()
            if not self._search_matches:
                return

        idx = self._search_index_for_cursor()
        if idx < 0:
            pos = self.textCursor().position()
            idx = 0
            for i, (start, _end) in enumerate(self._search_matches):
                if start >= pos:
                    idx = i
                    break

        start, end = self._search_matches[idx]
        repl = self._replacement_text_for_span(start, end)
        cur = self.textCursor()
        cur.beginEditBlock()
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        cur.insertText(repl)
        cur.endEditBlock()
        self.setTextCursor(cur)
        self._schedule_search_refresh(immediate=True)
        self.search_next()

    def replace_all_matches(self):
        if not self._search_bar.isVisible():
            self.show_replace_bar()
            return
        if not self._search_matches:
            self._refresh_search_matches()
            if not self._search_matches:
                return

        cur = self.textCursor()
        cur.beginEditBlock()
        for start, end in reversed(self._search_matches):
            repl = self._replacement_text_for_span(start, end)
            span_cursor = QTextCursor(self.document())
            span_cursor.setPosition(start)
            span_cursor.setPosition(end, QTextCursor.KeepAnchor)
            span_cursor.insertText(repl)
        cur.endEditBlock()
        self._schedule_search_refresh(immediate=True)

    def _symbol_payload_from_cursor(self, cursor: QTextCursor | None = None) -> dict | None:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        block_text = cur.block().text()
        if not block_text:
            return None

        col = int(cur.positionInBlock())
        if cur.hasSelection():
            selected = str(cur.selectedText() or "").replace("\u2029", "")
            if selected and all(self._is_identifier_char(ch) for ch in selected):
                col = int(cur.selectionStart() - cur.block().position())
        else:
            if col >= len(block_text) and col > 0:
                col -= 1
            if col < 0:
                return None
            if col < len(block_text) and not self._is_identifier_char(block_text[col]):
                if col > 0 and self._is_identifier_char(block_text[col - 1]):
                    col -= 1
                else:
                    return None

        start = max(0, min(col, len(block_text)))
        while start > 0 and self._is_identifier_char(block_text[start - 1]):
            start -= 1
        end = start
        while end < len(block_text) and self._is_identifier_char(block_text[end]):
            end += 1
        symbol = block_text[start:end].strip()
        if not symbol:
            return None
        return {
            "symbol": symbol,
            "line": int(cur.blockNumber() + 1),
            "column": int(start),
        }

    def request_definition(self, trigger: str = "shortcut", cursor: QTextCursor | None = None) -> bool:
        payload = self._symbol_payload_from_cursor(cursor)
        if payload is None:
            return False
        payload["trigger"] = str(trigger or "shortcut")
        self.definitionRequested.emit(payload)
        return True

    def request_usages(self, trigger: str = "shortcut", cursor: QTextCursor | None = None) -> bool:
        payload = self._symbol_payload_from_cursor(cursor)
        if payload is None:
            return False
        payload["trigger"] = str(trigger or "shortcut")
        self.usagesRequested.emit(payload)
        return True

    def request_quick_fix(self, trigger: str = "shortcut", cursor: QTextCursor | None = None) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        payload = {
            "trigger": str(trigger or "shortcut"),
            "line": int(cur.blockNumber() + 1),
            "column": int(cur.positionInBlock() + 1),
            "cursor_pos": int(cur.position()),
        }
        self.quickFixRequested.emit(payload)
        return True

    def request_rename(self, trigger: str = "shortcut", cursor: QTextCursor | None = None) -> bool:
        payload = self._symbol_payload_from_cursor(cursor)
        if payload is None:
            return False
        payload["trigger"] = str(trigger or "shortcut")
        self.renameRequested.emit(payload)
        return True

    def _selection_payload(self, cursor: QTextCursor | None = None) -> dict | None:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        if not cur.hasSelection():
            return None
        start = int(cur.selectionStart())
        end = int(cur.selectionEnd())
        if end <= start:
            return None
        text = self.toPlainText()
        selected = text[start:end]
        if not selected.strip():
            return None
        return {
            "selection_start": start,
            "selection_end": end,
            "selected_text": selected,
        }

    def request_extract_variable(self, trigger: str = "shortcut", cursor: QTextCursor | None = None) -> bool:
        payload = self._selection_payload(cursor)
        if payload is None:
            return False
        payload["trigger"] = str(trigger or "shortcut")
        self.extractVariableRequested.emit(payload)
        return True

    def request_extract_method(self, trigger: str = "shortcut", cursor: QTextCursor | None = None) -> bool:
        payload = self._selection_payload(cursor)
        if payload is None:
            return False
        payload["trigger"] = str(trigger or "shortcut")
        self.extractMethodRequested.emit(payload)
        return True

    # --------- line number stuff (unchanged from your version) ---------
    def lineNumberAreaWidth(self):
        digits = 1
        max_num = max(1, self.blockCount())
        while max_num >= 10:
            max_num //= 10
            digits += 1
        space = 3 + self.fontMetrics().horizontalAdvance("9") * digits
        if self._fold_provider is not None:
            space += int(self._fold_gutter_width)
        return space

    def overviewMarkerAreaWidth(self) -> int:
        if not bool(self._overview_cfg.get("enabled", True)):
            return 0
        try:
            return max(6, int(self._overview_cfg.get("width", 10)))
        except Exception:
            return 10

    def updateLineNumberAreaWidth(self, _):
        self._apply_viewport_margins()
        self._refresh_overview_marker_area()

    def updateLineNumberArea(self, rect, dy):
        if dy:
            self.lineNumberArea.scroll(0, dy)
            if self.viewport() is not None:
                self.viewport().update()
        else:
            self.lineNumberArea.update(
                0, rect.y(), self.lineNumberArea.width(), rect.height()
            )
            if hasattr(self, "overviewMarkerArea") and isinstance(self.overviewMarkerArea, QWidget):
                self.overviewMarkerArea.update(0, rect.y(), self.overviewMarkerArea.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.updateLineNumberAreaWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_viewport_margins()
        self._editor_background_cache_size = QSize()
        self._editor_background_cache_pixmap = None
        self._apply_editor_background_palette()
        self._position_search_bar()
        if self.is_completion_popup_visible():
            self._position_completion_popup()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_viewport_margins)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if dx != 0 or dy != 0:
            self.viewport().update()

    def paintEvent(self, event):
        background_painter = QPainter(self.viewport())
        self._paint_editor_background_layer(background_painter, event.rect())
        background_painter.end()
        super().paintEvent(event)
        self._paint_lint_squiggles(event)
        self._paint_color_swatches(event)
        self._paint_inline_suggestion()

    def focusOutEvent(self, event):
        self.hide_completion_popup()
        self.clear_inline_suggestion()
        self._clear_hover_signature_tooltip()
        super().focusOutEvent(event)

    def eventFilter(self, watched, event):
        if watched is self._completion_popup.viewport():
            et = event.type()
            if et == QEvent.MouseMove and self.is_completion_popup_visible():
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                idx = self._completion_popup.indexAt(pos)
                row = idx.row() if idx.isValid() else -1
                global_pos = self._completion_popup.viewport().mapToGlobal(pos + QPoint(16, 14))
                self._schedule_completion_doc_tooltip(row, global_pos)
            elif et == QEvent.Hide:
                self._schedule_completion_doc_tooltip(-1, QPoint())
        elif watched is self.viewport():
            et = event.type()
            if et == QEvent.MouseMove:
                if self.is_completion_popup_visible():
                    self._hover_signature_timer.stop()
                    self._hover_signature_pending_label = ""
                    self._hover_signature_pending_key = None
                else:
                    pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                    label, global_pos, line, call_col = self._hover_call_target_at(pos)
                    if label:
                        self._schedule_hover_signature_tooltip(label, global_pos, line, call_col)
                    else:
                        self._clear_hover_signature_tooltip()
            elif et == QEvent.Hide:
                self._clear_hover_signature_tooltip()
        return super().eventFilter(watched, event)

    def lineNumberAreaPaintEvent(self, event):
        painter = QPainter(self.lineNumberArea)
        gutter = QColor(self._editor_background_color)
        if gutter.lightness() < 128:
            gutter = gutter.darker(125)
        else:
            gutter = gutter.darker(108)
        painter.fillRect(event.rect(), gutter)

        block = self.firstVisibleBlock()
        blockNumber = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(blockNumber + 1)
                number_color = QColor(gutter)
                if number_color.lightness() < 128:
                    number_color = number_color.lighter(155)
                else:
                    number_color = number_color.darker(155)
                painter.setPen(number_color)
                number_left = int(self._fold_gutter_width if self._fold_provider is not None else 0)
                painter.drawText(
                    number_left,
                    int(top),
                    max(0, self.lineNumberArea.width() - number_left - 2),
                    self.fontMetrics().height(),
                    Qt.AlignRight,
                    number,
                )
                if self._fold_provider is not None and blockNumber in self._fold_ranges:
                    marker = self._fold_marker_rect(int(top), self.fontMetrics().height())
                    marker_color = QColor(number_color)
                    marker_color.setAlpha(220)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(marker_color)
                    if blockNumber in self._folded_starts:
                        pts = [
                            QPoint(marker.left(), marker.top()),
                            QPoint(marker.left(), marker.bottom()),
                            QPoint(marker.right(), marker.center().y()),
                        ]
                    else:
                        pts = [
                            QPoint(marker.left(), marker.top()),
                            QPoint(marker.right(), marker.top()),
                            QPoint(marker.center().x(), marker.bottom()),
                        ]
                    painter.drawPolygon(QPolygon(pts))
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            blockNumber += 1

    def lineNumberAreaMousePressEvent(self, event):
        if self.is_completion_popup_visible():
            self.hide_completion_popup()
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        if self._fold_provider is None:
            event.ignore()
            return

        fold_gutter = int(self._fold_gutter_width)
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if int(point.x()) > fold_gutter:
            event.ignore()
            return

        block_number = self._block_number_at_y(int(point.y()))
        if block_number < 0 or block_number not in self._fold_ranges:
            event.ignore()
            return

        if self._toggle_fold_at_block(block_number):
            event.accept()
            return
        event.ignore()

    def overviewMarkerAreaPaintEvent(self, event):
        if not hasattr(self, "overviewMarkerArea") or not isinstance(self.overviewMarkerArea, QWidget):
            return
        painter = QPainter(self.overviewMarkerArea)
        base = QColor(self._editor_background_color)
        if base.lightness() < 128:
            base = base.lighter(110)
        else:
            base = base.darker(106)
        base.setAlpha(210)
        painter.fillRect(event.rect(), base)
        border = QColor(base)
        border.setAlpha(255)
        painter.setPen(border)
        painter.drawLine(0, 0, 0, max(0, self.overviewMarkerArea.height() - 1))

        content_h = int(self.overviewMarkerArea.height())
        if content_h <= 0:
            return
        total_lines = max(1, int(self.blockCount()))
        marker_w = max(2, int(self.overviewMarkerArea.width()) - 2)
        x = 1
        self._paint_overview_line_set(
            painter,
            self._overview_occurrence_lines,
            color=QColor(str(self._overview_cfg.get("occurrence_color", "#66A86A"))),
            x=x,
            width=marker_w,
            total_lines=total_lines,
            content_h=content_h,
        )
        self._paint_overview_line_set(
            painter,
            self._overview_search_lines,
            color=QColor(str(self._overview_cfg.get("search_color", "#4A8FD8"))),
            x=x,
            width=marker_w,
            total_lines=total_lines,
            content_h=content_h,
        )
        self._paint_overview_line_set(
            painter,
            self._overview_active_search_lines,
            color=QColor(str(self._overview_cfg.get("search_active_color", "#D6A853"))),
            x=x,
            width=marker_w,
            total_lines=total_lines,
            content_h=content_h,
        )
        for severity in ("info", "hint", "warning", "error"):
            lines = self._overview_lint_lines_for_severity(severity)
            if not lines:
                continue
            self._paint_overview_line_set(
                painter,
                lines,
                color=self._lint_underline_color(severity),
                x=x,
                width=marker_w,
                total_lines=total_lines,
                content_h=content_h,
            )

    @staticmethod
    def _paint_overview_line_set(
        painter: QPainter,
        lines: set[int],
        *,
        color: QColor,
        x: int,
        width: int,
        total_lines: int,
        content_h: int,
    ) -> None:
        if not lines or total_lines <= 0 or content_h <= 0 or width <= 0:
            return
        if not color.isValid():
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        line_max = max(1, total_lines - 1)
        max_y = max(0, content_h - 2)
        for line in lines:
            ln = max(1, min(int(line), total_lines))
            ratio = 0.0 if line_max <= 0 else (float(ln - 1) / float(line_max))
            y = int(round(ratio * float(max_y)))
            painter.drawRect(x, y, width, 2)

    def overviewMarkerAreaMousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        h = max(1, int(self.overviewMarkerArea.height()))
        y = int(event.position().y()) if hasattr(event, "position") else int(event.pos().y())
        y = max(0, min(y, h - 1))
        line_count = max(1, int(self.blockCount()))
        ratio = float(y) / float(max(1, h - 1))
        line_no = int(round(ratio * float(max(0, line_count - 1)))) + 1
        block = self.document().findBlockByNumber(max(0, line_no - 1))
        if not block.isValid():
            event.ignore()
            return
        cursor = self.textCursor()
        cursor.setPosition(int(block.position()))
        self.setTextCursor(cursor)
        self.centerCursor()
        event.accept()

    def _overview_lint_lines_for_severity(self, severity: str) -> set[int]:
        sev = str(severity or "").lower()
        out: set[int] = set()
        for line, line_sev in self._lint_line_severity.items():
            if str(line_sev or "").lower() != sev:
                continue
            out.add(max(1, int(line)))
        return out

    def _refresh_overview_marker_area(self) -> None:
        if hasattr(self, "overviewMarkerArea") and isinstance(self.overviewMarkerArea, QWidget):
            self.overviewMarkerArea.update()

    def highlightCurrentLine(self):
        if self._search_bar.isVisible():
            self._refresh_search_current_index()
            return
        self._rebuild_extra_selections()

    def set_lint_diagnostics(self, diagnostics: list[dict]):
        normalized: list[dict] = []
        line_severity: dict[int, str] = {}
        for item in diagnostics or []:
            if not isinstance(item, dict):
                continue
            try:
                line = int(item.get("line") or 0)
                col = int(item.get("column") or 1)
            except Exception:
                continue
            if line <= 0:
                continue
            sev = str(item.get("severity") or "warning").lower()
            try:
                end_line = int(item.get("end_line") or line)
            except Exception:
                end_line = line
            try:
                end_col = int(item.get("end_column") or item.get("end_col") or (col + 1))
            except Exception:
                end_col = col + 1

            normalized.append(
                {
                    "line": max(1, line),
                    "column": max(1, col),
                    "end_line": max(1, end_line),
                    "end_column": max(1, end_col),
                    "severity": sev,
                }
            )
            prev = line_severity.get(line)
            if prev is None or self._severity_rank(sev) > self._severity_rank(prev):
                line_severity[line] = sev

        self._lint_diagnostics = normalized
        self._lint_line_severity = line_severity
        self._rebuild_lint_selections()
        self._rebuild_extra_selections()
        self.viewport().update()
        self._refresh_overview_marker_area()

    def clear_lint_diagnostics(self):
        self._lint_diagnostics = []
        self._lint_line_severity = {}
        self._lint_selections = []
        self._rebuild_extra_selections()
        self.viewport().update()
        self._refresh_overview_marker_area()

    def _severity_rank(self, severity: str) -> int:
        if severity == "error":
            return 3
        if severity == "warning":
            return 2
        return 1

    def _rebuild_lint_selections(self):
        selections: list[QTextEdit.ExtraSelection] = []
        if self._lint_visual_mode() in {"line", "both"}:
            for line, severity in sorted(self._lint_line_severity.items()):
                block = self.document().findBlockByNumber(line - 1)
                if not block.isValid():
                    continue
                sel = QTextEdit.ExtraSelection()
                cursor = QTextCursor(block)
                cursor.clearSelection()
                sel.cursor = cursor
                sel.format.setProperty(QTextFormat.FullWidthSelection, True)
                sel.format.setBackground(self._lint_line_background_color(severity))
                selections.append(sel)
        self._lint_selections = selections

    def _document_position_for_line_column(self, line: int, column: int) -> int:
        block = self.document().findBlockByNumber(max(0, int(line) - 1))
        if not block.isValid():
            return -1
        text = block.text()
        col0 = max(0, int(column) - 1)
        col0 = min(col0, len(text))
        return int(block.position() + col0)

    def _lint_underline_color(self, severity: str) -> QColor:
        return QColor(self._lint_color_hex_for_severity(severity))

    def _lint_line_background_color(self, severity: str) -> QColor:
        color = QColor(self._lint_color_hex_for_severity(severity))
        try:
            alpha = int(self._lint_visual_cfg.get("line_alpha", 64))
        except Exception:
            alpha = 64
        color.setAlpha(max(0, min(255, alpha)))
        return color

    def _lint_color_hex_for_severity(self, severity: str) -> str:
        sev = str(severity or "").lower()
        if sev == "error":
            return str(self._lint_visual_cfg.get("error_color") or _LINT_VISUAL_DEFAULTS["error_color"])
        if sev == "warning":
            return str(self._lint_visual_cfg.get("warning_color") or _LINT_VISUAL_DEFAULTS["warning_color"])
        if sev == "hint":
            return str(self._lint_visual_cfg.get("hint_color") or _LINT_VISUAL_DEFAULTS["hint_color"])
        return str(self._lint_visual_cfg.get("info_color") or _LINT_VISUAL_DEFAULTS["info_color"])

    def _lint_visual_mode(self) -> str:
        mode = str(self._lint_visual_cfg.get("mode") or "squiggle").strip().lower()
        if mode not in {"squiggle", "line", "both"}:
            return "squiggle"
        return mode

    def _paint_lint_squiggles(self, event) -> None:
        if self._lint_visual_mode() not in {"squiggle", "both"}:
            return
        if not self._lint_diagnostics:
            return
        visible_first, visible_last = self._visible_line_range()
        if visible_last < visible_first:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(event.rect())

        try:
            thickness = max(1, min(6, int(self._lint_visual_cfg.get("squiggle_thickness", 2))))
        except Exception:
            thickness = 2
        amplitude = 1.4 + (float(thickness) * 0.32)
        step = 3.8

        for diag in self._lint_diagnostics:
            if not isinstance(diag, dict):
                continue
            severity = str(diag.get("severity") or "warning").lower()
            pen = QPen(self._lint_underline_color(severity))
            pen.setWidth(max(1, int(thickness)))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)

            try:
                start_line = max(1, int(diag.get("line") or 1))
                start_col = max(1, int(diag.get("column") or 1))
                end_line = max(1, int(diag.get("end_line") or start_line))
                end_col = max(1, int(diag.get("end_column") or (start_col + 1)))
            except Exception:
                continue

            if end_line < start_line or (end_line == start_line and end_col <= start_col):
                end_line = start_line
                end_col = start_col + 1

            if end_line < visible_first or start_line > visible_last:
                continue

            draw_start = max(start_line, visible_first)
            draw_end = min(end_line, visible_last)
            for line in range(draw_start, draw_end + 1):
                block = self.document().findBlockByNumber(line - 1)
                if not block.isValid() or not block.isVisible():
                    continue
                block_text = block.text()
                block_len = len(block_text)
                seg_start_col = start_col if line == start_line else 1
                seg_end_col = end_col if line == end_line else (block_len + 1)
                seg_start_col = max(1, min(seg_start_col, block_len + 1))
                seg_end_col = max(seg_start_col + 1, min(seg_end_col, block_len + 1))

                start_pos = self._document_position_for_line_column(line, seg_start_col)
                end_pos = self._document_position_for_line_column(line, seg_end_col)
                if start_pos < 0 or end_pos < 0:
                    continue

                start_cursor = QTextCursor(self.document())
                start_cursor.setPosition(start_pos)
                end_cursor = QTextCursor(self.document())
                end_cursor.setPosition(end_pos)
                start_rect = self.cursorRect(start_cursor)
                end_rect = self.cursorRect(end_cursor)
                x1 = float(start_rect.left())
                x2 = float(end_rect.left())
                if x2 <= x1:
                    x2 = x1 + float(max(4, self.fontMetrics().horizontalAdvance(" ")))
                y = float(start_rect.bottom() - 1)
                self._draw_wave_segment(painter, x1, x2, y, amplitude=amplitude, step=step)

        painter.end()

    @staticmethod
    def _draw_wave_segment(painter: QPainter, x1: float, x2: float, y: float, *, amplitude: float, step: float) -> None:
        if x2 <= x1:
            return
        path = QPainterPath(QPointF(x1, y))
        x = float(x1)
        up = True
        while x < x2:
            nx = min(x2, x + step)
            mid = (x + nx) / 2.0
            if up:
                path.quadTo(QPointF(mid, y - amplitude), QPointF(nx, y))
            else:
                path.quadTo(QPointF(mid, y + amplitude), QPointF(nx, y))
            up = not up
            x = nx
        painter.drawPath(path)

    def _visible_line_range(self) -> tuple[int, int]:
        block = self.firstVisibleBlock()
        if not block.isValid():
            return (1, 0)
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        first = int(block.blockNumber() + 1)
        last = first
        viewport_bottom = float(self.viewport().rect().bottom())
        while block.isValid() and top <= viewport_bottom:
            if block.isVisible():
                last = int(block.blockNumber() + 1)
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
        return (first, last)

    def _rebuild_extra_selections(self):
        extraSelections = list(self._lint_selections)
        extraSelections.extend(self._search_highlight_selections)
        if self._search_active_selection is not None:
            extraSelections.append(self._search_active_selection)
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            lineColor = QColor(self._editor_background_color)
            if lineColor.lightness() < 128:
                lineColor = lineColor.lighter(130)
            else:
                lineColor = lineColor.darker(112)
            lineColor.setAlpha(140)
            selection.format.setBackground(lineColor)
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extraSelections.append(selection)
        self.setExtraSelections(extraSelections)

    # --------- inline AI ghost text ---------
    def has_inline_suggestion(self) -> bool:
        return bool(self._inline_suggestion_text)

    def clear_inline_suggestion(self) -> None:
        if not self._inline_suggestion_text:
            return
        self._inline_suggestion_text = ""
        self._inline_suggestion_anchor_pos = -1
        self._inline_suggestion_anchor_revision = -1
        self.viewport().update()

    def set_inline_suggestion(self, text: str) -> None:
        value = str(text or "").replace("\r", "")
        if "\n" not in value and "\\n" in value:
            value = value.replace("\\n", "\n")
        if not value.strip():
            self.clear_inline_suggestion()
            return
        self._inline_suggestion_text = value
        self._inline_suggestion_anchor_pos = int(self.textCursor().position())
        self._inline_suggestion_anchor_revision = int(self.document().revision())
        self.viewport().update()

    def accept_inline_suggestion(self) -> bool:
        if not self._inline_suggestion_text:
            return False
        text = self._dedupe_ai_suggestion_for_cursor(self._inline_suggestion_text)
        self.clear_inline_suggestion()
        if not text:
            return True
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)
        self.inlineSuggestionAccepted.emit(text)
        return True

    def _dedupe_ai_suggestion_for_cursor(self, suggestion: str) -> str:
        text = str(suggestion or "").replace("\r", "")
        if not text:
            return ""
        cursor = self.textCursor()
        source = self.toPlainText()
        pos = int(cursor.position())
        if pos <= 0:
            return text

        left = source[max(0, pos - 400):pos]
        if not left:
            return text

        overlap = 0
        max_overlap = min(len(text), len(left), 160)
        for k in range(max_overlap, 0, -1):
            if left.endswith(text[:k]):
                overlap = k
                break
        if overlap > 0:
            text = text[overlap:]
        return text

    def _on_cursor_moved_inline_suggestion(self) -> None:
        if not self._inline_suggestion_text:
            return
        if int(self.textCursor().position()) != int(self._inline_suggestion_anchor_pos):
            self.clear_inline_suggestion()

    def _paint_inline_suggestion(self) -> None:
        if not self._inline_suggestion_text:
            return
        if self.is_completion_popup_visible():
            return
        if int(self.document().revision()) != int(self._inline_suggestion_anchor_revision):
            self.clear_inline_suggestion()
            return
        if int(self.textCursor().position()) != int(self._inline_suggestion_anchor_pos):
            return

        rect = self.cursorRect()
        fm = self.fontMetrics()
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(self.palette().color(QPalette.PlaceholderText))
        color.setAlpha(180)
        lines = self._inline_suggestion_text.split("\n")
        if not lines:
            return

        # Single-line suggestions stay inline beside the cursor.
        if len(lines) == 1:
            preview = lines[0]
            if not preview.strip():
                return
            y = int(rect.top() + max(0, (rect.height() - fm.height()) // 2) + fm.ascent())
            x = int(rect.left())
            max_w = max(8, self.viewport().width() - x - 8)
            text = fm.elidedText(preview, Qt.TextElideMode.ElideRight, max_w)
            painter.setPen(color)
            painter.drawText(x, y, text)
            return

        preview_lines = list(lines[:10])
        if len(lines) > 10:
            preview_lines[-1] = f"{preview_lines[-1]} ..."

        line_h = max(int(fm.lineSpacing()), int(rect.height()))
        pad_x = 6
        pad_y = 4
        x = max(0, int(rect.left()))
        max_panel_w = max(100, self.viewport().width() - x - 8)

        widest = 0
        for line in preview_lines:
            widest = max(widest, int(fm.horizontalAdvance(line)))
        panel_w = min(max_panel_w, widest + (pad_x * 2))
        text_w = max(20, panel_w - (pad_x * 2))
        draw_lines = [
            fm.elidedText(str(line or ""), Qt.TextElideMode.ElideRight, text_w)
            for line in preview_lines
        ]

        panel_h = (line_h * len(draw_lines)) + (pad_y * 2)
        y = int(rect.bottom() + 2)
        if y + panel_h > self.viewport().height():
            y = max(0, int(rect.top() - panel_h - 2))

        panel_rect = QRect(x, y, panel_w, panel_h)
        bg = QColor(self.palette().color(QPalette.Base))
        bg.setAlpha(224)
        border = QColor(self.palette().color(QPalette.Mid))
        border.setAlpha(200)

        painter.setPen(border)
        painter.setBrush(bg)
        painter.drawRoundedRect(panel_rect, 4, 4)

        painter.setPen(color)
        base_x = panel_rect.left() + pad_x
        base_y = panel_rect.top() + pad_y + fm.ascent()
        for idx, line in enumerate(draw_lines):
            painter.drawText(base_x, base_y + (idx * line_h), line)

    # --------- completion popup ---------
    def completion_context(self) -> dict:
        cursor = self.textCursor()
        abs_pos = cursor.position()
        col = cursor.positionInBlock()
        line = cursor.blockNumber() + 1
        text = self.toPlainText()

        start = abs_pos
        while start > 0 and self._is_identifier_char(text[start - 1]):
            start -= 1
        prefix = text[start:abs_pos]

        prev_char = text[abs_pos - 1] if abs_pos > 0 else ""
        return {
            "line": int(line),
            "column": int(col),
            "prefix": prefix,
            "prefix_start": int(start),
            "cursor_pos": int(abs_pos),
            "previous_char": prev_char,
        }

    def is_completion_popup_visible(self) -> bool:
        return self._completion_popup.isVisible()

    def set_completion_ai_suggestion(self, text: str) -> None:
        value = str(text or "").replace("\r", "")
        if "\n" not in value and "\\n" in value:
            value = value.replace("\\n", "\n")
        if not value.strip():
            self.clear_completion_ai_suggestion()
            return

        preview = _first_nonempty_line(value) or "AI suggestion"
        if len(preview) > 120:
            preview = preview[:117] + "..."
        self._completion_ai_item = {
            "label": f"AI: {preview}",
            "insert_text": value,
            "kind": "ai",
            "source_label": "AI",
            "source_scope": "ai",
            "detail": "AI inline continuation",
            "doc": "AI inline continuation",
            "is_ai_suggestion": True,
            "anchor_revision": int(self.document().revision()),
            "anchor_pos": int(self.textCursor().position()),
        }
        if self.is_completion_popup_visible():
            self._rebuild_completion_popup()
            if self._completion_popup.count() > 0:
                self._completion_popup.setCurrentRow(0)
                self._position_completion_popup()

    def clear_completion_ai_suggestion(self) -> None:
        if self._completion_ai_item is None:
            return
        self._completion_ai_item = None
        if self.is_completion_popup_visible():
            row_before = self._completion_popup.currentRow()
            self._rebuild_completion_popup()
            if self.is_completion_popup_visible():
                if self._completion_popup.count() > 0 and row_before >= 0:
                    self._completion_popup.setCurrentRow(min(row_before, self._completion_popup.count() - 1))
                self._position_completion_popup()

    def _completion_items_for_popup(self) -> list[dict]:
        items: list[dict] = []
        if isinstance(self._completion_ai_item, dict):
            ai_anchor_rev = int(self._completion_ai_item.get("anchor_revision") or -1)
            ai_anchor_pos = int(self._completion_ai_item.get("anchor_pos") or -1)
            if ai_anchor_rev != int(self.document().revision()) or ai_anchor_pos != int(self.textCursor().position()):
                self._completion_ai_item = None
            else:
                items.append(dict(self._completion_ai_item))
        for item in self._completion_items:
            if not isinstance(item, dict):
                continue
            if bool(item.get("is_ai_suggestion")):
                continue
            items.append(item)
        return items

    def hide_completion_popup(self):
        self._completion_doc_tooltip_timer.stop()
        self._completion_doc_future_pump.stop()
        for fut in list(self._completion_doc_futures.values()):
            try:
                fut.cancel()
            except Exception:
                pass
        self._completion_doc_futures.clear()
        self._completion_pending_doc_row = -1
        self._completion_pending_doc_key = None
        QToolTip.hideText()
        self._clear_hover_signature_tooltip()
        self._completion_popup.hide()
        self._completion_ai_item = None
        self._completion_items = []
        self._completion_filtered_items = []
        self._completion_popup.clear()

    def show_completion_popup(self, items: list[dict], *, file_path: str = "", token: int = 0):
        clean_items = [it for it in items if isinstance(it, dict)]
        if not clean_items:
            self.hide_completion_popup()
            return

        self.clear_inline_suggestion()
        self._clear_hover_signature_tooltip()
        self._completion_result_file_path = str(file_path or getattr(self, "file_path", "") or "")
        self._completion_result_token = max(0, int(token or 0))
        self._completion_result_revision = int(self.document().revision())
        self._completion_result_source_text = self.toPlainText()
        self._completion_row_meta_cache.clear()
        self._completion_doc_cache.clear()
        self._completion_source_sig_index_key = None
        self._completion_source_sig_index = {}
        self._completion_doc_tooltip_timer.stop()
        for fut in list(self._completion_doc_futures.values()):
            try:
                fut.cancel()
            except Exception:
                pass
        self._completion_doc_futures.clear()
        self._completion_doc_future_pump.stop()

        self._completion_items = clean_items
        self._rebuild_completion_popup()
        if self._completion_popup.count() > 0:
            self._position_completion_popup()
            self._completion_popup.show()
            self._completion_popup.raise_()

    def move_completion_selection(self, delta: int):
        if not self.is_completion_popup_visible():
            return
        count = self._completion_popup.count()
        if count <= 0:
            return
        row = self._completion_popup.currentRow()
        if row < 0:
            row = 0
        row = (row + delta) % count
        self._completion_popup.setCurrentRow(row)

    def accept_selected_completion(self) -> bool:
        if not self.is_completion_popup_visible():
            return False
        item = self._completion_popup.currentItem()
        if item is None:
            return False
        data = item.data(_COMPLETION_ITEM_ROLE)
        if not isinstance(data, dict):
            return False
        accepted = str(self._insert_completion(data) or "").strip()
        if not accepted:
            accepted = str(data.get("label") or data.get("insert_text") or "").strip()
        if accepted:
            self._completion_recency[accepted] = self._completion_recency.get(accepted, 0) + 1
        self.completionAccepted.emit(accepted)
        self.hide_completion_popup()
        return True

    def _on_completion_item_clicked(self, _item: QListWidgetItem):
        self.accept_selected_completion()
        self.setFocus()

    def _on_completion_row_changed(self, row: int):
        if not self.is_completion_popup_visible():
            return
        pos = self._completion_popup_global_pos_for_row(row)
        self._schedule_completion_doc_tooltip(row, pos)

    def _schedule_completion_doc_tooltip(self, row: int, global_pos: QPoint):
        if not bool(self._completion_ui_cfg.get("show_doc_tooltip", True)):
            return
        self._completion_doc_tooltip_timer.stop()
        if row < 0:
            self._completion_pending_doc_row = -1
            self._completion_pending_doc_key = None
            QToolTip.hideText()
            return
        self._completion_pending_doc_row = row
        self._completion_pending_doc_pos = QPoint(global_pos)
        self._completion_pending_doc_key = None
        self._completion_doc_tooltip_timer.start(int(self._completion_ui_cfg.get("doc_tooltip_delay_ms", 180)))

    def _on_completion_doc_timer(self):
        row = int(self._completion_pending_doc_row)
        if not self.is_completion_popup_visible() or row < 0 or row >= self._completion_popup.count():
            return
        popup_item = self._completion_popup.item(row)
        if popup_item is None:
            return
        item = popup_item.data(_COMPLETION_ITEM_ROLE)
        if not isinstance(item, dict):
            return

        key = self._completion_cache_key(item)
        self._completion_pending_doc_key = key
        cached = self._completion_doc_cache_get(key)
        if cached is not _COMPLETION_DOC_MISSING:
            if cached:
                QToolTip.showText(self._completion_pending_doc_pos, cached, self._completion_popup)
            else:
                QToolTip.hideText()
            return

        try:
            fut = self._completion_doc_executor.submit(
                _compute_completion_doc_preview,
                dict(item),
                self._completion_result_source_text,
            )
            self._completion_doc_futures[key] = fut
            if not self._completion_doc_future_pump.isActive():
                self._completion_doc_future_pump.start()
        except Exception:
            QToolTip.hideText()

    def _drain_completion_doc_futures(self):
        if not self._completion_doc_futures:
            self._completion_doc_future_pump.stop()
            return
        done_keys: list[tuple[str, str, int]] = []
        for key, fut in self._completion_doc_futures.items():
            if not fut.done():
                continue
            done_keys.append(key)
            summary = ""
            try:
                summary = str(fut.result() or "")
            except Exception:
                summary = ""
            self._completion_doc_cache_put(key, summary)

        for key in done_keys:
            self._completion_doc_futures.pop(key, None)

        if not self._completion_doc_futures:
            self._completion_doc_future_pump.stop()

        pending = self._completion_pending_doc_key
        if self.is_completion_popup_visible() and pending and pending in done_keys:
            summary = self._completion_doc_cache_get(pending)
            if isinstance(summary, str) and summary:
                QToolTip.showText(self._completion_pending_doc_pos, summary, self._completion_popup)
            else:
                QToolTip.hideText()

    def _completion_popup_global_pos_for_row(self, row: int) -> QPoint:
        if row < 0 or row >= self._completion_popup.count():
            return self.mapToGlobal(self.cursorRect().bottomLeft())
        item = self._completion_popup.item(row)
        if item is None:
            return self.mapToGlobal(self.cursorRect().bottomLeft())
        rect = self._completion_popup.visualItemRect(item)
        return self._completion_popup.viewport().mapToGlobal(rect.bottomLeft() + QPoint(16, 8))

    def _hover_signature_cache_key(self, label: str) -> tuple[str, str, int]:
        file_path = str(getattr(self, "file_path", "") or "__editor__/missing")
        revision = int(self.document().revision())
        return file_path, str(label or "").strip(), revision

    def _hover_signature_cache_get(self, key):
        if key in self._hover_signature_cache:
            value = self._hover_signature_cache[key]
            self._hover_signature_cache.move_to_end(key)
            if isinstance(value, str) and value.strip():
                return value
            return _COMPLETION_DOC_MISSING
        return _COMPLETION_DOC_MISSING

    def _hover_signature_cache_put(self, key, value: str):
        text = str(value or "")
        if not text.strip():
            self._hover_signature_cache.pop(key, None)
            return
        self._cache_put_lru(self._hover_signature_cache, key, text)

    def _hover_call_target_at(self, pos: QPoint) -> tuple[str, QPoint, int, int]:
        cursor = self.cursorForPosition(pos)
        block_text = cursor.block().text()
        col = int(cursor.positionInBlock())
        language_id = str(self.language_id() or "").strip().lower()
        if not block_text:
            return "", QPoint(), 0, 0

        if col >= len(block_text) and col > 0:
            col -= 1
        if col < 0 or col >= len(block_text):
            return "", QPoint(), 0, 0

        if not self._is_identifier_char(block_text[col]):
            if col > 0 and self._is_identifier_char(block_text[col - 1]):
                col -= 1
            else:
                return "", QPoint(), 0, 0

        start = col
        while start > 0 and self._is_identifier_char(block_text[start - 1]):
            start -= 1
        end = col + 1
        while end < len(block_text) and self._is_identifier_char(block_text[end]):
            end += 1

        label = block_text[start:end].strip()
        if not label:
            return "", QPoint(), 0, 0

        probe = end
        while probe < len(block_text) and block_text[probe].isspace():
            probe += 1
        if probe >= len(block_text) or block_text[probe] != "(":
            # For C/C++ and Rust, hover info is useful on any symbol.
            if language_id not in {"c", "cpp", "rust"}:
                return "", QPoint(), 0, 0
            line = int(cursor.blockNumber() + 1)
            hover_col = int(start)
            global_pos = self.viewport().mapToGlobal(pos + QPoint(16, 14))
            return label, global_pos, line, hover_col

        line = int(cursor.blockNumber() + 1)
        call_col = int(probe + 1)  # inside `name(` for get_signatures
        global_pos = self.viewport().mapToGlobal(pos + QPoint(16, 14))
        return label, global_pos, line, call_col

    def _schedule_hover_signature_tooltip(self, label: str, global_pos: QPoint, line: int, column: int):
        norm = str(label or "").strip()
        if not norm:
            self._clear_hover_signature_tooltip()
            return
        self._hover_signature_pending_label = norm
        self._hover_signature_pending_key = self._hover_signature_cache_key(norm)
        self._hover_signature_pending_pos = QPoint(global_pos)
        self._hover_signature_pending_line = max(1, int(line))
        self._hover_signature_pending_column = max(0, int(column))
        self._hover_signature_timer.start(_HOVER_SIGNATURE_DELAY_MS)

    def _clear_hover_signature_tooltip(self):
        self._hover_signature_timer.stop()
        self._hover_signature_pending_label = ""
        self._hover_signature_pending_key = None
        self._hover_signature_pending_line = 0
        self._hover_signature_pending_column = 0
        self._hover_signature_active_request_id = 0
        QToolTip.hideText()

    def _on_hover_signature_timer(self):
        if self.is_completion_popup_visible():
            return
        label = str(self._hover_signature_pending_label or "").strip()
        key = self._hover_signature_pending_key
        if not label or key is None:
            return
        cached = self._hover_signature_cache_get(key)
        if cached is not _COMPLETION_DOC_MISSING:
            if cached:
                QToolTip.showText(self._hover_signature_pending_pos, cached, self)
            else:
                QToolTip.hideText()
            return

        self._hover_signature_request_seq += 1
        self._hover_signature_active_request_id = self._hover_signature_request_seq
        self.signatureRequested.emit(
            {
                "request_id": int(self._hover_signature_active_request_id),
                "label": label,
                "line": int(self._hover_signature_pending_line),
                "column": int(self._hover_signature_pending_column),
                "revision": int(self.document().revision()),
            }
        )

        try:
            fut = self._hover_signature_executor.submit(_signature_for_label, label, self.toPlainText())
            self._hover_signature_futures[key] = fut
            if not self._hover_signature_future_pump.isActive():
                self._hover_signature_future_pump.start()
        except Exception:
            QToolTip.hideText()

    def _drain_hover_signature_futures(self):
        if not self._hover_signature_futures:
            self._hover_signature_future_pump.stop()
            return
        done_keys: list[tuple[str, str, int]] = []
        for key, fut in self._hover_signature_futures.items():
            if not fut.done():
                continue
            done_keys.append(key)
            sig = ""
            try:
                sig = str(fut.result() or "")
            except Exception:
                sig = ""
            label = key[1] if len(key) > 1 else ""
            html_tip = _build_signature_tooltip_html(signature=sig, label=label)
            if html_tip:
                self._hover_signature_cache_put(key, html_tip)

        for key in done_keys:
            self._hover_signature_futures.pop(key, None)

        if not self._hover_signature_futures:
            self._hover_signature_future_pump.stop()

        pending = self._hover_signature_pending_key
        if pending and pending in done_keys and not self.is_completion_popup_visible():
            value = self._hover_signature_cache_get(pending)
            if isinstance(value, str) and value:
                QToolTip.showText(self._hover_signature_pending_pos, value, self)
            else:
                QToolTip.hideText()

    def apply_signature_lookup_result(self, payload: dict):
        if not isinstance(payload, dict):
            return
        request_id = int(payload.get("request_id") or 0)
        if request_id <= 0:
            return

        label = str(payload.get("label") or "").strip()
        if not label:
            return

        signature = str(payload.get("signature") or "").strip()
        if not signature:
            return

        documentation = str(payload.get("documentation") or "")
        full_name = str(payload.get("full_name") or "").strip()
        module_name = str(payload.get("module_name") or "").strip()
        owner = full_name or ""
        if not owner and module_name:
            owner = f"{module_name}.{label}"
        elif not owner:
            owner = str(payload.get("source") or "")

        tooltip_html = _build_signature_tooltip_html(
            signature=signature,
            label=label,
            documentation=documentation,
            owner=owner,
        )
        if not tooltip_html:
            return

        key = self._hover_signature_cache_key(label)
        self._hover_signature_cache_put(key, tooltip_html)

        pending = self._hover_signature_pending_key
        if (
            request_id == self._hover_signature_active_request_id
            and label == str(self._hover_signature_pending_label or "").strip()
            and pending == key
            and not self.is_completion_popup_visible()
        ):
            QToolTip.showText(self._hover_signature_pending_pos, tooltip_html, self)

    def _position_completion_popup(self):
        cursor_rect = self.cursorRect()
        x = cursor_rect.left() + self.lineNumberAreaWidth()
        y = cursor_rect.bottom() + 2
        row_h = max(20, self._completion_popup.sizeHintForRow(0), self.fontMetrics().height() + 6)
        visible_rows = min(self._completion_popup.count(), self._completion_max_visible_rows)
        h = max(28, visible_rows * row_h + 6)
        w = max(320, min(760, int(self.viewport().width() * 0.78)))

        # keep popup inside editor viewport
        if y + h > self.viewport().height():
            y = max(0, cursor_rect.top() - h - 2)
        if x + w > self.viewport().width():
            x = max(0, self.viewport().width() - w - 2)

        self._completion_popup.setGeometry(x, y, w, h)

    def _is_identifier_char(self, ch: str) -> bool:
        return bool(ch) and (ch.isalnum() or ch == "_")

    def _completion_matches_prefix(self, item: dict, prefix: str) -> bool:
        if bool(item.get("is_ai_suggestion")):
            return True
        label = str(item.get("label") or item.get("insert_text") or "")
        if not prefix:
            return True
        low = label.lower()
        p = prefix.lower()
        return low.startswith(p) or p in low

    def _current_completion_prefix(self) -> str:
        start, end = self._current_prefix_bounds()
        if end < start:
            return ""
        text = self.toPlainText()
        return text[start:end]

    def _completion_cache_key(self, item: dict) -> tuple[str, str, int]:
        label = str(item.get("label") or item.get("insert_text") or "").strip()
        token_or_rev = self._completion_result_token if self._completion_result_token > 0 else self._completion_result_revision
        return self._completion_result_file_path, label, int(token_or_rev)

    def _completion_source_signature_index(self) -> dict[str, str]:
        key = (self._completion_result_file_path, int(self._completion_result_revision))
        if self._completion_source_sig_index_key != key:
            self._completion_source_sig_index_key = key
            self._completion_source_sig_index = _collect_source_signatures(self._completion_result_source_text)
        return self._completion_source_sig_index

    def _cache_put_lru(self, cache: OrderedDict, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._completion_cache_max:
            cache.popitem(last=False)

    def _completion_doc_cache_get(self, key):
        if key in self._completion_doc_cache:
            value = self._completion_doc_cache[key]
            self._completion_doc_cache.move_to_end(key)
            return value
        return _COMPLETION_DOC_MISSING

    def _completion_doc_cache_put(self, key, value: str):
        self._cache_put_lru(self._completion_doc_cache, key, str(value or ""))

    def _completion_kind_color(self, group: str, palette: QPalette, selected: bool) -> QColor:
        if selected:
            return QColor(palette.color(QPalette.HighlightedText))

        base = palette.color(QPalette.Text)
        accent = _COMPLETION_KIND_COLOR_FALLBACKS.get(group, _COMPLETION_KIND_COLOR_FALLBACKS["default"])
        mix = QColor(base)
        mix.setRed(int(base.red() * 0.45 + accent.red() * 0.55))
        mix.setGreen(int(base.green() * 0.45 + accent.green() * 0.55))
        mix.setBlue(int(base.blue() * 0.45 + accent.blue() * 0.55))
        return mix

    def _is_callable_item(self, item: dict) -> bool:
        kind = str(item.get("kind") or "").strip().lower()
        return kind in {"function", "method", "class", "type"}

    def _best_effort_callable_signature(self, item: dict, label: str) -> str:
        if not label:
            return ""
        scope = str(item.get("source_scope") or "").strip().lower()
        if scope == "builtins" and hasattr(py_builtins, label):
            try:
                obj = getattr(py_builtins, label)
                if callable(obj):
                    return _normalize_signature_text(f"{label}{inspect.signature(obj)}", label)
            except Exception:
                pass

        source_sig = self._completion_source_signature_index().get(label, "")
        if source_sig:
            return _normalize_signature_text(source_sig, label)
        return _normalize_signature_text(f"{label}()", label)

    def _derive_completion_right_label(self, item: dict) -> str:
        for key in ("source_label", "type_label", "owner", "module"):
            value = str(item.get(key) or "").strip()
            if value:
                return value[:28]

        detail = str(item.get("detail") or "").strip()
        source = str(item.get("source") or "").strip()
        scope = str(item.get("source_scope") or "").strip().lower()
        kind = str(item.get("kind") or "").strip().lower()

        module_m = re.search(r"\bmodule\s+([A-Za-z_][\w\.]*)", detail)
        if module_m:
            return module_m.group(1)[:28]
        class_m = re.search(r"\bclass\s+([A-Za-z_]\w*)", detail)
        if class_m:
            return class_m.group(1)[:28]
        from_m = re.search(r"\bfrom\s+([A-Za-z_][\w\.]*)", detail)
        if from_m:
            return from_m.group(1)[:28]
        arrow_m = re.search(r"->\s*([A-Za-z_][\w\.\[\], ]*)$", detail)
        if arrow_m:
            return arrow_m.group(1).strip()[:28]

        if scope == "builtins":
            return "builtins"
        if scope == "interpreter_modules":
            return "stdlib"
        if scope == "project":
            return "project"
        if scope == "current_file":
            return "file"
        if source and source.lower() not in {"fallback", "jedi"}:
            return source[:28]
        if source:
            return source[:28]
        if kind == "keyword":
            return "keyword"
        return ""

    def _completion_row_meta(self, item: dict) -> dict:
        key = self._completion_cache_key(item)
        cached = self._completion_row_meta_cache.get(key)
        if isinstance(cached, dict):
            self._completion_row_meta_cache.move_to_end(key)
            return cached

        label = str(item.get("label") or item.get("insert_text") or "")
        kind = str(item.get("kind") or "")
        detail = str(item.get("detail") or "")

        primary = label
        if bool(self._completion_ui_cfg.get("show_signatures", True)) and self._is_callable_item(item):
            sig = _extract_compact_signature(label, detail)
            primary = sig if sig else self._best_effort_callable_signature(item, label)
            primary = _normalize_signature_text(primary, label) or primary

        right = ""
        if bool(self._completion_ui_cfg.get("show_right_label", True)):
            right = self._derive_completion_right_label(item)

        meta = {
            "primary": primary or label,
            "right": right,
            "kind_group": _kind_group(kind),
        }
        self._cache_put_lru(self._completion_row_meta_cache, key, meta)
        return meta

    def _completion_ui_sort_key(self, item: dict, prefix: str, base_index: int) -> tuple[int, int]:
        if bool(item.get("is_ai_suggestion")):
            return -1, base_index
        label = str(item.get("label") or item.get("insert_text") or "")
        demote = 0
        if label.startswith("__") and label.endswith("__") and not prefix.startswith("_"):
            demote = 2
        elif label.startswith("_") and prefix == "":
            demote = 1
        return demote, base_index

    def _rebuild_completion_popup(self):
        prefix = self._current_completion_prefix()
        indexed_filtered = []
        items = self._completion_items_for_popup()
        for idx, item in enumerate(items):
            if self._completion_matches_prefix(item, prefix):
                indexed_filtered.append((idx, item))
        indexed_filtered.sort(key=lambda entry: self._completion_ui_sort_key(entry[1], prefix, entry[0]))
        filtered = [item for _, item in indexed_filtered]
        self._completion_filtered_items = filtered

        self._completion_popup.clear()
        for item in filtered:
            row_meta = self._completion_row_meta(item)
            row_item = QListWidgetItem(str(row_meta.get("primary") or item.get("label") or ""))
            row_item.setData(_COMPLETION_ITEM_ROLE, item)
            row_item.setData(_COMPLETION_ROW_META_ROLE, row_meta)
            self._completion_popup.addItem(row_item)

        if self._completion_popup.count() <= 0:
            self.hide_completion_popup()
            return
        if self._completion_popup.currentRow() < 0:
            self._completion_popup.setCurrentRow(0)

    def refresh_completion_popup_filter(self):
        if not self.is_completion_popup_visible():
            return
        row_before = self._completion_popup.currentRow()
        self._rebuild_completion_popup()
        if self._completion_popup.count() > 0 and row_before >= 0:
            self._completion_popup.setCurrentRow(min(row_before, self._completion_popup.count() - 1))
        if self.is_completion_popup_visible():
            self._position_completion_popup()

    def _current_prefix_bounds(self) -> tuple[int, int]:
        cursor = self.textCursor()
        text = self.toPlainText()
        end = cursor.position()
        start = end
        while start > 0 and self._is_identifier_char(text[start - 1]):
            start -= 1
        return start, end

    @staticmethod
    def _plain_text_from_lsp_snippet(text: str) -> str:
        # Basic snippet fallback: drop tabstop/placeholder syntax.
        value = str(text or "")
        value = re.sub(r"\$\{(\d+):([^}]*)\}", r"\2", value)
        value = re.sub(r"\$\{(\d+)\}", "", value)
        value = re.sub(r"\$(\d+)", "", value)
        return value

    def _doc_pos_from_lsp_position(self, line_zero: int, utf16_col: int) -> int:
        line_no = max(0, int(line_zero))
        col = max(0, int(utf16_col))
        block = self.document().findBlockByNumber(line_no)
        if not block.isValid():
            block = self.document().lastBlock()
        col = min(col, max(0, len(block.text())))
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, col)
        return int(cursor.position())

    def _apply_lsp_text_edit(self, text_edit_obj: object) -> str:
        text_edit = text_edit_obj if isinstance(text_edit_obj, dict) else {}
        if not text_edit:
            return ""

        new_text = str(text_edit.get("newText") or "")
        if not new_text and "new_text" in text_edit:
            new_text = str(text_edit.get("new_text") or "")
        if not new_text:
            return ""

        range_obj = text_edit.get("range")
        if not isinstance(range_obj, dict):
            # Insert/replace edit shape.
            range_obj = text_edit.get("replace")
            if not isinstance(range_obj, dict):
                range_obj = text_edit.get("insert")
        if not isinstance(range_obj, dict):
            return ""

        start_obj = range_obj.get("start") if isinstance(range_obj.get("start"), dict) else {}
        end_obj = range_obj.get("end") if isinstance(range_obj.get("end"), dict) else {}
        if not isinstance(start_obj, dict) or not isinstance(end_obj, dict):
            return ""

        start_pos = self._doc_pos_from_lsp_position(
            int(start_obj.get("line", 0)),
            int(start_obj.get("character", 0)),
        )
        end_pos = self._doc_pos_from_lsp_position(
            int(end_obj.get("line", 0)),
            int(end_obj.get("character", 0)),
        )
        if end_pos < start_pos:
            start_pos, end_pos = end_pos, start_pos

        insert_text_format = int(text_edit.get("insertTextFormat") or text_edit.get("insert_text_format") or 1)
        if insert_text_format == 2:
            new_text = self._plain_text_from_lsp_snippet(new_text)

        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(start_pos)
        cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
        cursor.insertText(new_text)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return new_text

    def _insert_completion(self, item: dict) -> str:
        is_ai_suggestion = bool(item.get("is_ai_suggestion"))
        raw_insert = str(item.get("insert_text") or item.get("label") or "")
        if is_ai_suggestion:
            insert_text = raw_insert.replace("\r", "")
            if "\n" not in insert_text and "\\n" in insert_text:
                insert_text = insert_text.replace("\\n", "\n")
        else:
            insert_text = raw_insert.strip()
        label = str(item.get("label") or "").strip()

        if is_ai_suggestion:
            if not insert_text.strip():
                return ""
            cursor = self.textCursor()
            ai_insert = self._dedupe_ai_suggestion_for_cursor(insert_text)
            if ai_insert:
                cursor.insertText(ai_insert)
            self.setTextCursor(cursor)
            return "AI suggestion"

        lsp_edit = item.get("lsp_text_edit")
        if isinstance(lsp_edit, dict):
            merged_edit = dict(lsp_edit)
            if "newText" not in merged_edit and "new_text" not in merged_edit:
                merged_edit["newText"] = raw_insert
            if "insertTextFormat" not in merged_edit:
                merged_edit["insertTextFormat"] = int(item.get("lsp_insert_text_format") or 1)
            applied = self._apply_lsp_text_edit(merged_edit)
            if applied:
                return str(label or applied)

        insert_text_format = int(item.get("lsp_insert_text_format") or 1)
        if insert_text_format == 2:
            insert_text = self._plain_text_from_lsp_snippet(insert_text)
        if not insert_text.strip():
            return ""

        cursor = self.textCursor()
        source = self.toPlainText()
        start, end = self._current_prefix_bounds()
        prefix_text = source[start:end]

        # Jedi returns suffix text in `insert_text` for many cases (e.g. "pr" -> "int").
        # Expand to the full label when suffix semantics are detected.
        if (not is_ai_suggestion) and label and prefix_text:
            pfx_low = prefix_text.lower()
            lbl_low = label.lower()
            if lbl_low.startswith(pfx_low):
                suffix = label[len(prefix_text):]
                if insert_text.lower() == suffix.lower():
                    insert_text = label

        right = source[end:]
        suffix = []
        for ch in right:
            if self._is_identifier_char(ch):
                suffix.append(ch)
            else:
                break
        suffix_text = "".join(suffix)

        overlap = 0
        max_overlap = min(len(insert_text), len(suffix_text))
        for k in range(max_overlap, 0, -1):
            if insert_text.endswith(suffix_text[:k]):
                overlap = k
                break
        final_insert = insert_text[:-overlap] if overlap > 0 else insert_text

        cursor.beginEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.insertText(final_insert)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return str(insert_text or label)

    def request_manual_completion(self):
        self.completionRequested.emit("manual")

    # --------- helpers for indentation / pairing ---------
    def _line_before_cursor(self) -> str:
        c = self.textCursor()
        block = c.block()
        line = block.text()
        col = c.positionInBlock()
        return line[:col]

    def _line_after_cursor(self) -> str:
        c = self.textCursor()
        block = c.block()
        line = block.text()
        col = c.positionInBlock()
        return line[col:]

    def _active_indent_width(self) -> int:
        try:
            return max(1, int(getattr(self, "indent_width", 4)))
        except Exception:
            return 4

    def _indent_columns(self, text: str) -> int:
        # tabs treated as indent_width columns
        indent_width = self._active_indent_width()
        cols = 0
        for ch in text:
            if ch == " ":
                cols += 1
            elif ch == "\t":
                cols += indent_width
            else:
                break
        return cols

    def _indent_string_from_columns(self, cols: int) -> str:
        cols = max(0, cols)
        return " " * cols

    def _leading_indent_text(self, text: str) -> str:
        i = 0
        while i < len(text) and text[i] in (" ", "\t"):
            i += 1
        return text[:i]

    def _python_should_dedent_next_line(self, stripped_before: str) -> bool:
        # Dedent triggers for next line
        # Covers plain keywords and "except ValueError as e:"
        return bool(re.match(r"^(elif\b|else\b|except\b|finally\b)", stripped_before))

    def _python_starts_block(self, stripped_before: str) -> bool:
        # Increase indent after line ending with ':'
        # (good enough without full parser)
        return stripped_before.endswith(":")

    def _matching_pair_for_right(self, ch: str) -> str | None:
        pairs = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
        return pairs.get(ch)

    def _matching_pair_for_left(self, ch: str) -> str | None:
        pairs = {")": "(", "]": "[", "}": "{", '"': '"', "'": "'"}
        return pairs.get(ch)

    def _leading_indent_string(self, text: str) -> str:
        """Return the leading indentation from a line, converting tabs to spaces."""
        indent_width = self._active_indent_width()
        indent = []
        for ch in text:
            if ch == " ":
                indent.append(" ")
            elif ch == "\t":
                indent.append(" " * indent_width)
            else:
                break
        return "".join(indent)

    def _matching_bracket(self, ch: str) -> str | None:
        pairs = {
            "(": ")",
            "[": "]",
            "{": "}",
            '"': '"',
            "'": "'",
        }
        return pairs.get(ch)


    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        menu.addSeparator()

        act_def = QAction("Go to Definition", menu)
        act_def.setShortcut(self._sequence_to_qkeysequence(self._action_sequence("general", "action.go_to_definition")))
        menu.addAction(act_def)

        act_usages = QAction("Find Usages", menu)
        act_usages.setShortcut(self._sequence_to_qkeysequence(self._action_sequence("general", "action.find_usages")))
        menu.addAction(act_usages)
        act_quick_fix = QAction("Quick Fix...", menu)
        act_quick_fix.setShortcut(QKeySequence("Alt+Return"))
        menu.addAction(act_quick_fix)

        pos = event.pos() if hasattr(event, "pos") else event.position().toPoint()
        cursor = self.cursorForPosition(pos)
        symbol_payload = self._symbol_payload_from_cursor(cursor)
        selection_payload = self._selection_payload()
        raw_language = str(self.language_id() or "").strip().lower()
        file_suffix = os.path.splitext(str(self.file_path or "").strip().lower())[1]
        effective_language = raw_language
        if raw_language == "c" and file_suffix in {".h", ".hpp", ".hh", ".hxx", ".cpp", ".cc", ".cxx"}:
            effective_language = "cpp"
        act_rename = None
        if isinstance(symbol_payload, dict):
            act_rename = QAction("Rename Symbol...", menu)
            act_rename.setShortcut(
                self._sequence_to_qkeysequence(self._action_sequence("general", "action.rename_symbol"))
            )
            menu.addAction(act_rename)

        act_extract_var = None
        act_extract_method = None
        if isinstance(selection_payload, dict) and effective_language in {"python", "c", "cpp"}:
            act_extract_var = QAction("Extract Variable...", menu)
            act_extract_var.setShortcut(
                self._sequence_to_qkeysequence(self._action_sequence("general", "action.extract_variable"))
            )
            menu.addAction(act_extract_var)

            if effective_language in {"python", "cpp"}:
                act_extract_method = QAction("Extract Method...", menu)
                act_extract_method.setShortcut(
                    self._sequence_to_qkeysequence(self._action_sequence("general", "action.extract_method"))
                )
                menu.addAction(act_extract_method)

        menu.addSeparator()

        act_ai = QAction("AI Inline Assist", menu)
        act_ai.setShortcut(self._sequence_to_qkeysequence(self._action_sequence("general", "action.ai_inline_assist")))
        act_ai.setToolTip(
            "Shortcuts: "
            + ", ".join(
                filter(
                    None,
                    [
                        self._sequence_to_text(self._action_sequence("general", "action.ai_inline_assist")),
                        self._sequence_to_text(self._action_sequence("general", "action.ai_inline_assist_ctrl_alt_space")),
                        self._sequence_to_text(self._action_sequence("general", "action.ai_inline_assist_alt_space")),
                    ],
                )
            )
        )
        menu.addAction(act_ai)

        menu.addSeparator()

        act_find = QAction("Find", menu)
        act_find.setShortcut(self._sequence_to_qkeysequence(self._action_sequence("general", "action.find")))
        menu.addAction(act_find)

        act_replace = QAction("Replace", menu)
        act_replace.setShortcut(self._sequence_to_qkeysequence(self._action_sequence("general", "action.replace")))
        menu.addAction(act_replace)

        menu.addSeparator()

        act_word_wrap = QAction("Word Wrap", menu)
        act_word_wrap.setCheckable(True)
        act_word_wrap.setChecked(self.is_word_wrap_enabled())
        menu.addAction(act_word_wrap)

        payload = {
            "line": int(cursor.blockNumber() + 1),
            "column": int(cursor.positionInBlock() + 1),
            "cursor_pos": int(cursor.position()),
            "local_pos": QPoint(pos),
            "global_pos": QPoint(event.globalPos()),
        }
        self.contextMenuAboutToShow.emit(menu, payload)

        chosen = menu.exec(event.globalPos())
        if chosen is act_def:
            self.request_definition("context", cursor)
            return
        if chosen is act_usages:
            self.request_usages("context", cursor)
            return
        if chosen is act_quick_fix:
            self.request_quick_fix("context", cursor)
            return
        if act_rename is not None and chosen is act_rename:
            self.request_rename("context", cursor)
            return
        if act_extract_var is not None and chosen is act_extract_var:
            self.request_extract_variable("context")
            return
        if act_extract_method is not None and chosen is act_extract_method:
            self.request_extract_method("context")
            return
        if chosen is act_ai:
            self.aiAssistRequested.emit("manual")
            return
        if chosen is act_find:
            self.show_find_bar()
            return
        if chosen is act_replace:
            self.show_replace_bar()
            return
        if chosen is act_word_wrap:
            enabled = bool(act_word_wrap.isChecked())
            self.set_word_wrap_enabled(enabled)
            self.wordWrapPreferenceChanged.emit(
                {
                    "enabled": enabled,
                    "file_path": str(getattr(self, "file_path", "") or ""),
                    "language_id": str(self.language_id() or "plaintext"),
                }
            )
            return
        return

    def mousePressEvent(self, event):
        if self.is_completion_popup_visible():
            self.hide_completion_popup()
        if self._dispatch_language_mouse_press(event):
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            # If we successfully clicked a color and replaced it, stop processing
            if self._handle_color_click(event):
                return
        if (
            event.button() == Qt.LeftButton
            and bool(event.modifiers() & Qt.ControlModifier)
        ):
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            cursor = self.cursorForPosition(pos)
            payload = self._symbol_payload_from_cursor(cursor)
            if payload is not None:
                payload["trigger"] = "ctrl_click"
                self.definitionRequested.emit(payload)
                event.accept()
                return
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        mods = event.modifiers()
        ctrl_zoom = bool(mods & Qt.ControlModifier) and not bool(mods & (Qt.AltModifier | Qt.MetaModifier))
        if ctrl_zoom:
            delta_y = int(event.angleDelta().y())
            if delta_y == 0:
                delta_y = int(event.pixelDelta().y())
            if delta_y != 0:
                self.editorFontSizeStepRequested.emit(1 if delta_y > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def mouseMoveEvent(self, event):
        if self.language_id() == "todo":
            over_box = self._is_pos_over_todo_checkbox(event.pos())
            if over_box != self._todo_hovering_box:
                self._todo_hovering_box = over_box
                self.viewport().setCursor(
                    QCursor(Qt.PointingHandCursor if over_box else Qt.IBeamCursor)
                )
        else:
            if self._todo_hovering_box:
                self._todo_hovering_box = False
                self.viewport().setCursor(QCursor(Qt.IBeamCursor))

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        # Reset cursor when leaving editor viewport
        if self._todo_hovering_box:
            self._todo_hovering_box = False
            self.viewport().setCursor(QCursor(Qt.IBeamCursor))
        super().leaveEvent(event)


    # --------- key handling: pairing + indent logic ---------

    def keyPressEvent(self, event):
        key = event.key()
        text = event.text()
        mods = event.modifiers()

        if self._dispatch_language_key_press(event):
            event.accept()
            return

        if self._handle_editor_shortcut_fallback(event):
            event.accept()
            return

        # --- block indent / unindent ---
        if key == Qt.Key_Tab and mods == Qt.NoModifier:
            # Tab should commit completion first when suggestions are visible.
            if self.is_completion_popup_visible():
                if self.accept_selected_completion():
                    event.accept()
                    return
                self.hide_completion_popup()
                event.accept()
                return
            # Inline suggestion acceptance also takes precedence over indentation.
            if self.has_inline_suggestion() and self.accept_inline_suggestion():
                event.accept()
                return
            if self.textCursor().hasSelection():
                self._indent_selection()
            else:
                self.insertPlainText(self._indent_unit())
            event.accept()
            return

        if key == Qt.Key_Backtab:  # Shift+Tab usually comes as Backtab in Qt
            self._unindent_selection_or_line()
            event.accept()
            return

        if self._search_bar.isVisible():
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if mods & Qt.ShiftModifier:
                    self.search_previous()
                else:
                    self.search_next()
                return
            if key == Qt.Key_Escape:
                self.hide_search_bar()
                return

        if self.has_inline_suggestion():
            if key == Qt.Key_Escape:
                self.clear_inline_suggestion()
                return
            if key == Qt.Key_Tab and not self.is_completion_popup_visible():
                if self.accept_inline_suggestion():
                    return
            if key in {
                Qt.Key_Left,
                Qt.Key_Right,
                Qt.Key_Up,
                Qt.Key_Down,
                Qt.Key_Home,
                Qt.Key_End,
                Qt.Key_PageUp,
                Qt.Key_PageDown,
                Qt.Key_Backspace,
                Qt.Key_Delete,
                Qt.Key_Return,
                Qt.Key_Enter,
            } or (text and not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))):
                self.clear_inline_suggestion()

        if self.is_completion_popup_visible():
            if key == Qt.Key_Escape:
                self.hide_completion_popup()
                return
            if key == Qt.Key_Tab:
                if self.accept_selected_completion():
                    return
                self.hide_completion_popup()
                return
            if key in (Qt.Key_Return, Qt.Key_Enter):
                # Enter should keep normal editor behavior (newline/indent),
                # not commit the highlighted completion row.
                self.hide_completion_popup()
            if key == Qt.Key_Up:
                self.move_completion_selection(-1)
                return
            if key == Qt.Key_Down:
                self.move_completion_selection(1)
                return

        # ---------- bracket/quote autopair ----------
        if text and not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            match = self._matching_pair_for_right(text)
            if match is not None:
                cursor = self.textCursor()
                if cursor.hasSelection():
                    selected = cursor.selectedText()
                    cursor.insertText(text + selected + match)
                else:
                    # If next char is same quote, just move right for quotes
                    if text in ("'", '"'):
                        right = self._line_after_cursor()
                        if right.startswith(text):
                            cursor.movePosition(QTextCursor.Right)
                            self.setTextCursor(cursor)
                            return
                    cursor.insertText(text + match)
                    cursor.movePosition(QTextCursor.Left)
                    self.setTextCursor(cursor)
                return

        # ---------- bracket-aware backspace ----------
        if key == Qt.Key_Backspace and not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            cursor = self.textCursor()
            if not cursor.hasSelection():
                left = self._line_before_cursor()
                right = self._line_after_cursor()
                if left and right:
                    lch = left[-1]
                    rch = right[0]
                    pair = self._matching_pair_for_right(lch)
                    if pair is not None and pair == rch:
                        # delete both chars as a pair
                        cursor.beginEditBlock()
                        cursor.deletePreviousChar()   # delete left char
                        cursor.deleteChar()           # delete right char
                        cursor.endEditBlock()
                        return

                # smart backspace over indentation: remove up to indent_width spaces
                if left and left.strip() == "":
                    remove = min(self._active_indent_width(), len(left))
                    # remove only spaces
                    spaces = 0
                    for i in range(1, remove + 1):
                        if left[-i] == " ":
                            spaces += 1
                        else:
                            break
                    if spaces > 0:
                        cursor.beginEditBlock()
                        for _ in range(spaces):
                            cursor.deletePreviousChar()
                        cursor.endEditBlock()
                        return

        # ---------- Tab / Shift+Tab ----------
        if key == Qt.Key_Tab:
            self.textCursor().insertText(self._indent_unit())
            return

        if key == Qt.Key_Backtab:
            cursor = self.textCursor()
            block = cursor.block()
            line = block.text()
            indent_width = self._active_indent_width()

            remove_count = 0
            for ch in line[: indent_width]:
                if ch == " ":
                    remove_count += 1
                else:
                    break

            if remove_count > 0:
                cursor.beginEditBlock()
                cursor.movePosition(QTextCursor.StartOfBlock)
                for _ in range(remove_count):
                    cursor.deleteChar()
                cursor.endEditBlock()
            return

        # ---------- Enter / Return: auto-indent + python dedent ----------
        if key in (Qt.Key_Return, Qt.Key_Enter):
            cursor = self.textCursor()

            before = self._line_before_cursor()
            after = self._line_after_cursor()

            # Base indent from current line's leading whitespace
            full_line = cursor.block().text()
            leading = self._leading_indent_text(full_line)
            base_cols = self._indent_columns(leading)

            stripped_before = before.strip()

            # Python-smart dedent/indent logic
            dedent_cols = 0
            extra_cols = 0

            if self.language_id() == "python":
                indent_width = self._active_indent_width()
                if self._python_should_dedent_next_line(stripped_before):
                    dedent_cols = indent_width
                if self._python_starts_block(stripped_before):
                    extra_cols = indent_width

            new_cols = max(0, base_cols - dedent_cols) + extra_cols
            indent_text = self._indent_string_from_columns(new_cols)

            # Keep handy auto-close behavior for braces in generic languages:
            # if cursor is between {} / [] / (), insert newline+indent and keep closer on next line.
            pair_split = False
            if before and after:
                lch = before[-1]
                rch = after[0]
                pair = self._matching_pair_for_right(lch)
                if pair and pair == rch and lch in "([{":
                    pair_split = True

            cursor.beginEditBlock()
            if pair_split:
                # produce:
                # {
                #     |
                # }
                # with correct base indent
                cursor.insertText("\n" + indent_text + "\n" + self._indent_string_from_columns(max(0, base_cols - dedent_cols)))
                cursor.movePosition(QTextCursor.Up)
                cursor.movePosition(QTextCursor.EndOfLine)
                self.setTextCursor(cursor)
            else:
                cursor.insertText("\n" + indent_text)
                self.setTextCursor(cursor)

            cursor.endEditBlock()
            return

        # fallback
        super().keyPressEvent(event)
        if self.is_completion_popup_visible():
            self.refresh_completion_popup_filter()

    def _indent_unit(self) -> str:
        # Configure these attributes wherever you keep settings:
        # self.use_tabs: bool
        # self.indent_width: int
        indent_width = self._active_indent_width()
        use_tabs = _coerce_bool(getattr(self, "use_tabs", False), default=False)
        return "\t" if use_tabs else (" " * indent_width)


    def _selected_block_range(self, cursor: QTextCursor) -> tuple[int, int]:
        """
        Returns (first_block_number, last_block_number) for all touched lines.
        Handles the common edge case where selection ends at column 0:
        that trailing line is NOT included.
        """
        start = cursor.selectionStart()
        end = cursor.selectionEnd()

        doc = self.document()
        first_block = doc.findBlock(start)
        last_block = doc.findBlock(max(start, end - 1))  # default include last touched char

        # If selection ends exactly at start of a block, exclude that block
        end_block = doc.findBlock(end)
        if end_block.isValid() and end == end_block.position() and end > start:
            prev = end_block.previous()
            if prev.isValid():
                last_block = prev

        return first_block.blockNumber(), last_block.blockNumber()


    def _indent_selection(self):
        cursor = self.textCursor()
        old_anchor = cursor.anchor()
        old_pos = cursor.position()
        unit = self._indent_unit()

        if not cursor.hasSelection():
            cursor.insertText(unit)
            return

        first_bn, last_bn = self._selected_block_range(cursor)
        doc = self.document()

        # Snapshot line starts BEFORE edit
        line_starts: list[int] = []
        deltas: list[int] = []

        cursor.beginEditBlock()
        try:
            for bn in range(first_bn, last_bn + 1):
                block = doc.findBlockByNumber(bn)
                if not block.isValid():
                    continue
                line_starts.append(block.position())
                deltas.append(len(unit))

                c = QTextCursor(block)
                c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                c.insertText(unit)
        finally:
            cursor.endEditBlock()

        new_anchor = self._remap_pos_by_line_deltas(old_anchor, line_starts, deltas)
        new_pos = self._remap_pos_by_line_deltas(old_pos, line_starts, deltas)
        self._set_selection_preserve_tip(new_anchor, new_pos)


    def _unindent_selection_or_line(self):
        cursor = self.textCursor()
        old_anchor = cursor.anchor()
        old_pos = cursor.position()
        unit = self._indent_unit()
        indent_width = self._active_indent_width()
        doc = self.document()

        had_selection = cursor.hasSelection()
        if had_selection:
            first_bn, last_bn = self._selected_block_range(cursor)
        else:
            bn = cursor.block().blockNumber()
            first_bn = last_bn = bn

        line_starts: list[int] = []
        deltas: list[int] = []  # negative values for unindent

        cursor.beginEditBlock()
        try:
            for bn in range(first_bn, last_bn + 1):
                block = doc.findBlockByNumber(bn)
                if not block.isValid():
                    continue

                text = block.text()
                remove_n = 0

                if not text:
                    remove_n = 0
                elif text.startswith(unit):
                    remove_n = len(unit)
                elif text.startswith("\t"):
                    remove_n = 1
                else:
                    leading_spaces = 0
                    for ch in text:
                        if ch == " " and leading_spaces < indent_width:
                            leading_spaces += 1
                        else:
                            break
                    remove_n = leading_spaces

                line_starts.append(block.position())
                deltas.append(-remove_n)

                if remove_n <= 0:
                    continue

                c = QTextCursor(block)
                c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                c.movePosition(
                    QTextCursor.MoveOperation.Right,
                    QTextCursor.MoveMode.KeepAnchor,
                    remove_n,
                )
                c.removeSelectedText()
        finally:
            cursor.endEditBlock()

        new_anchor = self._remap_pos_by_line_deltas(old_anchor, line_starts, deltas)
        new_pos = self._remap_pos_by_line_deltas(old_pos, line_starts, deltas)

        if had_selection:
            self._set_selection_preserve_tip(new_anchor, new_pos)
        else:
            c = self.textCursor()
            c.setPosition(new_pos)
            self.setTextCursor(c)
            self.ensureCursorVisible()


    def _set_selection_preserve_tip(self, new_anchor: int, new_pos: int) -> None:
        c = self.textCursor()
        c.setPosition(max(0, new_anchor))
        c.setPosition(max(0, new_pos), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(c)
        self.ensureCursorVisible()  # keeps tip in view, no random jump

    def _remap_pos_by_line_deltas(self, pos: int, line_starts: list[int], deltas: list[int]) -> int:
        """
        Remap absolute document position after applying per-line prefix changes.
        line_starts[i]: absolute start position of line i (snapshot from BEFORE edits)
        deltas[i]: +N for indent, -N for unindent on that line
        """
        new_pos = pos
        for ls, d in zip(line_starts, deltas):
            if d == 0:
                continue
            # If caret is at or after line start, it shifts with inserted/removed prefix.
            if pos >= ls:
                new_pos += d
        return max(0, new_pos)

    def _is_pos_over_todo_checkbox(self, pos, hit_slop_chars: int = 0) -> bool:
        return is_todo_checkbox_at_pos(self, pos, hit_slop_chars=hit_slop_chars)


# ---------------- Highlighter selection ----------------

def set_highlighter_for_file(editor: QPlainTextEdit, file_path: str):
    set_editor_highlighter_for_file(editor, file_path)
