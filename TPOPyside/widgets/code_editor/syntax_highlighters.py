"""Syntax highlighters and language-to-highlighter mapping for CodeEditor."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable

from PySide6.QtGui import QBrush, QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextBlockUserData

from .keypress_handlers import get_language_id

if TYPE_CHECKING:
    from .editor import CodeEditor

_COLOR_PATTERN = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6})\b")

# Supports:
#   [ ] task
#   [x] task
#   [✔] task
#   - [ ] task
#   * [x] task
TODO_BOX_RE = re.compile(
    r'^(?P<prefix>[ \t]*(?:[-*+]\s+)?)\[(?P<state>[ xX✔])\](?P<suffix>.*)$'
)

_transparent_hash_fmt = QTextCharFormat()
_transparent_hash_fmt.setForeground(QBrush(QColor(0, 0, 0, 0)))  # fully transparent
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")

SyntaxColorResolver = Callable[[str, str, str], str | None]
_syntax_color_resolver: SyntaxColorResolver | None = None

SYNTAX_LANGUAGE_LABELS: dict[str, str] = {
    "python": "Python",
    "html": "HTML/XML",
    "javascript": "JavaScript",
    "php": "PHP",
    "cpp": "C/C++",
    "json": "JSON",
    "toml": "TOML/QSST",
    "rust": "Rust",
    "css": "CSS/SCSS/LESS",
    "shell": "Shell",
    "markdown": "Markdown",
    "todo": "Todo",
}

SYNTAX_LANGUAGE_ALIASES: dict[str, str] = {
    "python": "python",
    "html": "html",
    "xml": "html",
    "javascript": "javascript",
    "javascriptreact": "javascript",
    "php": "php",
    "c": "cpp",
    "cpp": "cpp",
    "make": "cpp",
    "json": "json",
    "jsonc": "json",
    "toml": "toml",
    "rust": "rust",
    "css": "css",
    "scss": "css",
    "less": "css",
    "shell": "shell",
    "bash": "shell",
    "zsh": "shell",
    "markdown": "markdown",
    "todo": "todo",
}

SYNTAX_TOKEN_DEFAULTS: dict[str, dict[str, str]] = {
    "python": {
        "keyword": "#569Cff",
        "soft_keyword": "#4FC1FF",
        "builtin": "#4EC9B0",
        "exception": "#DCDCAA",
        "decorator": "#C586C0",
        "definition_keyword": "#569Cff",
        "definition_name": "#DCDCAA",
        "operator": "#D4D4D4",
        "bracket": "#D7BA7D",
        "string": "#CE9178",
        "fstring_expression": "#FFD580",
        "comment": "#6A9955",
        "comment_tag": "#FFB86C",
        "number": "#B5CEA8",
    },
    "html": {
        "tag": "#4EC9B0",
        "attribute": "#9CDCFE",
        "value": "#CE9178",
        "comment": "#6A9955",
    },
    "javascript": {
        "keyword": "#569Cff",
        "string": "#CE9178",
        "comment": "#6A9955",
        "number": "#B5CEA8",
        "function": "#DCDCAA",
    },
    "php": {
        "keyword": "#569Cff",
        "variable": "#9CDCFE",
        "string": "#CE9178",
        "comment": "#6A9955",
        "tag": "#569Cff",
    },
    "cpp": {
        "keyword": "#569Cff",
        "preprocessor": "#C586C0",
        "string": "#CE9178",
        "number": "#B5CEA8",
        "comment": "#6A9955",
    },
    "json": {
        "key": "#9CDCFE",
        "string": "#CE9178",
        "number": "#B5CEA8",
    },
    "toml": {
        "key": "#9CDCFE",
        "table": "#4EC9B0",
        "string": "#CE9178",
        "number": "#B5CEA8",
        "literal": "#569Cff",
        "date": "#DCDCAA",
        "comment": "#6A9955",
    },
    "rust": {
        "keyword": "#569Cff",
        "string": "#CE9178",
        "number": "#B5CEA8",
        "macro": "#DCDCAA",
        "attribute": "#C586C0",
        "comment": "#6A9955",
    },
    "css": {
        "selector_id": "#D7BA7D",
        "selector_class": "#4EC9B0",
        "selector_tag": "#D7BA7D",
        "property": "#9CDCFE",
        "value": "#CE9178",
        "comment": "#6A9955",
    },
    "shell": {
        "keyword": "#569Cff",
        "variable": "#9CDCFE",
        "string": "#CE9178",
        "comment": "#6A9955",
        "shebang": "#C586C0",
    },
    "markdown": {
        "heading_1": "#4FC1FF",
        "heading_2": "#61AFEF",
        "heading_3": "#C586C0",
        "heading_4": "#D7BA7D",
        "heading_5": "#B5CEA8",
        "heading_6": "#9CDCFE",
        "blockquote": "#6A9955",
        "list_marker": "#D7BA7D",
        "horizontal_rule": "#808080",
        "inline_code": "#D7BA7D",
        "emphasis": "#CE9178",
        "link_text": "#4FC1FF",
        "link_url": "#9CDCFE",
        "image": "#C586C0",
        "autolink": "#4FC1FF",
        "comment": "#6A9955",
        "fence": "#569CD6",
        "code_block": "#DCDCAA",
    },
    "todo": {
        "checkbox_open": "#DCDCAA",
        "checkbox_done": "#6A9955",
        "task_text": "#D4D4D4",
        "bullet": "#C586C0",
        "comment": "#6A9955",
        "header": "#4FC1FF",
        "tag": "#9CDCFE",
        "priority": "#CE9178",
    },
}


def set_syntax_color_resolver(resolver: SyntaxColorResolver | None) -> None:
    global _syntax_color_resolver
    _syntax_color_resolver = resolver


def canonicalize_syntax_language(language_id: str) -> str:
    raw = str(language_id or "").strip().lower()
    return SYNTAX_LANGUAGE_ALIASES.get(raw, raw)


def syntax_token_defaults() -> dict[str, dict[str, str]]:
    return {lang: dict(tokens) for lang, tokens in SYNTAX_TOKEN_DEFAULTS.items()}


def syntax_language_labels() -> dict[str, str]:
    return dict(SYNTAX_LANGUAGE_LABELS)


def _normalized_hex(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or not _HEX_COLOR_RE.fullmatch(text):
        return None
    return text


def _resolve_syntax_color(language: str, token: str, default: str) -> str:
    canonical_language = canonicalize_syntax_language(language)
    fallback = _normalized_hex(default) or "#ffffff"
    resolver = _syntax_color_resolver
    if resolver is None:
        return fallback
    try:
        resolved = resolver(canonical_language, str(token or "").strip(), fallback)
    except Exception:
        return fallback
    return _normalized_hex(resolved) or fallback


def _fmt(
    language: str,
    token: str,
    default_color: str,
    *,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    family: str = "",
) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(_resolve_syntax_color(language, token, default_color)))
    if bold:
        fmt.setFontWeight(QFont.Bold)
    if italic:
        fmt.setFontItalic(True)
    if underline:
        fmt.setFontUnderline(True)
    if family:
        fmt.setFontFamily(family)
    return fmt


# ---------------- Syntax Highlighters ----------------


class PythonHighlighter(QSyntaxHighlighter):
    """
    Python-first highlighter:
    - keywords, builtins, exceptions
    - decorators
    - def/class name highlighting
    - numbers
    - single/double/triple strings
    - f-strings + {expr} segments
    - comment tags: TODO/FIXME/NOTE/HACK/BUG
    """

    STATE_NORMAL = 0
    STATE_TRIPLE_SINGLE = 1
    STATE_TRIPLE_DOUBLE = 2
    STATE_TRIPLE_SINGLE_F = 3
    STATE_TRIPLE_DOUBLE_F = 4

    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "python")

        # ---------- formats ----------
        self.fmt_kw = _fmt(self._language_id, "keyword", "#569Cff", bold=True)
        self.fmt_soft_kw = _fmt(self._language_id, "soft_keyword", "#4FC1FF", bold=True)
        self.fmt_builtin = _fmt(self._language_id, "builtin", "#4EC9B0")
        self.fmt_exception = _fmt(self._language_id, "exception", "#DCDCAA")
        self.fmt_decorator = _fmt(self._language_id, "decorator", "#C586C0")
        self.fmt_defclass_kw = _fmt(self._language_id, "definition_keyword", "#569Cff", bold=True)
        self.fmt_defclass_name = _fmt(self._language_id, "definition_name", "#DCDCAA", bold=True)
        self.fmt_operator = _fmt(self._language_id, "operator", "#D4D4D4")
        self.fmt_bracket = _fmt(self._language_id, "bracket", "#D7BA7D")
        self.fmt_string = _fmt(self._language_id, "string", "#CE9178")
        self.fmt_fexpr = _fmt(self._language_id, "fstring_expression", "#FFD580")
        self.fmt_comment = _fmt(self._language_id, "comment", "#6A9955", italic=True)
        self.fmt_comment_tag = _fmt(self._language_id, "comment_tag", "#FFB86C", bold=True)
        self.fmt_number = _fmt(self._language_id, "number", "#B5CEA8")

        # ---------- regex sets ----------
        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []

        keywords = (
            "False", "None", "True", "and", "as", "assert", "async", "await", "break",
            "class", "continue", "def", "del", "elif", "else", "except", "finally",
            "for", "from", "global", "if", "import", "in", "is", "lambda", "match",
            "case", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while",
            "with", "yield"
        )
        for w in keywords:
            self.rules.append((re.compile(rf"\b{w}\b"), self.fmt_kw))

        # self/cls lightly highlighted
        for w in ("self", "cls"):
            self.rules.append((re.compile(rf"\b{w}\b"), self.fmt_soft_kw))

        builtins = (
            "abs","all","any","ascii","bin","bool","breakpoint","bytearray","bytes","callable","chr",
            "classmethod","compile","complex","delattr","dict","dir","divmod","enumerate","eval","exec",
            "filter","float","format","frozenset","getattr","globals","hasattr","hash","help","hex","id",
            "input","int","isinstance","issubclass","iter","len","list","locals","map","max","memoryview",
            "min","next","object","oct","open","ord","pow","print","property","range","repr","reversed",
            "round","set","setattr","slice","sorted","staticmethod","str","sum","super","tuple","type",
            "vars","zip","__import__"
        )
        for w in builtins:
            self.rules.append((re.compile(rf"\b{w}\b"), self.fmt_builtin))

        exceptions = (
            "BaseException","Exception","ArithmeticError","BufferError","LookupError","AssertionError",
            "AttributeError","EOFError","FloatingPointError","GeneratorExit","ImportError","ModuleNotFoundError",
            "IndexError","KeyError","KeyboardInterrupt","MemoryError","NameError","NotImplementedError","OSError",
            "OverflowError","RecursionError","ReferenceError","RuntimeError","StopIteration","StopAsyncIteration",
            "SyntaxError","IndentationError","TabError","SystemError","SystemExit","TypeError","UnboundLocalError",
            "UnicodeError","UnicodeEncodeError","UnicodeDecodeError","UnicodeTranslateError","ValueError",
            "ZeroDivisionError","FileNotFoundError","PermissionError","TimeoutError"
        )
        for w in exceptions:
            self.rules.append((re.compile(rf"\b{w}\b"), self.fmt_exception))

        self.decorator_pat = re.compile(r"(?<!\w)@[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
        self.def_pat = re.compile(r"\b(def)\s+([A-Za-z_]\w*)")
        self.class_pat = re.compile(r"\b(class)\s+([A-Za-z_]\w*)")
        self.comment_pat = re.compile(r"#.*$")
        self.comment_tag_pat = re.compile(r"\b(TODO|FIXME|NOTE|HACK|BUG|XXX)\b")
        self.operator_pat = re.compile(
            r"(?:\*\*=?|//=?|<<=?|>>=?|:=|==|!=|<=|>=|->|\+=|-=|\*=|/=|%=|@=?|&=|\|=|\^=|[+\-*/%&|^~<>!=:@])"
        )
        self.bracket_pat = re.compile(r"[\[\]\(\)\{\}]")

        self.number_pat = re.compile(
            r"\b("
            r"0[bB][01](?:_?[01])*|"
            r"0[oO][0-7](?:_?[0-7])*|"
            r"0[xX][0-9a-fA-F](?:_?[0-9a-fA-F])*|"
            r"(?:\d(?:_?\d)*)?\.\d(?:_?\d)*(?:[eE][+-]?\d(?:_?\d)*)?|"
            r"\d(?:_?\d)*(?:[eE][+-]?\d(?:_?\d)*)?|"
            r"\d(?:_?\d)*"
            r")(?:[jJ])?\b"
        )

        # prefixes
        p_any = r"(?:[rRuUbBfF]{,2})"
        p_f = r"(?:(?:[fF][rR]?)|(?:[rR][fF]))"

        self.sq_pat = re.compile(p_any + r"'([^'\\]|\\.)*'")
        self.dq_pat = re.compile(p_any + r'"([^"\\]|\\.)*"')

        self.fsq_pat = re.compile(p_f + r"'([^'\\]|\\.)*'")
        self.fdq_pat = re.compile(p_f + r'"([^"\\]|\\.)*"')

        self.tri_sq_start = re.compile(p_any + r"'''")
        self.tri_dq_start = re.compile(p_any + r'"""')
        self.ftri_sq_start = re.compile(p_f + r"'''")
        self.ftri_dq_start = re.compile(p_f + r'"""')
        self.tri_sq_end = re.compile(r"'''")
        self.tri_dq_end = re.compile(r'"""')

    # ---------- helpers ----------

    def _apply_basic_rules(self, text: str, offset: int):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(offset + m.start(), m.end() - m.start(), fmt)

        for m in self.number_pat.finditer(text):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.fmt_number)

        for m in self.decorator_pat.finditer(text):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.fmt_decorator)

        for m in self.def_pat.finditer(text):
            ks, ke = m.span(1)
            ns, ne = m.span(2)
            self.setFormat(offset + ks, ke - ks, self.fmt_defclass_kw)
            self.setFormat(offset + ns, ne - ns, self.fmt_defclass_name)

        for m in self.class_pat.finditer(text):
            ks, ke = m.span(1)
            ns, ne = m.span(2)
            self.setFormat(offset + ks, ke - ks, self.fmt_defclass_kw)
            self.setFormat(offset + ns, ne - ns, self.fmt_defclass_name)

        for m in self.operator_pat.finditer(text):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.fmt_operator)

        for m in self.bracket_pat.finditer(text):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.fmt_bracket)

    def _highlight_fexpr_regions(self, text: str, base_offset: int):
        # lightweight brace parser for f-string { ... } regions
        i = 0
        n = len(text)
        while i < n:
            if text[i] == "{":
                if i + 1 < n and text[i + 1] == "{":  # escaped {{
                    i += 2
                    continue
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    ch = text[j]
                    if ch == "{":
                        if j + 1 < n and text[j + 1] == "{":
                            j += 2
                            continue
                        depth += 1
                    elif ch == "}":
                        if j + 1 < n and text[j + 1] == "}":
                            j += 2
                            continue
                        depth -= 1
                    j += 1
                end = j if depth == 0 else n
                self.setFormat(base_offset + i, end - i, self.fmt_fexpr)
                i = end
            else:
                i += 1

    def _highlight_comment_with_tags(self, text: str, offset: int):
        m = self.comment_pat.search(text)
        if not m:
            return
        cs, ce = m.span()
        self.setFormat(offset + cs, ce - cs, self.fmt_comment)

        comment_text = text[cs:ce]
        for tm in self.comment_tag_pat.finditer(comment_text):
            ts, te = tm.span()
            self.setFormat(offset + cs + ts, te - ts, self.fmt_comment_tag)

    # ---------- main ----------

    def highlightBlock(self, text: str):
        self.setCurrentBlockState(self.STATE_NORMAL)

        prev = self.previousBlockState()
        offset = 0
        segment = text

        # Continue multiline string state first
        if prev in (
                self.STATE_TRIPLE_SINGLE, self.STATE_TRIPLE_DOUBLE,
                self.STATE_TRIPLE_SINGLE_F, self.STATE_TRIPLE_DOUBLE_F
        ):
            if prev in (self.STATE_TRIPLE_SINGLE, self.STATE_TRIPLE_SINGLE_F):
                end_pat = self.tri_sq_end
            else:
                end_pat = self.tri_dq_end

            end_m = end_pat.search(text)
            if end_m:
                end = end_m.end()
                self.setFormat(0, end, self.fmt_string)

                if prev in (self.STATE_TRIPLE_SINGLE_F, self.STATE_TRIPLE_DOUBLE_F):
                    self._highlight_fexpr_regions(text[:end], 0)

                offset = end
                segment = text[end:]
                self.setCurrentBlockState(self.STATE_NORMAL)
            else:
                self.setFormat(0, len(text), self.fmt_string)
                if prev in (self.STATE_TRIPLE_SINGLE_F, self.STATE_TRIPLE_DOUBLE_F):
                    self._highlight_fexpr_regions(text, 0)
                self.setCurrentBlockState(prev)
                return

        # Basic rules in remaining segment
        self._apply_basic_rules(segment, offset)

        # Single-line strings
        for m in self.dq_pat.finditer(segment):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.fmt_string)
        for m in self.sq_pat.finditer(segment):
            self.setFormat(offset + m.start(), m.end() - m.start(), self.fmt_string)

        # f-string single-line + {expr}
        for m in self.fdq_pat.finditer(segment):
            s, e = m.span()
            self.setFormat(offset + s, e - s, self.fmt_string)
            self._highlight_fexpr_regions(segment[s:e], offset + s)

        for m in self.fsq_pat.finditer(segment):
            s, e = m.span()
            self.setFormat(offset + s, e - s, self.fmt_string)
            self._highlight_fexpr_regions(segment[s:e], offset + s)

        # Triple strings that start in this block
        i = 0
        n = len(segment)
        while i < n:
            candidates = []
            for pat, kind in (
                    (self.ftri_sq_start, "ftri_sq"),
                    (self.ftri_dq_start, "ftri_dq"),
                    (self.tri_sq_start, "tri_sq"),
                    (self.tri_dq_start, "tri_dq"),
            ):
                m = pat.search(segment, i)
                if m:
                    candidates.append((m.start(), m, kind))

            if not candidates:
                break

            _, m, kind = min(candidates, key=lambda x: x[0])
            s = m.start()
            start_end = m.end()

            if kind in ("tri_sq", "ftri_sq"):
                end_m = self.tri_sq_end.search(segment, start_end)
                end_state = self.STATE_TRIPLE_SINGLE_F if kind == "ftri_sq" else self.STATE_TRIPLE_SINGLE
            else:
                end_m = self.tri_dq_end.search(segment, start_end)
                end_state = self.STATE_TRIPLE_DOUBLE_F if kind == "ftri_dq" else self.STATE_TRIPLE_DOUBLE

            if end_m:
                e = end_m.end()
                self.setFormat(offset + s, e - s, self.fmt_string)
                if kind in ("ftri_sq", "ftri_dq"):
                    self._highlight_fexpr_regions(segment[s:e], offset + s)
                i = e
            else:
                self.setFormat(offset + s, n - s, self.fmt_string)
                if kind in ("ftri_sq", "ftri_dq"):
                    self._highlight_fexpr_regions(segment[s:n], offset + s)
                self.setCurrentBlockState(end_state)
                break

        # Comments + tags (last pass)
        self._highlight_comment_with_tags(segment, offset)
        hide_hash_for_colors(self, text)


class HtmlHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "html")
        tag = _fmt(self._language_id, "tag", "#4EC9B0")
        attr = _fmt(self._language_id, "attribute", "#9CDCFE")
        val = _fmt(self._language_id, "value", "#CE9178")
        com = _fmt(self._language_id, "comment", "#6A9955", italic=True)
        self.rules = [
            (re.compile(r"</?\\w+\\b"), tag),
            (re.compile(r"\\b\\w+(?=\\s*=)"), attr),
            (re.compile(r'"[^"\\\\]*(\\\\.[^"\\\\]*)*"'), val),
            (re.compile(r"'[^'\\\\]*(\\\\.[^'\\\\]*)*'"), val),
            (re.compile(r"<!--.*?-->"), com),
        ]
    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        hide_hash_for_colors(self, text)


class JavaScriptHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "javascript")
        kw = _fmt(self._language_id, "keyword", "#569Cff", bold=True)
        self.rules = [(re.compile(r"\\b" + w + r"\\b"), kw) for w in (
            "break","case","catch","class","const","continue","debugger","default","delete","do",
            "else","export","extends","finally","for","function","if","import","in","instanceof","let",
            "new","return","super","switch","this","throw","try","typeof","var","void","while","with",
            "yield","true","false","null","async","await"
        )]
        strf = _fmt(self._language_id, "string", "#CE9178")
        self.rules += [
            (re.compile(r'"[^"\\\\]*(\\\\.[^"\\\\]*)*"'), strf),
            (re.compile(r"'[^'\\\\]*(\\\\.[^'\\\\]*)*'"), strf),
            (re.compile(r"`[^`\\\\]*(\\\\.[^`\\\\]*)*`"), strf),
        ]
        com = _fmt(self._language_id, "comment", "#6A9955")
        self.rules += [
            (re.compile(r"//[^\n]*"), com), (re.compile(r"/\\*.*?\\*/"), com),
        ]
        num = _fmt(self._language_id, "number", "#B5CEA8")
        self.rules.append((re.compile(r"\\b\\d+(\\.\\d+)?\\b"), num))
        fn = _fmt(self._language_id, "function", "#DCDCAA")
        self.rules.append((re.compile(r"\\b\\w+(?=\\()"), fn))
    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        hide_hash_for_colors(self, text)


class PhpHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "php")
        kw = _fmt(self._language_id, "keyword", "#569Cff", bold=True)
        kws = (
            "__halt_compiler","abstract","and","array","as","break","callable","case","catch","class",
            "clone","const","continue","declare","default","die","do","echo","else","elseif","empty",
            "enddeclare","endfor","endforeach","endif","endswitch","endwhile","eval","exit","extends",
            "final","finally","for","foreach","function","global","goto","if","implements","include",
            "include_once","instanceof","insteadof","interface","isset","list","namespace","new","or",
            "print","private","protected","public","require","require_once","return","static","switch",
            "throw","trait","try","unset","use","var","while","xor","yield","true","false","null"
        )
        self.rules = [(re.compile(r"\\b" + w + r"\\b"), kw) for w in kws]
        var = _fmt(self._language_id, "variable", "#9CDCFE")
        self.rules.append((re.compile(r"\\$\\w+\\b"), var))
        strf = _fmt(self._language_id, "string", "#CE9178")
        self.rules += [
            (re.compile(r'"[^"\\\\]*(\\\\.[^"\\\\]*)*"'), strf),
            (re.compile(r"'[^'\\\\]*(\\\\.[^'\\\\]*)*'"), strf),
        ]
        com = _fmt(self._language_id, "comment", "#6A9955")
        self.rules += [
            (re.compile(r"//[^\n]*"), com), (re.compile(r"#[^\n]*"), com), (re.compile(r"/\\*.*?\\*/"), com)
        ]
        tag = _fmt(self._language_id, "tag", "#569Cff", bold=True)
        self.rules.append((re.compile(r"<\\?php|\\?>"), tag))
    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
            hide_hash_for_colors(self, text)


class CppHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "cpp")
        kw = _fmt(self._language_id, "keyword", "#569Cff", bold=True)
        keywords = (
            "alignas", "alignof", "asm", "auto", "bool", "break", "case", "catch", "char",
            "char8_t", "char16_t", "char32_t", "class", "concept", "const", "consteval",
            "constexpr", "constinit", "const_cast", "continue", "co_await", "co_return",
            "co_yield", "decltype", "default", "delete", "do", "double", "dynamic_cast",
            "else", "enum", "explicit", "export", "extern", "false", "float", "for", "friend",
            "goto", "if", "inline", "int", "long", "mutable", "namespace", "new", "noexcept",
            "nullptr", "operator", "private", "protected", "public", "register", "reinterpret_cast",
            "requires", "return", "short", "signed", "sizeof", "static", "static_assert",
            "static_cast", "struct", "switch", "template", "this", "thread_local", "throw",
            "true", "try", "typedef", "typeid", "typename", "union", "unsigned", "using",
            "virtual", "void", "volatile", "wchar_t", "while",
        )
        self.rules = [(re.compile(rf"\b{re.escape(w)}\b"), kw) for w in keywords]

        pre = _fmt(self._language_id, "preprocessor", "#C586C0")
        self.rules.append((re.compile(r"^\s*#.*"), pre))

        strf = _fmt(self._language_id, "string", "#CE9178")
        self.rules += [
            (re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), strf),
            (re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), strf),
        ]

        num = _fmt(self._language_id, "number", "#B5CEA8")
        self.rules.append(
            (
                re.compile(
                    r"\b(?:"
                    r"0b[01](?:'?[01])*"
                    r"|0x[0-9a-fA-F](?:'?[0-9a-fA-F])*"
                    r"|0[0-7](?:'?[0-7])*"
                    r"|\d(?:'?\d)*(?:\.\d(?:'?\d)*)?(?:[eE][+-]?\d(?:'?\d)*)?"
                    r")(?:u|U|l|L|ll|LL|f|F)?\b"
                ),
                num,
            )
        )

        com = _fmt(self._language_id, "comment", "#6A9955")
        self.rules += [
            (re.compile(r"//[^\n]*"), com),
            (re.compile(r"/\*.*?\*/"), com),
        ]
    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        hide_hash_for_colors(self, text)


class JsonHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "json")
        key = _fmt(self._language_id, "key", "#9CDCFE")
        strf = _fmt(self._language_id, "string", "#CE9178")
        num = _fmt(self._language_id, "number", "#B5CEA8")
        self.rules = [
            (re.compile(r'"[^"\\\\]*(\\\\.[^"\\\\]*)*"(?=\s*:)'), key),
            (re.compile(r'"[^"\\\\]*(\\\\.[^"\\\\]*)*"'), strf),
            (re.compile(r"\\b-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b"), num),
        ]
    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        hide_hash_for_colors(self, text)


class TomlHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "toml")

        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []

        key = _fmt(self._language_id, "key", "#9CDCFE")
        self.rules.extend(
            [
                (re.compile(r"^\s*[A-Za-z0-9_.-]+\s*(?=\=)"), key),
                (re.compile(r'^\s*"[^"\\]*(?:\\.[^"\\]*)*"\s*(?=\=)'), key),
                (re.compile(r"^\s*'[^'\\]*(?:\\.[^'\\]*)*'\s*(?=\=)"), key),
            ]
        )

        table = _fmt(self._language_id, "table", "#4EC9B0", bold=True)
        self.rules.extend(
            [
                (re.compile(r"\[\[[^\]]+\]\]"), table),
                (re.compile(r"\[[^\]]+\]"), table),
            ]
        )

        string = _fmt(self._language_id, "string", "#CE9178")
        self.rules.extend(
            [
                (re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'), string),
                (re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'"), string),
            ]
        )

        number = _fmt(self._language_id, "number", "#B5CEA8")
        self.rules.extend(
            [
                (re.compile(r"\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"), number),
            ]
        )

        literal = _fmt(self._language_id, "literal", "#569Cff", bold=True)
        self.rules.extend(
            [
                (re.compile(r"\b(?:true|false)\b"), literal),
            ]
        )

        date = _fmt(self._language_id, "date", "#DCDCAA")
        self.rules.extend(
            [
                (
                    re.compile(
                        r"\b\d{4}-\d{2}-\d{2}"
                        r"(?:[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?"
                        r"(?:[Zz]|[+-]\d{2}:\d{2})?\b"
                    ),
                    date,
                )
            ]
        )

        comment = _fmt(self._language_id, "comment", "#6A9955", italic=True)
        self.rules.append((re.compile(r"#[^\n]*"), comment))

    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        hide_hash_for_colors(self, text)


class RustHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "rust")
        kw = _fmt(self._language_id, "keyword", "#569Cff", bold=True)
        self.rules = [(re.compile(r"\b" + w + r"\b"), kw) for w in (
            "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum",
            "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod",
            "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super",
            "trait", "true", "type", "unsafe", "use", "where", "while",
            # common contextual/reserved words
            "abstract", "become", "box", "do", "final", "macro", "override", "priv",
            "typeof", "unsized", "virtual", "yield", "try",
        )]

        strf = _fmt(self._language_id, "string", "#CE9178")
        self.rules += [
            (re.compile(r'r#*"(?:[^"\\]|\\.)*"#*'), strf),
            (re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), strf),
            (re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), strf),
        ]

        num = _fmt(self._language_id, "number", "#B5CEA8")
        self.rules += [
            (
                re.compile(
                    r"\b(?:"
                    r"0b[01](?:_?[01])*"
                    r"|0o[0-7](?:_?[0-7])*"
                    r"|0x[0-9a-fA-F](?:_?[0-9a-fA-F])*"
                    r"|\d(?:_?\d)*(?:\.\d(?:_?\d)*)?(?:[eE][+-]?\d(?:_?\d)*)?"
                    r")(?:u8|u16|u32|u64|u128|usize|i8|i16|i32|i64|i128|isize|f32|f64)?\b"
                ),
                num,
            ),
        ]

        macro = _fmt(self._language_id, "macro", "#DCDCAA")
        self.rules += [(re.compile(r"\b[A-Za-z_]\w*!\b"), macro)]

        attr = _fmt(self._language_id, "attribute", "#C586C0")
        self.rules += [(re.compile(r"#\s*!\[[^\]]*\]|#\s*\[[^\]]*\]"), attr)]

        com = _fmt(self._language_id, "comment", "#6A9955")
        self.rules += [
            (re.compile(r"//[^\n]*"), com),
            (re.compile(r"/\*.*?\*/"), com),
        ]

    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        hide_hash_for_colors(self, text)


class CssHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "css")
        idf = _fmt(self._language_id, "selector_id", "#D7BA7D")
        clsf = _fmt(self._language_id, "selector_class", "#4EC9B0")
        tagf = _fmt(self._language_id, "selector_tag", "#D7BA7D")
        prop = _fmt(self._language_id, "property", "#9CDCFE")
        val = _fmt(self._language_id, "value", "#CE9178")
        com = _fmt(self._language_id, "comment", "#6A9955", italic=True)
        self.rules = [
            (re.compile(r"/\\*.*?\\*/"), com),
            (re.compile(r"#[A-Za-z0-9_-]+"), idf),
            (re.compile(r"\\.[A-Za-z0-9_-]+"), clsf),
            (re.compile(r"\\b[a-z-]+\\s*(?=\\s*:)"), prop),
            (re.compile(r"\\b\\d+(\\.\\d+)?(px|em|%|rem|pt|vw|vh)\\b"), val),
            (re.compile(r"#[0-9a-fA-F]{3,6}\\b"), val),
            (re.compile(r'"[^"\\\\]*(\\\\.[^"\\\\]*)*"'), val),
            (re.compile(r"'[^'\\\\]*(\\\\.[^'\\\\]*)*'"), val),
            (re.compile(r"\\b(a|abbr|address|area|article|aside|audio|b|base|bdi|bdo|blockquote|body|br|button|canvas|caption|cite|code|col|colgroup|data|datalist|dd|del|details|dfn|dialog|div|dl|dt|em|embed|fieldset|figcaption|figure|footer|form|h1|h2|h3|h4|h5|h6|head|header|hr|html|i|iframe|img|input|ins|kbd|label|legend|li|link|main|map|mark|meta|meter|nav|noscript|object|ol|optgroup|option|output|p|param|picture|pre|progress|q|rp|rt|ruby|s|samp|script|section|select|small|source|span|strong|style|sub|summary|sup|table|tbody|td|template|textarea|tfoot|th|thead|time|title|tr|track|u|ul|var|video|wbr)\\b"), tagf),
        ]
    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        hide_hash_for_colors(self, text)

class BashHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "shell")

        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []

        # --- Keywords / builtins ---
        kw = _fmt(self._language_id, "keyword", "#569Cff", bold=True)

        keywords = (
            "if", "then", "else", "elif", "fi",
            "for", "while", "until", "do", "done",
            "case", "esac", "in", "select",
            "function", "time",
            # common builtins
            "echo", "printf", "read", "exit", "return",
            "cd", "pwd", "export", "local", "shift",
            "trap", "source", ".", "alias", "unalias",
            "set", "unset", "test", "[", "]",
        )

        for w in keywords:
            self.rules.append((re.compile(r"\b" + re.escape(w) + r"\b"), kw))

        # --- Variables ---
        var = _fmt(self._language_id, "variable", "#9CDCFE")

        # $VAR
        self.rules.append(
            (re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*"), var)
        )
        # ${VAR...}
        self.rules.append(
            (re.compile(r"\$\{[^}]+\}"), var)
        )
        # $1, $2, ...
        self.rules.append(
            (re.compile(r"\$[0-9]+"), var)
        )
        # Special parameters: $#, $@, $*, $?, $$, $!, $-
        # (avoid a character class to dodge any engine weirdness)
        self.rules.append(
            (re.compile(r"\$(?:#|@|\*|\?|\$|!|-)"), var)
        )

        # --- Strings ---
        strf = _fmt(self._language_id, "string", "#CE9178")
        self.rules += [
            (re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), strf),
            (re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), strf),
        ]

        # --- Comments (# ... end of line) ---
        com = _fmt(self._language_id, "comment", "#6A9955", italic=True)
        self.rules.append(
            (re.compile(r"#[^\n]*"), com)
        )

        # --- Shebang line ---
        shebang = _fmt(self._language_id, "shebang", "#C586C0")
        self.rules.append(
            (re.compile(r"^#!.*$"), shebang)
        )

    def highlightBlock(self, text: str):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        hide_hash_for_colors(self, text)


class MarkdownBlockData(QTextBlockUserData):
    def __init__(
        self,
        *,
        in_fence: bool = False,
        fence_delim: str = "",
        fence_lang: str = "",
        delegate_state: int = -1,
    ):
        super().__init__()
        self.in_fence = in_fence
        self.fence_delim = fence_delim
        self.fence_lang = fence_lang
        self.delegate_state = delegate_state
        
