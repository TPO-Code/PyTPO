from __future__ import annotations

import html
import json
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

from PySide6.QtCore import QObject, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
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
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from src.ui.widgets.spellcheck_inputs import SpellcheckTextEdit
from src.ui.dialogs.file_dialog_bridge import get_open_file_names

DEFAULT_CODEX_COMMAND = "codex exec --skip-git-repo-check --sandbox workspace-write -"
_SESSION_ID_RE = re.compile(r"session id:\s*([0-9a-fA-F-]{36})", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_STATUS_PATH_RE = re.compile(r"^[A-Z?]{1,2}\s+(.+)$")
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
_APP_ROOT = Path(__file__).resolve().parents[3]
_ROLE_LABELS = {
    "user": "You",
    "assistant": "Assistant",
    "thinking": "Thinking",
    "tools": "Tools",
    "diff": "Diff",
    "system": "System",
    "meta": "Meta",
}


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


@dataclass(slots=True)
class _CodexInvocation:
    project_dir: Path
    command_template: str
    prompt_text: str


@dataclass(slots=True)
class _RecentSession:
    session_id: str
    cwd: str
    model: str
    first_user_message: str
    updated_at: datetime
    log_path: Path


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
        command = self._invocation.command_template.format(
            project=str(self._invocation.project_dir)
        )
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAcceptRichText(False)
        self._link_target_provider = link_target_provider
        self._mention_provider = mention_provider
        self._is_internal_change = False
        self._last_cursor_pos = int(self.textCursor().position())
        self._mention_popup = QListWidget(self)
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
        fmt.setForeground(QColor("#8ab4f8"))
        fmt.setFontWeight(700)
        fmt.setProperty(self.LINK_RAW_PROPERTY, raw_link)
        fmt.setProperty(self.LINK_TARGET_PROPERTY, target)
        return fmt

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
        if key in {Qt.Key_Return, Qt.Key_Enter} and modifiers & Qt.ControlModifier:
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


class _BubbleWidget(QFrame):
    def __init__(
        self,
        role: str,
        text: str,
        timestamp: str | None = None,
        link_activated: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.role = role
        self.timestamp = timestamp
        self._link_activated = link_activated
        self._text = ""

        self.setObjectName("codexBubble")
        self.setProperty("role", role)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header_label = _ROLE_LABELS.get(role, role.title())
        header_text = f"{header_label}  {timestamp}" if timestamp else header_label
        self.header = QLabel(header_text)
        self.header.setObjectName("codexBubbleHeader")
        self.header.setStyleSheet("background: transparent;")
        layout.addWidget(self.header)

        self.body = QLabel("")
        self.body.setObjectName("codexBubbleBody")
        self.body.setStyleSheet("background: transparent;")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.body.setTextFormat(Qt.RichText)
        self.body.setOpenExternalLinks(False)
        self.body.linkActivated.connect(self._on_link_activated)
        layout.addWidget(self.body)

        self.append_line(text)

    def append_line(self, text: str) -> None:
        if self._text:
            self._text = f"{self._text}\n{text}"
        else:
            self._text = text
        self.body.setText(self._render_html())

    def _render_html(self) -> str:
        if self.role == "diff":
            return self._render_diff_html(self._text)
        return self._render_text_with_links(self._text)

    @staticmethod
    def _render_text_with_links(text: str) -> str:
        source = str(text or "")
        parts: list[str] = []
        cursor = 0
        for match in _MARKDOWN_LINK_RE.finditer(source):
            start, end = match.span()
            if start > cursor:
                parts.append(html.escape(source[cursor:start]))
            label = html.escape(str(match.group(1) or "link"))
            raw_target = str(match.group(2) or "").strip()
            if raw_target.startswith("/"):
                href = QUrl.fromLocalFile(raw_target).toString()
            else:
                href = raw_target
            parts.append(
                f'<a href="{html.escape(href, quote=True)}">{label}</a>'
            )
            cursor = end
        if cursor < len(source):
            parts.append(html.escape(source[cursor:]))
        content = "".join(parts).replace("\n", "<br>")
        return f'<div style="white-space: pre-wrap;">{content}</div>'

    @staticmethod
    def _render_diff_html(text: str) -> str:
        lines = str(text or "").splitlines()
        rendered: list[str] = []
        for raw in lines:
            escaped = html.escape(raw)
            style = "color: #e6edf3;"
            if raw.startswith("+") and not raw.startswith("+++"):
                style = "color: #b5cea8; background-color: #17361f;"
            elif raw.startswith("-") and not raw.startswith("---"):
                style = "color: #f2a7a7; background-color: #3c1820;"
            elif raw.startswith("@@"):
                style = "color: #d7ba7d;"
            elif raw.startswith("diff --git") or raw.startswith("index "):
                style = "color: #8ab4f8;"
            elif raw.startswith("--- ") or raw.startswith("+++ "):
                style = "color: #9cdcfe;"
            rendered.append(f'<span style="{style}">{escaped}</span>')
        body = "<br>".join(rendered)
        return (
            '<div style="white-space: pre; font-family: '
            '\'Cascadia Code\', \'Fira Code\', \'Consolas\', monospace;">'
            f"{body}</div>"
        )

    def _on_link_activated(self, href: str) -> None:
        handler = self._link_activated
        if callable(handler):
            handler(str(href or ""))


class CodexAgentDockWidget(QWidget):
    statusMessage = Signal(str)

    def __init__(
        self,
        *,
        project_dir_provider: Callable[[], str],
        settings_provider: Callable[[], dict[str, Any]],
        settings_saver: Callable[[dict[str, Any]], None],
        file_opener: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_dir_provider = project_dir_provider
        self._settings_provider = settings_provider
        self._settings_saver = settings_saver
        self._file_opener = file_opener

        self._runner = _CodexRunner()
        self._session_id: str | None = None
        self._session_project = ""
        self._session_options_signature: tuple[str, str, str] | None = None
        self._command_template = DEFAULT_CODEX_COMMAND
        self._stream_mode = "assistant"
        self._stream_partial = ""
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines: list[str] = []
        self._post_tokens_replay_index = 0
        self._latest_assistant_bubble_text = ""
        self._last_bubble: _BubbleWidget | None = None
        self._last_item: QListWidgetItem | None = None
        self._bubble_debug_entries: list[tuple[str, str]] = []
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

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._persist_settings)

        self._build_ui()
        self._wire_signals()
        self._load_settings()

        self.destroyed.connect(lambda *_args: self.shutdown())

    def shutdown(self) -> None:
        self._save_timer.stop()
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
        self.preamble_toggle_btn = QPushButton("Instructions (hidden)")
        self.preamble_toggle_btn.setCheckable(True)
        self.session_picker = QComboBox()
        self.session_picker.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.session_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.session_picker.setMinimumContentsLength(14)
        self.new_chat_btn = QPushButton("New Chat")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        top_row.addWidget(self.preamble_toggle_btn)
        top_row.addWidget(self.session_picker)
        top_row.addStretch(1)
        top_row.addWidget(self.new_chat_btn)
        top_row.addWidget(self.stop_btn)
        root.addLayout(top_row)

        self.preamble_container = QWidget()
        preamble_layout = QVBoxLayout(self.preamble_container)
        preamble_layout.setContentsMargins(0, 0, 0, 0)
        preamble_layout.setSpacing(4)
        preamble_layout.addWidget(QLabel("System preamble (first turn only)"))
        self.preamble_edit = QPlainTextEdit()
        self.preamble_edit.setPlaceholderText("Optional Codex instructions...")
        self.preamble_edit.setMaximumBlockCount(400)
        self.preamble_edit.setMinimumHeight(74)
        self.preamble_edit.setMaximumHeight(120)
        preamble_layout.addWidget(self.preamble_edit)
        root.addWidget(self.preamble_container)
        self._set_preamble_visible(False)

        root.addWidget(QLabel("Transcript"))
        self.chat_splitter = QSplitter(Qt.Vertical)
        self.chat_splitter.setChildrenCollapsible(False)
        self.chat_splitter.setHandleWidth(7)
        self.chat_splitter.setStyleSheet(
            """
            QSplitter::handle:vertical { background: #273140; }
            QSplitter::handle:vertical:hover { background: #324055; }
            """
        )

        self.transcript = QListWidget()
        self.transcript.setSelectionMode(QListWidget.NoSelection)
        self.transcript.setFocusPolicy(Qt.NoFocus)
        self.transcript.setSpacing(7)
        self.transcript.setAlternatingRowColors(False)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.transcript.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.transcript.verticalScrollBar().setSingleStep(18)
        self.transcript.setStyleSheet(
            """
            QListWidget { background: #0f131a; border: 1px solid #2c3440; border-radius: 10px; padding: 8px; }
            QListWidget::item { background: transparent; border: none; }
            QFrame#codexBubble { border-radius: 10px; border: 1px solid #2f3746; background: #1a1f2a; }
            QFrame#codexBubble[role="user"] { border-color: #2e6ad9; background: #16386f; }
            QFrame#codexBubble[role="assistant"] { border-color: #2f3f5e; background: #1a1f2a; }
            QFrame#codexBubble[role="thinking"] { border-color: #7d5ba6; background: #2a2234; }
            QFrame#codexBubble[role="tools"] { border-color: #2e5c47; background: #1d2a24; }
            QFrame#codexBubble[role="diff"] { border-color: #2c6a4f; background: #111a14; }
            QFrame#codexBubble[role="system"], QFrame#codexBubble[role="meta"] { border-color: #3f4b5f; background: #232b38; }
            QLabel#codexBubbleHeader { color: #96a3b8; font-size: 11px; font-weight: 600; background: transparent; }
            QLabel#codexBubbleBody { color: #e6edf3; font-size: 13px; background: transparent; }
            QLabel#codexBubbleBody a { color: #8ab4f8; text-decoration: none; }
            """
        )
        self.chat_splitter.addWidget(self.transcript)

        composer_container = QWidget()
        composer_layout = QVBoxLayout(composer_container)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setSpacing(6)

        self.input_edit = _ChatInputEdit(
            mention_provider=self._mention_candidates,
            link_target_provider=self._mention_link_target,
        )
        self.input_edit.setPlaceholderText("Ask Codex...")
        self.input_edit.setMinimumHeight(92)
        self.input_edit.setMaximumBlockCount(1200)
        composer_layout.addWidget(self.input_edit)

        self.attachments_container = QWidget()
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
        self.input_hint_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.options_toggle_btn = QPushButton("Agent Options (shown)")
        self.options_toggle_btn.setCheckable(True)
        self.options_toggle_btn.setChecked(True)
        self.add_file_btn = QPushButton("+")
        self.add_file_btn.setToolTip("Attach files")
        self.add_file_btn.setFixedWidth(26)
        self.send_btn = QPushButton("Send")
        input_row.addWidget(self.input_hint_label)
        input_row.addStretch(1)
        input_row.addWidget(self.options_toggle_btn)
        input_row.addWidget(self.add_file_btn)
        input_row.addWidget(self.send_btn)
        composer_layout.addLayout(input_row)

        self.options_container = QWidget()
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
        self.rate_limits_label = QLabel(_RATE_LIMITS_UNAVAILABLE)
        self.rate_limits_label.setObjectName("CodexRateLimits")
        self.rate_limits_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.rate_limits_label.setWordWrap(True)
        self.rate_limits_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        options_grid.addWidget(self.rate_limits_label, 4, 0, 1, 2)
        options_grid.setColumnStretch(0, 1)
        options_grid.setColumnStretch(1, 1)
        composer_layout.addWidget(self.options_container)
        self._set_options_visible(True)

        self.chat_splitter.addWidget(composer_container)
        self.chat_splitter.setStretchFactor(0, 6)
        self.chat_splitter.setStretchFactor(1, 2)
        self.chat_splitter.setSizes([560, 210])
        root.addWidget(self.chat_splitter, 1)
        self._refresh_attachment_summary()

    def _wire_signals(self) -> None:
        self.new_chat_btn.clicked.connect(self._new_chat)
        self.session_picker.activated.connect(self._on_session_picker_activated)
        self.stop_btn.clicked.connect(self._runner.stop)
        self.send_btn.clicked.connect(self._send)
        self.add_file_btn.clicked.connect(self._on_add_files_clicked)
        self.clear_attachments_btn.clicked.connect(self._clear_selected_attachments)
        self.input_edit.sendRequested.connect(self._send)
        self.preamble_toggle_btn.toggled.connect(self._on_preamble_toggled)
        self.options_toggle_btn.toggled.connect(self._on_options_toggled)

        self.preamble_edit.textChanged.connect(self._schedule_persist_settings)
        self.model_combo.currentIndexChanged.connect(self._on_option_changed)
        self.reasoning_combo.currentIndexChanged.connect(self._on_option_changed)
        self.permissions_combo.currentIndexChanged.connect(self._on_option_changed)

        self._runner.output.connect(self._append_raw)
        self._runner.busyChanged.connect(self._on_busy_changed)
        self._runner.exitCode.connect(self._on_exit_code)

    def _set_preamble_visible(self, visible: bool) -> None:
        shown = bool(visible)
        self.preamble_container.setVisible(shown)
        self.preamble_toggle_btn.setText(
            "Instructions (shown)" if shown else "Instructions (hidden)"
        )

    def _on_preamble_toggled(self, checked: bool) -> None:
        self._set_preamble_visible(bool(checked))
        if self._updating_options:
            return
        self._schedule_persist_settings()

    def _set_options_visible(self, visible: bool) -> None:
        shown = bool(visible)
        self.options_container.setVisible(shown)
        self.options_toggle_btn.setText(
            "Agent Options (shown)" if shown else "Agent Options (hidden)"
        )

    def _on_options_toggled(self, checked: bool) -> None:
        self._set_options_visible(bool(checked))
        if self._updating_options:
            return
        self._schedule_persist_settings()

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
            self.preamble_edit.setPlainText(str(data.get("system_preamble") or ""))
            show_preamble = bool(data.get("show_system_preamble", False))
            self.preamble_toggle_btn.blockSignals(True)
            self.preamble_toggle_btn.setChecked(show_preamble)
            self.preamble_toggle_btn.blockSignals(False)
            self._set_preamble_visible(show_preamble)

            show_options = bool(data.get("show_agent_options", True))
            self.options_toggle_btn.blockSignals(True)
            self.options_toggle_btn.setChecked(show_options)
            self.options_toggle_btn.blockSignals(False)
            self._set_options_visible(show_options)

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
            self._session_options_signature = (
                self._active_options_signature() if self._session_id else None
            )
            self._refresh_recent_sessions_picker(select_session_id=self._session_id)
            if self._session_id and self.transcript.count() == 0:
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
            "system_preamble": str(self.preamble_edit.toPlainText() or ""),
            "show_system_preamble": bool(self.preamble_toggle_btn.isChecked()),
            "show_agent_options": bool(self.options_toggle_btn.isChecked()),
            "model": model,
            "model_reasoning_effort": reasoning,
            "permission_mode": permission_mode,
            "session_id": str(self._session_id or ""),
            "session_project_dir": str(self._session_project or ""),
        }
        try:
            self._settings_saver(payload)
        except Exception:
            pass

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
        text = str(path_text or "").strip()
        if not text:
            return ""
        try:
            canonical = str(Path(text).expanduser().resolve(strict=False))
        except Exception:
            canonical = text
        return canonical.casefold()

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
                    pruned_dirs.append(dirname)
                dirnames[:] = pruned_dirs

                for filename in filenames:
                    if len(collected) >= _MENTION_MAX_FILES:
                        break
                    absolute = os.path.join(dirpath, filename)
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
        session_id = ""
        cwd = ""
        model = ""
        first_user_message = ""
        fallback_user_message = ""
        try:
            updated_at = datetime.fromtimestamp(log_path.stat().st_mtime)
        except Exception:
            updated_at = datetime.now()
        try:
            with log_path.open("r", encoding="utf-8") as handle:
                for index, raw in enumerate(handle):
                    line = str(raw or "").strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    payload = data.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    message_type = str(data.get("type") or "").strip()
                    if message_type == "session_meta":
                        session_id = str(payload.get("id") or "").strip() or session_id
                        cwd = str(payload.get("cwd") or "").strip() or cwd
                    elif message_type == "turn_context":
                        model = str(payload.get("model") or "").strip() or model
                        cwd = str(payload.get("cwd") or "").strip() or cwd
                    elif message_type == "response_item":
                        if str(payload.get("type") or "").strip() != "message":
                            continue
                        if str(payload.get("role") or "").strip() != "user":
                            continue
                        raw_text = self._extract_message_text(payload.get("content"))
                        text = self._extract_user_visible_text(raw_text)
                        if text and not fallback_user_message:
                            fallback_user_message = text
                        if text and not self._is_non_user_facing_user_text(text):
                            first_user_message = text
                    if session_id and cwd and model and first_user_message:
                        break
                    if index >= 500:
                        break
        except Exception:
            return None
        if not session_id:
            return None
        friendly_message = first_user_message or fallback_user_message
        return _RecentSession(
            session_id=session_id,
            cwd=cwd,
            model=model,
            first_user_message=friendly_message,
            updated_at=updated_at,
            log_path=log_path,
        )

    def _recent_sessions(self, *, limit: int = 40, project_dir: Path | None = None) -> list[_RecentSession]:
        sessions_dir = Path.home() / ".codex" / "sessions"
        if not sessions_dir.is_dir():
            return []
        project_key = self._canonical_path_text(str(project_dir or ""))
        try:
            candidates = sorted(
                sessions_dir.rglob("*.jsonl"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            return []
        found: list[_RecentSession] = []
        seen_ids: set[str] = set()
        for log_path in candidates:
            session = self._read_recent_session(log_path)
            if session is None:
                continue
            session_key = session.session_id.casefold()
            if session_key in seen_ids:
                continue
            if project_key:
                if self._canonical_path_text(session.cwd) != project_key:
                    continue
            seen_ids.add(session_key)
            found.append(session)
            if len(found) >= max(1, int(limit)):
                break
        return found

    @staticmethod
    def _format_recent_session_label(item: _RecentSession) -> str:
        date_text = item.updated_at.strftime("%Y-%m-%d %H:%M")
        summary = CodexAgentDockWidget._single_line_preview(
            item.first_user_message or f"Session {item.session_id[:8]}...",
            max_chars=58,
        )
        return f"{summary} | {date_text}"

    def _refresh_recent_sessions_picker(self, *, select_session_id: str | None = None) -> None:
        project = self._project_dir()
        sessions = self._recent_sessions(limit=80, project_dir=project)
        self._recent_sessions_by_id = {item.session_id: item for item in sessions}
        self._updating_session_picker = True
        try:
            self.session_picker.blockSignals(True)
            self.session_picker.clear()
            self.session_picker.addItem("Recent sessions", "")
            for item in sessions:
                self.session_picker.addItem(
                    self._format_recent_session_label(item),
                    item.session_id,
                )
            wanted = str(select_session_id or "").strip()
            if wanted:
                index = self.session_picker.findData(wanted)
                if index < 0:
                    index = 0
                self.session_picker.setCurrentIndex(index)
            else:
                self.session_picker.setCurrentIndex(0)
        finally:
            self.session_picker.blockSignals(False)
            self._updating_session_picker = False

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
                    if str(payload.get("type") or "").strip() != "message":
                        continue
                    role = str(payload.get("role") or "").strip()
                    if role not in {"user", "assistant"}:
                        continue
                    text = self._extract_message_text(payload.get("content"))
                    if not text:
                        continue
                    if role == "user":
                        text = self._extract_user_visible_text(text)
                        if self._is_non_user_facing_user_text(text):
                            continue
                    stamp = self._format_iso_timestamp(data.get("timestamp"))
                    messages.append((role, text, stamp))
        except Exception:
            return [], False
        truncated = len(messages) > max_messages
        if truncated:
            messages = messages[-max_messages:]
        return messages, truncated

    def _restore_session_transcript(self, session: _RecentSession) -> None:
        bubbles, truncated = self._load_session_visible_messages(session.log_path)
        self._stream_mode = "assistant"
        self._stream_partial = ""
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines = []
        self._post_tokens_replay_index = 0
        self._latest_assistant_bubble_text = ""
        self._last_bubble = None
        self._last_item = None
        self._bubble_debug_entries.clear()
        self._write_bubble_debug_log()
        self.transcript.clear()
        for role, text, stamp in bubbles:
            self._add_bubble(role, text, timestamp=stamp or _timestamp())
        if truncated:
            self._add_bubble(
                "system",
                "Showing the latest part of this restored session.",
                timestamp=_timestamp(),
            )

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

    def _on_session_picker_activated(self, index: int) -> None:
        if self._updating_session_picker:
            return
        if int(index) <= 0:
            return
        session_id = str(self.session_picker.itemData(index) or "").strip()
        if not session_id:
            return
        session = self._recent_sessions_by_id.get(session_id)
        if session is None:
            return
        self._attach_recent_session(session, restore_visible=True, announce=True)

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

    def _apply_agent_options(self, command: str) -> str:
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

        updated = list(args)
        updated = self._drop_flag_with_value(updated, {"-m", "--model"})
        updated = self._drop_flag_with_value(updated, {"-s", "--sandbox"})
        updated = self._drop_reasoning_config(updated)
        updated = [token for token in updated if token != "--dangerously-bypass-approvals-and-sandbox"]

        model, reasoning, permission_mode = self._active_options_signature()
        insert_at = 2
        if model:
            updated[insert_at:insert_at] = ["--model", model]
            insert_at += 2
        updated[insert_at:insert_at] = ["--config", f'model_reasoning_effort="{reasoning}"']
        insert_at += 2
        sandbox_mode = "danger-full-access" if permission_mode == "full_access" else "workspace-write"
        updated[insert_at:insert_at] = ["--sandbox", sandbox_mode]
        return shlex.join(updated)

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
            insert_at = 2
            if "--skip-git-repo-check" not in normalized:
                normalized[insert_at:insert_at] = ["--skip-git-repo-check"]
                insert_at += 1
            if "--sandbox" not in normalized:
                normalized[insert_at:insert_at] = ["--sandbox", "workspace-write"]
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
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
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
        resume_args = self._drop_flag_with_value(list(base[2:]), {"-s", "--sandbox"})
        resume_args = [part for part in resume_args if part not in {"--full-auto", "--dangerously-bypass-approvals-and-sandbox"}]
        permission_mode = str(self.permissions_combo.currentData() or "default").strip().lower()
        permission_flag = (
            "--dangerously-bypass-approvals-and-sandbox"
            if permission_mode == "full_access"
            else "--full-auto"
        )
        return shlex.join([base[0], "exec", "resume", permission_flag, *resume_args, self._session_id, "-"])

    def _compose_prompt(
        self,
        project: Path,
        user_text: str,
        attachment_references: list[str] | None = None,
    ) -> str:
        preamble = str(self.preamble_edit.toPlainText() or "").strip()
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
                f"User message:\n{user_text}\n"
            )
        return (
            f"Project path: {project}\n\n"
            f"{attachment_block}"
            f"User message:\n{user_text}\n"
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
                f"User message:\n{user_text}\n"
            )
        return f"{user_text}\n"

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
        line = str(text).rstrip("\n")
        if not line.strip():
            return
        if (
            merge
            and timestamp is None
            and self._last_bubble is not None
            and self._last_item is not None
            and self._last_bubble.role == role
            and self._last_bubble.timestamp is None
        ):
            self._last_bubble.append_line(line)
            self._last_item.setSizeHint(self._last_bubble.sizeHint())
            if role == "assistant":
                self._latest_assistant_bubble_text = str(self._last_bubble._text or "")
            if self._bubble_debug_entries:
                self._bubble_debug_entries[-1] = (
                    str(self._last_bubble.role),
                    str(self._last_bubble._text or line),
                )
                self._write_bubble_debug_log()
        else:
            bubble = _BubbleWidget(
                role=role,
                text=line,
                timestamp=timestamp,
                link_activated=self._on_bubble_link_activated,
            )
            item = QListWidgetItem()
            item.setFlags(Qt.NoItemFlags)
            item.setSizeHint(bubble.sizeHint())
            self.transcript.addItem(item)
            self.transcript.setItemWidget(item, bubble)
            self._last_bubble = bubble
            self._last_item = item
            if role == "assistant":
                self._latest_assistant_bubble_text = str(bubble._text or "")
            self._bubble_debug_entries.append(
                (str(bubble.role), str(bubble._text or line))
            )
            self._write_bubble_debug_log()
        self.transcript.scrollToBottom()

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
        return raw.startswith(
            (
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

    def _write_bubble_debug_log(self) -> None:
        log_path = self._bubble_debug_log_path()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8") as handle:
                for role, text in self._bubble_debug_entries:
                    handle.write(f"{role}:\n")
                    handle.write('"""\n')
                    handle.write(f"{text}\n")
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
        self._stream_mode = "assistant"
        self._stream_partial = ""
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines = []
        self._post_tokens_replay_index = 0
        self._latest_assistant_bubble_text = ""
        self._last_bubble = None
        self._last_item = None
        self._turn_changed_files.clear()
        self._turn_changed_file_set.clear()
        self._bubble_debug_entries.clear()
        self._write_bubble_debug_log()
        self.transcript.clear()
        self._refresh_recent_sessions_picker(select_session_id=None)
        self._refresh_session_ui()
        self._update_rate_limits_label()
        self._add_bubble("system", "Started a new chat session.", timestamp=_timestamp())
        self._schedule_persist_settings()

    def _send(self) -> None:
        self.input_edit.close_mention_popup()
        user_text = str(self.input_edit.to_codex_text() or "").strip()
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
            self._bubble_debug_entries.clear()
            self._write_bubble_debug_log()
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
            self._bubble_debug_entries.clear()
            self._write_bubble_debug_log()
            self._refresh_recent_sessions_picker(select_session_id=None)
            self._refresh_session_ui()
            self._update_rate_limits_label()
            self._add_bubble(
                "system",
                "Agent options changed. Started a new chat session.",
                timestamp=_timestamp(),
            )

        command = self._apply_agent_options(command)
        run_command = self._build_runtime_command(command)
        self._stream_mode = "assistant"
        self._stream_partial = ""
        self._turn_changed_files.clear()
        self._turn_changed_file_set.clear()
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines = []
        self._post_tokens_replay_index = 0
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
        self.input_edit.clear()
        self._runner.start(invocation)
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
        if not stripped:
            return True
        if stripped == "mcp startup: no servers":
            return True
        if "codex_core::state_db" in stripped and "WARN" in stripped:
            return True
        return False

    def _handle_stream_line(self, line: str) -> None:
        raw_line = str(line or "")
        stripped = raw_line.strip()
        self._capture_changed_files_from_line(raw_line)

        if self._stream_mode == "diff":
            if stripped.startswith("tokens used"):
                self._stream_mode = "meta_tokens"
                self._add_bubble("meta", raw_line, merge=True)
                return
            if self._is_diff_content_line(raw_line) or not stripped:
                self._add_bubble("diff", raw_line, merge=True)
                return
            self._stream_mode = "assistant"

        if self._is_noise_line(stripped):
            return
        if stripped.startswith("file update"):
            self._stream_mode = "diff"
            return
        if stripped.startswith("diff --git "):
            self._stream_mode = "diff"
            self._add_bubble("diff", raw_line, merge=True)
            return
        if stripped in {"user", "assistant", "system", "meta", "tools", "diff"}:
            if stripped == "tools":
                self._stream_mode = "tools"
            elif stripped == "diff":
                self._stream_mode = "diff"
            else:
                self._stream_mode = "assistant"
            return
        if stripped == "thinking":
            self._stream_mode = "thinking"
            return
        if stripped == "codex":
            self._stream_mode = "assistant"
            return
        if stripped == "exec":
            self._stream_mode = "tools"
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
        self._add_bubble(role, raw_line, merge=True)

    def _append_raw(self, text: str) -> None:
        match = _SESSION_ID_RE.search(str(text or ""))
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
                self._schedule_persist_settings()

        self._stream_partial += str(text or "")
        while "\n" in self._stream_partial:
            line, self._stream_partial = self._stream_partial.split("\n", 1)
            self._handle_stream_line(line.rstrip("\r"))

    def _on_busy_changed(self, busy: bool) -> None:
        running = bool(busy)
        if running:
            self.input_edit.close_mention_popup()
        self.send_btn.setEnabled(not running)
        self.add_file_btn.setEnabled(not running)
        self.clear_attachments_btn.setEnabled(not running)
        self.input_edit.setEnabled(not running)
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
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "--"

    def _update_rate_limits_label(self) -> None:
        session_id = str(self._session_id or "").strip()
        if not session_id:
            self.rate_limits_label.setText(_RATE_LIMITS_UNAVAILABLE)
            self.rate_limits_label.setToolTip("Rate limit data unavailable")
            return

        sessions_dir = Path.home() / ".codex" / "sessions"
        if not sessions_dir.is_dir():
            self.rate_limits_label.setText(_RATE_LIMITS_UNAVAILABLE)
            self.rate_limits_label.setToolTip("Codex sessions directory not found")
            return

        candidates = list(sessions_dir.rglob(f"*{session_id}.jsonl"))
        if not candidates:
            self.rate_limits_label.setText(_RATE_LIMITS_UNAVAILABLE)
            self.rate_limits_label.setToolTip("No rate limit data found for current session yet")
            return

        try:
            log_path = max(candidates, key=lambda path: path.stat().st_mtime)
        except Exception:
            log_path = candidates[-1]

        rate_limits: dict[str, Any] | None = None
        try:
            with log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
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

        if not isinstance(rate_limits, dict):
            self.rate_limits_label.setText(_RATE_LIMITS_UNAVAILABLE)
            self.rate_limits_label.setToolTip("No rate limit data found for current session yet")
            return

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
        self.rate_limits_label.setText(
            f"5h: {_remaining(primary)} remaining | Weekly: {_remaining(secondary)} remaining"
        )
        primary_reset = self._format_reset_time(primary.get("resets_at") if isinstance(primary, dict) else None)
        secondary_reset = self._format_reset_time(secondary.get("resets_at") if isinstance(secondary, dict) else None)
        self.rate_limits_label.setToolTip(
            f"5h reset: {primary_reset}\nWeekly reset: {secondary_reset}"
        )

    def _on_exit_code(self, code: int) -> None:
        if self._stream_partial.strip():
            self._handle_stream_line(self._stream_partial.rstrip("\r"))
        self._stream_partial = ""
        self._stream_mode = "assistant"
        self._suppress_post_tokens_echo = False
        self._post_tokens_replay_expected_lines = []
        self._post_tokens_replay_index = 0
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
