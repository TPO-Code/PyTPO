from __future__ import annotations

import secrets
import string
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.dialogs.custom_dialog import DialogWindow


class MessageDialog(DialogWindow):
    def __init__(
        self,
        *,
        title: str,
        message: str,
        buttons: Iterable[tuple[str, bool]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=False, resizable=False, parent=parent)
        self.setWindowTitle(str(title or "Message"))
        self.resize(460, 180)
        self.accepted_choice = False

        host = QWidget(self)
        self.set_content_widget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        message_label = QLabel(str(message or ""))
        message_label.setWordWrap(True)
        message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        root.addWidget(message_label)
        root.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        for label, accepted in buttons:
            button = QPushButton(str(label or "OK"), self)
            button.clicked.connect(lambda _checked=False, choice=bool(accepted): self._finish(choice))
            if accepted:
                button.setDefault(True)
            actions.addWidget(button)
        root.addLayout(actions)

    def _finish(self, accepted: bool) -> None:
        self.accepted_choice = bool(accepted)
        if accepted:
            self.accept()
            return
        self.reject()

    @classmethod
    def information(cls, parent: QWidget | None, title: str, message: str) -> None:
        cls(title=title, message=message, buttons=[("OK", True)], parent=parent).exec()

    @classmethod
    def warning(cls, parent: QWidget | None, title: str, message: str) -> None:
        cls(title=title, message=message, buttons=[("OK", True)], parent=parent).exec()

    @classmethod
    def critical(cls, parent: QWidget | None, title: str, message: str) -> None:
        cls(title=title, message=message, buttons=[("OK", True)], parent=parent).exec()

    @classmethod
    def question(
        cls,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        accept_text: str = "Yes",
        reject_text: str = "No",
    ) -> bool:
        dialog = cls(
            title=title,
            message=message,
            buttons=[(reject_text, False), (accept_text, True)],
            parent=parent,
        )
        return dialog.exec() == QDialog.DialogCode.Accepted and dialog.accepted_choice


class TextInputDialog(DialogWindow):
    def __init__(
        self,
        *,
        title: str,
        prompt: str,
        text: str = "",
        ok_text: str = "OK",
        cancel_text: str = "Cancel",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=False, resizable=False, parent=parent)
        self.setWindowTitle(str(title or "Input"))
        self.resize(480, 180)

        host = QWidget(self)
        self.set_content_widget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        prompt_label = QLabel(str(prompt or ""))
        prompt_label.setWordWrap(True)
        root.addWidget(prompt_label)

        self.line_edit = QLineEdit(self)
        self.line_edit.setText(str(text or ""))
        self.line_edit.selectAll()
        self.line_edit.returnPressed.connect(self._accept_if_valid)
        root.addWidget(self.line_edit)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton(str(cancel_text or "Cancel"), self)
        self.ok_button = QPushButton(str(ok_text or "OK"), self)
        self.ok_button.setDefault(True)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.ok_button)
        root.addLayout(actions)

        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self._accept_if_valid)

        self.line_edit.setFocus()
        self.line_edit.selectAll()

    def _accept_if_valid(self) -> None:
        if not str(self.line_edit.text() or "").strip():
            self.line_edit.setFocus()
            self.line_edit.selectAll()
            return
        self.accept()

    @classmethod
    def get_text(
        cls,
        parent: QWidget | None,
        title: str,
        prompt: str,
        *,
        text: str = "",
        ok_text: str = "OK",
        cancel_text: str = "Cancel",
    ) -> tuple[str, bool]:
        dialog = cls(
            title=title,
            prompt=prompt,
            text=text,
            ok_text=ok_text,
            cancel_text=cancel_text,
            parent=parent,
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return str(dialog.line_edit.text() or "").strip(), accepted


class DangerCodeDialog(DialogWindow):
    def __init__(
        self,
        *,
        title: str,
        warning_html: str,
        confirmation_code: str,
        confirm_text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=False, resizable=False, parent=parent)
        self.setWindowTitle(str(title or "Confirm"))
        self.resize(520, 260)
        self.confirmation_code = str(confirmation_code or "").strip().upper()

        host = QWidget(self)
        self.set_content_widget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        warning_label = QLabel(str(warning_html or ""))
        warning_label.setWordWrap(True)
        warning_label.setTextFormat(Qt.TextFormat.RichText)
        warning_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(warning_label)

        code_label = QLabel(f"Type <b>{self.confirmation_code}</b> to continue.")
        code_label.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(code_label)

        self.code_edit = QLineEdit(self)
        self.code_edit.textChanged.connect(self._refresh_confirm_enabled)
        root.addWidget(self.code_edit)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.confirm_button = QPushButton(str(confirm_text or "Confirm"), self)
        self.confirm_button.setEnabled(False)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.confirm_button)
        root.addLayout(actions)

        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)

        self.code_edit.setFocus()

    def _refresh_confirm_enabled(self) -> None:
        typed = str(self.code_edit.text() or "").strip().upper()
        self.confirm_button.setEnabled(typed == self.confirmation_code)

    @classmethod
    def confirm(
        cls,
        parent: QWidget | None,
        *,
        title: str,
        warning_html: str,
        confirm_text: str,
    ) -> bool:
        alphabet = string.ascii_uppercase + string.digits
        code = "".join(secrets.choice(alphabet) for _ in range(4))
        dialog = cls(
            title=title,
            warning_html=warning_html,
            confirmation_code=code,
            confirm_text=confirm_text,
            parent=parent,
        )
        return dialog.exec() == QDialog.DialogCode.Accepted