class MarkdownHighlighter(QSyntaxHighlighter):
    
    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "markdown")

        # -----------------------------
        # Core formats
        # -----------------------------
        self.fmt_h1 = _fmt(self._language_id, "heading_1", "#4FC1FF", bold=True)
        self.fmt_h2 = _fmt(self._language_id, "heading_2", "#61AFEF", bold=True)
        self.fmt_h3 = _fmt(self._language_id, "heading_3", "#C586C0", bold=True)
        self.fmt_h4 = _fmt(self._language_id, "heading_4", "#D7BA7D", bold=True)
        self.fmt_h5 = _fmt(self._language_id, "heading_5", "#B5CEA8", bold=True)
        self.fmt_h6 = _fmt(self._language_id, "heading_6", "#9CDCFE", bold=True)
        self.fmt_blockquote = _fmt(self._language_id, "blockquote", "#6A9955", italic=True)
        self.fmt_list = _fmt(self._language_id, "list_marker", "#D7BA7D", bold=True)
        self.fmt_hr = _fmt(self._language_id, "horizontal_rule", "#808080", bold=True)
        self.fmt_inline_code = _fmt(self._language_id, "inline_code", "#D7BA7D", family="monospace")
        self.fmt_bold = _fmt(self._language_id, "emphasis", "#CE9178", bold=True)
        self.fmt_italic = _fmt(self._language_id, "emphasis", "#CE9178", italic=True)
        self.fmt_bold_italic = _fmt(self._language_id, "emphasis", "#CE9178", bold=True, italic=True)
        self.fmt_link_text = _fmt(self._language_id, "link_text", "#4FC1FF", underline=True)
        self.fmt_link_url = _fmt(self._language_id, "link_url", "#9CDCFE")
        self.fmt_image = _fmt(self._language_id, "image", "#C586C0")
        self.fmt_autolink = _fmt(self._language_id, "autolink", "#4FC1FF", underline=True)
        self.fmt_comment = _fmt(self._language_id, "comment", "#6A9955", italic=True)
        self.fmt_fence = _fmt(self._language_id, "fence", "#569CD6", bold=True)
        self.fmt_code_block_fallback = _fmt(self._language_id, "code_block", "#DCDCAA", family="monospace")

        # -----------------------------
        # Regexes
        # -----------------------------
        self.re_atx_headings = [
            (re.compile(r"^\s*#(?!#)\s+.*$"), self.fmt_h1),
            (re.compile(r"^\s*##(?!#)\s+.*$"), self.fmt_h2),
            (re.compile(r"^\s*###(?!#)\s+.*$"), self.fmt_h3),
            (re.compile(r"^\s*####(?!#)\s+.*$"), self.fmt_h4),
            (re.compile(r"^\s*#####(?!#)\s+.*$"), self.fmt_h5),
            (re.compile(r"^\s*######(?!#)\s+.*$"), self.fmt_h6),
        ]

        self.re_blockquote = re.compile(r"^\s*>\s?.*$")
        self.re_ulist = re.compile(r"^\s*[-+*]\s+")
        self.re_olist = re.compile(r"^\s*\d+\.\s+")
        self.re_hr = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
        self.re_html_comment = re.compile(r"<!--.*?-->")

        self.re_inline_code = re.compile(r"`[^`\n]+`")

        self.re_bold_italic = re.compile(r"(\*\*\*|___)(?=\S)(.+?)(?<=\S)\1")
        self.re_bold = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
        self.re_italic = re.compile(r"(\*|_)(?=\S)(.+?)(?<=\S)\1")

        self.re_autolink_url = re.compile(r"<https?://[^ >]+>")
        self.re_autolink_email = re.compile(r"<[^ >@]+@[^ >]+>")

        self.re_image = re.compile(r"!\[([^\]]*)\]\(([^)\s]+(?:\s+\"[^\"]*\")?)\)")
        self.re_link_inline = re.compile(r"\[([^\]]+)\]\(([^)\s]+(?:\s+\"[^\"]*\")?)\)")
        self.re_link_reference = re.compile(r"\[([^\]]+)\]\[([^\]]*)\]")
        self.re_link_def = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s+(\S+).*$")

        self.re_fence = re.compile(r"^\s*(```+|~~~+)\s*([\w#+.-]*)\s*$")
        self.re_setext_h1 = re.compile(r"^\s*=+\s*$")
        self.re_setext_h2 = re.compile(r"^\s*-+\s*$")

        # alias map for fenced code language names
        self.fence_language_aliases = {
            "py": "python",
            "python": "python",
            "html": "html",
            "htm": "html",
            "xml": "xml",
            "js": "javascript",
            "javascript": "javascript",
            "jsx": "javascriptreact",
            "php": "php",
            "c": "c",
            "h": "c",
            "hpp": "cpp",
            "cc": "cpp",
            "cxx": "cpp",
            "cpp": "cpp",
            "json": "json",
            "jsonc": "jsonc",
            "rs": "rust",
            "rust": "rust",
            "css": "css",
            "scss": "scss",
            "less": "less",
            "toml": "toml",
            "qsst": "toml",
            "sh": "shell",
            "bash": "shell",
            "zsh": "shell",
            "make": "make",
            "mk": "make",
            "md": "markdown",
            "markdown": "markdown",
            "todo": "todo",
        }

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------
    def _apply_match(self, m: re.Match, fmt: QTextCharFormat):
        self.setFormat(m.start(), m.end() - m.start(), fmt)

    def _apply_regex(self, text: str, pattern: re.Pattern, fmt: QTextCharFormat):
        for m in pattern.finditer(text):
            self._apply_match(m, fmt)

    def _previous_markdown_data(self) -> MarkdownBlockData | None:
        prev_block = self.currentBlock().previous()
        if not prev_block.isValid():
            return None
        data = prev_block.userData()
        return data if isinstance(data, MarkdownBlockData) else None

    def _current_text(self) -> str:
        return self.currentBlock().text()

    def _next_text(self) -> str:
        next_block = self.currentBlock().next()
        if not next_block.isValid():
            return ""
        return next_block.text()

    def _set_block_data(
        self,
        *,
        in_fence: bool,
        fence_delim: str = "",
        fence_lang: str = "",
        delegate_state: int = -1,
    ):
        self.setCurrentBlockUserData(
            MarkdownBlockData(
                in_fence=in_fence,
                fence_delim=fence_delim,
                fence_lang=fence_lang,
                delegate_state=delegate_state,
            )
        )

    def _is_setext_heading_text_line(self, text: str) -> QTextCharFormat | None:
        if not text.strip():
            return None
        next_text = self._next_text()
        if self.re_setext_h1.match(next_text):
            return self.fmt_h1
        if self.re_setext_h2.match(next_text):
            return self.fmt_h2
        return None

    def _format_inline_links_and_images(self, text: str):
        # images: ![alt](url)
        for m in self.re_image.finditer(text):
            full_start, full_end = m.span(0)
            alt_start, alt_end = m.span(1)
            url_start, url_end = m.span(2)

            self.setFormat(full_start, full_end - full_start, self.fmt_image)
            self.setFormat(alt_start, alt_end - alt_start, self.fmt_link_text)
            self.setFormat(url_start, url_end - url_start, self.fmt_link_url)

        # inline links: [text](url)
        for m in self.re_link_inline.finditer(text):
            txt_start, txt_end = m.span(1)
            url_start, url_end = m.span(2)

            self.setFormat(txt_start, txt_end - txt_start, self.fmt_link_text)
            self.setFormat(url_start, url_end - url_start, self.fmt_link_url)

        # reference links: [text][id]
        for m in self.re_link_reference.finditer(text):
            txt_start, txt_end = m.span(1)
            self.setFormat(txt_start, txt_end - txt_start, self.fmt_link_text)

        # link definitions: [id]: https://...
        for m in self.re_link_def.finditer(text):
            id_start, id_end = m.span(1)
            url_start, url_end = m.span(2)
            self.setFormat(id_start, id_end - id_start, self.fmt_link_text)
            self.setFormat(url_start, url_end - url_start, self.fmt_link_url)

        # autolinks
        self._apply_regex(text, self.re_autolink_url, self.fmt_autolink)
        self._apply_regex(text, self.re_autolink_email, self.fmt_autolink)

    def _delegate_to_highlighter(
        self,
        text: str,
        language: str,
        previous_delegate_state: int,
    ) -> int:
        mapped_language = self.fence_language_aliases.get(language.lower(), language.lower())
        delegate_cls = LANGUAGE_HIGHLIGHTER_MAP.get(mapped_language)

        if delegate_cls is None or delegate_cls is MarkdownHighlighter:
            self.setFormat(0, len(text), self.fmt_code_block_fallback)
            return -1

        delegate = delegate_cls(None, language_id=mapped_language)

        applied_formats: list[tuple[int, int, QTextCharFormat]] = []
        current_state = {"value": -1}

        def fake_set_format(start: int, count: int, fmt: QTextCharFormat):
            applied_formats.append((start, count, fmt))

        def fake_previous_block_state():
            return previous_delegate_state

        def fake_set_current_block_state(state: int):
            current_state["value"] = state

        # monkeypatch instance methods/attrs used by your existing highlighters
        delegate.setFormat = fake_set_format
        delegate.previousBlockState = fake_previous_block_state
        delegate.setCurrentBlockState = fake_set_current_block_state

        try:
            delegate.highlightBlock(text)
        except Exception:
            self.setFormat(0, len(text), self.fmt_code_block_fallback)
            return -1

        if not applied_formats:
            self.setFormat(0, len(text), self.fmt_code_block_fallback)
        else:
            for start, count, fmt in applied_formats:
                self.setFormat(start, count, fmt)

        return current_state["value"]

    # -------------------------------------------------
    # Main
    # -------------------------------------------------
    def highlightBlock(self, text: str):
        self.setCurrentBlockState(0)

        prev_data = self._previous_markdown_data()

        # ---------------------------------------------
        # Continue fenced code block from previous line
        # ---------------------------------------------
        if prev_data and prev_data.in_fence:
            fence_match = self.re_fence.match(text)
            if fence_match and fence_match.group(1).startswith(prev_data.fence_delim[0]):
                self.setFormat(0, len(text), self.fmt_fence)
                self._set_block_data(in_fence=False)
                hide_hash_for_colors(self, text)
                return

            next_delegate_state = self._delegate_to_highlighter(
                text=text,
                language=prev_data.fence_lang,
                previous_delegate_state=prev_data.delegate_state,
            )
            self._set_block_data(
                in_fence=True,
                fence_delim=prev_data.fence_delim,
                fence_lang=prev_data.fence_lang,
                delegate_state=next_delegate_state,
            )
            hide_hash_for_colors(self, text)
            return

        # ---------------------------------------------
        # Fence opening
        # ---------------------------------------------
        fence_match = self.re_fence.match(text)
        if fence_match:
            delim = fence_match.group(1)
            lang = (fence_match.group(2) or "").strip()
            self.setFormat(0, len(text), self.fmt_fence)
            self._set_block_data(
                in_fence=True,
                fence_delim=delim,
                fence_lang=lang,
                delegate_state=-1,
            )
            hide_hash_for_colors(self, text)
            return

        self._set_block_data(in_fence=False)

        # ---------------------------------------------
        # Setext heading underline line
        # ---------------------------------------------
        if self.re_setext_h1.match(text) or self.re_setext_h2.match(text):
            self.setFormat(0, len(text), self.fmt_hr)
            hide_hash_for_colors(self, text)
            return

        # ---------------------------------------------
        # Setext heading text line
        # ---------------------------------------------
        setext_fmt = self._is_setext_heading_text_line(text)
        if setext_fmt is not None:
            self.setFormat(0, len(text), setext_fmt)
            hide_hash_for_colors(self, text)
            return

        # ---------------------------------------------
        # ATX headings
        # ---------------------------------------------
        for pat, fmt in self.re_atx_headings:
            m = pat.match(text)
            if m:
                self.setFormat(0, len(text), fmt)
                hide_hash_for_colors(self, text)
                return

        # ---------------------------------------------
        # Block-level markdown
        # ---------------------------------------------
        if self.re_blockquote.match(text):
            self.setFormat(0, len(text), self.fmt_blockquote)

        if self.re_ulist.match(text):
            m = self.re_ulist.match(text)
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_list)

        if self.re_olist.match(text):
            m = self.re_olist.match(text)
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_list)

        if self.re_hr.match(text):
            self.setFormat(0, len(text), self.fmt_hr)

        self._apply_regex(text, self.re_html_comment, self.fmt_comment)

        # ---------------------------------------------
        # Inline markdown
        # ---------------------------------------------
        self._apply_regex(text, self.re_inline_code, self.fmt_inline_code)

        # order matters a bit here
        self._apply_regex(text, self.re_bold_italic, self.fmt_bold_italic)
        self._apply_regex(text, self.re_bold, self.fmt_bold)
        self._apply_regex(text, self.re_italic, self.fmt_italic)

        self._format_inline_links_and_images(text)

        hide_hash_for_colors(self, text)

