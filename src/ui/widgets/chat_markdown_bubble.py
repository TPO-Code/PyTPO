from __future__ import annotations

import html
from typing import Callable

import markdown
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
)


class _MarkdownBubbleBody(QTextEdit):
    linkActivated = Signal(str)
    heightChanged = Signal()

    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("codexBubbleBody")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setFrameStyle(QFrame.NoFrame)
        self.setLineWidth(0)
        self.setMidLineWidth(0)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setTextInteractionFlags(
            Qt.TextSelectableByMouse
            | Qt.TextSelectableByKeyboard
            | Qt.LinksAccessibleByMouse
        )
        self.document().setDocumentMargin(0.0)
        self.document().contentsChanged.connect(self._sync_height)
        self._max_content_height: int | None = None

    def set_scroll_limit(self, max_height: int | None) -> None:
        limit = int(max_height) if max_height is not None else None
        self._max_content_height = limit if limit is None or limit > 0 else None
        self._sync_height()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            href = str(self.anchorAt(event.position().toPoint()) or "").strip()
            if href:
                self.linkActivated.emit(href)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _sync_height(self) -> None:
        doc = self.document()
        width = max(120.0, float(self.viewport().width()))
        doc.setTextWidth(width)
        content_height = doc.size().height()
        margins = self.contentsMargins()
        frame = float(self.frameWidth()) * 2.0
        total = (
            float(max(1, int(round(content_height))))
            + float(margins.top() + margins.bottom())
            + frame
            + 2.0
        )
        target = max(22, int(round(total)))
        if self._max_content_height is not None:
            target = min(target, int(self._max_content_height))
        if target != int(self.height()):
            self.setFixedHeight(target)
            self.heightChanged.emit()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_height()


