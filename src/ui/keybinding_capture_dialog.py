"""Dialog for capturing a single key sequence (single or multi-press)."""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from src.core.keybindings import canonicalize_chord_text, normalize_sequence, sequence_to_text
from src.ui.custom_dialog import DialogWindow


def _manual_key_token_for_event(event: QKeyEvent) -> str:
    key = int(event.key())
    if key in {int(Qt.Key_Slash), int(getattr(Qt, "Key_Question", Qt.Key_Slash))}:
        return "/"
    key_text = QKeySequence(key).toString(QKeySequence.PortableText).strip()
    if key_text:
        return key_text
    text = str(event.text() or "").strip()
    if text in {"?", "/"}:
        return "/"
    return text


def _manual_chord_from_event(event: QKeyEvent, mods) -> str:
    token = _manual_key_token_for_event(event)
    if not token:
        return ""
    parts: list[str] = []
    if mods & Qt.ControlModifier:
        parts.append("Ctrl")
    if mods & Qt.AltModifier:
        parts.append("Alt")
    if mods & Qt.ShiftModifier:
        parts.append("Shift")
    if mods & Qt.MetaModifier:
        parts.append("Meta")
    parts.append(token)
    return "+".join(parts)


def event_to_chord_text(event: QKeyEvent) -> str:
    key = int(event.key())
    if key in {
        int(Qt.Key_Control),
        int(Qt.Key_Shift),
        int(Qt.Key_Alt),
        int(Qt.Key_Meta),
        int(Qt.Key_unknown),
    }:
        return ""
    mods = event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)
    try:
        combo = int(mods) | key
    except Exception:
        combo = key
    raw = QKeySequence(combo).toString(QKeySequence.PortableText).strip()
    manual_raw = _manual_chord_from_event(event, mods)
    if manual_raw and manual_raw.count("+") > raw.count("+"):
        raw = manual_raw
    if not raw:
        raw = manual_raw
    return canonicalize_chord_text(raw)


class KeybindingCaptureDialog(DialogWindow):
    def __init__(
        self,
        *,
        action_name: str,
        initial_sequence: Sequence[str] | None = None,
        chord_timeout_ms: int = 1200,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=False, parent=parent)
        self.setWindowTitle("Capture Keybinding")
        self.setModal(True)
        self.resize(440, 180)

        self._sequence: list[str] = normalize_sequence(list(initial_sequence or []))
        self._timeout_ms = max(250, int(chord_timeout_ms))

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_capture_timeout)

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self._title = QLabel(f"Capture binding for: <b>{action_name}</b>")
        root.addWidget(self._title)

        self._help = QLabel(
            "Press keys now. Multi-press is supported.\n"
            "Backspace removes last chord. Enter confirms. Esc cancels."
        )
        self._help.setWordWrap(True)
        root.addWidget(self._help)

        self._preview = QLabel("")
        self._preview.setObjectName("KeybindingCapturePreview")
        root.addWidget(self._preview)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=host)
        self._buttons.accepted.connect(self._accept_if_valid)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._refresh_preview()

    def sequence(self) -> list[str]:
        return list(self._sequence)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = int(event.key())
        if key == int(Qt.Key_Escape):
            self.reject()
            return
        if key in (int(Qt.Key_Return), int(Qt.Key_Enter)):
            self._accept_if_valid()
            return
        if key == int(Qt.Key_Backspace):
            if self._sequence:
                self._sequence.pop()
                self._refresh_preview()
            event.accept()
            return

        chord = event_to_chord_text(event)
        if not chord:
            event.accept()
            return

        self._sequence.append(chord)
        self._refresh_preview()
        self._timeout.start(self._timeout_ms)
        event.accept()

    def _accept_if_valid(self) -> None:
        normalized = normalize_sequence(self._sequence)
        if not normalized:
            QMessageBox.warning(self, "Invalid keybinding", "Capture at least one key chord.")
            return
        self._sequence = normalized
        self.accept()

    def _on_capture_timeout(self) -> None:
        if self._sequence:
            self._accept_if_valid()

    def _refresh_preview(self) -> None:
        text = sequence_to_text(self._sequence)
        if not text:
            text = "Waiting for input..."
        self._preview.setText(f"Current: <b>{text}</b>")


__all__ = ["KeybindingCaptureDialog", "event_to_chord_text"]
