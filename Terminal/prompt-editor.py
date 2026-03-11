from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextCursor, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)
from TPOPyside.widgets.terminal_widget import TerminalWidget


# ============================================================
# Model
# ============================================================

@dataclass
class PromptSegment:
    kind: str
    value: str = ""
    fg: Optional[str] = None
    bg: Optional[str] = None
    bold: bool = False
    underline: bool = False

    def label(self) -> str:
        if self.kind == "literal":
            text = self.value.replace("\n", "\\n")
            return f'Text "{text}"'
        if self.kind == "token":
            return f"Token {self.value}"
        if self.kind == "newline":
            return "New line"
        if self.kind == "style":
            parts = []
            if self.fg:
                parts.append(f"fg={self.fg}")
            if self.bg:
                parts.append(f"bg={self.bg}")
            if self.bold:
                parts.append("bold")
            if self.underline:
                parts.append("underline")
            if not parts:
                parts.append("style")
            return "Style " + ", ".join(parts)
        if self.kind == "reset":
            return "Reset style"
        return self.kind


@dataclass
class PromptDocument:
    shell: str = "bash"
    segments: List[PromptSegment] = field(default_factory=list)
    raw_custom_mode: bool = False
    raw_text: str = ""


DEFAULT_PRESET_NAMES = [
    "Minimal",
    "Classic user@host:path$",
    "Two-line dev prompt",
    "Git-friendly",
    "Colorful prompt",
]


# ============================================================
# Token metadata
# ============================================================

TOKEN_DEFS = {
    "username": {
        "label": "Username",
        "category": "Identity",
        "tooltip": "Current user name.",
        "bash": r"\u",
        "zsh": r"%n",
        "preview": "aceofjohn",
        "explain": "username",
    },
    "hostname": {
        "label": "Hostname",
        "category": "Identity",
        "tooltip": "Current host name.",
        "bash": r"\h",
        "zsh": r"%m",
        "preview": "pop-os",
        "explain": "hostname",
    },
    "prompt_symbol": {
        "label": "Prompt symbol",
        "category": "Identity",
        "tooltip": "User/root prompt symbol.",
        "bash": r"\$",
        "zsh": r"%#",
        "preview": "$",
        "explain": "prompt-symbol",
    },
    "cwd": {
        "label": "Current directory",
        "category": "Path",
        "tooltip": "Current working directory, tilde-shortened.",
        "bash": r"\w",
        "zsh": r"%~",
        "preview": "~/Work",
        "explain": "current-directory",
    },
    "cwd_base": {
        "label": "Directory basename",
        "category": "Path",
        "tooltip": "Just the current folder name.",
        "bash": r"\W",
        "zsh": r"%1~",
        "preview": "Work",
        "explain": "current-directory-basename",
    },
    "time": {
        "label": "Time",
        "category": "Time",
        "tooltip": "Current time.",
        "bash": r"\t",
        "zsh": r"%*",
        "preview": "13:37:42",
        "explain": "time",
    },
    "date": {
        "label": "Date",
        "category": "Time",
        "tooltip": "Current date.",
        "bash": r"\d",
        "zsh": r"%D",
        "preview": "Wed Mar 11",
        "explain": "date",
    },
    "shell_name": {
        "label": "Shell name",
        "category": "Environment",
        "tooltip": "Shell name placeholder.",
        "bash": "__SHELL__",
        "zsh": "__SHELL__",
        "preview": "bash",
        "explain": "shell-name",
    },
    "venv": {
        "label": "Virtual environment",
        "category": "Environment",
        "tooltip": "Virtual environment name placeholder.",
        "bash": "__VENV__",
        "zsh": "__VENV__",
        "preview": "(myenv)",
        "explain": "virtual-environment",
    },
    "last_status": {
        "label": "Last exit status",
        "category": "Status",
        "tooltip": "Last command exit status placeholder.",
        "bash": "__STATUS__",
        "zsh": "__STATUS__",
        "preview": "1",
        "explain": "last-exit-status",
    },
    "git_branch": {
        "label": "Git branch",
        "category": "VCS",
        "tooltip": "Git branch placeholder.",
        "bash": "__GIT__",
        "zsh": "__GIT__",
        "preview": "[main*]",
        "explain": "git-branch",
    },
}

PALETTE_STRUCTURE = {
    "Identity": ["username", "hostname", "prompt_symbol"],
    "Path": ["cwd", "cwd_base"],
    "Environment": ["venv", "shell_name"],
    "Time": ["time", "date"],
    "Status": ["last_status"],
    "VCS": ["git_branch"],
    "Structure": ["space", "literal", "separator", "newline"],
    "Style": ["style", "reset"],
}


ANSI_COLORS = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
}


# ============================================================
# Helpers
# ============================================================


def prompt_editor_state_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / ".terminal" / "prompt-editor-state.json"


def segment_to_dict(seg: PromptSegment) -> dict:
    return {
        "kind": str(seg.kind or ""),
        "value": str(seg.value or ""),
        "fg": seg.fg,
        "bg": seg.bg,
        "bold": bool(seg.bold),
        "underline": bool(seg.underline),
    }


def segment_from_dict(payload: object) -> PromptSegment:
    data = payload if isinstance(payload, dict) else {}
    return PromptSegment(
        kind=str(data.get("kind") or ""),
        value=str(data.get("value") or ""),
        fg=str(data.get("fg")) if data.get("fg") is not None else None,
        bg=str(data.get("bg")) if data.get("bg") is not None else None,
        bold=bool(data.get("bold", False)),
        underline=bool(data.get("underline", False)),
    )


def clone_segments(segments: List[PromptSegment]) -> List[PromptSegment]:
    return [segment_from_dict(segment_to_dict(seg)) for seg in segments]

def bash_nonprinting(code: str) -> str:
    return rf"\[\e[{code}m\]"


def zsh_nonprinting_start(seg: PromptSegment) -> str:
    parts = []
    if seg.fg:
        parts.append(f"%F{{{seg.fg}}}")
    if seg.bg:
        parts.append(f"%K{{{seg.bg}}}")
    if seg.bold:
        parts.append("%B")
    if seg.underline:
        parts.append("%U")
    return "".join(parts)


def bash_token_apply(token_key: str) -> str:
    mapping = {
        "username": r"\u",
        "hostname": r"\h",
        "prompt_symbol": r"\$",
        "cwd": r"\w",
        "cwd_base": r"\W",
        "time": r"\t",
        "date": r"\d",
        "shell_name": r"\s",
        "venv": r'${VIRTUAL_ENV:+($(basename "$VIRTUAL_ENV")) }',
        "last_status": r'${__prompt_editor_last_status:+[${__prompt_editor_last_status}] }',
        "git_branch": r'$(git branch --show-current 2>/dev/null | sed "s/.*/[&]/")',
    }
    return mapping.get(token_key, "")


