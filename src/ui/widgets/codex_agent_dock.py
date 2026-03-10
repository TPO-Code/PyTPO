from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEasingCurve, QObject, QRectF, QSize, QThread, QTimer, Qt, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from src.ui.codex_session_store import (
    CodexSessionRecord,
    canonical_path_text,
    find_codex_session,
    list_codex_sessions,
    read_codex_session,
    session_preview_text,
)
from src.ui.dialogs.codex_sessions_dialog import CodexSessionsDialog
from src.ui.theme_runtime import (
    current_codex_agent_bubble_theme,
    current_codex_agent_composer_theme,
    current_codex_agent_link_color,
    current_codex_agent_panel_theme,
)
from src.ui.widgets.chat_markdown_bubble import ChatMarkdownBubble
from src.ui.widgets.spellcheck_inputs import SpellcheckTextEdit
from src.ui.dialogs.file_dialog_bridge import get_open_file_names

DEFAULT_CODEX_COMMAND = "codex exec -"
_SESSION_ID_RE = re.compile(r"session id:\s*([0-9a-fA-F-]{36})", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_STATUS_PATH_RE = re.compile(r"^[A-Z?]{1,2}\s+(.+)$")
_TOOL_EXIT_LINE_RE = re.compile(r"^[a-zA-Z0-9_.-]+(?:\([^)]*\))?\s+exited\s+\d+\s+in\s+\d+ms:$")
_REASONING_CHOICES: list[tuple[str, str]] = [
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
    ("Extra High", "xhigh"),
]
_PERMISSION_CHOICES: list[tuple[str, str]] = [
    ("Default Permissions", "default"),
    ("Full Access", "full_access"),
]
_RATE_LIMITS_UNAVAILABLE = "5h: -- | Weekly: --"
_ATTACHMENTS_SUBDIR = Path(".tide") / "codex-agent" / "attachments"
_MENTION_SKIP_DIRS: set[str] = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "target",
    "build",
    "dist",
}
_MENTION_MAX_FILES = 5000
_MENTION_CACHE_TTL_SECONDS = 8.0
_BUBBLE_DEBUG_LOG_BASENAME = "codex-agent-bubble-debug.log"
_RATE_LIMITS_DEBUG_LOG_BASENAME = "codex-agent-rate-limits-debug.log"
_APP_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_USER_MESSAGE_START = "<tide_user_message>"
_PROMPT_USER_MESSAGE_END = "</tide_user_message>"
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_STREAM_RATE_LIMITS_RE = re.compile(
    r"5h:\s*(\d{1,3})%\s*remaining.*?(?:weekly|week):\s*(\d{1,3})%\s*remaining",
    re.IGNORECASE,
)
_WORD_TOKEN_RE = re.compile(r"[a-z0-9_./-]+")
_HEX_COLOR_RE = re.compile(r"^#(?P<rgb>[0-9a-fA-F]{6})(?P<alpha>[0-9a-fA-F]{2})?$")
_RGBA_COLOR_RE = re.compile(
    r"^rgba?\(\s*"
    r"(?P<red>\d{1,3})\s*,\s*"
    r"(?P<green>\d{1,3})\s*,\s*"
    r"(?P<blue>\d{1,3})"
    r"(?:\s*,\s*(?P<alpha>\d{1,3}(?:\.\d+)?|\.\d+))?"
    r"\s*\)$",
    re.IGNORECASE,
)
_ROLE_LABELS = {
    "user": "You",
    "assistant": "Assistant",
    "thinking": "Thinking",
    "tools": "Tools",
    "diff": "Diff",
    "system": "System",
    "meta": "Meta",
}


def _clamp_color_channel(value: object, *, fallback: int = 0) -> int:
    try:
        return max(0, min(255, int(value)))
    except Exception:
        return max(0, min(255, int(fallback)))


def _parse_theme_color(value: object, fallback: str | QColor) -> QColor:
    fallback_color = QColor(fallback) if not isinstance(fallback, QColor) else QColor(fallback)
    if not fallback_color.isValid():
        fallback_color = QColor("#000000")

    if isinstance(value, QColor):
        color = QColor(value)
        return color if color.isValid() else fallback_color

    text = str(value or "").strip()
    if not text:
        return fallback_color

    hex_match = _HEX_COLOR_RE.fullmatch(text)
    if hex_match:
        rgb = str(hex_match.group("rgb") or "")
        alpha_hex = str(hex_match.group("alpha") or "")
        try:
            red = int(rgb[0:2], 16)
            green = int(rgb[2:4], 16)
            blue = int(rgb[4:6], 16)
            alpha = int(alpha_hex, 16) if alpha_hex else 255
        except Exception:
            return fallback_color
        return QColor(red, green, blue, alpha)

    rgba_match = _RGBA_COLOR_RE.fullmatch(text)
    if rgba_match:
        red = _clamp_color_channel(rgba_match.group("red"))
        green = _clamp_color_channel(rgba_match.group("green"))
        blue = _clamp_color_channel(rgba_match.group("blue"))
        alpha_text = str(rgba_match.group("alpha") or "").strip()
        alpha = 255
        if alpha_text:
            try:
                alpha_value = float(alpha_text)
                alpha = round(alpha_value * 255.0) if alpha_value <= 1.0 else round(alpha_value)
            except Exception:
                alpha = 255
        return QColor(red, green, blue, _clamp_color_channel(alpha, fallback=255))

    color = QColor(text)
    return color if color.isValid() else fallback_color


def _normalize_rate_limits_display(text: object) -> str:
    normalized = str(text or "").strip()
    return normalized or _RATE_LIMITS_UNAVAILABLE


def _normalize_rate_limits_tooltip(text: object) -> str:
    normalized = str(text or "").strip()
    return normalized or "Rate limit data unavailable"


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", str(text or ""))


def _normalize_newlines(text: str) -> str:
    source = str(text or "")
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    source = source.replace("\u2028", "\n").replace("\u2029", "\n")
    return source


def _wrap_user_prompt_text(text: str) -> str:
    body = _normalize_newlines(str(text or "")).strip("\n")
    return f"{_PROMPT_USER_MESSAGE_START}\n{body}\n{_PROMPT_USER_MESSAGE_END}"


@dataclass(slots=True)
class _CodexInvocation:
    project_dir: Path
    command_template: str
    prompt_text: str


_RecentSession = CodexSessionRecord


@dataclass(slots=True)
class _TranscriptEntry:
    role: str
    text: str
    timestamp: str | None = None


@dataclass(slots=True)
class _PlanStep:
    status: str
    step: str


@dataclass(slots=True)
class _PlanState:
    explanation: str
    steps: list[_PlanStep]