class TodoHighlighter(QSyntaxHighlighter):
    """
    Lightweight highlighter for .todo files.

    Supported line types:
      - Task lines: optional bullet + [ ] / [x] / [✔] + text
      - Comment lines: optional indent + #
      - Header lines: text ending with ':' (not comments/tasks)
      - Tags inside task text: @word
      - Priority markers: !, !!, !!!
    """

    # Accepts:
    #   [ ] task
    #   [x] task
    #   [✔] task
    #   - [ ] task
    #   * [x] task
    TASK_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?:(?P<bullet>[-*+])\s+)?(?P<box>\[(?: |x|X|✔)\])(?P<rest>.*)$'
    )
    COMMENT_RE = re.compile(r'^[ \t]*#.*$')
    HEADER_RE = re.compile(r'^[ \t]*[^#\[\]\n][^#\n]*:\s*$')  # simple "Section:" lines
    TAG_RE = re.compile(r'(?<!\w)@[A-Za-z_][\w-]*')
    PRIORITY_RE = re.compile(r'(?<!\w)!{1,3}(?!\w)')

    def __init__(self, parent=None, *, language_id: str | None = None):
        super().__init__(parent)
        self._language_id = canonicalize_syntax_language(language_id or "todo")

        # Task checkbox format
        self.fmt_box_open = _fmt(self._language_id, "checkbox_open", "#DCDCAA")
        self.fmt_box_done = _fmt(self._language_id, "checkbox_done", "#6A9955")

        # Rest of task text
        self.fmt_task_text = _fmt(self._language_id, "task_text", "#D4D4D4")

        # Optional bullet
        self.fmt_bullet = _fmt(self._language_id, "bullet", "#C586C0")

        # Comments
        self.fmt_comment = _fmt(self._language_id, "comment", "#6A9955")

        # Headers
        self.fmt_header = _fmt(self._language_id, "header", "#4FC1FF")

        # Tags / priority
        self.fmt_tag = _fmt(self._language_id, "tag", "#9CDCFE")
        self.fmt_priority = _fmt(self._language_id, "priority", "#CE9178")

    def highlightBlock(self, text: str) -> None:
        # 1) Comment line wins immediately
        if self.COMMENT_RE.match(text):
            self.setFormat(0, len(text), self.fmt_comment)
            return

        # 2) Task line
        m_task = self.TASK_RE.match(text)
        if m_task:
            indent_len = len(m_task.group("indent") or "")
            bullet = m_task.group("bullet")
            box = m_task.group("box")
            rest = m_task.group("rest") or ""

            # bullet formatting
            if bullet:
                # bullet position is after indent
                self.setFormat(indent_len, 1, self.fmt_bullet)

            # checkbox formatting
            box_start = m_task.start("box")
            box_len = len(box)
            if box in {"[x]", "[X]", "[✔]"}:
                self.setFormat(box_start, box_len, self.fmt_box_done)
            else:
                self.setFormat(box_start, box_len, self.fmt_box_open)

            # rest text formatting
            rest_start = m_task.start("rest")
            if rest:
                self.setFormat(rest_start, len(rest), self.fmt_task_text)

            # inline tags / priorities inside task rest
            for tm in self.TAG_RE.finditer(text):
                self.setFormat(tm.start(), tm.end() - tm.start(), self.fmt_tag)

            for pm in self.PRIORITY_RE.finditer(text):
                self.setFormat(pm.start(), pm.end() - pm.start(), self.fmt_priority)

            return

        # 3) Header line
        if self.HEADER_RE.match(text):
            self.setFormat(0, len(text), self.fmt_header)
            return

        # 4) Fallback: still highlight tags/priorities in plain lines
        for tm in self.TAG_RE.finditer(text):
            self.setFormat(tm.start(), tm.end() - tm.start(), self.fmt_tag)
        for pm in self.PRIORITY_RE.finditer(text):
            self.setFormat(pm.start(), pm.end() - pm.start(), self.fmt_priority)