def zsh_token_apply(token_key: str) -> str:
    mapping = {
        "username": r"%n",
        "hostname": r"%m",
        "prompt_symbol": r"%#",
        "cwd": r"%~",
        "cwd_base": r"%1~",
        "time": r"%*",
        "date": r"%D",
        "shell_name": r"%N",
        "venv": r'${VIRTUAL_ENV:+($(basename "$VIRTUAL_ENV")) }',
        "last_status": r'${__prompt_editor_last_status:+[${__prompt_editor_last_status}] }',
        "git_branch": r'$(git branch --show-current 2>/dev/null | sed "s/.*/[&]/")',
    }
    return mapping.get(token_key, "")


def bash_style_apply(seg: PromptSegment) -> str:
    codes = []
    if seg.bold:
        codes.append("1")
    if seg.underline:
        codes.append("4")
    if seg.fg and seg.fg in ANSI_COLORS:
        codes.append(str(ANSI_COLORS[seg.fg]))
    if seg.bg and seg.bg in ANSI_COLORS:
        codes.append(str(ANSI_COLORS[seg.bg] + 10))
    if not codes:
        return ""
    return bash_nonprinting(";".join(codes))


def zsh_style_apply(seg: PromptSegment) -> str:
    return zsh_nonprinting_start(seg)


def bash_reset_apply(reset_kind: str = "") -> str:
    if not reset_kind:
        return bash_nonprinting("0")

    codes = []
    if reset_kind == "fg":
        codes.append("39")
    elif reset_kind == "bg":
        codes.append("49")
    elif reset_kind == "bold":
        codes.append("22")
    elif reset_kind == "underline":
        codes.append("24")

    if not codes:
        return bash_nonprinting("0")
    return bash_nonprinting(";".join(codes))


def zsh_reset_apply(reset_kind: str = "") -> str:
    if not reset_kind:
        return "%f%k%b%u"
    if reset_kind == "fg":
        return "%f"
    if reset_kind == "bg":
        return "%k"
    if reset_kind == "bold":
        return "%b"
    if reset_kind == "underline":
        return "%u"
    return "%f%k%b%u"


def document_to_shell_markup(doc: PromptDocument) -> str:
    parts = []

    for seg in doc.segments:
        if seg.kind == "literal":
            parts.append(seg.value)
        elif seg.kind == "newline":
            parts.append("\n")
        elif seg.kind == "token":
            if doc.shell == "bash":
                parts.append(bash_token_apply(seg.value))
            else:
                parts.append(zsh_token_apply(seg.value))
        elif seg.kind == "style":
            if doc.shell == "bash":
                parts.append(bash_style_apply(seg))
            else:
                parts.append(zsh_style_apply(seg))
        elif seg.kind == "reset":
            if doc.shell == "bash":
                parts.append(bash_reset_apply(seg.value))
            else:
                parts.append(zsh_reset_apply(seg.value))

    return "".join(parts)
def shell_rc_path(shell_name: str) -> Path:
    home = Path.home()
    return home / (".bashrc" if shell_name == "bash" else ".zshrc")


def detect_current_prompt(shell_name: str) -> str:
    rc = shell_rc_path(shell_name)
    if not rc.exists():
        return default_prompt(shell_name)

    text = rc.read_text(encoding="utf-8", errors="ignore")

    managed = re.search(
        r"# >>> prompt-editor managed >>>\n(.*?)# <<< prompt-editor managed <<<",
        text,
        flags=re.DOTALL,
    )
    if managed:
        block = managed.group(1)
        if shell_name == "bash":
            m = re.search(r'PS1=(["\'])(.*?)\1', block, flags=re.DOTALL)
        else:
            m = re.search(r'PROMPT=(["\'])(.*?)\1', block, flags=re.DOTALL)
        if m:
            return m.group(2)

    if shell_name == "bash":
        patterns = [
            r'^\s*export\s+PS1=(["\'])(.*?)\1',
            r'^\s*PS1=(["\'])(.*?)\1',
        ]
    else:
        patterns = [
            r'^\s*export\s+PROMPT=(["\'])(.*?)\1',
            r'^\s*PROMPT=(["\'])(.*?)\1',
        ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.MULTILINE)
        if m:
            return m.group(2)

    return default_prompt(shell_name)


def default_prompt(shell_name: str) -> str:
    if shell_name == "bash":
        return r"\u@\h:\w\$ "
    return r"%n@%m:%~%# "


def build_managed_block(shell_name: str, markup: str) -> str:
    if shell_name == "bash":
        body = (
            "__prompt_editor_last_status=$?\n"
            f"PS1='{markup}'\n"
        )
    else:
        body = (
            "__prompt_editor_last_status=$?\n"
            f"PROMPT='{markup}'\n"
        )

    return (
        "# >>> prompt-editor managed >>>\n"
        + body +
        "# <<< prompt-editor managed <<<\n"
    )
    
def apply_prompt(shell_name: str, markup: str) -> tuple[bool, str]:
    rc = shell_rc_path(shell_name)
    existing = ""
    if rc.exists():
        existing = rc.read_text(encoding="utf-8", errors="ignore")

    block = build_managed_block(shell_name, markup)

    managed_re = re.compile(
        r"\n?# >>> prompt-editor managed >>>\n.*?# <<< prompt-editor managed <<<\n?",
        flags=re.DOTALL,
    )

    if managed_re.search(existing):
        updated = managed_re.sub(lambda _m: "\n" + block, existing, count=1)
    else:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        updated = existing + "\n" + block

    rc.write_text(updated, encoding="utf-8")
    return True, f"Updated {rc}"


def bash_style_escape(seg: PromptSegment) -> str:
    parts = []
    if seg.fg and seg.fg in ANSI_COLORS:
        parts.append(str(ANSI_COLORS[seg.fg]))
    if seg.bg and seg.bg in ANSI_COLORS:
        parts.append(str(ANSI_COLORS[seg.bg] + 10))
    if seg.bold:
        parts.append("1")
    if seg.underline:
        parts.append("4")
    if not parts:
        return ""
    return r"\[" + f"\033[{';'.join(parts)}m" + r"\]"


def zsh_style_escape(seg: PromptSegment) -> str:
    parts = []
    if seg.fg:
        parts.append(f"%F{{{seg.fg}}}")
    if seg.bg:
        parts.append(f"%K{{{seg.bg}}}")
    if seg.bold:
        parts.append("%B")
    if seg.underline:
        parts.append("%U")
    return "".join(parts)


def style_reset(shell_name: str) -> str:
    if shell_name == "bash":
        return r"\[\e[0m\]"
    return "%f%k%b%u"