class _CodexPlanPanel(QFrame):
    _STATUS_MARKERS = {
        "completed": "✓",
        "in_progress": "→",
        "pending": "•",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("codexPlanPanel")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(4)

        self._title = QLabel("Current plan")
        self._title.setObjectName("codexPlanTitle")
        self._layout.addWidget(self._title)

        self._summary = QLabel("")
        self._summary.setObjectName("codexPlanSummary")
        self._summary.setWordWrap(True)
        self._summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._layout.addWidget(self._summary)

        self._steps_host = QWidget()
        self._steps_layout = QVBoxLayout(self._steps_host)
        self._steps_layout.setContentsMargins(0, 0, 0, 0)
        self._steps_layout.setSpacing(3)
        self._layout.addWidget(self._steps_host)

        self.apply_theme()
        self.setVisible(False)

    def clear_plan(self) -> None:
        while self._steps_layout.count():
            item = self._steps_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._summary.clear()
        self._summary.setVisible(False)
        self.setVisible(False)

    def set_plan(self, plan: _PlanState | None) -> None:
        self.clear_plan()
        if plan is None:
            return
        summary = str(plan.explanation or "").strip()
        self._summary.setText(summary)
        self._summary.setVisible(bool(summary))
        for step in plan.steps:
            status_key = str(step.status or "").strip().casefold()
            marker = self._STATUS_MARKERS.get(status_key, "•")
            text = str(step.step or "").strip()
            if not text:
                continue
            label = QLabel(f"{marker} {text}")
            if status_key == "completed":
                label.setObjectName("codexPlanStepCompleted")
            elif status_key == "in_progress":
                label.setObjectName("codexPlanStepInProgress")
            else:
                label.setObjectName("codexPlanStepPending")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._steps_layout.addWidget(label)
        self.setVisible(bool(summary) or bool(plan.steps))

    def apply_theme(self) -> None:
        theme = current_codex_agent_panel_theme()
        border_color = str(theme.get("border_color") or "#3f4b5f")
        background_color = str(theme.get("background_color") or "#232b38")
        border_width = str(theme.get("border_width") or "1px")
        radius = str(theme.get("radius") or "0px")
        title_color = str(theme.get("title_color") or "#b7c6dc")
        text_color = str(theme.get("text_color") or "#d7e0ec")
        title_font_size = str(theme.get("title_font_size") or "11px")
        text_font_size = str(theme.get("text_font_size") or "12px")
        padding_x = max(0, int(str(theme.get("padding_x") or "10")))
        padding_y = max(0, int(str(theme.get("padding_y") or "8")))
        section_spacing = max(0, int(str(theme.get("section_spacing") or "4")))
        step_spacing = max(0, int(str(theme.get("step_spacing") or "3")))
        completed_color = str(theme.get("completed_color") or text_color)
        in_progress_color = str(theme.get("in_progress_color") or text_color)
        pending_color = str(theme.get("pending_color") or text_color)
        self._layout.setContentsMargins(padding_x, padding_y, padding_x, padding_y)
        self._layout.setSpacing(section_spacing)
        self._steps_layout.setSpacing(step_spacing)
        self.setStyleSheet(
            f"""
            QFrame#codexPlanPanel {{
                border: {border_width} solid {border_color};
                background-color: {background_color};
                border-radius: {radius};
            }}
            QLabel#codexPlanTitle {{
                color: {title_color};
                font-size: {title_font_size};
                font-weight: 600;
                background-color: transparent;
            }}
            QLabel#codexPlanSummary {{
                color: {text_color};
                font-size: {text_font_size};
                background-color: transparent;
            }}
            QLabel#codexPlanStepCompleted {{
                color: {completed_color};
                font-size: {text_font_size};
                background-color: transparent;
            }}
            QLabel#codexPlanStepInProgress {{
                color: {in_progress_color};
                font-size: {text_font_size};
                background-color: transparent;
            }}
            QLabel#codexPlanStepPending {{
                color: {pending_color};
                font-size: {text_font_size};
                background-color: transparent;
            }}
            """
        )


class _CodexWorker(QObject):
    output = Signal(str)
    finished = Signal(int)
    started = Signal()

    def __init__(self, invocation: _CodexInvocation) -> None:
        super().__init__()
        self._invocation = invocation
        self._process: subprocess.Popen[str] | None = None
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True
        process = self._process
        if process is None:
            return
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except Exception:
            pass

    def run(self) -> None:
        self.started.emit()
        try:
            command = self._invocation.command_template.format(
                project=str(self._invocation.project_dir)
            )
        except Exception as exc:
            self.output.emit(f"[error] Invalid command template: {exc}\n")
            self.finished.emit(2)
            return

        try:
            args = shlex.split(command)
        except Exception:
            self.output.emit("[error] Invalid command syntax.\n")
            self.finished.emit(2)
            return
        if not args:
            self.output.emit("[error] Command is empty.\n")
            self.finished.emit(2)
            return

        try:
            self._process = subprocess.Popen(
                args,
                cwd=str(self._invocation.project_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
        except FileNotFoundError:
            self.output.emit(
                f"[error] Command not found: {args[0]!r}\n"
                "Update the Codex command in the dock settings.\n"
            )
            self.finished.emit(127)
            return
        except Exception as exc:
            self.output.emit(f"[error] Failed to start process: {exc}\n")
            self.finished.emit(1)
            return

        try:
            stdin = self._process.stdin
            if stdin is not None:
                stdin.write(self._invocation.prompt_text)
                if not self._invocation.prompt_text.endswith("\n"):
                    stdin.write("\n")
                stdin.close()
        except Exception as exc:
            self.output.emit(f"[warn] Failed writing to stdin: {exc}\n")

        try:
            stdout = self._process.stdout
            if stdout is not None:
                for line in stdout:
                    if self._stop_requested:
                        break
                    self.output.emit(line)
        except Exception as exc:
            self.output.emit(f"[warn] Output stream error: {exc}\n")

        exit_code = 0
        try:
            if self._process is not None:
                exit_code = self._process.wait(timeout=2)
        except Exception:
            exit_code = 1
        self.finished.emit(exit_code)


class _CodexRunner(QObject):
    output = Signal(str)
    busyChanged = Signal(bool)
    exitCode = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._thread: QThread | None = None
        self._worker: _CodexWorker | None = None
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def start(self, invocation: _CodexInvocation) -> None:
        if self._busy:
            self.output.emit("[info] Codex is already running.\n")
            return
        self._thread = QThread()
        self._worker = _CodexWorker(invocation)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.output.connect(self.output)
        self._worker.started.connect(self._on_started)
        self._worker.finished.connect(self._on_finished)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def stop(self) -> None:
        worker = self._worker
        if worker is None:
            return
        worker.stop()

    def _on_started(self) -> None:
        self._busy = True
        self.busyChanged.emit(True)

    def _on_finished(self, code: int) -> None:
        self._busy = False
        self.busyChanged.emit(False)
        self.exitCode.emit(code)
        thread = self._thread
        if thread is None or not thread.isRunning():
            return
        thread.quit()
        if QThread.currentThread() is not thread:
            thread.wait(2000)

    def _on_thread_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None


class _ChatInputEdit(SpellcheckTextEdit):
    sendRequested = Signal()
    LINK_RAW_PROPERTY = QTextCharFormat.UserProperty + 101
    LINK_TARGET_PROPERTY = QTextCharFormat.UserProperty + 102

    def __init__(
        self,
        *,
        link_target_provider: Callable[[str], str] | None = None,
        mention_provider: Callable[[str], list[str]] | None = None,
        send_shortcut_enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("codexInputEdit")
        self.setAcceptRichText(False)
        self._link_target_provider = link_target_provider
        self._mention_provider = mention_provider
        self._send_shortcut_enabled = bool(send_shortcut_enabled)
        self._is_internal_change = False
        self._last_cursor_pos = int(self.textCursor().position())
        self._mention_popup = QListWidget(self)
        self._mention_popup.setObjectName("codexMentionPopup")
        self._mention_popup.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self._mention_popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._mention_popup.setFocusPolicy(Qt.NoFocus)
        self._mention_popup.setSelectionMode(QListWidget.SingleSelection)
        self._mention_popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._mention_popup.setAlternatingRowColors(False)
        self._mention_popup.itemClicked.connect(self._accept_mention_from_item)
        self.textChanged.connect(self._update_mention_popup)
        self.cursorPositionChanged.connect(self._on_cursor_position_changed_for_link_editing)
        self.cursorPositionChanged.connect(self._update_mention_popup)

    def setMaximumBlockCount(self, value: int) -> None:  # type: ignore[override]
        self.document().setMaximumBlockCount(max(0, int(value)))

    def set_mention_provider(
        self, provider: Callable[[str], list[str]] | None
    ) -> None:
        self._mention_provider = provider
        self._update_mention_popup()

    def set_link_target_provider(
        self, provider: Callable[[str], str] | None
    ) -> None:
        self._link_target_provider = provider

    def set_send_shortcut_enabled(self, enabled: bool) -> None:
        self._send_shortcut_enabled = bool(enabled)

    def close_mention_popup(self) -> None:
        self._mention_popup.hide()

    @staticmethod
    def _parse_markdown_link(raw_link: str) -> tuple[str, str] | None:
        raw = str(raw_link or "").strip()
        if not raw:
            return None
        match = _MARKDOWN_LINK_RE.fullmatch(raw)
        if match is None:
            return None
        label = str(match.group(1) or "").strip()
        target = str(match.group(2) or "").strip()
        if not label or not target:
            return None
        return label, target

    @classmethod
    def _link_display_text(cls, raw_link: str) -> str:
        parsed = cls._parse_markdown_link(raw_link)
        if parsed is None:
            return str(raw_link or "").strip()
        return parsed[0]

    def _make_link_char_format(self, raw_link: str) -> QTextCharFormat:
        parsed = self._parse_markdown_link(raw_link)
        fmt = QTextCharFormat()
        if parsed is None:
            return fmt
        _label, target = parsed
        fmt.setForeground(QColor(current_codex_agent_link_color()))
        fmt.setFontWeight(700)
        fmt.setProperty(self.LINK_RAW_PROPERTY, raw_link)
        fmt.setProperty(self.LINK_TARGET_PROPERTY, target)
        return fmt

    def _apply_codex_agent_theme(self) -> None:
        # Mention popup follows dock-specific QSS rules after theme reload.
        self._mention_popup.style().unpolish(self._mention_popup)
        self._mention_popup.style().polish(self._mention_popup)
        self._mention_popup.update()

    def _replace_range_with_link_label(
        self,
        *,
        start: int,
        end: int,
        raw_link: str,
        new_cursor_pos: int | None = None,
    ) -> None:
        shown = self._link_display_text(raw_link)
        if not shown:
            return
        was_internal = bool(self._is_internal_change)
        was_modified = bool(self.document().isModified())
        self._is_internal_change = True
        try:
            edit = QTextCursor(self.document())
            edit.beginEditBlock()
            edit.setPosition(max(0, int(start)))
            edit.setPosition(max(0, int(end)), QTextCursor.KeepAnchor)
            edit.removeSelectedText()
            edit.insertText(shown, self._make_link_char_format(raw_link))
            edit.endEditBlock()
            if new_cursor_pos is not None:
                max_pos = max(0, int(self.document().characterCount()) - 1)
                safe_pos = max(0, min(int(new_cursor_pos), max_pos))
                cur = self.textCursor()
                cur.setPosition(safe_pos)
                self.setTextCursor(cur)
        finally:
            self.document().setModified(was_modified)
            self._is_internal_change = was_internal
        self._last_cursor_pos = int(self.textCursor().position())

    def _link_raw_at_doc_pos(self, pos: int) -> str | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        probe = int(pos)
        if probe < 0 or probe > max_pos:
            return None
        cursor = QTextCursor(doc)
        cursor.setPosition(probe)
        fmt = cursor.charFormat()
        if not fmt.hasProperty(self.LINK_RAW_PROPERTY):
            return None
        raw = fmt.property(self.LINK_RAW_PROPERTY)
        text = str(raw or "").strip()
        return text or None

    def _link_span_at_cursor(
        self,
        cursor: QTextCursor | None = None,
    ) -> tuple[int, int, str] | None:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        if cur.hasSelection():
            return None
        pos = int(cur.position())

        left_raw = self._link_raw_at_doc_pos(pos - 1)
        right_raw = self._link_raw_at_doc_pos(pos)
        if not left_raw and not right_raw:
            return None
        raw = str(left_raw or right_raw or "")
        if not raw:
            return None
        probe = pos - 1 if left_raw else pos

        probe_block = self.document().findBlock(probe)
        if not probe_block.isValid():
            return None
        block_start = int(probe_block.position())
        block_end = block_start + len(str(probe_block.text() or ""))
        if probe < block_start or probe >= block_end:
            return None

        start = probe
        while start > block_start:
            if self._link_raw_at_doc_pos(start - 1) != raw:
                break
            start -= 1

        end = probe + 1
        while end < block_end:
            if self._link_raw_at_doc_pos(end) != raw:
                break
            end += 1

        if end <= start:
            return None
        return start, end, raw

    def _expand_link_for_editing(
        self,
        cursor: QTextCursor | None = None,
    ) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        span = self._link_span_at_cursor(cur)
        if not span:
            return False
        start, end, raw = span
        pos = int(cur.position())
        if pos <= start or pos >= end:
            return False

        relative = pos - start
        was_internal = bool(self._is_internal_change)
        was_modified = bool(self.document().isModified())
        self._is_internal_change = True
        try:
            edit = QTextCursor(self.document())
            edit.beginEditBlock()
            edit.setPosition(start)
            edit.setPosition(end, QTextCursor.KeepAnchor)
            edit.removeSelectedText()
            edit.insertText(raw, QTextCharFormat())
            edit.endEditBlock()

            new_cursor = self.textCursor()
            new_cursor.setPosition(start + 1 + relative)
            self.setTextCursor(new_cursor)
        finally:
            self.document().setModified(was_modified)
            self._is_internal_change = was_internal
        self._last_cursor_pos = int(self.textCursor().position())
        return True

    def _bracket_link_span_containing_doc_pos(self, pos: int) -> tuple[int, int, str] | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        probe = int(pos)
        if probe < 0 or probe > max_pos:
            return None
        block = doc.findBlock(probe)
        if not block.isValid():
            return None
        text = str(block.text() or "")
        if not text or "[" not in text or "]" not in text or "(" not in text:
            return None
        block_start = int(block.position())
        local = probe - block_start
        for match in _MARKDOWN_LINK_RE.finditer(text):
            m_start = int(match.start())
            m_end = int(match.end())
            if local <= m_start or local >= m_end:
                continue
            raw = str(match.group(0) or "").strip()
            if not self._parse_markdown_link(raw):
                continue
            return block_start + m_start, block_start + m_end, raw
        return None

    def _collapse_bracket_link_on_cursor_move(self, old_pos: int, new_pos: int) -> bool:
        old_i = int(old_pos)
        new_i = int(new_pos)
        candidates = [old_i]
        if new_i > old_i:
            candidates.append(old_i - 1)
        elif new_i < old_i:
            candidates.append(old_i + 1)

        span = None
        for candidate in candidates:
            span = self._bracket_link_span_containing_doc_pos(candidate)
            if span:
                break
        if not span:
            return False
        start, end, raw = span
        if start < new_i < end:
            return False

        adjusted_pos = new_i
        if adjusted_pos >= end:
            shown = self._link_display_text(raw)
            adjusted_pos = start + len(shown) + max(0, adjusted_pos - end)
        self._replace_range_with_link_label(
            start=start,
            end=end,
            raw_link=raw,
            new_cursor_pos=adjusted_pos,
        )
        return True

    def _on_cursor_position_changed_for_link_editing(self) -> None:
        if bool(self._is_internal_change):
            self._last_cursor_pos = int(self.textCursor().position())
            return
        old_pos = int(self._last_cursor_pos)
        new_pos = int(self.textCursor().position())
        if old_pos != new_pos:
            self._collapse_bracket_link_on_cursor_move(old_pos, new_pos)
            new_pos = int(self.textCursor().position())
        self._expand_link_for_editing(self.textCursor())
        self._last_cursor_pos = int(self.textCursor().position())

    def _cursor_is_appending_to_link(self, cursor: QTextCursor | None = None) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        pos = int(cur.position())
        left = self._link_raw_at_doc_pos(pos - 1)
        if not left:
            return False
        right = self._link_raw_at_doc_pos(pos)
        if right and right == left:
            return False
        return True

    def _cursor_is_prepending_to_link(self, cursor: QTextCursor | None = None) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        pos = int(cur.position())
        right = self._link_raw_at_doc_pos(pos)
        if not right:
            return False
        left = self._link_raw_at_doc_pos(pos - 1)
        if left and left == right:
            return False
        return True

    def _break_link_boundary_format(self) -> None:
        self.setCurrentCharFormat(QTextCharFormat())

    def _mention_context(self) -> tuple[int, int, str] | None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return None
        text = str(self.toPlainText() or "")
        pos = int(cursor.position())
        if pos < 0 or pos > len(text):
            return None
        scan = pos - 1
        while scan >= 0 and not text[scan].isspace():
            scan -= 1
        start = scan + 1
        if start >= pos:
            return None
        token = text[start:pos]
        if not token.startswith("@"):
            return None
        if len(token) > 1 and token[1].isspace():
            return None
        if token.count("@") > 1:
            return None
        query = token[1:]
        if any(ch.isspace() for ch in query):
            return None
        return start, pos, query

    def _update_mention_popup(self) -> None:
        context = self._mention_context()
        provider = self._mention_provider
        if context is None or not callable(provider):
            self._mention_popup.hide()
            return
        _start, _end, query = context
        try:
            candidates = list(provider(str(query)))
        except Exception:
            candidates = []
        if not candidates:
            self._mention_popup.hide()
            return
        self._mention_popup.clear()
        for rel_path in candidates:
            item = QListWidgetItem(f"@{rel_path}")
            item.setData(Qt.UserRole, str(rel_path))
            self._mention_popup.addItem(item)
        self._mention_popup.setCurrentRow(0)

        row_height = max(20, self._mention_popup.sizeHintForRow(0))
        height = min(280, 6 + row_height * min(len(candidates), 9))
        width = max(260, min(640, self.viewport().width()))
        anchor = self.cursorRect().bottomLeft()
        global_pos = self.mapToGlobal(anchor)
        self._mention_popup.setGeometry(global_pos.x(), global_pos.y() + 2, width, height)
        self._mention_popup.show()
        self.setFocus(Qt.OtherFocusReason)

    def _accept_mention_from_item(self, item: QListWidgetItem) -> bool:
        if item is None:
            return False
        context = self._mention_context()
        if context is None:
            self._mention_popup.hide()
            return False
        start, end, _query = context
        rel_path = str(item.data(Qt.UserRole) or "").strip()
        if not rel_path:
            return False
        target_provider = self._link_target_provider
        target_path = rel_path
        if callable(target_provider):
            try:
                candidate = str(target_provider(rel_path) or "").strip()
                if candidate:
                    target_path = candidate
            except Exception:
                pass
        label = Path(rel_path).name or rel_path
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            raw_link = f"[{label}]({target_path})"
            cursor.insertText(label, self._make_link_char_format(raw_link))
            cursor.insertText(" ", QTextCharFormat())
        finally:
            cursor.endEditBlock()
        self.setTextCursor(cursor)
        self._break_link_boundary_format()
        self._mention_popup.hide()
        self.setFocus()
        return True

    def _accept_current_mention(self) -> bool:
        item = self._mention_popup.currentItem()
        if item is None and self._mention_popup.count() > 0:
            item = self._mention_popup.item(0)
        if item is None:
            return False
        return self._accept_mention_from_item(item)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        key = int(event.key())
        modifiers = event.modifiers()
        if (
            self._send_shortcut_enabled
            and key in {Qt.Key_Return, Qt.Key_Enter}
            and modifiers & Qt.ControlModifier
        ):
            self._mention_popup.hide()
            self.sendRequested.emit()
            event.accept()
            return
        if self._mention_popup.isVisible():
            if key == Qt.Key_Escape:
                self._mention_popup.hide()
                event.accept()
                return
            if key in {Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown, Qt.Key_PageUp}:
                current = self._mention_popup.currentRow()
                if current < 0:
                    current = 0
                if key == Qt.Key_Down:
                    current = min(self._mention_popup.count() - 1, current + 1)
                elif key == Qt.Key_Up:
                    current = max(0, current - 1)
                elif key == Qt.Key_PageDown:
                    current = min(self._mention_popup.count() - 1, current + 5)
                else:
                    current = max(0, current - 5)
                self._mention_popup.setCurrentRow(current)
                event.accept()
                return
            if key in {Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab} and not modifiers:
                if self._accept_current_mention():
                    event.accept()
                    return
        text = str(event.text() or "")
        if (
            text
            and not (modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            and (
                self._cursor_is_appending_to_link(self.textCursor())
                or self._cursor_is_prepending_to_link(self.textCursor())
            )
        ):
            self._break_link_boundary_format()
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        super().focusOutEvent(event)
        QTimer.singleShot(0, self._hide_mention_if_focus_left)

    def _hide_mention_if_focus_left(self) -> None:
        if not self._mention_popup.isVisible():
            return
        focus_widget = QApplication.focusWidget()
        if focus_widget is not None:
            if focus_widget is self or self.isAncestorOf(focus_widget):
                return
            if focus_widget is self._mention_popup or self._mention_popup.isAncestorOf(focus_widget):
                return
        if self._mention_popup.underMouse():
            return
        self._mention_popup.hide()

    def to_codex_text(self) -> str:
        lines: list[str] = []
        block = self.document().firstBlock()
        while block.isValid():
            parts: list[str] = []
            it = block.begin()
            last_raw: str | None = None
            last_was_link = False
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    text = str(fragment.text() or "")
                    fmt = fragment.charFormat()
                    if fmt.hasProperty(self.LINK_RAW_PROPERTY):
                        raw = str(fmt.property(self.LINK_RAW_PROPERTY) or "").strip()
                        if raw:
                            if not last_was_link or raw != last_raw:
                                parts.append(raw)
                            last_was_link = True
                            last_raw = raw
                        else:
                            parts.append(text)
                            last_was_link = False
                            last_raw = None
                    else:
                        parts.append(text)
                        last_was_link = False
                        last_raw = None
                it += 1
            lines.append("".join(parts))
            block = block.next()
        return "\n".join(lines)

    def set_codex_text(self, text: str) -> None:
        raw_text = _normalize_newlines(str(text or ""))
        was_modified = bool(self.document().isModified())
        prior_blocked = self.blockSignals(True)
        was_internal = bool(self._is_internal_change)
        self._is_internal_change = True
        try:
            cursor = QTextCursor(self.document())
            cursor.beginEditBlock()
            cursor.select(QTextCursor.Document)
            cursor.removeSelectedText()
            cursor.deletePreviousChar()

            last_end = 0
            for match in _MARKDOWN_LINK_RE.finditer(raw_text):
                start = int(match.start())
                end = int(match.end())
                if start > last_end:
                    cursor.insertText(raw_text[last_end:start], QTextCharFormat())
                raw_link = str(match.group(0) or "").strip()
                parsed = self._parse_markdown_link(raw_link)
                if parsed is None:
                    cursor.insertText(raw_text[start:end], QTextCharFormat())
                else:
                    label, _target = parsed
                    cursor.insertText(label, self._make_link_char_format(raw_link))
                last_end = end
            if last_end < len(raw_text):
                cursor.insertText(raw_text[last_end:], QTextCharFormat())
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            self._last_cursor_pos = int(self.textCursor().position())
        finally:
            self.document().setModified(was_modified)
            self._is_internal_change = was_internal
            self.blockSignals(prior_blocked)


class _ShimmerBorderFrame(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("codexComposerFrame")
        self._shimmer_enabled = False
        self._shimmer_progress = 0.0
        self._travel_angle_degrees = -25.0
        self._border_radius = 10.0
        self._border_width = 2.0
        self._padding = 4
        self._content_layout = QVBoxLayout(self)
        self._content_layout.setContentsMargins(
            self._padding,
            self._padding,
            self._padding,
            self._padding,
        )
        self._content_layout.setSpacing(0)
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(1800)
        self._animation.setLoopCount(-1)
        self._animation.setEasingCurve(QEasingCurve.Linear)
        self._animation.valueChanged.connect(self._on_animation_value_changed)
        self._base_border_color = QColor("#4a4a4a")
        self._shimmer_color = QColor(120, 180, 255, 60)
        self._shimmer_highlight_color = QColor(180, 220, 255, 180)

    def set_content_widget(self, widget: QWidget) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            old = item.widget()
            if old is not None and old is not widget:
                old.setParent(None)
        self._content_layout.addWidget(widget)

    def set_shimmer_enabled(self, enabled: bool) -> None:
        wanted = bool(enabled)
        if wanted == self._shimmer_enabled:
            return
        self._shimmer_enabled = wanted
        if wanted:
            self._animation.start()
        else:
            self._animation.stop()
            self.update()

    def _on_animation_value_changed(self, value: object) -> None:
        try:
            self._shimmer_progress = float(value)
        except Exception:
            self._shimmer_progress = 0.0
        self.update()

    def apply_theme(self) -> None:
        theme = current_codex_agent_composer_theme()
        self._base_border_color = _parse_theme_color(
            theme.get("border_color"),
            "#4a4a4a",
        )
        self._shimmer_color = _parse_theme_color(
            theme.get("shimmer_color"),
            QColor(120, 180, 255, 60),
        )
        self._shimmer_highlight_color = _parse_theme_color(
            theme.get("shimmer_highlight_color"),
            QColor(180, 220, 255, 180),
        )
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(
            self._border_width / 2.0,
            self._border_width / 2.0,
            -self._border_width / 2.0,
            -self._border_width / 2.0,
        )
        if rect.width() <= 0.0 or rect.height() <= 0.0:
            return

        path = QPainterPath()
        path.addRoundedRect(rect, self._border_radius, self._border_radius)

        base_pen = QPen(self._base_border_color)
        base_pen.setWidthF(self._border_width)
        painter.setPen(base_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        if not self._shimmer_enabled:
            return

        angle_rad = math.radians(self._travel_angle_degrees)
        dx = math.cos(angle_rad)
        dy = math.sin(angle_rad)
        diagonal = math.hypot(rect.width(), rect.height())
        travel_distance = diagonal * 2.2
        offset = (self._shimmer_progress - 0.5) * travel_distance

        cx = rect.center().x() + dx * offset
        cy = rect.center().y() + dy * offset
        half_len = diagonal
        x1 = cx - dx * half_len
        y1 = cy - dy * half_len
        x2 = cx + dx * half_len
        y2 = cy + dy * half_len

        gradient = QLinearGradient(x1, y1, x2, y2)
        shimmer_clear = QColor(self._shimmer_color)
        shimmer_clear.setAlpha(0)
        gradient.setColorAt(0.00, shimmer_clear)
        gradient.setColorAt(0.42, shimmer_clear)
        gradient.setColorAt(0.48, self._shimmer_color)
        gradient.setColorAt(0.50, self._shimmer_highlight_color)
        gradient.setColorAt(0.52, self._shimmer_color)
        gradient.setColorAt(0.58, shimmer_clear)
        gradient.setColorAt(1.00, shimmer_clear)

        shimmer_pen = QPen()
        shimmer_pen.setBrush(gradient)
        shimmer_pen.setWidthF(self._border_width + 0.8)
        painter.setPen(shimmer_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


class CodexAgentDockWidget(QWidget):
    statusMessage = Signal(str)

    def __init__(
        self,
        *,
        project_dir_provider: Callable[[], str],
        tree_path_excluded_predicate: Callable[[str, bool], bool] | None = None,
        settings_provider: Callable[[], dict[str, Any]],
        settings_saver: Callable[[dict[str, Any]], None],
        file_opener: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_dir_provider = project_dir_provider
        self._tree_path_excluded_predicate = tree_path_excluded_predicate
        self._settings_provider = settings_provider
        self._settings_saver = settings_saver
        self._file_opener = file_opener

        self._runner = _CodexRunner()
        self._session_id: str | None = None
        self._session_project = ""
        self._session_options_signature: tuple[str, str, str] | None = None
        self._command_template = DEFAULT_CODEX_COMMAND
        self._auto_skip_git_repo_check = True
        self._sandbox_mode = "workspace-write"
        self._non_git_warning_shown_for_chat = False
        self._stream_mode = "assistant"
        self._stream_partial = ""
        self._pending_diff_lines: list[str] = []
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines: list[str] = []
        self._post_tokens_replay_index = 0
        self._suppress_prompt_echo = False
        self._prompt_echo_expected_lines: list[str] = []
        self._prompt_echo_index = 0
        self._prompt_echo_source_text = ""
        self._latest_assistant_bubble_text = ""
        self._forced_bubble_role_boundary: str | None = None
        self._transcript_entries: list[_TranscriptEntry] = []
        self._transcript_bubbles: list[ChatMarkdownBubble] = []
        self._turn_changed_files: list[str] = []
        self._turn_changed_file_set: set[str] = set()
        self._attached_source_files: list[str] = []
        self._attachment_chat_id: str | None = None
        self._attachment_stage_dir: Path | None = None
        self._mention_file_cache: list[str] = []
        self._mention_cache_project = ""
        self._mention_cache_at = 0.0
        self._recent_sessions_by_id: dict[str, _RecentSession] = {}
        self._updating_session_picker = False
        self._updating_options = False
        self._sessions_refresh_token = 0
        self._rate_limits_refresh_token = 0
        self._transcript_scroll_pending = False
        self._transcript_follow_after_layout = False
        self._transcript_scroll_animated = False
        self._transcript_internal_scroll = False
        self._current_plan: _PlanState | None = None
        self._plan_watch_active = False
        self._plan_log_path: Path | None = None
        self._plan_log_position = 0
        self._plan_start_from_beginning = False
        self._transcript_watch_active = False
        self._transcript_log_path: Path | None = None
        self._transcript_log_position = 0
        self._transcript_start_from_beginning = False
        self._last_rate_limits_text = _RATE_LIMITS_UNAVAILABLE
        self._last_rate_limits_tooltip = "Rate limit data unavailable"

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._persist_settings)

        self._transcript_scroll_animation = QVariantAnimation(self)
        self._transcript_scroll_animation.setDuration(180)
        self._transcript_scroll_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._transcript_scroll_animation.valueChanged.connect(
            self._on_transcript_scroll_animation_value_changed
        )

        self._plan_poll_timer = QTimer(self)
        self._plan_poll_timer.setInterval(700)
        self._plan_poll_timer.timeout.connect(self._poll_plan_updates)

        self._transcript_poll_timer = QTimer(self)
        self._transcript_poll_timer.setInterval(250)
        self._transcript_poll_timer.timeout.connect(self._poll_transcript_updates)

        self._build_ui()
        self._wire_signals()
        self._load_settings()

        self.destroyed.connect(lambda *_args: self.shutdown())

    def shutdown(self) -> None:
        self._save_timer.stop()
        self._plan_poll_timer.stop()
        self._transcript_poll_timer.stop()
        self._runner.stop()
        self._reset_attachments_for_new_chat()

    def reload_settings(self) -> None:
        if self._runner.busy:
            return
        self._load_settings()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        top_row = QHBoxLayout()
        self.session_picker = QToolButton()
        self.session_picker.setText("Recent Sessions")
        self.session_picker.setPopupMode(QToolButton.InstantPopup)
        self.session_picker.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.session_picker.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.session_menu = QMenu(self.session_picker)
        self.session_picker.setMenu(self.session_menu)
        self.new_chat_btn = QPushButton("New Chat")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        top_row.addWidget(self.session_picker)
        top_row.addStretch(1)
        top_row.addWidget(self.new_chat_btn)
        top_row.addWidget(self.stop_btn)
        root.addLayout(top_row)

        root.addWidget(QLabel("Transcript"))
        self.chat_splitter = QSplitter(Qt.Vertical)
        self.chat_splitter.setChildrenCollapsible(False)
        self.chat_splitter.setHandleWidth(7)

        transcript_panel = QWidget()
        transcript_panel.setObjectName("codexTranscriptPanel")
        transcript_panel_layout = QVBoxLayout(transcript_panel)
        transcript_panel_layout.setContentsMargins(0, 0, 0, 0)
        transcript_panel_layout.setSpacing(6)

        self.transcript_scroll = QScrollArea()
        self.transcript_scroll.setObjectName("codexTranscript")
        self.transcript_scroll.setWidgetResizable(True)
        self.transcript_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.transcript_scroll.verticalScrollBar().setSingleStep(18)

        self._transcript_container = QWidget()
        self._transcript_container.setObjectName("codexTranscriptContent")
        self._transcript_layout = QVBoxLayout(self._transcript_container)
        self._transcript_layout.setContentsMargins(8, 8, 8, 8)
        self._transcript_layout.setSpacing(2)
        self._transcript_layout.addStretch(1)
        self.transcript_scroll.setWidget(self._transcript_container)
        transcript_panel_layout.addWidget(self.transcript_scroll, 1)

        self.plan_panel = _CodexPlanPanel(transcript_panel)
        transcript_panel_layout.addWidget(self.plan_panel)
        self.chat_splitter.addWidget(transcript_panel)

        composer_container = QWidget()
        composer_container.setObjectName("codexComposerPanel")
        composer_layout = QVBoxLayout(composer_container)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setSpacing(6)

        self.input_frame = _ShimmerBorderFrame()
        self.input_edit = _ChatInputEdit(
            mention_provider=self._mention_candidates,
            link_target_provider=self._mention_link_target,
            parent=self.input_frame,
        )
        self.input_edit.setPlaceholderText("Ask Codex...")
        self.input_edit.setMinimumHeight(92)
        self.input_edit.setMaximumBlockCount(1200)
        self.input_frame.set_content_widget(self.input_edit)
        self.input_frame.apply_theme()
        composer_layout.addWidget(self.input_frame)

        self.attachments_container = QWidget()
        self.attachments_container.setObjectName("codexAttachmentsRow")
        attachments_layout = QHBoxLayout(self.attachments_container)
        attachments_layout.setContentsMargins(0, 0, 0, 0)
        attachments_layout.setSpacing(8)
        self.attachments_label = QLabel("")
        self.attachments_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.attachments_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.clear_attachments_btn = QPushButton("Clear")
        self.clear_attachments_btn.setToolTip("Remove all attached files")
        attachments_layout.addWidget(self.attachments_label, 1)
        attachments_layout.addWidget(self.clear_attachments_btn)
        composer_layout.addWidget(self.attachments_container)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input_hint_label = QLabel("Enter: new line  |  Ctrl+Enter: send")
        self.input_hint_label.setObjectName("codexInputHint")
        self.input_hint_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.rate_limits_label = QLabel(_RATE_LIMITS_UNAVAILABLE)
        self.rate_limits_label.setObjectName("CodexRateLimits")
        self.rate_limits_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.rate_limits_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.rate_limits_label.setWordWrap(False)
        self.rate_limits_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.agent_options_toggle_btn = QToolButton()
        self.agent_options_toggle_btn.setCheckable(True)
        self.agent_options_toggle_btn.setChecked(True)
        self.agent_options_toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.agent_options_toggle_btn.setIcon(self._load_settings_icon())
        self.agent_options_toggle_btn.setIconSize(QSize(16, 16))
        self.agent_options_toggle_btn.setToolTip("Show or hide agent options")
        self.add_file_btn = QPushButton("+")
        self.add_file_btn.setToolTip("Attach files")
        self.add_file_btn.setFixedWidth(26)
        self.send_btn = QPushButton("Send")
        input_row.addWidget(self.input_hint_label)
        input_row.addWidget(self.rate_limits_label, 1)
        input_row.addWidget(self.agent_options_toggle_btn)
        input_row.addWidget(self.add_file_btn)
        input_row.addWidget(self.send_btn)
        composer_layout.addLayout(input_row)

        self.agent_options_container = QWidget()
        self.agent_options_container.setObjectName("codexAgentOptionsPanel")
        agent_options_layout = QVBoxLayout(self.agent_options_container)
        agent_options_layout.setContentsMargins(0, 0, 0, 0)
        agent_options_layout.setSpacing(6)

        self.preamble_container = QWidget()
        self.preamble_container.setObjectName("codexPreamblePanel")
        preamble_layout = QVBoxLayout(self.preamble_container)
        preamble_layout.setContentsMargins(0, 0, 0, 0)
        preamble_layout.setSpacing(4)
        preamble_layout.addWidget(QLabel("System preamble (first turn only)"))
        self.preamble_frame = _ShimmerBorderFrame()
        self.preamble_edit = _ChatInputEdit(
            mention_provider=self._mention_candidates,
            link_target_provider=self._mention_link_target,
            send_shortcut_enabled=False,
            parent=self.preamble_frame,
        )
        self.preamble_edit.setPlaceholderText("Optional Codex instructions...")
        self.preamble_edit.setMaximumBlockCount(400)
        self.preamble_edit.setMinimumHeight(74)
        self.preamble_edit.setMaximumHeight(120)
        self.preamble_frame.set_content_widget(self.preamble_edit)
        self.preamble_frame.apply_theme()
        preamble_layout.addWidget(self.preamble_frame)
        agent_options_layout.addWidget(self.preamble_container)

        self.options_container = QWidget()
        self.options_container.setObjectName("codexOptionsPanel")
        options_grid = QGridLayout(self.options_container)
        options_grid.setContentsMargins(0, 0, 0, 0)
        options_grid.setHorizontalSpacing(8)
        options_grid.setVerticalSpacing(4)
        options_grid.addWidget(QLabel("Model"), 0, 0)
        self.model_combo = QComboBox()
        self.model_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(8)
        options_grid.addWidget(self.model_combo, 1, 0)
        options_grid.addWidget(QLabel("Reasoning"), 0, 1)
        self.reasoning_combo = QComboBox()
        self.reasoning_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.reasoning_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.reasoning_combo.setMinimumContentsLength(6)
        for label, value in _REASONING_CHOICES:
            self.reasoning_combo.addItem(label, value)
        options_grid.addWidget(self.reasoning_combo, 1, 1)
        options_grid.addWidget(QLabel("Permissions"), 2, 0, 1, 2)
        self.permissions_combo = QComboBox()
        self.permissions_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.permissions_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.permissions_combo.setMinimumContentsLength(8)
        for label, value in _PERMISSION_CHOICES:
            self.permissions_combo.addItem(label, value)
        options_grid.addWidget(self.permissions_combo, 3, 0, 1, 2)
        options_grid.setColumnStretch(0, 1)
        options_grid.setColumnStretch(1, 1)
        agent_options_layout.addWidget(self.options_container)
        composer_layout.addWidget(self.agent_options_container)
        self._set_agent_options_visible(True)

        self.chat_splitter.addWidget(composer_container)
        self.chat_splitter.setStretchFactor(0, 6)
        self.chat_splitter.setStretchFactor(1, 2)
        self.chat_splitter.setSizes([560, 210])
        root.addWidget(self.chat_splitter, 1)
        self._refresh_attachment_summary()

    def _wire_signals(self) -> None:
        self.new_chat_btn.clicked.connect(self._new_chat)
        self.stop_btn.clicked.connect(self._runner.stop)
        self.send_btn.clicked.connect(self._send)
        self.add_file_btn.clicked.connect(self._on_add_files_clicked)
        self.clear_attachments_btn.clicked.connect(self._clear_selected_attachments)
        self.input_edit.sendRequested.connect(self._send)
        self.agent_options_toggle_btn.toggled.connect(self._on_agent_options_toggled)

        self.preamble_edit.textChanged.connect(self._schedule_persist_settings)
        self.model_combo.currentIndexChanged.connect(self._on_option_changed)
        self.reasoning_combo.currentIndexChanged.connect(self._on_option_changed)
        self.permissions_combo.currentIndexChanged.connect(self._on_option_changed)

        self._runner.output.connect(self._append_raw)
        self._runner.busyChanged.connect(self._on_busy_changed)
        self._runner.exitCode.connect(self._on_exit_code)
        self.transcript_scroll.verticalScrollBar().valueChanged.connect(
            self._on_transcript_scrollbar_value_changed
        )
        self.transcript_scroll.verticalScrollBar().sliderPressed.connect(
            self._transcript_scroll_animation.stop
        )

    def _load_settings_icon(self) -> QIcon:
        icon_path = _APP_ROOT / "src" / "icons" / "settings.png"
        if icon_path.is_file():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                return icon
        return QIcon()

    def _set_agent_options_visible(self, visible: bool) -> None:
        shown = bool(visible)
        self.agent_options_container.setVisible(shown)
        self.preamble_container.setVisible(shown)
        self.options_container.setVisible(shown)
        self.agent_options_toggle_btn.setText("v" if shown else ">")

    def _on_agent_options_toggled(self, checked: bool) -> None:
        self._set_agent_options_visible(bool(checked))
        if self._updating_options:
            return
        self._schedule_persist_settings()

    def _clear_transcript(self) -> None:
        self._transcript_entries.clear()
        self._forced_bubble_role_boundary = None
        self._pending_diff_lines.clear()
        while self._transcript_layout.count() > 1:
            item = self._transcript_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._transcript_bubbles.clear()

    def _set_current_plan(self, plan: _PlanState | None) -> None:
        self._current_plan = plan
        self.plan_panel.set_plan(plan)

    def _clear_current_plan(self) -> None:
        self._set_current_plan(None)

    def _session_log_path(self, session_id: str) -> Path | None:
        normalized_id = str(session_id or "").strip()
        if not normalized_id:
            return None
        sessions_dir = Path.home() / ".codex" / "sessions"
        if not sessions_dir.is_dir():
            return None
        candidates = list(sessions_dir.rglob(f"*{normalized_id}.jsonl"))
        if not candidates:
            return None
        try:
            return max(candidates, key=lambda path: path.stat().st_mtime)
        except Exception:
            return candidates[-1]

    @staticmethod
    def _extract_plan_state_from_event(data: dict[str, Any]) -> _PlanState | None:
        if str(data.get("type") or "").strip() != "response_item":
            return None
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return None
        if str(payload.get("type") or "").strip() != "function_call":
            return None
        if str(payload.get("name") or "").strip() != "update_plan":
            return None
        raw_arguments = str(payload.get("arguments") or "").strip()
        if not raw_arguments:
            return None
        try:
            parsed = json.loads(raw_arguments)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        explanation = str(parsed.get("explanation") or "").strip()
        raw_steps = parsed.get("plan")
        steps: list[_PlanStep] = []
        if isinstance(raw_steps, list):
            for item in raw_steps:
                if not isinstance(item, dict):
                    continue
                step_text = str(item.get("step") or "").strip()
                if not step_text:
                    continue
                steps.append(
                    _PlanStep(
                        status=str(item.get("status") or "").strip().casefold(),
                        step=step_text,
                    )
                )
        if not explanation and not steps:
            return None
        return _PlanState(explanation=explanation, steps=steps)

    def _reset_plan_tracking(self, *, clear_panel: bool) -> None:
        self._plan_poll_timer.stop()
        self._plan_watch_active = False
        self._plan_log_path = None
        self._plan_log_position = 0
        self._plan_start_from_beginning = False
        if clear_panel:
            self._clear_current_plan()

    def _begin_plan_tracking_for_turn(self) -> None:
        self._clear_current_plan()
        self._plan_watch_active = True
        self._plan_log_path = None
        self._plan_log_position = 0
        self._plan_start_from_beginning = not bool(str(self._session_id or "").strip())
        session_id = str(self._session_id or "").strip()
        if session_id:
            log_path = self._session_log_path(session_id)
            self._plan_log_path = log_path
            if log_path is not None:
                try:
                    self._plan_log_position = log_path.stat().st_size
                except Exception:
                    self._plan_log_position = 0
        self._plan_poll_timer.start()

    def _ensure_plan_log_ready(self) -> bool:
        if not self._plan_watch_active:
            return False
        session_id = str(self._session_id or "").strip()
        if not session_id:
            return False
        if self._plan_log_path is None:
            self._plan_log_path = self._session_log_path(session_id)
            if self._plan_log_path is None:
                return False
            if self._plan_start_from_beginning:
                self._plan_log_position = 0
            else:
                try:
                    self._plan_log_position = self._plan_log_path.stat().st_size
                except Exception:
                    self._plan_log_position = 0
            self._plan_start_from_beginning = False
        return True

    def _poll_plan_updates(self) -> None:
        if not self._ensure_plan_log_ready():
            return
        log_path = self._plan_log_path
        if log_path is None:
            return
        try:
            file_size = log_path.stat().st_size
        except Exception:
            return
        if self._plan_log_position > file_size:
            self._plan_log_position = 0
        latest_plan: _PlanState | None = None
        try:
            with log_path.open("r", encoding="utf-8") as handle:
                if self._plan_log_position > 0:
                    handle.seek(self._plan_log_position)
                for raw_line in handle:
                    line = str(raw_line or "").strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    candidate = self._extract_plan_state_from_event(data)
                    if candidate is not None:
                        latest_plan = candidate
                self._plan_log_position = handle.tell()
        except Exception:
            return
        if latest_plan is not None:
            self._set_current_plan(latest_plan)

    def _reset_transcript_tracking(self) -> None:
        self._transcript_poll_timer.stop()
        self._transcript_watch_active = False
        self._transcript_log_path = None
        self._transcript_log_position = 0
        self._transcript_start_from_beginning = False

    def _begin_transcript_tracking_for_turn(self) -> None:
        self._transcript_watch_active = True
        self._transcript_log_path = None
        self._transcript_log_position = 0
        self._transcript_start_from_beginning = not bool(str(self._session_id or "").strip())
        session_id = str(self._session_id or "").strip()
        if session_id:
            log_path = self._session_log_path(session_id)
            self._transcript_log_path = log_path
            if log_path is not None:
                try:
                    self._transcript_log_position = log_path.stat().st_size
                except Exception:
                    self._transcript_log_position = 0
        self._transcript_poll_timer.start()

    def _ensure_transcript_log_ready(self) -> bool:
        if not self._transcript_watch_active:
            return False
        session_id = str(self._session_id or "").strip()
        if not session_id:
            return False
        if self._transcript_log_path is None:
            self._transcript_log_path = self._session_log_path(session_id)
            if self._transcript_log_path is None:
                return False
            if self._transcript_start_from_beginning:
                self._transcript_log_position = 0
            else:
                try:
                    self._transcript_log_position = self._transcript_log_path.stat().st_size
                except Exception:
                    self._transcript_log_position = 0
            self._transcript_start_from_beginning = False
        return True

    def _apply_live_transcript_item(self, role: str, text: str, stamp: str | None) -> None:
        normalized_role = str(role or "").strip()
        if normalized_role == "user":
            return
        if normalized_role == "assistant":
            for raw_line in str(text or "").splitlines():
                self._capture_changed_files_from_line(raw_line)
        else:
            for raw_line in str(text or "").splitlines():
                self._capture_changed_files_from_line(raw_line)
        self._add_bubble(normalized_role, text, timestamp=stamp)

    def _poll_transcript_updates(self) -> None:
        if not self._ensure_transcript_log_ready():
            return
        log_path = self._transcript_log_path
        if log_path is None:
            return
        try:
            file_size = log_path.stat().st_size
        except Exception:
            return
        if self._transcript_log_position > file_size:
            self._transcript_log_position = 0
        pending_items: list[tuple[str, str, str | None]] = []
        try:
            with log_path.open("r", encoding="utf-8") as handle:
                if self._transcript_log_position > 0:
                    handle.seek(self._transcript_log_position)
                for raw_line in handle:
                    line = str(raw_line or "").strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    if str(data.get("type") or "").strip() != "response_item":
                        continue
                    payload = data.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    role, text = self._extract_visible_session_item(payload)
                    if not role or not text:
                        continue
                    stamp = self._format_iso_timestamp(data.get("timestamp"))
                    pending_items.append((role, text, stamp))
                self._transcript_log_position = handle.tell()
        except Exception:
            return
        for role, text, stamp in pending_items:
            self._apply_live_transcript_item(role, text, stamp)

    def _schedule_transcript_render(
        self,
        *,
        scroll_to_bottom: bool = True,
        immediate: bool = False,
        animated: bool = False,
    ) -> None:
        self._transcript_follow_after_layout = bool(scroll_to_bottom)
        self._transcript_scroll_animated = bool(animated)
        if not scroll_to_bottom:
            return
        if immediate:
            self._transcript_scroll_pending = False
            self._scroll_transcript_to_bottom(animated=False)
            return
        if self._transcript_scroll_pending:
            return
        self._transcript_scroll_pending = True
        QTimer.singleShot(16, self._flush_scheduled_transcript_render)

    def _flush_scheduled_transcript_render(self) -> None:
        self._transcript_scroll_pending = False
        if not self._transcript_follow_after_layout:
            return
        self._scroll_transcript_to_bottom(animated=self._transcript_scroll_animated)

    def _is_transcript_at_bottom(self, tolerance: int = 4) -> bool:
        bar = self.transcript_scroll.verticalScrollBar()
        return bar.value() >= max(0, bar.maximum() - max(0, tolerance))

    def _scroll_transcript_to_bottom(self, *, animated: bool = False) -> None:
        bar = self.transcript_scroll.verticalScrollBar()
        maximum = bar.maximum()
        if not animated or bar.value() >= maximum:
            self._transcript_scroll_animation.stop()
            self._transcript_internal_scroll = True
            try:
                bar.setValue(maximum)
            finally:
                self._transcript_internal_scroll = False
            return
        self._transcript_scroll_animation.stop()
        self._transcript_scroll_animation.setStartValue(bar.value())
        self._transcript_scroll_animation.setEndValue(maximum)
        self._transcript_scroll_animation.start()

    def _on_transcript_scroll_animation_value_changed(self, value: Any) -> None:
        bar = self.transcript_scroll.verticalScrollBar()
        try:
            self._transcript_internal_scroll = True
            bar.setValue(int(value))
        except (TypeError, ValueError):
            return
        finally:
            self._transcript_internal_scroll = False

    def _on_transcript_scrollbar_value_changed(self, _value: int) -> None:
        if self._transcript_internal_scroll:
            return
        self._transcript_follow_after_layout = self._is_transcript_at_bottom()

    def _on_transcript_bubble_size_hint_changed(self) -> None:
        if self._transcript_follow_after_layout or self._is_transcript_at_bottom():
            self._schedule_transcript_render(
                scroll_to_bottom=True,
                animated=self._transcript_scroll_animated,
            )

    @staticmethod
    def _append_transcript_line(current_text: str, line: str) -> str:
        chunk = str(line or "")
        if not current_text:
            return chunk.lstrip("\r\n")
        return f"{current_text}\n{chunk}"

    def _buffer_diff_line(self, line: str) -> None:
        self._pending_diff_lines.append(str(line or ""))

    def _flush_pending_diff_bubble(self) -> None:
        if not self._pending_diff_lines:
            return
        text = "\n".join(self._pending_diff_lines)
        self._pending_diff_lines.clear()
        self._add_bubble("diff", text, merge=True)

    def _bubble_stylesheet(self, role: str) -> str:
        theme = current_codex_agent_bubble_theme(role)
        border_width = str(theme["border_width"] or "1px")
        border_color = str(theme["border_color"] or "#2f3746")
        background_color = str(theme["background_color"] or "#1a1f2a")
        text_color = str(theme["text_color"] or "#e6edf3")
        header_color = str(theme.get("header_color") or "#8d9cb4")
        toggle_color = str(theme.get("toggle_color") or "#9db1cb")
        preview_color = str(theme.get("preview_color") or "#c8d2e2")
        return f"""
            QFrame#codexBubble {{
                border: {border_width} solid {border_color};
                background-color: {background_color};
                border-radius: 0px;
            }}
            QLabel#codexBubbleHeader {{
                color: {header_color};
                font-size: 10px;
                font-weight: 500;
            }}
            QPushButton#codexBubbleToggle {{
                color: {toggle_color};
                border: none;
                background: transparent;
                font-size: 10px;
                min-height: 20px;
                padding: 0px 4px;
            }}
            QLabel#codexBubblePreview {{
                color: {preview_color};
                font-size: 12px;
            }}
            QTextEdit#codexBubbleBody {{
                color: {text_color};
                background-color: transparent;
                font-size: 13px;
                border: none;
                padding: 0px;
                margin: 0px;
                border-radius: 0px;
            }}
            """

    def _apply_bubble_theme(self, bubble: ChatMarkdownBubble) -> None:
        if not isinstance(bubble, ChatMarkdownBubble):
            return
        bubble.setStyleSheet(self._bubble_stylesheet(str(bubble.role or "")))

    def _apply_codex_agent_theme(self) -> None:
        for bubble in self._transcript_bubbles:
            self._apply_bubble_theme(bubble)
        self.plan_panel.apply_theme()
        self.input_frame.apply_theme()
        self.input_edit._apply_codex_agent_theme()
        self.preamble_frame.apply_theme()
        self.preamble_edit._apply_codex_agent_theme()

    def _new_transcript_bubble(self, entry: _TranscriptEntry) -> ChatMarkdownBubble:
        role = str(entry.role or "").strip()
        bubble = ChatMarkdownBubble(
            role=role,
            text=str(entry.text or ""),
            timestamp=str(entry.timestamp or "").strip() or None,
            link_activated=self._on_bubble_link_activated,
            role_label=_ROLE_LABELS.get(role, role.title()),
        )
        self._apply_bubble_theme(bubble)
        bubble.sizeHintChanged.connect(self._on_transcript_bubble_size_hint_changed)
        return bubble

    def _load_model_choices(self, selected_model: str) -> None:
        selected = str(selected_model or "").strip()
        entries: list[tuple[str, str]] = [("Default", "")]
        cache_path = Path.home() / ".codex" / "models_cache.json"
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

        models = payload.get("models")
        if isinstance(models, list):
            for item in models:
                if not isinstance(item, dict):
                    continue
                visibility = str(item.get("visibility") or "list").strip().lower()
                if visibility != "list":
                    continue
                slug = str(item.get("slug") or "").strip()
                if not slug:
                    continue
                display = str(item.get("display_name") or slug).strip() or slug
                entries.append((display, slug))

        known = {value for _label, value in entries}
        if selected and selected not in known:
            entries.append((f"{selected} (custom)", selected))

        self.model_combo.clear()
        for label, value in entries:
            self.model_combo.addItem(label, value)
        self._set_combo_data(self.model_combo, selected if selected in known or selected else "")

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index < 0:
            index = 0
        combo.setCurrentIndex(index)

    def _load_settings(self) -> None:
        raw = self._settings_provider() if callable(self._settings_provider) else {}
        data = dict(raw) if isinstance(raw, dict) else {}

        command = str(data.get("command_template") or "").strip()
        if not command:
            command = DEFAULT_CODEX_COMMAND
        if command == "codex":
            command = DEFAULT_CODEX_COMMAND
        if command == "codex exec -":
            command = DEFAULT_CODEX_COMMAND
        if command == "codex exec --skip-git-repo-check -":
            command = DEFAULT_CODEX_COMMAND
        if command == "codex exec --skip-git-repo-check --sandbox workspace-write --ask-for-approval never -":
            command = DEFAULT_CODEX_COMMAND

        self._updating_options = True
        try:
            self._command_template = command
            self.preamble_edit.set_codex_text(str(data.get("system_preamble") or ""))
            self._auto_skip_git_repo_check = bool(data.get("auto_skip_git_repo_check", True))
            sandbox_mode = str(data.get("sandbox_mode") or "").strip().lower()
            if sandbox_mode not in {"read-only", "workspace-write", "danger-full-access"}:
                sandbox_mode = "workspace-write"
            self._sandbox_mode = sandbox_mode
            show_options = bool(data.get("show_agent_options", True))
            show_preamble = bool(data.get("show_system_preamble", False))
            show_agent_options = show_options or show_preamble
            self.agent_options_toggle_btn.blockSignals(True)
            self.agent_options_toggle_btn.setChecked(show_agent_options)
            self.agent_options_toggle_btn.blockSignals(False)
            self._set_agent_options_visible(show_agent_options)

            model = str(data.get("model") or "").strip()
            self._load_model_choices(model)

            reasoning = str(data.get("model_reasoning_effort") or "").strip().lower()
            if reasoning not in {"low", "medium", "high", "xhigh"}:
                reasoning = "medium"
            self._set_combo_data(self.reasoning_combo, reasoning)

            permission_mode = str(data.get("permission_mode") or "").strip().lower()
            if permission_mode not in {"default", "full_access"}:
                permission_mode = "default"
            self._set_combo_data(self.permissions_combo, permission_mode)

            session_id = str(data.get("session_id") or "").strip()
            self._session_id = session_id or None
            self._session_project = str(data.get("session_project_dir") or "").strip()
            self._last_rate_limits_text = _normalize_rate_limits_display(
                data.get("last_rate_limits_text")
            )
            self._last_rate_limits_tooltip = _normalize_rate_limits_tooltip(
                data.get("last_rate_limits_tooltip")
            )
            self._session_options_signature = (
                self._active_options_signature() if self._session_id else None
            )
            self._refresh_recent_sessions_picker(select_session_id=self._session_id)
            if self._session_id and not self._transcript_entries:
                session = self._recent_sessions_by_id.get(self._session_id)
                if session is not None:
                    self._restore_session_transcript(session)
            self._refresh_session_ui()
            self._update_rate_limits_label()
        finally:
            self._updating_options = False

    def _schedule_persist_settings(self) -> None:
        self._save_timer.start()

    def _persist_settings(self) -> None:
        model = str(self.model_combo.currentData() or "").strip()
        reasoning = str(self.reasoning_combo.currentData() or "medium").strip()
        permission_mode = str(self.permissions_combo.currentData() or "default").strip()
        payload = {
            "command_template": str(self._command_template or "").strip() or DEFAULT_CODEX_COMMAND,
            "system_preamble": str(self.preamble_edit.to_codex_text() or ""),
            "auto_skip_git_repo_check": bool(self._auto_skip_git_repo_check),
            "sandbox_mode": str(self._sandbox_mode or "workspace-write"),
            "show_system_preamble": bool(self.agent_options_toggle_btn.isChecked()),
            "show_agent_options": bool(self.agent_options_toggle_btn.isChecked()),
            "model": model,
            "model_reasoning_effort": reasoning,
            "permission_mode": permission_mode,
            "session_id": str(self._session_id or ""),
            "session_project_dir": str(self._session_project or ""),
            "last_rate_limits_text": str(self._last_rate_limits_text or ""),
            "last_rate_limits_tooltip": str(self._last_rate_limits_tooltip or ""),
        }
        try:
            self._settings_saver(payload)
        except Exception:
            pass

    def _apply_rate_limits_label(
        self,
        text: str,
        tooltip: str,
        *,
        remember: bool,
    ) -> None:
        normalized_text = _normalize_rate_limits_display(text)
        normalized_tooltip = _normalize_rate_limits_tooltip(tooltip)
        self.rate_limits_label.setText(normalized_text)
        self.rate_limits_label.setToolTip(normalized_tooltip)
        if not remember or normalized_text == _RATE_LIMITS_UNAVAILABLE:
            return
        changed = (
            normalized_text != self._last_rate_limits_text
            or normalized_tooltip != self._last_rate_limits_tooltip
        )
        self._last_rate_limits_text = normalized_text
        self._last_rate_limits_tooltip = normalized_tooltip
        if changed:
            self._schedule_persist_settings()

    def _project_dir(self) -> Path | None:
        raw = self._project_dir_provider() if callable(self._project_dir_provider) else ""
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            path = Path(text).expanduser().resolve()
        except Exception:
            return None
        if not path.is_dir():
            return None
        return path

    def _settings_manager(self) -> Any | None:
        current: QWidget | None = self
        while isinstance(current, QWidget):
            manager = getattr(current, "settings_manager", None)
            if manager is not None:
                return manager
            current = current.parentWidget()

        app = QApplication.instance()
        if app is None:
            return None
        for top in app.topLevelWidgets():
            manager = getattr(top, "settings_manager", None)
            if manager is not None:
                return manager
        return None

    def _is_tree_path_excluded(self, path: str, is_dir: bool) -> bool:
        predicate = self._tree_path_excluded_predicate
        if not callable(predicate):
            return False
        try:
            return bool(predicate(path, is_dir))
        except Exception:
            return False

    def _refresh_attachment_summary(self) -> None:
        total = len(self._attached_source_files)
        if total <= 0:
            self.attachments_container.setVisible(False)
            self.attachments_label.setText("")
            self.attachments_label.setToolTip("")
            return
        names = [Path(path).name or path for path in self._attached_source_files]
        preview_items = names[:3]
        preview = ", ".join(preview_items)
        if total > 3:
            preview = f"{preview} +{total - 3} more"
        self.attachments_label.setText(f"Attached ({total}): {preview}")
        self.attachments_label.setToolTip("\n".join(self._attached_source_files))
        self.attachments_container.setVisible(True)

    def _clear_attachment_stage_dir(self) -> None:
        stage_dir = self._attachment_stage_dir
        self._attachment_stage_dir = None
        if stage_dir is None:
            return
        try:
            shutil.rmtree(stage_dir, ignore_errors=True)
        except Exception:
            pass

    def _reset_attachments_for_new_chat(self) -> None:
        self._clear_attachment_stage_dir()
        self._attachment_chat_id = None
        self._attached_source_files.clear()
        self._refresh_attachment_summary()

    def _clear_selected_attachments(self) -> None:
        self._attached_source_files.clear()
        self._clear_attachment_stage_dir()
        self._refresh_attachment_summary()

    def _clear_selected_attachments_after_send(self) -> None:
        self._attached_source_files.clear()
        self._refresh_attachment_summary()

    def _on_add_files_clicked(self) -> None:
        project = self._project_dir()
        initial_dir = str(project) if project is not None else str(Path.home())
        selected, _filter = get_open_file_names(
            parent=self,
            manager=self._settings_manager(),
            caption="Attach Files for Codex",
            directory=initial_dir,
            file_filter="All Files (*)",
        )
        if not selected:
            return
        existing = {
            self._canonical_path_text(path)
            for path in self._attached_source_files
        }
        added = 0
        for item in selected:
            raw = str(item or "").strip()
            if not raw:
                continue
            path = Path(raw).expanduser()
            if not path.is_file():
                continue
            try:
                normalized = str(path.resolve(strict=False))
            except Exception:
                normalized = str(path)
            key = self._canonical_path_text(normalized)
            if not key or key in existing:
                continue
            existing.add(key)
            self._attached_source_files.append(normalized)
            added += 1
        if added <= 0:
            return
        self._clear_attachment_stage_dir()
        self._refresh_attachment_summary()

    def _ensure_attachment_chat_id(self) -> str:
        session_id = str(self._attachment_chat_id or "").strip()
        if session_id:
            return session_id
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
        self._attachment_chat_id = session_id
        return session_id

    def _stage_attachments_for_turn(self, project: Path) -> tuple[list[str], list[str]]:
        if not self._attached_source_files:
            self._clear_attachment_stage_dir()
            return [], []

        session_id = self._ensure_attachment_chat_id()
        stage_dir = project / _ATTACHMENTS_SUBDIR / session_id
        self._clear_attachment_stage_dir()
        try:
            stage_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return [], [f"Could not create attachment folder: {exc}"]

        references: list[str] = []
        failures: list[str] = []
        for index, source_text in enumerate(self._attached_source_files, start=1):
            source = Path(source_text).expanduser()
            if not source.is_file():
                failures.append(f"Missing file: {source_text}")
                continue
            safe_name = source.name or f"file-{index}"
            target = stage_dir / f"{index:02d}_{safe_name}"
            try:
                shutil.copy2(source, target)
            except Exception as exc:
                failures.append(f"Could not copy '{source_text}': {exc}")
                continue
            try:
                rel = str(target.relative_to(project)).replace(os.sep, "/")
            except Exception:
                rel = target.as_posix()
            references.append(f"- [{safe_name}]({rel})")

        if references:
            self._attachment_stage_dir = stage_dir
        else:
            try:
                shutil.rmtree(stage_dir, ignore_errors=True)
            except Exception:
                pass
            self._attachment_stage_dir = None
        return references, failures

    @staticmethod
    def _canonical_path_text(path_text: str) -> str:
        return canonical_path_text(path_text)

    def _refresh_mention_file_cache(self) -> None:
        project = self._project_dir()
        if project is None:
            self._mention_file_cache = []
            self._mention_cache_project = ""
            self._mention_cache_at = 0.0
            return
        project_text = str(project)
        now = time.monotonic()
        if (
            self._mention_cache_project == project_text
            and self._mention_file_cache
            and (now - self._mention_cache_at) < _MENTION_CACHE_TTL_SECONDS
        ):
            return
        collected: list[str] = []
        try:
            for dirpath, dirnames, filenames in os.walk(project_text, topdown=True):
                pruned_dirs: list[str] = []
                for dirname in dirnames:
                    if str(dirname or "") in _MENTION_SKIP_DIRS:
                        continue
                    abs_dir = os.path.join(dirpath, dirname)
                    if self._is_tree_path_excluded(abs_dir, True):
                        continue
                    pruned_dirs.append(dirname)
                dirnames[:] = pruned_dirs

                for filename in filenames:
                    absolute = os.path.join(dirpath, filename)
                    if self._is_tree_path_excluded(absolute, False):
                        continue
                    if len(collected) >= _MENTION_MAX_FILES:
                        break
                    try:
                        relative = os.path.relpath(absolute, project_text)
                    except Exception:
                        continue
                    rel = str(relative or "").replace(os.sep, "/").strip()
                    if not rel or rel == ".":
                        continue
                    collected.append(rel)
                if len(collected) >= _MENTION_MAX_FILES:
                    break
        except Exception:
            collected = []
        self._mention_file_cache = sorted(set(collected), key=lambda part: part.casefold())
        self._mention_cache_project = project_text
        self._mention_cache_at = now

    def _mention_candidates(self, query: str, *, limit: int = 24) -> list[str]:
        self._refresh_mention_file_cache()
        files = self._mention_file_cache
        if not files:
            return []
        q = str(query or "").strip().replace("\\", "/").casefold()
        if not q:
            return files[: max(1, int(limit))]
        ranked: list[tuple[tuple[int, int, int, int, int, str], str]] = []
        for rel in files:
            rel_fold = rel.casefold()
            base_fold = Path(rel).name.casefold()
            rel_index = rel_fold.find(q)
            base_index = base_fold.find(q)
            if rel_index < 0 and base_index < 0:
                continue
            rank = (
                0 if rel_fold.startswith(q) else 1,
                0 if base_fold.startswith(q) else 1,
                base_index if base_index >= 0 else 9999,
                rel_index if rel_index >= 0 else 9999,
                len(rel),
                rel_fold,
            )
            ranked.append((rank, rel))
        ranked.sort(key=lambda item: item[0])
        return [rel for _rank, rel in ranked[: max(1, int(limit))]]

    def _mention_link_target(self, rel_path: str) -> str:
        raw = str(rel_path or "").strip().replace("\\", "/")
        if not raw:
            return ""
        candidate = Path(raw)
        if not candidate.is_absolute():
            project = self._project_dir()
            if project is None and self._session_project:
                try:
                    project = Path(self._session_project).expanduser().resolve(strict=False)
                except Exception:
                    project = None
            if project is not None:
                candidate = project / candidate
        try:
            absolute = candidate.expanduser().resolve(strict=False)
        except Exception:
            absolute = candidate

        try:
            home = Path.home().expanduser().resolve(strict=False)
            relative_home = absolute.relative_to(home)
            target = relative_home.as_posix()
            if target:
                return target
        except Exception:
            pass
        return absolute.as_posix()

    @staticmethod
    def _extract_message_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text_value = (
                str(item.get("text") or "")
                or str(item.get("input_text") or "")
                or str(item.get("output_text") or "")
            )
            value = text_value.strip()
            if value:
                parts.append(value)
        return "\n".join(parts).strip()

    @staticmethod
    def _extract_user_visible_text(text: str) -> str:
        source = str(text or "").strip()
        start_index = source.find(_PROMPT_USER_MESSAGE_START)
        if start_index >= 0:
            start_index += len(_PROMPT_USER_MESSAGE_START)
            end_index = source.find(_PROMPT_USER_MESSAGE_END, start_index)
            if end_index >= 0:
                return source[start_index:end_index].strip()
            return source[start_index:].strip()
        marker = "User message:\n"
        index = source.find(marker)
        if index >= 0:
            return source[index + len(marker):].strip()
        return source

    @staticmethod
    def _is_non_user_facing_user_text(text: str) -> bool:
        source = str(text or "").strip()
        if not source:
            return True
        if source.startswith("# AGENTS.md instructions"):
            return True
        if source.startswith("<environment_context>"):
            return True
        if source.startswith("<collaboration_mode>"):
            return True
        return False

    @staticmethod
    def _single_line_preview(text: str, max_chars: int = 74) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= max_chars:
            return compact
        return compact[: max(1, max_chars - 3)].rstrip() + "..."

    @staticmethod
    def _format_iso_timestamp(value: Any) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return stamp.astimezone().strftime("%H:%M:%S")
        except Exception:
            return None

    def _read_recent_session(self, log_path: Path) -> _RecentSession | None:
        return read_codex_session(log_path)

    def _recent_sessions(self, *, limit: int = 40, project_dir: Path | None = None) -> list[_RecentSession]:
        return list_codex_sessions(limit=limit, project_dir=project_dir)

    @staticmethod
    def _format_recent_session_label(item: _RecentSession) -> str:
        date_text = item.updated_at.strftime("%Y-%m-%d %H:%M")
        summary = session_preview_text(
            item.first_user_message or f"Session {item.session_id[:8]}...",
            max_chars=58,
        )
        return f"{summary} | {date_text}"

    def _refresh_recent_sessions_picker(self, *, select_session_id: str | None = None) -> None:
        project = self._project_dir()
        wanted = str(select_session_id or "").strip()
        sessions = self._recent_sessions(limit=80, project_dir=project)
        self._apply_recent_sessions_picker_results(sessions, select_session_id=wanted)

    def _apply_recent_sessions_picker_results(
        self,
        sessions: list[_RecentSession],
        *,
        select_session_id: str | None = None,
    ) -> None:
        self._recent_sessions_by_id = {item.session_id: item for item in sessions}
        self._updating_session_picker = True
        try:
            self.session_menu.clear()
            if not sessions:
                empty_action = self.session_menu.addAction("No recent sessions")
                empty_action.setEnabled(False)
            else:
                header_action = self.session_menu.addAction("Recent Sessions")
                header_action.setEnabled(False)
                self.session_menu.addSeparator()
                for item in sessions:
                    action = QAction(self._format_recent_session_label(item), self.session_menu)
                    action.setData(item.session_id)
                    action.triggered.connect(
                        lambda _checked=False, sid=item.session_id: self._on_session_menu_triggered(sid)
                    )
                    self.session_menu.addAction(action)
            self.session_menu.addSeparator()
            manage_action = self.session_menu.addAction("Manage Sessions...")
            manage_action.triggered.connect(self._open_manage_sessions_dialog)
        finally:
            self.session_picker.setEnabled(not self._runner.busy)
            self._refresh_session_ui()
            self._updating_session_picker = False
        wanted = str(select_session_id or "").strip()
        if wanted and not self._transcript_entries and wanted == str(self._session_id or "").strip():
            session = self._recent_sessions_by_id.get(wanted)
            if session is not None:
                self._restore_session_transcript(session)

    def _load_session_visible_messages(
        self, log_path: Path, *, max_messages: int = 220
    ) -> tuple[list[tuple[str, str, str | None]], bool]:
        messages: list[tuple[str, str, str | None]] = []
        try:
            with log_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    line = str(raw or "").strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    if str(data.get("type") or "").strip() != "response_item":
                        continue
                    payload = data.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    role, text = self._extract_visible_session_item(payload)
                    if not role or not text:
                        continue
                    stamp = self._format_iso_timestamp(data.get("timestamp"))
                    messages.append((role, text, stamp))
        except Exception:
            return [], False
        truncated = len(messages) > max_messages
        if truncated:
            messages = messages[-max_messages:]
        return messages, truncated

    @classmethod
    def _extract_visible_session_item(cls, payload: dict[str, Any]) -> tuple[str, str]:
        payload_type = str(payload.get("type") or "").strip()
        if payload_type == "message":
            role = str(payload.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                return "", ""
            text = cls._extract_message_text(payload.get("content"))
            if not text:
                return "", ""
            if role == "user":
                text = cls._extract_user_visible_text(text)
                if cls._is_non_user_facing_user_text(text):
                    return "", ""
            return role, text

        if payload_type == "reasoning":
            text = cls._extract_reasoning_text(payload)
            if not text:
                return "", ""
            return "thinking", text

        if payload_type in {"function_call", "custom_tool_call"}:
            role, text = cls._extract_tool_call_text(payload)
            if not role or not text:
                return "", ""
            return role, text

        if payload_type in {"function_call_output", "custom_tool_call_output"}:
            role, text = cls._extract_tool_output_text(payload)
            if not role or not text:
                return "", ""
            return role, text

        return "", ""

    @staticmethod
    def _extract_reasoning_text(payload: dict[str, Any]) -> str:
        summary = payload.get("summary")
        if isinstance(summary, list):
            lines: list[str] = []
            for item in summary:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if text:
                    lines.append(text)
            if lines:
                return "\n\n".join(lines).strip()
        return ""

    @classmethod
    def _extract_tool_call_text(cls, payload: dict[str, Any]) -> tuple[str, str]:
        name = str(payload.get("name") or payload.get("tool_name") or "tool").strip()
        status = str(payload.get("status") or "").strip()
        body = str(payload.get("arguments") or payload.get("input") or "").strip()
        header = name
        if status:
            header = f"{header} ({status})"
        text = f"{header}\n{body}".strip() if body else header
        role = cls._classify_tool_restore_role(name=name, text=text)
        return role, text

    @classmethod
    def _extract_tool_output_text(cls, payload: dict[str, Any]) -> tuple[str, str]:
        raw_output = payload.get("output")
        output_text = ""
        if isinstance(raw_output, (dict, list)):
            try:
                output_text = json.dumps(raw_output, ensure_ascii=True, indent=2)
            except Exception:
                output_text = str(raw_output)
        else:
            output_text = str(raw_output or "").strip()
            if output_text.startswith("{") and output_text.endswith("}"):
                try:
                    parsed = json.loads(output_text)
                    if isinstance(parsed, dict):
                        nested = str(parsed.get("output") or "").strip()
                        if nested:
                            output_text = nested
                except Exception:
                    pass

        call_id = str(payload.get("call_id") or "").strip()
        if call_id and output_text:
            text = f"{call_id}\n{output_text}".strip()
        elif call_id:
            text = call_id
        else:
            text = output_text
        if not text:
            return "", ""
        role = cls._classify_tool_restore_role(name="", text=text)
        return role, text

    @staticmethod
    def _classify_tool_restore_role(*, name: str, text: str) -> str:
        tool_name = str(name or "").strip().casefold()
        source = str(text or "")
        if tool_name == "apply_patch":
            return "diff"
        if source.startswith(("*** Begin Patch", "diff --git ")):
            return "diff"
        if "\ndiff --git " in source or "\n@@ " in source:
            return "diff"
        return "tools"

    def _restore_session_transcript(self, session: _RecentSession) -> None:
        bubbles, truncated = self._load_session_visible_messages(session.log_path)
        self._stream_mode = "assistant"
        self._stream_partial = ""
        self._pending_diff_lines.clear()
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines = []
        self._post_tokens_replay_index = 0
        self._clear_prompt_echo_suppression()
        self._latest_assistant_bubble_text = ""
        self._forced_bubble_role_boundary = None
        self._reset_bubble_debug_log()
        self._reset_plan_tracking(clear_panel=True)
        self._reset_transcript_tracking()
        self._clear_transcript()
        for role, text, stamp in bubbles:
            self._add_bubble(role, text, timestamp=stamp or _timestamp())
        if truncated:
            self._add_bubble(
                "system",
                "Showing the latest part of this restored session.",
                timestamp=_timestamp(),
            )
        self._schedule_transcript_render(scroll_to_bottom=True, immediate=True)

    def _attach_recent_session(
        self,
        session: _RecentSession,
        *,
        restore_visible: bool,
        announce: bool,
    ) -> None:
        self._reset_attachments_for_new_chat()
        self._session_id = session.session_id
        self._session_project = str(session.cwd or "")
        self._session_options_signature = self._active_options_signature()
        if restore_visible:
            self._restore_session_transcript(session)
        self._refresh_session_ui()
        self._refresh_recent_sessions_picker(select_session_id=session.session_id)
        self._update_rate_limits_label()
        self._schedule_persist_settings()
        if announce:
            self._add_bubble(
                "system",
                f"Attached session {session.session_id[:8]}... from recent history.",
                timestamp=_timestamp(),
            )

    def _on_session_menu_triggered(self, session_id: str) -> None:
        if self._updating_session_picker:
            return
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        session = self._recent_sessions_by_id.get(session_id)
        if session is None:
            return
        self._attach_recent_session(session, restore_visible=True, announce=True)

    def _open_manage_sessions_dialog(self) -> None:
        if self._runner.busy:
            return
        dialog = CodexSessionsDialog(
            project_dir=self._project_dir(),
            active_session_id=self._session_id,
            parent=self,
        )
        result = dialog.exec()
        self._refresh_recent_sessions_picker(select_session_id=self._session_id)
        self._clear_deleted_active_session()
        selected_session_id = str(dialog.selected_session_id or "").strip()
        if result and selected_session_id:
            session = find_codex_session(selected_session_id)
            if session is not None:
                self._attach_recent_session(session, restore_visible=True, announce=True)

    def _clear_deleted_active_session(self) -> None:
        session_id = str(self._session_id or "").strip()
        if not session_id:
            return
        if find_codex_session(session_id) is not None:
            return
        self._reset_attachments_for_new_chat()
        self._reset_transcript_tracking()
        self._reset_plan_tracking(clear_panel=True)
        self._session_id = None
        self._session_project = ""
        self._session_options_signature = None
        self._clear_transcript()
        self._refresh_recent_sessions_picker(select_session_id=None)
        self._refresh_session_ui()
        self._update_rate_limits_label()
        self._schedule_persist_settings()
        self._add_bubble(
            "system",
            "The attached Codex session was deleted. Started a new chat session.",
            timestamp=_timestamp(),
        )

    def _active_options_signature(self) -> tuple[str, str, str]:
        model = str(self.model_combo.currentData() or "").strip()
        reasoning = str(self.reasoning_combo.currentData() or "medium").strip().lower()
        permission_mode = str(self.permissions_combo.currentData() or "default").strip().lower()
        return model, reasoning, permission_mode

    def _on_option_changed(self, *_args: Any) -> None:
        if self._updating_options:
            return
        self._schedule_persist_settings()

    @staticmethod
    def _drop_flag_with_value(args: list[str], names: set[str]) -> list[str]:
        out: list[str] = []
        idx = 0
        while idx < len(args):
            token = args[idx]
            handled = False
            for name in names:
                if token == name:
                    idx += 1
                    if idx < len(args) and not str(args[idx]).startswith("-"):
                        idx += 1
                    handled = True
                    break
                if name.startswith("--") and token.startswith(name + "="):
                    idx += 1
                    handled = True
                    break
            if handled:
                continue
            out.append(token)
            idx += 1
        return out

    @staticmethod
    def _drop_flags(args: list[str], names: set[str]) -> list[str]:
        blocked = {str(name or "").strip() for name in names if str(name or "").strip()}
        return [token for token in args if str(token or "").strip() not in blocked]

    @staticmethod
    def _is_project_source_controlled(project: Path) -> bool:
        root = Path(project).expanduser()
        for marker in (".git", ".hg", ".svn"):
            try:
                if (root / marker).exists():
                    return True
            except Exception:
                continue
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
            )
        except Exception:
            return False
        if proc.returncode != 0:
            return False
        return str(proc.stdout or "").strip().lower() == "true"

    def _resolved_sandbox_mode(self) -> str:
        mode = str(self._sandbox_mode or "").strip().lower()
        if mode not in {"read-only", "workspace-write", "danger-full-access"}:
            return "workspace-write"
        return mode

    @staticmethod
    def _drop_reasoning_config(args: list[str]) -> list[str]:
        out: list[str] = []
        idx = 0
        while idx < len(args):
            token = str(args[idx] or "")
            if token in {"-c", "--config"}:
                next_idx = idx + 1
                if next_idx < len(args):
                    config_value = str(args[next_idx] or "").strip()
                    if config_value.split("=", 1)[0].strip() == "model_reasoning_effort":
                        idx += 2
                        continue
                out.append(token)
                idx += 1
                continue
            if token.startswith("--config="):
                config_value = token.split("=", 1)[1].strip()
                if config_value.split("=", 1)[0].strip() == "model_reasoning_effort":
                    idx += 1
                    continue
            out.append(token)
            idx += 1
        return out

    def _apply_agent_options(self, command: str, project: Path) -> tuple[str, bool]:
        try:
            args = shlex.split(command)
        except Exception:
            return command, False
        if len(args) < 2:
            return command, False
        if Path(str(args[0])).name.lower() != "codex" or args[1] != "exec":
            return command, False
        if args[2:3] and args[2] in {"resume", "review", "help"}:
            return command, False

        updated = list(args)
        updated = self._drop_flag_with_value(updated, {"-m", "--model"})
        updated = self._drop_flag_with_value(updated, {"-s", "--sandbox"})
        updated = self._drop_reasoning_config(updated)
        updated = [token for token in updated if token != "--dangerously-bypass-approvals-and-sandbox"]
        updated = self._drop_flags(updated, {"--skip-git-repo-check"})

        added_skip_git_repo_check = False

        model, reasoning, _ = self._active_options_signature()
        insert_at = 2
        if self._auto_skip_git_repo_check and not self._is_project_source_controlled(project):
            updated[insert_at:insert_at] = ["--skip-git-repo-check"]
            insert_at += 1
            added_skip_git_repo_check = True
        if model:
            updated[insert_at:insert_at] = ["--model", model]
            insert_at += 2
        updated[insert_at:insert_at] = ["--config", f'model_reasoning_effort="{reasoning}"']
        insert_at += 2
        updated[insert_at:insert_at] = ["--sandbox", self._resolved_sandbox_mode()]
        return shlex.join(updated), added_skip_git_repo_check

    def _normalize_command(self, command: str) -> str:
        try:
            args = shlex.split(command)
        except Exception:
            return command
        if not args:
            return DEFAULT_CODEX_COMMAND

        executable = Path(str(args[0])).name.lower()
        if executable != "codex":
            return command
        if args[1:2] == ["exec"]:
            if args[2:3] and args[2] in {"resume", "review", "help"}:
                return command
            normalized = list(args)
            index = 2
            while index < len(normalized):
                part = normalized[index]
                if part in {"--ask-for-approval", "-a"}:
                    del normalized[index]
                    if index < len(normalized) and not normalized[index].startswith("-"):
                        del normalized[index]
                    continue
                index += 1
            if "-" not in normalized:
                normalized.append("-")
            return shlex.join(normalized)

        subcommands = {
            "review",
            "login",
            "logout",
            "mcp",
            "mcp-server",
            "app-server",
            "completion",
            "sandbox",
            "debug",
            "apply",
            "resume",
            "fork",
            "cloud",
            "features",
            "help",
        }
        if any(arg in subcommands for arg in args[1:]):
            return command

        return shlex.join(
            [
                args[0],
                "exec",
                *args[1:],
                "-",
            ]
        )

    def _build_runtime_command(self, command: str) -> str:
        if not self._session_id:
            return command
        try:
            args = shlex.split(command)
        except Exception:
            return command
        if len(args) < 2:
            return command
        if Path(str(args[0])).name.lower() != "codex" or args[1] != "exec":
            return command
        if args[2:3] and args[2] in {"resume", "review", "help"}:
            return command
        base = list(args)
        if base and base[-1] == "-":
            base = base[:-1]
        resume_args = self._resume_supported_args(base[2:])
        permission_mode = str(self.permissions_combo.currentData() or "default").strip().lower()
        permission_flag = (
            "--dangerously-bypass-approvals-and-sandbox"
            if permission_mode == "full_access"
            else "--full-auto"
        )
        return shlex.join([base[0], "exec", "resume", permission_flag, *resume_args, self._session_id, "-"])

    @staticmethod
    def _resume_supported_args(args: list[str]) -> list[str]:
        out: list[str] = []
        idx = 0
        while idx < len(args):
            token = str(args[idx] or "")
            if token == "--skip-git-repo-check":
                out.append(token)
                idx += 1
                continue
            if token in {"--full-auto", "--dangerously-bypass-approvals-and-sandbox"}:
                idx += 1
                continue
            if token in {"--model", "-m", "--config", "-c"}:
                out.append(token)
                idx += 1
                if idx < len(args):
                    out.append(str(args[idx] or ""))
                    idx += 1
                continue
            if token.startswith("--model=") or token.startswith("--config="):
                out.append(token)
                idx += 1
                continue
            idx += 1
        return out

    def _compose_prompt(
        self,
        project: Path,
        user_text: str,
        attachment_references: list[str] | None = None,
    ) -> str:
        preamble = str(self.preamble_edit.to_codex_text() or "").strip()
        attachments = list(attachment_references or [])
        attachment_block = ""
        if attachments:
            attachment_block = (
                "Attached files (staged for this turn):\n"
                f"{chr(10).join(attachments)}\n\n"
            )
        if preamble:
            return (
                f"{preamble}\n\nProject path: {project}\n\n"
                f"{attachment_block}"
                f"User message:\n{_wrap_user_prompt_text(user_text)}\n"
            )
        return (
            f"Project path: {project}\n\n"
            f"{attachment_block}"
            f"User message:\n{_wrap_user_prompt_text(user_text)}\n"
        )

    @staticmethod
    def _compose_followup_prompt(
        user_text: str,
        attachment_references: list[str] | None = None,
    ) -> str:
        attachments = list(attachment_references or [])
        if attachments:
            attachment_block = "\n".join(attachments)
            return (
                "Attached files (staged for this turn):\n"
                f"{attachment_block}\n\n"
                f"User message:\n{_wrap_user_prompt_text(user_text)}\n"
            )
        return f"{_wrap_user_prompt_text(user_text)}\n"

    def _refresh_session_ui(self) -> None:
        if self._session_id:
            self.session_picker.setToolTip(self._session_id)
        else:
            self.session_picker.setToolTip("No active session")

    def _begin_post_tokens_replay_suppression(self) -> None:
        source = str(self._latest_assistant_bubble_text or "")
        expected = [line for line in source.splitlines() if str(line).strip()]
        self._post_tokens_replay_expected_lines = expected
        self._post_tokens_replay_index = 0
        self._suppress_post_tokens_echo = bool(expected)

    def _begin_prompt_echo_suppression(self, prompt_text: str) -> None:
        source = str(prompt_text or "")
        expected = [line.strip() for line in source.splitlines() if line.strip()]
        self._prompt_echo_expected_lines = expected
        self._prompt_echo_index = 0
        self._prompt_echo_source_text = source
        self._suppress_prompt_echo = bool(expected)

    def _clear_prompt_echo_suppression(self) -> None:
        self._suppress_prompt_echo = False
        self._prompt_echo_expected_lines = []
        self._prompt_echo_index = 0
        self._prompt_echo_source_text = ""
    def _advance_prompt_echo_index_to_line(self, target: str) -> bool:
        expected = self._prompt_echo_expected_lines
        index = max(0, int(self._prompt_echo_index))
        for offset in range(index, len(expected)):
            if str(expected[offset] or "").strip() == target:
                self._prompt_echo_index = offset + 1
                if self._prompt_echo_index >= len(expected):
                    self._clear_prompt_echo_suppression()
                return True
        return False

    def _mark_forced_bubble_boundary(self, role: str) -> None:
        self._forced_bubble_role_boundary = str(role or "").strip() or None

    def _consume_forced_bubble_boundary(self, role: str) -> bool:
        wanted = str(self._forced_bubble_role_boundary or "").strip()
        if not wanted:
            return False
        matches = wanted == str(role or "").strip()
        if matches:
            self._forced_bubble_role_boundary = None
        return matches

    def _sync_latest_assistant_bubble_text(self) -> None:
        for entry in reversed(self._transcript_entries):
            if str(entry.role or "").strip() == "assistant":
                self._latest_assistant_bubble_text = str(entry.text or "")
                return
        self._latest_assistant_bubble_text = ""

    @staticmethod
    def _is_replay_prune_role(role: str) -> bool:
        return str(role or "").strip() in {"assistant", "thinking", "tools", "diff"}

    @classmethod
    def _entries_match_for_replay(cls, left: _TranscriptEntry, right: _TranscriptEntry) -> bool:
        return (
            cls._is_replay_prune_role(left.role)
            and cls._is_replay_prune_role(right.role)
            and str(left.role or "").strip() == str(right.role or "").strip()
            and left.timestamp is None
            and right.timestamp is None
            and str(left.text or "") == str(right.text or "")
        )

    def _remove_transcript_tail_entries(self, count: int) -> None:
        remove_count = max(0, int(count))
        for _ in range(remove_count):
            if self._transcript_bubbles:
                bubble = self._transcript_bubbles.pop()
                self._transcript_layout.removeWidget(bubble)
                bubble.deleteLater()
            if self._transcript_entries:
                self._transcript_entries.pop()
        self._sync_latest_assistant_bubble_text()

    def _prune_replayed_transcript_tail(self) -> None:
        total = len(self._transcript_entries)
        max_tail = min(3, total // 2)
        for tail_size in range(max_tail, 0, -1):
            first_half = self._transcript_entries[total - (tail_size * 2) : total - tail_size]
            second_half = self._transcript_entries[total - tail_size :]
            if len(first_half) != tail_size or len(second_half) != tail_size:
                continue
            if all(
                self._entries_match_for_replay(left, right)
                for left, right in zip(first_half, second_half)
            ):
                self._remove_transcript_tail_entries(tail_size)
                return

    @staticmethod
    def _collapse_adjacent_repeated_suffix(text: str, *, max_lines: int = 80) -> str:
        lines = str(text or "").splitlines()
        total = len(lines)
        if total < 4:
            return str(text or "")
        max_tail = min(int(max_lines), total // 2)
        for tail_size in range(max_tail, 1, -1):
            first_half = lines[total - (tail_size * 2) : total - tail_size]
            second_half = lines[total - tail_size :]
            if first_half != second_half:
                continue
            if not any(str(line).strip() for line in second_half):
                continue
            return "\n".join(lines[: total - tail_size])
        return str(text or "")

    def _prune_repeated_suffix_in_last_entry(self) -> None:
        if not self._transcript_entries or not self._transcript_bubbles:
            return
        entry = self._transcript_entries[-1]
        if not self._is_replay_prune_role(entry.role):
            return
        collapsed = self._collapse_adjacent_repeated_suffix(entry.text)
        if collapsed == str(entry.text or ""):
            return
        entry.text = collapsed
        self._transcript_bubbles[-1].set_text(collapsed)
        self._sync_latest_assistant_bubble_text()

    @staticmethod
    def _paraphrase_overlap_score(source: str, candidate: str) -> float:
        source_tokens = set(_WORD_TOKEN_RE.findall(str(source or "").casefold()))
        candidate_tokens = set(_WORD_TOKEN_RE.findall(str(candidate or "").casefold()))
        if not source_tokens or not candidate_tokens:
            return 0.0
        intersection = len(source_tokens.intersection(candidate_tokens))
        return intersection / float(max(1, len(candidate_tokens)))

    def _capture_rate_limits_from_stream_line(self, text: str) -> None:
        stripped = _strip_ansi(str(text or "")).strip()
        if not stripped:
            return
        match = _STREAM_RATE_LIMITS_RE.search(stripped)
        if match is None:
            return
        five_hour = max(0, min(100, int(match.group(1))))
        weekly = max(0, min(100, int(match.group(2))))
        label = f"5h: {five_hour}% remaining | Weekly: {weekly}% remaining"
        self._apply_rate_limits_label(label, "Live rate limits from Codex output", remember=True)

    def _use_stream_transcript_fallback(self) -> bool:
        if not self._transcript_watch_active:
            return True
        if self._ensure_transcript_log_ready():
            return False
        return not bool(str(self._session_id or "").strip())

    def _consume_prompt_echo_line(self, line: str) -> bool:
        if not self._suppress_prompt_echo:
            return False
        incoming = str(line or "").strip()
        if not incoming:
            return True
        expected = self._prompt_echo_expected_lines
        index = int(self._prompt_echo_index)
        if index < 0 or index >= len(expected):
            self._clear_prompt_echo_suppression()
            return False
        wanted = str(expected[index] or "").strip()
        if incoming != wanted:
            return self._advance_prompt_echo_index_to_line(incoming)
        self._prompt_echo_index = index + 1
        if self._prompt_echo_index >= len(expected):
            self._clear_prompt_echo_suppression()
        return True

    def _consume_post_tokens_replay_line(self, line: str) -> bool:
        if not self._suppress_post_tokens_echo:
            return False
        expected = self._post_tokens_replay_expected_lines
        index = int(self._post_tokens_replay_index)
        if index < 0 or index >= len(expected):
            self._suppress_post_tokens_echo = False
            self._post_tokens_replay_expected_lines = []
            self._post_tokens_replay_index = 0
            return False
        incoming = str(line or "").strip()
        wanted = str(expected[index] or "").strip()
        if incoming != wanted:
            self._suppress_post_tokens_echo = False
            self._post_tokens_replay_expected_lines = []
            self._post_tokens_replay_index = 0
            return False
        self._post_tokens_replay_index = index + 1
        if self._post_tokens_replay_index >= len(expected):
            self._suppress_post_tokens_echo = False
            self._post_tokens_replay_expected_lines = []
            self._post_tokens_replay_index = 0
        return True

    def _add_bubble(
        self,
        role: str,
        text: str,
        *,
        timestamp: str | None = None,
        merge: bool = False,
    ) -> None:
        if role in {"meta", "system"}:
            return
        follow_output = self._transcript_follow_after_layout or self._is_transcript_at_bottom()
        line = str(text).rstrip("\n")
        last_entry = self._transcript_entries[-1] if self._transcript_entries else None
        force_boundary = self._consume_forced_bubble_boundary(role)
        can_merge = (
            merge
            and not force_boundary
            and timestamp is None
            and last_entry is not None
            and last_entry.role == role
            and last_entry.timestamp is None
        )
        if (
            not line.strip()
            and not can_merge
        ):
            return

        if can_merge and last_entry is not None:
            last_entry.text = self._append_transcript_line(last_entry.text, line)
            if role == "assistant":
                self._latest_assistant_bubble_text = str(last_entry.text or "")
            if self._transcript_bubbles:
                self._transcript_bubbles[-1].append_line(line)
                self._prune_repeated_suffix_in_last_entry()
            self._prune_replayed_transcript_tail()
        else:
            entry = _TranscriptEntry(
                role=role,
                timestamp=timestamp,
                text=self._append_transcript_line("", line),
            )
            self._transcript_entries.append(entry)
            bubble = self._new_transcript_bubble(entry)
            insert_at = max(0, self._transcript_layout.count() - 1)
            self._transcript_layout.insertWidget(insert_at, bubble)
            self._transcript_bubbles.append(bubble)
            if role == "assistant":
                self._latest_assistant_bubble_text = str(entry.text or "")
            self._prune_replayed_transcript_tail()
        self._rewrite_bubble_debug_log()
        self._schedule_transcript_render(
            scroll_to_bottom=follow_output,
            animated=follow_output,
        )

    def _on_bubble_link_activated(self, href: str) -> None:
        target = str(href or "").strip()
        if not target:
            return
        url = QUrl(target)
        local_path = ""
        if url.isLocalFile():
            local_path = str(url.toLocalFile() or "")
        elif target.startswith("/"):
            local_path = target
        elif not url.scheme():
            raw_target = target.split("#", 1)[0].split("?", 1)[0].strip()
            if raw_target:
                candidate = Path(raw_target).expanduser()
                if not candidate.is_absolute():
                    base_dir = self._project_dir()
                    if base_dir is None and self._session_project:
                        try:
                            session_dir = Path(self._session_project).expanduser().resolve(strict=False)
                            if session_dir.is_dir():
                                base_dir = session_dir
                        except Exception:
                            base_dir = None
                    if base_dir is not None:
                        candidate = base_dir / candidate
                try:
                    local_path = str(candidate.resolve(strict=False))
                except Exception:
                    local_path = str(candidate)
        if local_path and callable(self._file_opener):
            try:
                self._file_opener(local_path)
                return
            except Exception:
                pass
        if not url.isValid() or not url.scheme():
            if target.startswith("/"):
                url = QUrl.fromLocalFile(target)
            else:
                url = QUrl(target)
        QDesktopServices.openUrl(url)

    def _normalize_changed_path(self, raw_path: str) -> str:
        text = str(raw_path or "").strip().strip("`").strip('"').strip("'")
        if not text:
            return ""
        if text.startswith(("http://", "https://")):
            return ""
        text = text.replace("\\", "/")
        if text.startswith("a/") or text.startswith("b/"):
            text = text[2:]
        text = text.strip()
        if not text:
            return ""
        if " -> " in text:
            parts = [part.strip() for part in text.split(" -> ", 1)]
            text = parts[-1]
        candidate = Path(text)
        if not candidate.is_absolute():
            project = self._project_dir()
            if project is None and self._session_project:
                try:
                    project = Path(self._session_project).expanduser()
                except Exception:
                    project = None
            if project is not None:
                candidate = project / candidate
        try:
            normalized = str(candidate.expanduser().resolve(strict=False))
        except Exception:
            normalized = str(candidate)
        return normalized.strip()

    def _remember_changed_file(self, raw_path: str) -> None:
        normalized = self._normalize_changed_path(raw_path)
        if not normalized:
            return
        key = normalized.casefold()
        if key in self._turn_changed_file_set:
            return
        self._turn_changed_file_set.add(key)
        self._turn_changed_files.append(normalized)

    def _capture_changed_files_from_line(self, line: str) -> None:
        text = str(line or "")
        if not text:
            return
        diff_match = _DIFF_GIT_RE.match(text.strip())
        if diff_match is not None:
            self._remember_changed_file(str(diff_match.group(2) or ""))
        status_match = _STATUS_PATH_RE.match(text.strip())
        if status_match is not None:
            self._remember_changed_file(str(status_match.group(1) or ""))
        for match in _MARKDOWN_LINK_RE.finditer(text):
            target = str(match.group(2) or "").strip()
            self._remember_changed_file(target)

    @staticmethod
    def _is_diff_content_line(line: str) -> bool:
        raw = str(line or "")
        stripped = raw.strip()
        if CodexAgentDockWidget._is_status_diff_line(stripped):
            return True
        return raw.startswith(
            (
                "*** Begin Patch",
                "*** Update File: ",
                "*** Add File: ",
                "*** Delete File: ",
                "*** Move to: ",
                "*** End Patch",
                "diff --git ",
                "index ",
                "--- ",
                "+++ ",
                "@@",
                "new file mode ",
                "deleted file mode ",
                "old mode ",
                "new mode ",
                "rename from ",
                "rename to ",
                "similarity index ",
                "dissimilarity index ",
                "Binary files ",
                "GIT binary patch",
                "\\ No newline at end of file",
                "+",
                "-",
                " ",
            )
        )

    @staticmethod
    def _is_status_diff_line(text: str) -> bool:
        stripped = str(text or "").strip()
        match = _STATUS_PATH_RE.match(stripped)
        if match is None:
            return False
        status = stripped.split(None, 1)[0]
        if status not in {"M", "A", "D", "R", "C", "T", "U", "??", "AM", "MM", "UU"}:
            return False
        target = str(match.group(1) or "").strip()
        if not target:
            return False
        if target.startswith(("/", "./", "../", "~")):
            return True
        if "/" in target or "\\" in target:
            return True
        return bool(Path(target).suffix)

    @staticmethod
    def _is_diff_start_line(line: str) -> bool:
        stripped = str(line or "").strip()
        if stripped.startswith("file update"):
            return True
        if CodexAgentDockWidget._is_status_diff_line(stripped):
            return True
        return stripped.startswith(
            (
                "*** Begin Patch",
                "*** Update File: ",
                "*** Add File: ",
                "*** Delete File: ",
                "diff --git ",
                "@@",
            )
        )

    def _append_turn_changed_files_bubble(self) -> None:
        if not self._turn_changed_files:
            return
        project = self._project_dir()
        if project is None and self._session_project:
            try:
                project = Path(self._session_project).expanduser().resolve(strict=False)
            except Exception:
                project = None
        lines = ["Changed files:"]
        for path_text in self._turn_changed_files:
            display = path_text
            if project is not None:
                try:
                    display = str(Path(path_text).resolve(strict=False).relative_to(project))
                except Exception:
                    display = path_text
            lines.append(f"- [{display}]({path_text})")
        self._add_bubble("meta", "\n".join(lines))

    def _bubble_debug_log_path(self) -> Path:
        return _APP_ROOT / ".tide" / _BUBBLE_DEBUG_LOG_BASENAME

    def _rate_limits_debug_log_path(self) -> Path:
        return _APP_ROOT / ".tide" / _RATE_LIMITS_DEBUG_LOG_BASENAME

    def _reset_bubble_debug_log(self) -> None:
        log_path = self._bubble_debug_log_path()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8") as handle:
                handle.write("")
        except Exception:
            pass

    def _append_rate_limits_debug_log(self, message: str) -> None:
        log_path = self._rate_limits_debug_log_path()
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {str(message or '').strip()}\n")
        except Exception:
            pass

    def _rewrite_bubble_debug_log(self) -> None:
        log_path = self._bubble_debug_log_path()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8") as handle:
                visible_entries = [
                    entry
                    for entry in self._transcript_entries
                    if str(entry.role or "").strip() not in {"meta", "system"}
                ]
                if not visible_entries:
                    handle.write("(no visible bubbles)\n")
                    return
                for index, entry in enumerate(visible_entries, start=1):
                    role = str(entry.role or "").strip()
                    label = _ROLE_LABELS.get(role, role.title())
                    header = f"[bubble {index}] {label}"
                    if entry.timestamp:
                        header = f"{header} @ {entry.timestamp}"
                    handle.write(f"{header}:\n")
                    handle.write('"""\n')
                    handle.write(f"{entry.text}\n")
                    handle.write('"""\n\n')
        except Exception:
            pass

    def _new_chat(self) -> None:
        if self._runner.busy:
            QMessageBox.information(self, "Codex Busy", "Wait for the current request to finish.")
            return
        self._reset_attachments_for_new_chat()
        self._session_id = None
        self._session_project = ""
        self._session_options_signature = None
        self._non_git_warning_shown_for_chat = False
        self._stream_mode = "assistant"
        self._stream_partial = ""
        self._pending_diff_lines.clear()
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines = []
        self._post_tokens_replay_index = 0
        self._clear_prompt_echo_suppression()
        self._latest_assistant_bubble_text = ""
        self._forced_bubble_role_boundary = None
        self._turn_changed_files.clear()
        self._turn_changed_file_set.clear()
        self._reset_bubble_debug_log()
        self._reset_plan_tracking(clear_panel=True)
        self._reset_transcript_tracking()
        self._clear_transcript()
        self._refresh_recent_sessions_picker(select_session_id=None)
        self._refresh_session_ui()
        self._update_rate_limits_label()
        self._add_bubble("system", "Started a new chat session.", timestamp=_timestamp())
        self._schedule_persist_settings()

    def _send(self) -> None:
        if self._runner.busy:
            self.statusMessage.emit("Codex is busy. Wait for the current turn to finish before sending.")
            return
        self.input_edit.close_mention_popup()
        user_text = _normalize_newlines(str(self.input_edit.to_codex_text() or "")).strip()
        if not user_text:
            return
        project = self._project_dir()
        if project is None:
            QMessageBox.warning(self, "No Project", "Open a project before using Codex chat.")
            return

        command = str(self._command_template or "").strip()
        if not command:
            QMessageBox.warning(
                self,
                "Command Missing",
                "Set the Codex command in Settings -> Code Intelligence -> Code Agents.",
            )
            return

        current_project = str(project)
        if self._session_id and self._session_project and current_project != self._session_project:
            self._reset_attachments_for_new_chat()
            self._session_id = None
            self._session_project = ""
            self._session_options_signature = None
            self._non_git_warning_shown_for_chat = False
            self._reset_bubble_debug_log()
            self._reset_plan_tracking(clear_panel=True)
            self._reset_transcript_tracking()
            self._refresh_recent_sessions_picker(select_session_id=None)
            self._refresh_session_ui()
            self._update_rate_limits_label()
            self._add_bubble(
                "system",
                "Project changed. Started a new chat session.",
                timestamp=_timestamp(),
            )

        normalized = self._normalize_command(command)
        if normalized != command:
            command = normalized
            self._command_template = command
            self._schedule_persist_settings()

        option_signature = self._active_options_signature()
        if self._session_id and self._session_options_signature != option_signature:
            self._reset_attachments_for_new_chat()
            self._session_id = None
            self._session_project = ""
            self._session_options_signature = None
            self._non_git_warning_shown_for_chat = False
            self._reset_bubble_debug_log()
            self._reset_plan_tracking(clear_panel=True)
            self._reset_transcript_tracking()
            self._refresh_recent_sessions_picker(select_session_id=None)
            self._refresh_session_ui()
            self._update_rate_limits_label()
            self._add_bubble(
                "system",
                "Agent options changed. Started a new chat session.",
                timestamp=_timestamp(),
            )

        command, added_skip_git_repo_check = self._apply_agent_options(command, project)
        if (
            added_skip_git_repo_check
            and not self._session_id
            and not self._non_git_warning_shown_for_chat
        ):
            self._non_git_warning_shown_for_chat = True
            QMessageBox.warning(
                self,
                "Non-Git Project",
                "This project is not under source control, so --skip-git-repo-check was added for this Codex session.",
            )
        run_command = self._build_runtime_command(command)
        self._stream_mode = "assistant"
        self._stream_partial = ""
        self._pending_diff_lines.clear()
        self._turn_changed_files.clear()
        self._turn_changed_file_set.clear()
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines = []
        self._post_tokens_replay_index = 0
        self._clear_prompt_echo_suppression()
        self._forced_bubble_role_boundary = None
        attachment_refs, attachment_failures = self._stage_attachments_for_turn(project)
        if attachment_failures:
            self._add_bubble(
                "system",
                f"Skipped {len(attachment_failures)} attachment(s) that could not be staged.",
                timestamp=_timestamp(),
            )
        if self._attached_source_files and not attachment_refs:
            QMessageBox.warning(
                self,
                "Attachments Unavailable",
                "None of the selected attachments could be staged for this turn.",
            )
            return

        self._add_bubble("user", user_text, timestamp=_timestamp())
        if attachment_refs:
            self._add_bubble(
                "meta",
                "Attached files for this turn:\n" + "\n".join(attachment_refs),
            )
        prompt = (
            self._compose_prompt(project, user_text, attachment_refs)
            if not self._session_id
            else self._compose_followup_prompt(user_text, attachment_refs)
        )
        invocation = _CodexInvocation(
            project_dir=project,
            command_template=run_command,
            prompt_text=prompt,
        )
        self._begin_prompt_echo_suppression(prompt)
        self.input_edit.clear()
        self._begin_transcript_tracking_for_turn()
        self._begin_plan_tracking_for_turn()
        self._runner.start(invocation)
        self._clear_selected_attachments_after_send()
        self._schedule_persist_settings()

    @staticmethod
    def _classify_stream_line(text: str, current_mode: str) -> str:
        stripped = text.strip()
        if stripped in {"--------"}:
            return "meta"
        if stripped.startswith("OpenAI Codex") or stripped.startswith("workdir:"):
            return "meta"
        if stripped.startswith("model:") or stripped.startswith("provider:"):
            return "meta"
        if stripped.startswith("approval:") or stripped.startswith("sandbox:"):
            return "meta"
        if stripped.startswith("reasoning effort:") or stripped.startswith("reasoning summaries:"):
            return "meta"
        if stripped.startswith("session id:"):
            return "meta"
        if stripped.startswith("mcp startup:"):
            return "tools"
        if stripped.startswith("/bin/") or stripped.startswith("exec "):
            return "tools"
        if _TOOL_EXIT_LINE_RE.match(stripped):
            return "tools"
        if stripped.startswith("succeeded in ") or stripped.startswith("failed in "):
            return "tools"
        if stripped.startswith("tokens used"):
            return "meta"
        if current_mode == "meta_tokens":
            return "meta"
        if current_mode == "thinking":
            return "thinking"
        if current_mode == "tools":
            return "tools"
        return "assistant"

    @staticmethod
    def _is_noise_line(text: str) -> bool:
        stripped = text.strip()
        if stripped == "mcp startup: no servers":
            return True
        if "codex_core::state_db" in stripped and "WARN" in stripped:
            return True
        return False

    def _handle_stream_line(self, line: str) -> None:
        raw_line = str(line or "")
        stripped = raw_line.strip()
        self._capture_rate_limits_from_stream_line(stripped)
        if self._consume_prompt_echo_line(raw_line):
            return
        marker = stripped[:-1].strip() if stripped.endswith(":") else stripped
        self._capture_changed_files_from_line(raw_line)

        if self._stream_mode == "diff":
            if stripped.startswith("tokens used"):
                self._stream_mode = "meta_tokens"
                self._flush_pending_diff_bubble()
                self._add_bubble("meta", raw_line, merge=True)
                return
            if (
                _TOOL_EXIT_LINE_RE.match(stripped)
                or stripped.startswith("succeeded in ")
                or stripped.startswith("failed in ")
                or stripped.startswith("Success. Updated the following files:")
            ):
                self._stream_mode = "tools"
                self._flush_pending_diff_bubble()
            if self._is_diff_content_line(raw_line) or not stripped:
                self._buffer_diff_line(raw_line)
                return
            self._stream_mode = "assistant"
            self._flush_pending_diff_bubble()

        if self._is_noise_line(stripped):
            return
        if self._is_diff_start_line(raw_line):
            self._stream_mode = "diff"
            self._buffer_diff_line(raw_line)
            return
        if marker in {"user", "assistant", "system", "meta", "tools", "diff"}:
            if marker == "tools":
                self._clear_prompt_echo_suppression()
                self._stream_mode = "tools"
                self._mark_forced_bubble_boundary("tools")
            elif marker == "diff":
                self._clear_prompt_echo_suppression()
                self._stream_mode = "diff"
                self._mark_forced_bubble_boundary("diff")
            else:
                self._stream_mode = "assistant"
                if marker == "assistant":
                    self._mark_forced_bubble_boundary("assistant")
                elif marker == "user":
                    self._mark_forced_bubble_boundary("user")
            return
        if marker == "thinking":
            self._clear_prompt_echo_suppression()
            self._stream_mode = "thinking"
            self._mark_forced_bubble_boundary("thinking")
            return
        if marker == "codex":
            self._stream_mode = "assistant"
            self._mark_forced_bubble_boundary("assistant")
            return
        if marker == "exec":
            self._clear_prompt_echo_suppression()
            self._stream_mode = "tools"
            self._mark_forced_bubble_boundary("tools")
            return
        if stripped.startswith("tokens used"):
            self._stream_mode = "meta_tokens"
            self._begin_post_tokens_replay_suppression()
            self._add_bubble("meta", raw_line, merge=True)
            return
        if self._stream_mode == "meta_tokens":
            self._add_bubble("meta", raw_line, merge=True)
            self._stream_mode = "assistant"
            return
        role = self._classify_stream_line(stripped, self._stream_mode)
        if self._suppress_post_tokens_echo and role == "assistant":
            if self._consume_post_tokens_replay_line(raw_line):
                return
        elif role != "meta":
            self._suppress_post_tokens_echo = False
            self._post_tokens_replay_expected_lines = []
            self._post_tokens_replay_index = 0
        if role != "assistant" and role != "meta":
            self._clear_prompt_echo_suppression()
        self._add_bubble(role, raw_line, merge=True)

    def _append_raw(self, text: str) -> None:
        clean_text = _strip_ansi(str(text or ""))
        match = _SESSION_ID_RE.search(clean_text)
        if match is not None:
            session_id = str(match.group(1) or "").strip()
            if session_id and session_id != self._session_id:
                self._session_id = session_id
                project = self._project_dir()
                self._session_project = str(project) if project is not None else ""
                self._session_options_signature = self._active_options_signature()
                self._refresh_recent_sessions_picker(select_session_id=self._session_id)
                self._refresh_session_ui()
                self._update_rate_limits_label()
                self._add_bubble(
                    "system",
                    f"Attached session {session_id[:8]}...",
                    timestamp=_timestamp(),
                )
                if self._plan_watch_active:
                    self._plan_log_path = None
                    self._plan_log_position = 0
                    self._plan_start_from_beginning = True
                    self._poll_plan_updates()
                if self._transcript_watch_active:
                    self._transcript_log_path = None
                    self._transcript_log_position = 0
                    self._transcript_start_from_beginning = True
                    self._poll_transcript_updates()
                self._schedule_persist_settings()

        self._capture_rate_limits_from_stream_line(clean_text)
        self._poll_transcript_updates()
        self._stream_partial += str(text or "")
        while "\n" in self._stream_partial:
            line, self._stream_partial = self._stream_partial.split("\n", 1)
            if self._use_stream_transcript_fallback():
                self._handle_stream_line(line.rstrip("\r"))

    def _on_busy_changed(self, busy: bool) -> None:
        running = bool(busy)
        if running:
            self.input_edit.close_mention_popup()
        self.input_frame.set_shimmer_enabled(running)
        self.send_btn.setEnabled(not running)
        self.add_file_btn.setEnabled(not running)
        self.clear_attachments_btn.setEnabled(not running)
        self.model_combo.setEnabled(not running)
        self.reasoning_combo.setEnabled(not running)
        self.permissions_combo.setEnabled(not running)
        self.session_picker.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.new_chat_btn.setEnabled(not running)

    @staticmethod
    def _format_reset_time(unix_seconds: object) -> str:
        try:
            timestamp = int(float(unix_seconds))
            return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")
        except Exception:
            return "--"

    def _rate_limit_display_for_session(self, session_id: str) -> tuple[str, str]:
        normalized_id = str(session_id or "").strip()
        if not normalized_id:
            self._append_rate_limits_debug_log("task: empty session_id")
            return _RATE_LIMITS_UNAVAILABLE, "Rate limit data unavailable"

        sessions_dir = Path.home() / ".codex" / "sessions"
        if not sessions_dir.is_dir():
            self._append_rate_limits_debug_log("task: sessions_dir not found")
            return _RATE_LIMITS_UNAVAILABLE, "Codex sessions directory not found"

        candidates = list(sessions_dir.rglob(f"*{normalized_id}.jsonl"))
        if not candidates:
            self._append_rate_limits_debug_log(f"task: no candidates for {normalized_id}")
            return _RATE_LIMITS_UNAVAILABLE, "No rate limit data found for current session yet"

        try:
            log_path = max(candidates, key=lambda path: path.stat().st_mtime)
        except Exception:
            log_path = candidates[-1]
        self._append_rate_limits_debug_log(
            f"task: reading {log_path} (candidates={len(candidates)}) for {normalized_id}"
        )

        rate_limits: dict[str, Any] | None = None
        scanned_lines = 0
        try:
            with log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    scanned_lines += 1
                    line = str(raw_line or "").strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    payload = data.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if str(payload.get("type") or "").strip() != "token_count":
                        continue
                    candidate = payload.get("rate_limits")
                    if isinstance(candidate, dict):
                        rate_limits = candidate
        except Exception:
            rate_limits = None
            self._append_rate_limits_debug_log(
                f"task: read exception while scanning {log_path} (lines={scanned_lines})"
            )

        if not isinstance(rate_limits, dict):
            self._append_rate_limits_debug_log(
                f"task: no rate_limits found after scan (lines={scanned_lines}) for {normalized_id}"
            )
            return _RATE_LIMITS_UNAVAILABLE, "No rate limit data found for current session yet"

        def _remaining(bucket: Any) -> str:
            if not isinstance(bucket, dict):
                return "--"
            try:
                used = float(bucket.get("used_percent", 0.0))
            except Exception:
                return "--"
            remaining = max(0.0, min(100.0, 100.0 - used))
            return f"{remaining:.0f}%"

        primary = rate_limits.get("primary")
        secondary = rate_limits.get("secondary")
        display = f"5h: {_remaining(primary)} remaining | Weekly: {_remaining(secondary)} remaining"
        primary_reset = self._format_reset_time(primary.get("resets_at") if isinstance(primary, dict) else None)
        secondary_reset = self._format_reset_time(secondary.get("resets_at") if isinstance(secondary, dict) else None)
        tooltip = f"5h reset: {primary_reset}\nWeekly reset: {secondary_reset}"
        self._append_rate_limits_debug_log(
            f"task: parsed display='{display}' (lines={scanned_lines})"
        )
        return display, tooltip

    def _update_rate_limits_label(self) -> None:
        session_id = str(self._session_id or "").strip()
        if not session_id:
            self._apply_rate_limits_label(
                self._last_rate_limits_text,
                self._last_rate_limits_tooltip,
                remember=False,
            )
            self._append_rate_limits_debug_log("update: no active session id, set unavailable")
            return
        self._rate_limits_refresh_token += 1
        token = int(self._rate_limits_refresh_token)
        self.rate_limits_label.setText("5h: ... | Weekly: ...")
        self.rate_limits_label.setToolTip("Refreshing rate limit data...")
        started_at = time.monotonic()
        self._append_rate_limits_debug_log(
            f"update: start token={token} session_id={session_id}"
        )
        result: object
        try:
            result = self._rate_limit_display_for_session(session_id)
        except Exception:
            result = None
        elapsed_ms = int((time.monotonic() - started_at) * 1000.0)
        if token != self._rate_limits_refresh_token:
            self._append_rate_limits_debug_log(
                f"update: discard stale token={token} current={self._rate_limits_refresh_token} elapsed_ms={elapsed_ms}"
            )
            return
        if isinstance(result, tuple) and len(result) == 2:
            text = str(result[0] or _RATE_LIMITS_UNAVAILABLE)
            tooltip = str(result[1] or "Rate limit data unavailable")
            if text == _RATE_LIMITS_UNAVAILABLE and self._last_rate_limits_text != _RATE_LIMITS_UNAVAILABLE:
                text = self._last_rate_limits_text
                tooltip = self._last_rate_limits_tooltip
        else:
            text = self._last_rate_limits_text
            tooltip = self._last_rate_limits_tooltip
        self._apply_rate_limits_label(text, tooltip, remember=True)
        self._append_rate_limits_debug_log(
            f"update: applied token={token} elapsed_ms={elapsed_ms} text='{text}'"
        )

    def _on_exit_code(self, code: int) -> None:
        if self._stream_partial.strip() and self._use_stream_transcript_fallback():
            self._handle_stream_line(self._stream_partial.rstrip("\r"))
        self._stream_partial = ""
        if self._use_stream_transcript_fallback():
            self._flush_pending_diff_bubble()
        self._poll_transcript_updates()
        self._reset_transcript_tracking()
        self._poll_plan_updates()
        self._plan_poll_timer.stop()
        self._stream_mode = "assistant"
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines = []
        self._post_tokens_replay_index = 0
        self._clear_prompt_echo_suppression()
        self._forced_bubble_role_boundary = None
        self._update_rate_limits_label()
        self._append_turn_changed_files_bubble()
        if int(code) == 0:
            self._add_bubble("system", "Turn complete", timestamp=_timestamp())
        else:
            self._add_bubble(
                "system",
                f"Process exited with code {int(code)}",
                timestamp=_timestamp(),
            )
