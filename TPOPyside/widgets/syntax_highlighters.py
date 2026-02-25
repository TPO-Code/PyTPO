"""Syntax highlighters and language-to-highlighter mapping for CodeEditor."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PySide6.QtGui import QBrush, QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from TPOPyside.widgets.keypress_handlers import get_language_id

if TYPE_CHECKING:
    from TPOPyside.widgets.code_editor import CodeEditor

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

    def __init__(self, parent=None):
        super().__init__(parent)

        # ---------- formats ----------
        self.fmt_kw = QTextCharFormat()
        self.fmt_kw.setForeground(QColor("#569Cff"))
        self.fmt_kw.setFontWeight(QFont.Bold)

        self.fmt_soft_kw = QTextCharFormat()
        self.fmt_soft_kw.setForeground(QColor("#4FC1FF"))
        self.fmt_soft_kw.setFontWeight(QFont.Bold)

        self.fmt_builtin = QTextCharFormat()
        self.fmt_builtin.setForeground(QColor("#4EC9B0"))

        self.fmt_exception = QTextCharFormat()
        self.fmt_exception.setForeground(QColor("#DCDCAA"))

        self.fmt_decorator = QTextCharFormat()
        self.fmt_decorator.setForeground(QColor("#C586C0"))

        self.fmt_defclass_kw = QTextCharFormat()
        self.fmt_defclass_kw.setForeground(QColor("#569Cff"))
        self.fmt_defclass_kw.setFontWeight(QFont.Bold)

        self.fmt_defclass_name = QTextCharFormat()
        self.fmt_defclass_name.setForeground(QColor("#DCDCAA"))
        self.fmt_defclass_name.setFontWeight(QFont.Bold)

        self.fmt_string = QTextCharFormat()
        self.fmt_string.setForeground(QColor("#CE9178"))

        self.fmt_fexpr = QTextCharFormat()
        self.fmt_fexpr.setForeground(QColor("#FFD580"))  # warm highlight inside {...}

        self.fmt_comment = QTextCharFormat()
        self.fmt_comment.setForeground(QColor("#6A9955"))
        self.fmt_comment.setFontItalic(True)

        self.fmt_comment_tag = QTextCharFormat()
        self.fmt_comment_tag.setForeground(QColor("#FFB86C"))
        self.fmt_comment_tag.setFontWeight(QFont.Bold)

        self.fmt_number = QTextCharFormat()
        self.fmt_number.setForeground(QColor("#B5CEA8"))

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
    def __init__(self, parent=None):
        super().__init__(parent)
        tag = QTextCharFormat(); tag.setForeground(QColor("#4EC9B0"))
        attr = QTextCharFormat(); attr.setForeground(QColor("#9CDCFE"))
        val = QTextCharFormat(); val.setForeground(QColor("#CE9178"))
        com = QTextCharFormat(); com.setForeground(QColor("#6A9955")); com.setFontItalic(True)
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
    def __init__(self, parent=None):
        super().__init__(parent)
        kw = QTextCharFormat(); kw.setForeground(QColor("#569Cff")); kw.setFontWeight(QFont.Bold)
        self.rules = [(re.compile(r"\\b" + w + r"\\b"), kw) for w in (
            "break","case","catch","class","const","continue","debugger","default","delete","do",
            "else","export","extends","finally","for","function","if","import","in","instanceof","let",
            "new","return","super","switch","this","throw","try","typeof","var","void","while","with",
            "yield","true","false","null","async","await"
        )]
        strf = QTextCharFormat(); strf.setForeground(QColor("#CE9178"))
        self.rules += [
            (re.compile(r'"[^"\\\\]*(\\\\.[^"\\\\]*)*"'), strf),
            (re.compile(r"'[^'\\\\]*(\\\\.[^'\\\\]*)*'"), strf),
            (re.compile(r"`[^`\\\\]*(\\\\.[^`\\\\]*)*`"), strf),
        ]
        com = QTextCharFormat(); com.setForeground(QColor("#6A9955"))
        self.rules += [
            (re.compile(r"//[^\n]*"), com), (re.compile(r"/\\*.*?\\*/"), com),
        ]
        num = QTextCharFormat(); num.setForeground(QColor("#B5CEA8"))
        self.rules.append((re.compile(r"\\b\\d+(\\.\\d+)?\\b"), num))
        fn = QTextCharFormat(); fn.setForeground(QColor("#DCDCAA"))
        self.rules.append((re.compile(r"\\b\\w+(?=\\()"), fn))
    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        hide_hash_for_colors(self, text)


class PhpHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        kw = QTextCharFormat(); kw.setForeground(QColor("#569Cff")); kw.setFontWeight(QFont.Bold)
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
        var = QTextCharFormat(); var.setForeground(QColor("#9CDCFE"))
        self.rules.append((re.compile(r"\\$\\w+\\b"), var))
        strf = QTextCharFormat(); strf.setForeground(QColor("#CE9178"))
        self.rules += [
            (re.compile(r'"[^"\\\\]*(\\\\.[^"\\\\]*)*"'), strf),
            (re.compile(r"'[^'\\\\]*(\\\\.[^'\\\\]*)*'"), strf),
        ]
        com = QTextCharFormat(); com.setForeground(QColor("#6A9955"))
        self.rules += [
            (re.compile(r"//[^\n]*"), com), (re.compile(r"#[^\n]*"), com), (re.compile(r"/\\*.*?\\*/"), com)
        ]
        tag = QTextCharFormat(); tag.setForeground(QColor("#569Cff")); tag.setFontWeight(QFont.Bold)
        self.rules.append((re.compile(r"<\\?php|\\?>"), tag))
    def highlightBlock(self, text):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
            hide_hash_for_colors(self, text)


class CppHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        kw = QTextCharFormat()
        kw.setForeground(QColor("#569Cff"))
        kw.setFontWeight(QFont.Bold)
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

        pre = QTextCharFormat()
        pre.setForeground(QColor("#C586C0"))
        self.rules.append((re.compile(r"^\s*#.*"), pre))

        strf = QTextCharFormat()
        strf.setForeground(QColor("#CE9178"))
        self.rules += [
            (re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), strf),
            (re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), strf),
        ]

        num = QTextCharFormat()
        num.setForeground(QColor("#B5CEA8"))
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

        com = QTextCharFormat()
        com.setForeground(QColor("#6A9955"))
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
    def __init__(self, parent=None):
        super().__init__(parent)
        key = QTextCharFormat(); key.setForeground(QColor("#9CDCFE"))
        strf = QTextCharFormat(); strf.setForeground(QColor("#CE9178"))
        num = QTextCharFormat(); num.setForeground(QColor("#B5CEA8"))
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


class RustHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        kw = QTextCharFormat(); kw.setForeground(QColor("#569Cff")); kw.setFontWeight(QFont.Bold)
        self.rules = [(re.compile(r"\b" + w + r"\b"), kw) for w in (
            "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum",
            "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod",
            "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super",
            "trait", "true", "type", "unsafe", "use", "where", "while",
            # common contextual/reserved words
            "abstract", "become", "box", "do", "final", "macro", "override", "priv",
            "typeof", "unsized", "virtual", "yield", "try",
        )]

        strf = QTextCharFormat(); strf.setForeground(QColor("#CE9178"))
        self.rules += [
            (re.compile(r'r#*"(?:[^"\\]|\\.)*"#*'), strf),
            (re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), strf),
            (re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), strf),
        ]

        num = QTextCharFormat(); num.setForeground(QColor("#B5CEA8"))
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

        macro = QTextCharFormat(); macro.setForeground(QColor("#DCDCAA"))
        self.rules += [(re.compile(r"\b[A-Za-z_]\w*!\b"), macro)]

        attr = QTextCharFormat(); attr.setForeground(QColor("#C586C0"))
        self.rules += [(re.compile(r"#\s*!\[[^\]]*\]|#\s*\[[^\]]*\]"), attr)]

        com = QTextCharFormat(); com.setForeground(QColor("#6A9955"))
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
    def __init__(self, parent=None):
        super().__init__(parent)
        idf = QTextCharFormat(); idf.setForeground(QColor("#D7BA7D"))
        clsf = QTextCharFormat(); clsf.setForeground(QColor("#4EC9B0"))
        tagf = QTextCharFormat(); tagf.setForeground(QColor("#D7BA7D"))
        prop = QTextCharFormat(); prop.setForeground(QColor("#9CDCFE"))
        val = QTextCharFormat(); val.setForeground(QColor("#CE9178"))
        com = QTextCharFormat(); com.setForeground(QColor("#6A9955")); com.setFontItalic(True)
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
    def __init__(self, parent=None):
        super().__init__(parent)

        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []

        # --- Keywords / builtins ---
        kw = QTextCharFormat()
        kw.setForeground(QColor("#569Cff"))
        kw.setFontWeight(QFont.Bold)

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
        var = QTextCharFormat()
        var.setForeground(QColor("#9CDCFE"))

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
        strf = QTextCharFormat()
        strf.setForeground(QColor("#CE9178"))
        self.rules += [
            (re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), strf),
            (re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), strf),
        ]

        # --- Comments (# ... end of line) ---
        com = QTextCharFormat()
        com.setForeground(QColor("#6A9955"))
        com.setFontItalic(True)
        self.rules.append(
            (re.compile(r"#[^\n]*"), com)
        )

        # --- Shebang line ---
        shebang = QTextCharFormat()
        shebang.setForeground(QColor("#C586C0"))
        self.rules.append(
            (re.compile(r"^#!.*$"), shebang)
        )

    def highlightBlock(self, text: str):
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
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

    def __init__(self, parent=None):
        super().__init__(parent)

        # Task checkbox format
        self.fmt_box_open = QTextCharFormat()
        self.fmt_box_open.setForeground(QColor("#DCDCAA"))  # [ ]

        self.fmt_box_done = QTextCharFormat()
        self.fmt_box_done.setForeground(QColor("#6A9955"))  # [x] / [✔]

        # Rest of task text
        self.fmt_task_text = QTextCharFormat()
        self.fmt_task_text.setForeground(QColor("#D4D4D4"))

        # Optional bullet
        self.fmt_bullet = QTextCharFormat()
        self.fmt_bullet.setForeground(QColor("#C586C0"))

        # Comments
        self.fmt_comment = QTextCharFormat()
        self.fmt_comment.setForeground(QColor("#6A9955"))

        # Headers
        self.fmt_header = QTextCharFormat()
        self.fmt_header.setForeground(QColor("#4FC1FF"))

        # Tags / priority
        self.fmt_tag = QTextCharFormat()
        self.fmt_tag.setForeground(QColor("#9CDCFE"))

        self.fmt_priority = QTextCharFormat()
        self.fmt_priority.setForeground(QColor("#CE9178"))

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
    "shell": BashHighlighter,
    "make": CppHighlighter,
    "todo": TodoHighlighter,
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

    highlighter_cls = LANGUAGE_HIGHLIGHTER_MAP.get(str(language_id or "").strip().lower())
    if highlighter_cls is None:
        return

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
    "ensure_highlighter",
    "set_language_highlighter",
    "set_highlighter_for_file",
    "PythonHighlighter",
    "HtmlHighlighter",
    "JavaScriptHighlighter",
    "PhpHighlighter",
    "CppHighlighter",
    "JsonHighlighter",
    "RustHighlighter",
    "CssHighlighter",
    "BashHighlighter",
    "TodoHighlighter",
]