def hide_hash_for_colors(h :QSyntaxHighlighter, text: str):
    for m in _COLOR_PATTERN.finditer(text):
        start = m.start()
        h.setFormat(start, 1, _transparent_hash_fmt)



LANGUAGE_HIGHLIGHTER_MAP: dict[str, type[QSyntaxHighlighter]] = {
    "python": PythonHighlighter,
    "html": HtmlHighlighter,
    "xml": HtmlHighlighter,
    "javascript": JavaScriptHighlighter,
    "javascriptreact": JavaScriptHighlighter,
    "php": PhpHighlighter,
    "c": CppHighlighter,
    "cpp": CppHighlighter,
    "json": JsonHighlighter,
    "jsonc": JsonHighlighter,
    "rust": RustHighlighter,
    "css": CssHighlighter,
    "scss": CssHighlighter,
    "less": CssHighlighter,
    "toml": TomlHighlighter,
    "shell": BashHighlighter,
    "make": CppHighlighter,
    "todo": TodoHighlighter,
    "markdown": MarkdownHighlighter,
}


def _clear_editor_highlighter(editor: "CodeEditor") -> None:
    old = getattr(editor, "_highlighter", None)
    try:
        editor._highlighter = None
    except Exception:
        pass
    if old is not None:
        try:
            old.setDocument(None)
        except Exception:
            pass
        try:
            old.deleteLater()
        except Exception:
            pass


def set_language_highlighter(editor: "CodeEditor", language_id: str) -> None:
    _clear_editor_highlighter(editor)

    raw_language = str(language_id or "").strip().lower()
    highlighter_cls = LANGUAGE_HIGHLIGHTER_MAP.get(raw_language)
    if highlighter_cls is None:
        return
    canonical_language = canonicalize_syntax_language(raw_language)

    doc = editor.document()
    # Keep one syntax highlighter per document to avoid stale/duplicate
    # highlighter instances when multiple editor views share a document.
    for child in list(doc.children()):
        if isinstance(child, QSyntaxHighlighter):
            try:
                child.setDocument(None)
            except Exception:
                pass
            try:
                child.deleteLater()
            except Exception:
                pass
    try:
        editor._highlighter = highlighter_cls(doc, language_id=canonical_language)
    except TypeError:
        editor._highlighter = highlighter_cls(doc)


def ensure_highlighter(editor: "CodeEditor") -> None:
    language_id = "plaintext"
    resolver = getattr(editor, "language_id", None)
    if callable(resolver):
        try:
            language_id = str(resolver() or "plaintext")
        except Exception:
            language_id = "plaintext"
    set_language_highlighter(editor, language_id)


def set_highlighter_for_file(editor, file_path: str) -> None:
    language_id = get_language_id(file_path, fallback="plaintext")
    set_language_highlighter(editor, language_id)


__all__ = [
    "LANGUAGE_HIGHLIGHTER_MAP",
    "SYNTAX_TOKEN_DEFAULTS",
    "SYNTAX_LANGUAGE_LABELS",
    "canonicalize_syntax_language",
    "set_syntax_color_resolver",
    "syntax_language_labels",
    "syntax_token_defaults",
    "ensure_highlighter",
    "set_language_highlighter",
    "set_highlighter_for_file",
    "PythonHighlighter",
    "HtmlHighlighter",
    "JavaScriptHighlighter",
    "PhpHighlighter",
    "CppHighlighter",
    "JsonHighlighter",
    "TomlHighlighter",
    "RustHighlighter",
    "CssHighlighter",
    "BashHighlighter",
    "MarkdownHighlighter",
    "TodoHighlighter",
]