def segment_to_markup(seg: PromptSegment, shell_name: str) -> str:
    if seg.kind == "literal":
        return seg.value
    if seg.kind == "newline":
        return "\n"
    if seg.kind == "reset":
        return style_reset(shell_name)
    if seg.kind == "style":
        return bash_style_escape(seg) if shell_name == "bash" else zsh_style_escape(seg)
    if seg.kind == "token":
        meta = TOKEN_DEFS.get(seg.value)
        if not meta:
            return ""
        return meta[shell_name]
    return ""


def document_to_markup(doc: PromptDocument) -> str:
    return "".join(segment_to_markup(seg, doc.shell) for seg in doc.segments)


def explain_document(doc: PromptDocument) -> str:
    parts = []
    for seg in doc.segments:
        if seg.kind == "literal":
            if seg.value == " ":
                parts.append("space")
            elif seg.value:
                parts.append(f'literal "{seg.value}"')
        elif seg.kind == "newline":
            parts.append("new-line")
        elif seg.kind == "reset":
            parts.append("reset-style")
        elif seg.kind == "style":
            bits = []
            if seg.fg:
                bits.append(f"fg {seg.fg}")
            if seg.bg:
                bits.append(f"bg {seg.bg}")
            if seg.bold:
                bits.append("bold")
            if seg.underline:
                bits.append("underline")
            parts.append("style(" + ", ".join(bits or ["default"]) + ")")
        elif seg.kind == "token":
            parts.append(TOKEN_DEFS.get(seg.value, {}).get("explain", seg.value))
    return "  •  ".join(parts) if parts else "(empty prompt)"


def parse_markup(shell_name: str, text: str) -> tuple[List[PromptSegment], List[str], bool]:
    warnings: List[str] = []
    segments: List[PromptSegment] = []
    i = 0
    custom_mode = False

    bash_reverse = {
        r"\u": "username",
        r"\h": "hostname",
        r"\$": "prompt_symbol",
        r"\w": "cwd",
        r"\W": "cwd_base",
        r"\t": "time",
        r"\d": "date",
        "__SHELL__": "shell_name",
        "__VENV__": "venv",
        "__STATUS__": "last_status",
        "__GIT__": "git_branch",
    }
    zsh_reverse = {
        r"%n": "username",
        r"%m": "hostname",
        r"%#": "prompt_symbol",
        r"%~": "cwd",
        r"%1~": "cwd_base",
        r"%*": "time",
        r"%D": "date",
        "__SHELL__": "shell_name",
        "__VENV__": "venv",
        "__STATUS__": "last_status",
        "__GIT__": "git_branch",
    }
    reverse = bash_reverse if shell_name == "bash" else zsh_reverse
    multi_tokens = sorted(reverse.keys(), key=len, reverse=True)

    bash_style_re = re.compile(
        r"""
        \\\[                       # \[
        (?:
            \\033 | \\e | \\x1b    # \033 or \e or \x1b
        )
        \[
        ([0-9;]+)m
        \\\]                       # \]
        """,
        re.VERBOSE,
    )

    def parse_bash_style_codes(code_str: str) -> PromptSegment:
        seg = PromptSegment(kind="style")
        for code in code_str.split(";"):
            if code == "0":
                return PromptSegment(kind="reset")
            if code == "1":
                seg.bold = True
            elif code == "4":
                seg.underline = True
            elif code.isdigit():
                n = int(code)
                for name, base in ANSI_COLORS.items():
                    if n == base:
                        seg.fg = name
                    elif n == base + 10:
                        seg.bg = name
        return seg

    while i < len(text):
        if text[i] == "\n":
            segments.append(PromptSegment(kind="newline"))
            i += 1
            continue

        matched = False

        for token in multi_tokens:
            if text.startswith(token, i):
                segments.append(PromptSegment(kind="token", value=reverse[token]))
                i += len(token)
                matched = True
                break
        if matched:
            continue

        if shell_name == "bash":
            m = bash_style_re.match(text, i)
            if m:
                seg = parse_bash_style_codes(m.group(1))
                segments.append(seg)
                i = m.end()
                continue

        else:
            # zsh style starts
            if text.startswith("%F{", i) or text.startswith("%K{", i) or text.startswith("%B", i) or text.startswith("%U", i):
                seg = PromptSegment(kind="style")
                consumed_any = False
                while i < len(text):
                    progressed = False
                    if text.startswith("%F{", i):
                        end = text.find("}", i)
                        if end != -1:
                            seg.fg = text[i + 3:end]
                            i = end + 1
                            progressed = True
                            consumed_any = True
                    elif text.startswith("%K{", i):
                        end = text.find("}", i)
                        if end != -1:
                            seg.bg = text[i + 3:end]
                            i = end + 1
                            progressed = True
                            consumed_any = True
                    elif text.startswith("%B", i):
                        seg.bold = True
                        i += 2
                        progressed = True
                        consumed_any = True
                    elif text.startswith("%U", i):
                        seg.underline = True
                        i += 2
                        progressed = True
                        consumed_any = True
                    if not progressed:
                        break

                if consumed_any:
                    segments.append(seg)
                    continue

            # zsh individual resets
            if text.startswith("%f", i):
                segments.append(PromptSegment(kind="reset", value="fg"))
                i += 2
                continue
            if text.startswith("%k", i):
                segments.append(PromptSegment(kind="reset", value="bg"))
                i += 2
                continue
            if text.startswith("%b", i):
                segments.append(PromptSegment(kind="reset", value="bold"))
                i += 2
                continue
            if text.startswith("%u", i):
                segments.append(PromptSegment(kind="reset", value="underline"))
                i += 2
                continue

        # literal fallback
        start = i
        i += 1

        while i < len(text):
            stop = False

            if text[i] == "\n":
                stop = True
            else:
                for token in multi_tokens:
                    if text.startswith(token, i):
                        stop = True
                        break

                if shell_name == "bash":
                    if bash_style_re.match(text, i):
                        stop = True
                else:
                    if (
                        text.startswith("%F{", i)
                        or text.startswith("%K{", i)
                        or text.startswith("%B", i)
                        or text.startswith("%U", i)
                        or text.startswith("%f", i)
                        or text.startswith("%k", i)
                        or text.startswith("%b", i)
                        or text.startswith("%u", i)
                    ):
                        stop = True

            if stop:
                break
            i += 1

        literal = text[start:i]
        if literal:
            segments.append(PromptSegment(kind="literal", value=literal))

    # warnings
    if shell_name == "bash":
        if text.count(r"\[") != text.count(r"\]"):
            warnings.append("Suspicious bash non-printing escape count.")

    rebuilt = "".join(segment_to_markup(seg, shell_name) for seg in segments)
    if rebuilt != text:
        custom_mode = True
        warnings.append("Raw markup could not be perfectly round-tripped. Structured editor is in custom/raw mode.")

    return segments, warnings, custom_mode


