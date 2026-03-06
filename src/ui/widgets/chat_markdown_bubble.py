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

    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("codexBubbleBody")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
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
        self.setFixedHeight(max(22, int(round(total))))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_height()


class ChatMarkdownBubble(QFrame):
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
        self._collapsible = role == "tools"
        self._collapsed = self._collapsible

        self.setObjectName("codexBubble")
        self.setProperty("role", role)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header_label = str(role_label or role.title())
        header_text = f"{header_label}  {timestamp}" if timestamp else header_label
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        self.header = QLabel(header_text)
        self.header.setObjectName("codexBubbleHeader")
        self.header.setStyleSheet("background: transparent;")
        header_row.addWidget(self.header, 1)

        self.toggle_btn: QPushButton | None = None
        if self._collapsible:
            self.toggle_btn = QPushButton("")
            self.toggle_btn.setObjectName("codexBubbleToggle")
            self.toggle_btn.setCursor(Qt.PointingHandCursor)
            self.toggle_btn.setFlat(True)
            self.toggle_btn.clicked.connect(self._toggle_collapsed)
            header_row.addWidget(self.toggle_btn, 0)
        layout.addLayout(header_row)

        self.preview = QLabel("")
        self.preview.setObjectName("codexBubblePreview")
        self.preview.setStyleSheet("background: transparent;")
        self.preview.setWordWrap(False)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview.setVisible(False)
        layout.addWidget(self.preview)

        self.body = _MarkdownBubbleBody(self)
        self.body.linkActivated.connect(self._on_link_activated)
        layout.addWidget(self.body)

        self.append_line(text)
        self._apply_collapsed_state()

    def append_line(self, text: str) -> None:
        line = str(text or "")
        if self._text:
            self._text = f"{self._text}\n{line}"
        else:
            self._text = line
        if self._collapsed:
            self.preview.setText(self._collapsed_preview_text())
            return
        self.body.setHtml(self._render_html())

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
            self.body.setHtml(self._render_html())
        if self.toggle_btn is not None:
            self.toggle_btn.setText("Expand" if collapsed else "Collapse")

    def _collapsed_preview_text(self) -> str:
        for raw in str(self._text or "").splitlines():
            line = str(raw).strip()
            if line:
                if len(line) > 180:
                    return f"{line[:177].rstrip()}..."
                return line
        return "(no output)"

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