class _BubblePreviewLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ChatMarkdownBubble(QFrame):
    sizeHintChanged = Signal()
    _DIFF_SCROLL_THRESHOLD_LINES = 20
    _DIFF_MAX_BODY_HEIGHT = 280

    def __init__(
        self,
        role: str,
        text: str,
        timestamp: str | None = None,
        link_activated: Callable[[str], None] | None = None,
        role_label: str | None = None,
    ) -> None:
        super().__init__()
        self.role = role
        self.timestamp = timestamp
        self._text = ""
        self._link_activated = link_activated
        self._collapsible = role in {"tools", "diff"}
        self._collapsed = self._collapsible
        self._show_header = self._collapsible or bool(timestamp) or role != "assistant"

        self.setObjectName("codexBubble")
        self.setProperty("role", role)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 6)
        layout.setSpacing(2)

        header_label = str(role_label or role.title())
        header_text = f"{header_label}  {timestamp}" if timestamp else header_label
        header_row: QHBoxLayout | None = None
        self.header: QLabel | None = None
        if self._show_header:
            header_row = QHBoxLayout()
            header_row.setContentsMargins(0, 0, 2, 0)
            header_row.setSpacing(4)
            self.header = QLabel(header_text)
            self.header.setObjectName("codexBubbleHeader")
            self.header.setStyleSheet("background-color: transparent;")
            header_row.addWidget(self.header, 1)

        self.toggle_btn: QPushButton | None = None
        if self._collapsible:
            self.toggle_btn = QPushButton("")
            self.toggle_btn.setObjectName("codexBubbleToggle")
            self.toggle_btn.setCursor(Qt.PointingHandCursor)
            self.toggle_btn.setFlat(True)
            self.toggle_btn.setMinimumHeight(20)
            self.toggle_btn.setMinimumWidth(68)
            self.toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.toggle_btn.clicked.connect(self._toggle_collapsed)
            if header_row is not None:
                header_row.addWidget(self.toggle_btn, 0)
        if header_row is not None:
            layout.addLayout(header_row)

        self.preview = _BubblePreviewLabel("")
        self.preview.setObjectName("codexBubblePreview")
        self.preview.setStyleSheet("background-color: transparent;")
        self.preview.setWordWrap(False)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview.setTextFormat(Qt.RichText if role == "diff" else Qt.AutoText)
        self.preview.setCursor(
            Qt.PointingHandCursor if self._collapsible else Qt.IBeamCursor
        )
        self.preview.clicked.connect(self._toggle_collapsed)
        self.preview.setVisible(False)
        layout.addWidget(self.preview)

        self.body = _MarkdownBubbleBody(self)
        self.body.heightChanged.connect(self._notify_size_hint_changed)
        self.body.linkActivated.connect(self._on_link_activated)
        layout.addWidget(self.body)

        self.append_line(text)
        self._apply_collapsed_state()

    def append_line(self, text: str) -> None:
        line = str(text or "")
        if not self._text:
            line = line.lstrip("\r\n")
        if self._text:
            self._text = f"{self._text}\n{line}"
        else:
            self._text = line
        self._refresh_rendered_text()

    def set_text(self, text: str) -> None:
        self._text = str(text or "").lstrip("\r\n")
        self._refresh_rendered_text()

    def _refresh_rendered_text(self) -> None:
        if self._collapsed:
            self.preview.setText(self._collapsed_preview_text())
            self._notify_size_hint_changed()
            return
        self._apply_body_scroll_behavior()
        self.body.setHtml(self._render_html())
        self._notify_size_hint_changed()

    def _toggle_collapsed(self) -> None:
        if not self._collapsible:
            return
        self._collapsed = not self._collapsed
        self._apply_collapsed_state()

    def _apply_collapsed_state(self) -> None:
        collapsed = bool(self._collapsed and self._collapsible)
        self.body.setVisible(not collapsed)
        self.preview.setVisible(collapsed)
        if collapsed:
            self.preview.setText(self._collapsed_preview_text())
        else:
            self._apply_body_scroll_behavior()
            self.body.setHtml(self._render_html())
        if self.toggle_btn is not None:
            self.toggle_btn.setText("Expand" if collapsed else "Collapse")
        self._notify_size_hint_changed()

    def _apply_body_scroll_behavior(self) -> None:
        if self.role == "diff" and self._text_line_count(self._text) > self._DIFF_SCROLL_THRESHOLD_LINES:
            self.body.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.body.set_scroll_limit(self._DIFF_MAX_BODY_HEIGHT)
            return
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.set_scroll_limit(None)

    @staticmethod
    def _text_line_count(text: str) -> int:
        source = str(text or "")
        if not source:
            return 0
        return len(source.splitlines())

    def _collapsed_preview_text(self) -> str:
        if self.role == "diff":
            return self._collapsed_diff_preview_html()
        for raw in str(self._text or "").splitlines():
            line = str(raw).strip()
            if line:
                if len(line) > 180:
                    return f"{line[:177].rstrip()}..."
                return line
        return "(no output)"

    def _collapsed_diff_preview_html(self) -> str:
        file_label = "(unknown file)"
        added = 0
        removed = 0
        for raw in str(self._text or "").splitlines():
            line = str(raw)
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    candidate = parts[3].strip()
                    if candidate.startswith("b/"):
                        candidate = candidate[2:]
                    file_label = candidate or file_label
                    break
            if line.startswith("+++ "):
                candidate = line[4:].strip()
                if candidate.startswith("b/"):
                    candidate = candidate[2:]
                if candidate != "/dev/null":
                    file_label = candidate or file_label
        for raw in str(self._text or "").splitlines():
            if raw.startswith("+++") or raw.startswith("---"):
                continue
            if raw.startswith("+"):
                added += 1
            elif raw.startswith("-"):
                removed += 1
        escaped_file = html.escape(file_label)
        return (
            f'Diff: {escaped_file} '
            f'<span style="color: #f2a7a7;">-{removed}</span> '
            f'<span style="color: #b5cea8;">+{added}</span>'
        )

    def _render_html(self) -> str:
        if self.role == "diff":
            return self._render_diff_html(self._text)
        return self._render_markdown_html(self._text)

    @staticmethod
    def _render_markdown_html(text: str) -> str:
        source = str(text or "")
        parser = markdown.Markdown(
            extensions=[
                "fenced_code",
                "tables",
                "sane_lists",
                "nl2br",
            ]
        )
        body = parser.convert(source)
        return (
            "<style>"
            "body { color: #e6edf3; font-size: 13px; }"
            "p { margin: 0.1em 0 0.45em 0; }"
            "ul, ol { margin: 0.15em 0 0.45em 1.25em; }"
            "blockquote { margin: 0.25em 0; padding-left: 8px; border-left: 2px solid #3a4558; color: #c2cfdf; }"
            "pre { background: #111722; border: 1px solid #2f3f56; border-radius: 6px; padding: 8px; }"
            "code { font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; }"
            "pre code { color: #e6edf3; }"
            "table { border-collapse: collapse; margin: 0.25em 0 0.45em 0; }"
            "th, td { border: 1px solid #334155; padding: 4px 6px; }"
            "a { color: #8ab4f8; text-decoration: none; }"
            "</style>"
            f"{body}"
        )

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

    def _notify_size_hint_changed(self) -> None:
        self.updateGeometry()
        self.sizeHintChanged.emit()