def prompt_document_to_dict(doc: PromptDocument) -> dict:
    return {
        "shell": str(doc.shell or "bash"),
        "raw_custom_mode": bool(doc.raw_custom_mode),
        "raw_text": str(doc.raw_text or ""),
        "segments": [segment_to_dict(seg) for seg in doc.segments],
    }


def prompt_document_from_dict(payload: object, fallback_shell: str) -> PromptDocument:
    data = payload if isinstance(payload, dict) else {}
    shell = str(data.get("shell") or fallback_shell or "bash")
    raw_text = str(data.get("raw_text") or "")
    raw_custom_mode = bool(data.get("raw_custom_mode", False))
    raw_segments = data.get("segments")
    segments: List[PromptSegment] = []
    if isinstance(raw_segments, list):
        segments = [segment_from_dict(item) for item in raw_segments]
    if not segments and raw_text:
        parsed_segments, _warnings, parsed_custom = parse_markup(shell, raw_text)
        segments = parsed_segments
        raw_custom_mode = bool(parsed_custom)
    return PromptDocument(
        shell=shell,
        segments=segments,
        raw_custom_mode=raw_custom_mode,
        raw_text=raw_text,
    )

def preview_text_for_token(token_key: str, state: str, shell_name: str) -> str:
    if token_key == "venv":
        return "(myenv) " if state == "venv" else ""
    if token_key == "git_branch":
        return "[main*]" if state == "git" else ""
    if token_key == "last_status":
        return "1" if state == "failed" else "0"
    if token_key == "cwd":
        return "~/Very/Long/Path/To/Some/Project/Subdir" if state == "longpath" else "~/Work"
    if token_key == "cwd_base":
        return "Subdir" if state == "longpath" else "Work"
    if token_key == "prompt_symbol":
        return "$"
    if token_key == "shell_name":
        return shell_name
    return TOKEN_DEFS.get(token_key, {}).get("preview", "")


def render_preview_chunks(doc: PromptDocument, state: str) -> List[PreviewChunk]:
    chunks: List[PreviewChunk] = []
    style = PreviewStyleState()

    def add_text(text: str) -> None:
        if not text:
            return
        chunks.append(
            PreviewChunk(
                text=text,
                fg=style.fg,
                bg=style.bg,
                bold=style.bold,
                underline=style.underline,
            )
        )

    for seg in doc.segments:
        if seg.kind == "literal":
            add_text(seg.value)

        elif seg.kind == "newline":
            add_text("\n")

        elif seg.kind == "token":
            add_text(preview_text_for_token(seg.value, state, doc.shell))

        elif seg.kind == "style":
            if seg.fg is not None:
                style.fg = seg.fg
            if seg.bg is not None:
                style.bg = seg.bg
            if seg.bold:
                style.bold = True
            if seg.underline:
                style.underline = True

        elif seg.kind == "reset":
            # full reset for bash and generic reset
            if not seg.value:
                style = PreviewStyleState()
            else:
                if seg.value == "fg":
                    style.fg = None
                elif seg.value == "bg":
                    style.bg = None
                elif seg.value == "bold":
                    style.bold = False
                elif seg.value == "underline":
                    style.underline = False

    return chunks


def render_preview_plain_text(doc: PromptDocument, state: str) -> str:
    return "".join(chunk.text for chunk in render_preview_chunks(doc, state))

# ============================================================
# UI bits
# ============================================================

class StyleDialog(QDialog):
    def __init__(self, parent=None, segment: Optional[PromptSegment] = None):
        super().__init__(parent)
        self.setWindowTitle("Style segment")
        self.resize(320, 160)

        self.fg_combo = QComboBox()
        self.bg_combo = QComboBox()
        self.fg_combo.addItem("(none)")
        self.bg_combo.addItem("(none)")
        for c in ANSI_COLORS:
            self.fg_combo.addItem(c)
            self.bg_combo.addItem(c)

        self.bold_cb = QCheckBox("Bold")
        self.underline_cb = QCheckBox("Underline")

        if segment:
            if segment.fg:
                self.fg_combo.setCurrentText(segment.fg)
            if segment.bg:
                self.bg_combo.setCurrentText(segment.bg)
            self.bold_cb.setChecked(segment.bold)
            self.underline_cb.setChecked(segment.underline)

        form = QFormLayout()
        form.addRow("Foreground", self.fg_combo)
        form.addRow("Background", self.bg_combo)
        form.addRow("", self.bold_cb)
        form.addRow("", self.underline_cb)

        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ok_btn)
        row.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(row)

    def result_segment(self) -> PromptSegment:
        fg = self.fg_combo.currentText()
        bg = self.bg_combo.currentText()
        return PromptSegment(
            kind="style",
            fg=None if fg == "(none)" else fg,
            bg=None if bg == "(none)" else bg,
            bold=self.bold_cb.isChecked(),
            underline=self.underline_cb.isChecked(),
        )


class PromptEditorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.documents = {
            "bash": PromptDocument(shell="bash"),
            "zsh": PromptDocument(shell="zsh"),
        }
        self._custom_presets: dict[str, List[PromptSegment]] = {}
        self._selected_rows_by_shell: dict[str, int] = {"bash": -1, "zsh": -1}
        self.current_warnings: List[str] = []
        self._syncing = False
        self._state_loaded = False

        self._state_save_timer = QTimer(self)
        self._state_save_timer.setSingleShot(True)
        self._state_save_timer.setInterval(220)
        self._state_save_timer.timeout.connect(self._save_state_to_disk)

        self.shell_combo = QComboBox()
        self.shell_combo.addItems(["bash", "zsh"])

        self.preset_combo = QComboBox()
        self.preset_combo.addItems(DEFAULT_PRESET_NAMES)

        self.reload_btn = QPushButton("Reload")
        self.revert_btn = QPushButton("Revert")
        self.copy_btn = QPushButton("Copy markup")
        self.preview_live_btn = QPushButton("Preview in live shell")
        self.save_preset_btn = QPushButton("Save preset")
        self.delete_preset_btn = QPushButton("Delete preset")

        self.palette = QTreeWidget()
        self.palette.setHeaderHidden(True)
        self.palette.itemDoubleClicked.connect(self.on_palette_insert)

        self.segment_list = QListWidget()
        self.segment_list.itemDoubleClicked.connect(self.on_edit_selected_segment)
        self.segment_list.setContextMenuPolicy(Qt.CustomContextMenu)

        self.add_text_btn = QPushButton("Add text")
        self.edit_btn = QPushButton("Edit")
        self.remove_btn = QPushButton("Remove")
        self.up_btn = QPushButton("Up")
        self.down_btn = QPushButton("Down")

        self.raw_edit = QPlainTextEdit()
        font = QFont("DejaVu Sans Mono")
        font.setStyleHint(QFont.Monospace)
        self.raw_edit.setFont(font)
        self.raw_edit.textChanged.connect(self.on_raw_changed)

        self.preview_state_combo = QComboBox()
        self.preview_state_combo.addItems(["normal", "venv", "git", "longpath", "failed"])
        self.preview_state_combo.currentTextChanged.connect(self.refresh_preview)

        self.preview_render = QTextEdit()
        self.preview_render.setReadOnly(True)
        self.preview_render.setFont(font)
        self.preview_render.setFixedHeight(90)

        self.warning_box = QPlainTextEdit()
        self.warning_box.setReadOnly(True)
        self.warning_box.setFixedHeight(70)

        self.apply_btn = QPushButton("Apply to shell config")

        self.detected_label = QLabel()
        self.mode_label = QLabel()

        self._build_ui()
        self._wire()
        self._populate_palette()
        self._load_state_or_defaults()
    
    def color_to_qcolor(self, name: Optional[str]) -> Optional[QColor]:
        if not name:
            return None
        mapping = {
            "black": QColor(0, 0, 0),
            "red": QColor(205, 49, 49),
            "green": QColor(13, 188, 121),
            "yellow": QColor(229, 229, 16),
            "blue": QColor(36, 114, 200),
            "magenta": QColor(188, 63, 188),
            "cyan": QColor(17, 168, 205),
            "white": QColor(229, 229, 229),
        }
        return mapping.get(name, QColor(name))

    def set_preview_chunks(self, chunks: List[PreviewChunk]) -> None:
        self.preview_render.clear()
        cursor = self.preview_render.textCursor()
        cursor.movePosition(QTextCursor.Start)

        for chunk in chunks:
            fmt = QTextCharFormat()

            fg = self.color_to_qcolor(chunk.fg)
            bg = self.color_to_qcolor(chunk.bg)

            if fg:
                fmt.setForeground(fg)
            if bg:
                fmt.setBackground(bg)
            fmt.setFontWeight(QFont.Bold if chunk.bold else QFont.Normal)
            fmt.setFontUnderline(chunk.underline)

            cursor.insertText(chunk.text, fmt)

        self.preview_render.setTextCursor(cursor)
    
    def current_doc(self) -> PromptDocument:
        return self.documents[self.shell_combo.currentText()]

    def _build_ui(self) -> None:
        toolbar_row = QHBoxLayout()
        toolbar_row.addWidget(QLabel("Shell"))
        toolbar_row.addWidget(self.shell_combo)
        toolbar_row.addSpacing(12)
        toolbar_row.addWidget(QLabel("Preset"))
        toolbar_row.addWidget(self.preset_combo)
        toolbar_row.addWidget(self.save_preset_btn)
        toolbar_row.addWidget(self.delete_preset_btn)
        toolbar_row.addWidget(self.reload_btn)
        toolbar_row.addWidget(self.revert_btn)
        toolbar_row.addWidget(self.copy_btn)
        toolbar_row.addWidget(self.preview_live_btn)
        toolbar_row.addStretch(1)

        left_box = QGroupBox("Palette")
        left_layout = QVBoxLayout(left_box)
        left_layout.addWidget(self.palette)

        center_box = QGroupBox("Structured editor")
        center_layout = QVBoxLayout(center_box)
        center_layout.addWidget(self.segment_list)
        row = QHBoxLayout()
        row.addWidget(self.add_text_btn)
        row.addWidget(self.edit_btn)
        row.addWidget(self.remove_btn)
        row.addWidget(self.up_btn)
        row.addWidget(self.down_btn)
        center_layout.addLayout(row)

        right_box = QGroupBox("Raw markup")
        right_layout = QVBoxLayout(right_box)
        right_layout.addWidget(self.raw_edit)

        splitter = QSplitter()
        splitter.addWidget(left_box)
        splitter.addWidget(center_box)
        splitter.addWidget(right_box)
        splitter.setSizes([220, 320, 320])

        bottom_box = QGroupBox("Preview and validation")
        bottom_layout = QVBoxLayout(bottom_box)

        meta_row = QHBoxLayout()
        
        meta_row.addSpacing(12)
        meta_row.addWidget(QLabel("Mode:"))
        meta_row.addWidget(self.mode_label)
        meta_row.addSpacing(12)
        meta_row.addWidget(QLabel("Preview state"))
        meta_row.addWidget(self.preview_state_combo)
        meta_row.addStretch(1)

        bottom_layout.addLayout(meta_row)
        bottom_layout.addWidget(QLabel("Detected prompt markup"))
        bottom_layout.addWidget(self.detected_label)
        bottom_layout.addWidget(QLabel("Rendered preview"))
        bottom_layout.addWidget(self.preview_render)
        bottom_layout.addWidget(QLabel("Warnings"))
        bottom_layout.addWidget(self.warning_box)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        action_row.addWidget(self.apply_btn)
        bottom_layout.addLayout(action_row)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar_row)
        layout.addWidget(splitter, 1)
        layout.addWidget(bottom_box)

    def _wire(self) -> None:
        self.shell_combo.currentTextChanged.connect(self._load_document)
        self.reload_btn.clicked.connect(self.reload_current_prompt)
        self.revert_btn.clicked.connect(self.revert_document)
        self.copy_btn.clicked.connect(self.copy_markup)
        self.preset_combo.currentTextChanged.connect(self.apply_preset)
        self.save_preset_btn.clicked.connect(self.save_current_preset)
        self.delete_preset_btn.clicked.connect(self.delete_selected_preset)
        self.preview_live_btn.clicked.connect(self.preview_in_live_shell)
        self.add_text_btn.clicked.connect(self.add_text_segment)
        self.edit_btn.clicked.connect(self.on_edit_selected_segment)
        self.remove_btn.clicked.connect(self.remove_selected_segment)
        self.up_btn.clicked.connect(lambda: self.move_selected(-1))
        self.down_btn.clicked.connect(lambda: self.move_selected(1))
        self.apply_btn.clicked.connect(self.apply_current_markup)
        self.segment_list.customContextMenuRequested.connect(self.show_segment_context_menu)
        self.segment_list.currentRowChanged.connect(self._on_segment_row_changed)
        self.preview_state_combo.currentTextChanged.connect(lambda *_args: self._schedule_state_save())

    def _populate_palette(self) -> None:
        for category, keys in PALETTE_STRUCTURE.items():
            cat_item = QTreeWidgetItem([category])
            self.palette.addTopLevelItem(cat_item)
            for key in keys:
                if key in TOKEN_DEFS:
                    label = TOKEN_DEFS[key]["label"]
                    tip = TOKEN_DEFS[key]["tooltip"]
                else:
                    label_map = {
                        "space": "Space",
                        "literal": "Literal text",
                        "separator": "Separator",
                        "newline": "New line",
                        "style": "Style segment",
                        "reset": "Reset style",
                    }
                    tip_map = {
                        "space": "Insert a space character.",
                        "literal": "Insert custom text.",
                        "separator": "Insert a separator such as ':' or '|'.",
                        "newline": "Insert a new line.",
                        "style": "Insert a style control segment.",
                        "reset": "Reset styles.",
                    }
                    label = label_map[key]
                    tip = tip_map[key]
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.UserRole, key)
                item.setToolTip(0, tip)
                cat_item.addChild(item)
            cat_item.setExpanded(True)

    def _load_initial_documents(self) -> None:
        for shell_name in ("bash", "zsh"):
            raw = detect_current_prompt(shell_name)
            segs, warnings, custom = parse_markup(shell_name, raw)
            self.documents[shell_name] = PromptDocument(
                shell=shell_name,
                segments=segs,
                raw_custom_mode=custom,
                raw_text=raw,
            )
            if not segs:
                self.documents[shell_name].segments = self._preset_segments("Classic user@host:path$")
                self.documents[shell_name].raw_text = document_to_markup(self.documents[shell_name])
            self.current_warnings = warnings

    def _state_payload(self) -> dict:
        selected_rows = {
            "bash": int(self._selected_rows_by_shell.get("bash", -1)),
            "zsh": int(self._selected_rows_by_shell.get("zsh", -1)),
        }
        return {
            "version": 1,
            "active_shell": str(self.shell_combo.currentText() or "bash"),
            "preview_state": str(self.preview_state_combo.currentText() or "normal"),
            "selected_preset": str(self.preset_combo.currentText() or ""),
            "documents": {
                shell_name: prompt_document_to_dict(doc)
                for shell_name, doc in self.documents.items()
            },
            "custom_presets": {
                name: [segment_to_dict(seg) for seg in segments]
                for name, segments in self._custom_presets.items()
            },
            "selected_rows": selected_rows,
        }

    def _on_segment_row_changed(self, row: int) -> None:
        shell = str(self.shell_combo.currentText() or "bash")
        self._selected_rows_by_shell[shell] = int(row)
        self._schedule_state_save()

    def _schedule_state_save(self) -> None:
        if self._syncing or not self._state_loaded:
            return
        self._state_save_timer.start()

    def _save_state_to_disk(self) -> None:
        try:
            payload = self._state_payload()
            path = prompt_editor_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except Exception:
            return

    def _load_state_or_defaults(self) -> None:
        self._syncing = True
        loaded = self._load_state_from_disk()
        if not loaded:
            self._load_initial_documents()
            self._refresh_preset_combo(keep_current=DEFAULT_PRESET_NAMES[0])
            self.shell_combo.setCurrentText("bash")
            self.preview_state_combo.setCurrentText("normal")
            self._load_document("bash")
        self._syncing = False
        self._state_loaded = True
        self._schedule_state_save()

    def _load_state_from_disk(self) -> bool:
        path = prompt_editor_state_path()
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False

        docs_raw = payload.get("documents")
        loaded_docs = False
        if isinstance(docs_raw, dict):
            for shell_name in ("bash", "zsh"):
                self.documents[shell_name] = prompt_document_from_dict(docs_raw.get(shell_name), shell_name)
                if not self.documents[shell_name].segments:
                    self.documents[shell_name].segments = self._preset_segments("Classic user@host:path$")
                    self.documents[shell_name].raw_text = document_to_markup(self.documents[shell_name])
                loaded_docs = True
        if not loaded_docs:
            self._load_initial_documents()

        custom_raw = payload.get("custom_presets")
        self._custom_presets.clear()
        if isinstance(custom_raw, dict):
            for name, raw_segments in custom_raw.items():
                preset_name = str(name or "").strip()
                if not preset_name or preset_name in DEFAULT_PRESET_NAMES:
                    continue
                if not isinstance(raw_segments, list):
                    continue
                segments = [segment_from_dict(item) for item in raw_segments]
                if segments:
                    self._custom_presets[preset_name] = segments

        selected_preset = str(payload.get("selected_preset") or DEFAULT_PRESET_NAMES[0])
        self._refresh_preset_combo(keep_current=selected_preset)

        selected_rows_raw = payload.get("selected_rows")
        if isinstance(selected_rows_raw, dict):
            for shell_name in ("bash", "zsh"):
                try:
                    self._selected_rows_by_shell[shell_name] = int(selected_rows_raw.get(shell_name, -1))
                except Exception:
                    self._selected_rows_by_shell[shell_name] = -1

        active_shell = str(payload.get("active_shell") or "bash")
        if active_shell not in {"bash", "zsh"}:
            active_shell = "bash"

        preview_state = str(payload.get("preview_state") or "normal")
        if preview_state not in {"normal", "venv", "git", "longpath", "failed"}:
            preview_state = "normal"
        self.preview_state_combo.setCurrentText(preview_state)

        self.shell_combo.setCurrentText(active_shell)
        self._load_document(active_shell)
        return True

    def _load_document(self, shell_name: str) -> None:
        self._syncing = True
        doc = self.documents[shell_name]
        self.segment_list.clear()
        for seg in doc.segments:
            self.segment_list.addItem(seg.label())
        if doc.raw_text:
            self.raw_edit.setPlainText(doc.raw_text)
        else:
            doc.raw_text = document_to_markup(doc)
            self.raw_edit.setPlainText(doc.raw_text)
        self.detected_label.setText(detect_current_prompt(shell_name))
        self.mode_label.setText("custom/raw" if doc.raw_custom_mode else "structured")
        desired_row = int(self._selected_rows_by_shell.get(shell_name, -1))
        if 0 <= desired_row < self.segment_list.count():
            self.segment_list.setCurrentRow(desired_row)
        elif self.segment_list.count() > 0:
            self.segment_list.setCurrentRow(self.segment_list.count() - 1)
        self._syncing = False
        self.refresh_preview()
        self._schedule_state_save()

    def refresh_segment_list(self) -> None:
        self.segment_list.clear()
        for seg in self.current_doc().segments:
            self.segment_list.addItem(seg.label())

    def refresh_preview(self) -> None:
        doc = self.current_doc()
        state = self.preview_state_combo.currentText()

        chunks = render_preview_chunks(doc, state)
        self.set_preview_chunks(chunks)

        warnings = self.current_warnings[:]
        if doc.raw_custom_mode:
            warnings.append("Structured editor is in custom/raw mode for this prompt.")

        self.warning_box.setPlainText("\n".join(warnings) if warnings else "(none)")
        self.mode_label.setText("custom/raw" if doc.raw_custom_mode else "structured")

    def sync_markup_from_model(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        doc = self.current_doc()
        text = document_to_markup(doc)
        doc.raw_text = text
        self.raw_edit.setPlainText(text)
        self.refresh_segment_list()
        self._syncing = False
        self.current_warnings = []
        self.refresh_preview()
        self._schedule_state_save()

    def on_raw_changed(self) -> None:
        if self._syncing:
            return
        shell_name = self.shell_combo.currentText()
        text = self.raw_edit.toPlainText()
        segs, warnings, custom = parse_markup(shell_name, text)
        doc = self.current_doc()
        doc.segments = segs
        doc.raw_custom_mode = custom
        doc.raw_text = text
        self.current_warnings = warnings
        self.refresh_segment_list()
        self.refresh_preview()
        self._schedule_state_save()

    def on_palette_insert(self, item: QTreeWidgetItem) -> None:
        key = item.data(0, Qt.UserRole)
        if not key:
            return
        self._insert_palette_key(str(key), len(self.current_doc().segments))

    def _segment_from_palette_key(self, key: str) -> PromptSegment | None:
        if key == "space":
            return PromptSegment(kind="literal", value=" ")
        if key == "literal":
            return PromptSegment(kind="literal", value="text")
        if key == "separator":
            return PromptSegment(kind="literal", value=":")
        if key == "newline":
            return PromptSegment(kind="newline")
        if key == "style":
            dlg = StyleDialog(self)
            if dlg.exec() != QDialog.Accepted:
                return None
            return dlg.result_segment()
        if key == "reset":
            return PromptSegment(kind="reset")
        return PromptSegment(kind="token", value=key)

    def _insert_palette_key(self, key: str, index: int) -> None:
        seg = self._segment_from_palette_key(key)
        if seg is None:
            return
        doc = self.current_doc()
        clamped = max(0, min(int(index), len(doc.segments)))
        doc.segments.insert(clamped, seg)
        self.segment_list.setCurrentRow(clamped)
        self.current_doc().raw_custom_mode = False
        self.sync_markup_from_model()

    def _palette_entry_specs(self) -> list[tuple[str, str, str]]:
        specs: list[tuple[str, str, str]] = []
        for category, keys in PALETTE_STRUCTURE.items():
            for key in keys:
                if key in TOKEN_DEFS:
                    label = str(TOKEN_DEFS[key]["label"])
                else:
                    label = {
                        "space": "Space",
                        "literal": "Literal text",
                        "separator": "Separator",
                        "newline": "New line",
                        "style": "Style segment",
                        "reset": "Reset style",
                    }.get(key, key)
                specs.append((str(key), label, str(category)))
        return specs

    def _populate_insert_menu(self, menu: QMenu, *, insert_index: int) -> None:
        by_category: dict[str, list[tuple[str, str]]] = {}
        for key, label, category in self._palette_entry_specs():
            by_category.setdefault(category, []).append((key, label))
        for category, entries in by_category.items():
            sub = menu.addMenu(category)
            for key, label in entries:
                action = sub.addAction(label)
                action.triggered.connect(lambda _checked=False, k=key, i=insert_index: self._insert_palette_key(k, i))

    def show_segment_context_menu(self, pos) -> None:
        row = int(self.segment_list.indexAt(pos).row())
        if row < 0:
            row = len(self.current_doc().segments)

        menu = QMenu(self.segment_list)
        add_before = menu.addMenu("Add Before")
        self._populate_insert_menu(add_before, insert_index=row)
        add_after = menu.addMenu("Add After")
        self._populate_insert_menu(add_after, insert_index=row + 1)
        menu.exec(self.segment_list.mapToGlobal(pos))

    def add_text_segment(self) -> None:
        seg = PromptSegment(kind="literal", value="text")
        self.current_doc().segments.append(seg)
        self.current_doc().raw_custom_mode = False
        self.sync_markup_from_model()

    def selected_index(self) -> int:
        return self.segment_list.currentRow()

    def on_edit_selected_segment(self) -> None:
        idx = self.selected_index()
        if idx < 0 or idx >= len(self.current_doc().segments):
            return
        seg = self.current_doc().segments[idx]

        if seg.kind == "literal":
            text, ok = SimpleTextInput.get_text(self, "Edit text", "Literal text", seg.value)
            if ok:
                seg.value = text
        elif seg.kind == "style":
            dlg = StyleDialog(self, seg)
            if dlg.exec() == QDialog.Accepted:
                new_seg = dlg.result_segment()
                seg.fg = new_seg.fg
                seg.bg = new_seg.bg
                seg.bold = new_seg.bold
                seg.underline = new_seg.underline
        elif seg.kind == "token":
            QMessageBox.information(self, "Token segment", "Token segments are fixed. Remove and reinsert a different token if needed.")
            return
        else:
            return

        self.current_doc().raw_custom_mode = False
        self.sync_markup_from_model()

    def remove_selected_segment(self) -> None:
        idx = self.selected_index()
        if idx < 0:
            return
        del self.current_doc().segments[idx]
        self.current_doc().raw_custom_mode = False
        self.sync_markup_from_model()

    def move_selected(self, delta: int) -> None:
        idx = self.selected_index()
        if idx < 0:
            return
        new_idx = idx + delta
        segs = self.current_doc().segments
        if not (0 <= new_idx < len(segs)):
            return
        segs[idx], segs[new_idx] = segs[new_idx], segs[idx]
        self.current_doc().raw_custom_mode = False
        self.sync_markup_from_model()
        self.segment_list.setCurrentRow(new_idx)

    def apply_preset(self, name: str) -> None:
        if self._syncing:
            return
        if not str(name or "").strip():
            return
        self.current_doc().segments = clone_segments(self._preset_segments(name))
        self.current_doc().raw_text = document_to_markup(self.current_doc())
        self.current_doc().raw_custom_mode = False
        self.sync_markup_from_model()
        self._schedule_state_save()

    def _refresh_preset_combo(self, *, keep_current: str | None = None) -> None:
        current = str(keep_current or self.preset_combo.currentText() or "").strip()
        if not current:
            current = DEFAULT_PRESET_NAMES[0]
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for name in DEFAULT_PRESET_NAMES:
            self.preset_combo.addItem(name)
        for name in sorted(self._custom_presets.keys(), key=lambda value: value.lower()):
            if name not in DEFAULT_PRESET_NAMES:
                self.preset_combo.addItem(name)
        idx = self.preset_combo.findText(current)
        if idx < 0:
            idx = 0 if self.preset_combo.count() else -1
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
        self.preset_combo.blockSignals(False)

    def save_current_preset(self) -> None:
        suggested = str(self.preset_combo.currentText() or "").strip()
        if suggested in DEFAULT_PRESET_NAMES:
            suggested = ""
        name, ok = SimpleTextInput.get_text(self, "Save preset", "Preset name", suggested)
        if not ok:
            return
        preset_name = str(name or "").strip()
        if not preset_name:
            return
        if preset_name in DEFAULT_PRESET_NAMES:
            QMessageBox.warning(self, "Preset", "Built-in preset names cannot be overwritten.")
            return
        self._custom_presets[preset_name] = clone_segments(self.current_doc().segments)
        self._refresh_preset_combo(keep_current=preset_name)
        self._schedule_state_save()

    def delete_selected_preset(self) -> None:
        name = str(self.preset_combo.currentText() or "").strip()
        if not name:
            return
        if name in DEFAULT_PRESET_NAMES:
            QMessageBox.information(self, "Preset", "Built-in presets cannot be deleted.")
            return
        if name not in self._custom_presets:
            return
        response = QMessageBox.question(
            self,
            "Delete preset",
            f"Delete preset '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if response != QMessageBox.Yes:
            return
        self._custom_presets.pop(name, None)
        self._refresh_preset_combo(keep_current=DEFAULT_PRESET_NAMES[0])
        self._schedule_state_save()

    def _preset_segments(self, name: str) -> List[PromptSegment]:
        custom = self._custom_presets.get(str(name or "").strip())
        if custom is not None:
            return clone_segments(custom)
        if name == "Minimal":
            return [
                PromptSegment("token", "cwd_base"),
                PromptSegment("literal", " "),
                PromptSegment("token", "prompt_symbol"),
                PromptSegment("literal", " "),
            ]
        if name == "Classic user@host:path$":
            return [
                PromptSegment("token", "username"),
                PromptSegment("literal", "@"),
                PromptSegment("token", "hostname"),
                PromptSegment("literal", ":"),
                PromptSegment("token", "cwd"),
                PromptSegment("token", "prompt_symbol"),
                PromptSegment("literal", " "),
            ]
        if name == "Two-line dev prompt":
            return [
                PromptSegment("token", "venv"),
                PromptSegment("token", "username"),
                PromptSegment("literal", "@"),
                PromptSegment("token", "hostname"),
                PromptSegment("literal", " "),
                PromptSegment("token", "cwd"),
                PromptSegment("literal", " "),
                PromptSegment("token", "git_branch"),
                PromptSegment("newline"),
                PromptSegment("token", "prompt_symbol"),
                PromptSegment("literal", " "),
            ]
        if name == "Git-friendly":
            return [
                PromptSegment("token", "username"),
                PromptSegment("literal", "@"),
                PromptSegment("token", "hostname"),
                PromptSegment("literal", " "),
                PromptSegment("token", "cwd"),
                PromptSegment("literal", " "),
                PromptSegment("token", "git_branch"),
                PromptSegment("literal", " "),
                PromptSegment("token", "prompt_symbol"),
                PromptSegment("literal", " "),
            ]
        if name == "Colorful prompt":
            return [
                PromptSegment("style", fg="green", bold=True),
                PromptSegment("token", "username"),
                PromptSegment("reset"),
                PromptSegment("literal", "@"),
                PromptSegment("style", fg="blue", bold=True),
                PromptSegment("token", "hostname"),
                PromptSegment("reset"),
                PromptSegment("literal", ":"),
                PromptSegment("style", fg="yellow"),
                PromptSegment("token", "cwd"),
                PromptSegment("reset"),
                PromptSegment("token", "prompt_symbol"),
                PromptSegment("literal", " "),
            ]
        return clone_segments(self.current_doc().segments)

    def reload_current_prompt(self) -> None:
        shell_name = self.shell_combo.currentText()
        raw = detect_current_prompt(shell_name)
        segs, warnings, custom = parse_markup(shell_name, raw)
        self.documents[shell_name] = PromptDocument(
            shell=shell_name,
            segments=segs,
            raw_custom_mode=custom,
            raw_text=raw,
        )
        self.current_warnings = warnings
        self._load_document(shell_name)
        self._schedule_state_save()

    def revert_document(self) -> None:
        self.reload_current_prompt()

    def copy_markup(self) -> None:
        QApplication.clipboard().setText(self.raw_edit.toPlainText())

    @staticmethod
    def _single_quote(value: str) -> str:
        return "'" + str(value or "").replace("'", "'\"'\"'") + "'"

    def preview_in_live_shell(self) -> None:
        shell_name = str(self.shell_combo.currentText() or "").strip().lower()
        if shell_name not in {"bash", "zsh"}:
            shell_name = "bash"

        shell_path = shutil.which(shell_name)
        if not shell_path:
            QMessageBox.warning(self, "Live preview", f"Could not locate '{shell_name}' in PATH.")
            return

        markup = document_to_shell_markup(self.current_doc())
        assign = (
            f"__prompt_editor_last_status=0; export PS1={self._single_quote(markup)}; clear"
            if shell_name == "bash"
            else f"setopt PROMPT_SUBST; __prompt_editor_last_status=0; export PROMPT={self._single_quote(markup)}; clear"
        )

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Live Prompt Preview ({shell_name})")
        dialog.resize(1040, 620)

        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "Interactive live shell preview. This does not write shell rc files unless you apply changes separately."
            )
        )
        terminal = TerminalWidget(
            shell=shell_path,
            cwd=str(Path.home()),
            parent=dialog,
            show_toolbar=True,
            history_lines=3000,
        )
        layout.addWidget(terminal, 1)

        QTimer.singleShot(220, lambda: terminal.post(assign))
        dialog.exec()

    def apply_current_markup(self) -> None:
        shell_name = self.shell_combo.currentText()
        doc = self.current_doc()

        try:
            compiled_markup = document_to_shell_markup(doc)
            ok, msg = apply_prompt(shell_name, compiled_markup)
            if ok:
                QMessageBox.information(
                    self,
                    "Prompt applied",
                    f"{msg}\n\nApplied markup:\n{compiled_markup}\n\nOpen a new {shell_name} session to see the change."
                )
        except Exception as exc:
            QMessageBox.critical(self, "Apply failed", str(exc))


class SimpleTextInput(QDialog):
    def __init__(self, parent=None, title="Input", label="Text", text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.line = QLineEdit(text)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        layout.addWidget(self.line)

        row = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(ok_btn)
        row.addWidget(cancel_btn)
        layout.addLayout(row)

    @staticmethod
    def get_text(parent, title, label, text=""):
        dlg = SimpleTextInput(parent, title, label, text)
        ok = dlg.exec() == QDialog.Accepted
        return dlg.line.text(), ok

@dataclass
class PreviewStyleState:
    fg: Optional[str] = None
    bg: Optional[str] = None
    bold: bool = False
    underline: bool = False


@dataclass
class PreviewChunk:
    text: str
    fg: Optional[str] = None
    bg: Optional[str] = None
    bold: bool = False
    underline: bool = False
    
class PromptEditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Prompt Editor Prototype")
        self.resize(1200, 760)
        self.editor = PromptEditorWidget()
        self.setCentralWidget(self.editor)

        status = QStatusBar()
        status.showMessage("Prompt editor prototype ready.")
        self.setStatusBar(status)


def main() -> None:
    app = QApplication(sys.argv)
    win = PromptEditorWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
