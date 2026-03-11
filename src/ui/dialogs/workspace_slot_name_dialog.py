from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from TPOPyside.dialogs.custom_dialog import DialogWindow


class WorkspaceSlotNameDialog(DialogWindow):
    def __init__(
        self,
        *,
        slot_number: int,
        current_name: str,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=False, parent=parent)
        self.setWindowTitle(f"Workspace Slot {max(1, int(slot_number))} Name")
        self.resize(460, 150)

        self.workspace_name: str | None = None
        self._current_name = str(current_name or "").strip()
        if not self._current_name:
            self._current_name = f"Workspace {max(1, int(slot_number))}"

        self._build_ui(slot_number=max(1, int(slot_number)))

    def _build_ui(self, *, slot_number: int) -> None:
        host = QWidget(self)
        self.set_content_widget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        prompt = QLabel(f"Name for workspace slot {slot_number}:")
        prompt.setWordWrap(True)
        root.addWidget(prompt)

        self.name_edit = QLineEdit(self)
        self.name_edit.setText(self._current_name)
        self.name_edit.selectAll()
        self.name_edit.returnPressed.connect(self._save_clicked)
        root.addWidget(self.name_edit)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_btn = QPushButton("Cancel", self)
        self.save_btn = QPushButton("Save", self)
        self.save_btn.setDefault(True)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.save_btn)
        root.addLayout(actions)

        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self._save_clicked)

        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _save_clicked(self) -> None:
        value = str(self.name_edit.text() or "").strip()
        if not value:
            self.name_edit.setFocus()
            self.name_edit.selectAll()
            return
        self.workspace_name = value
        self.accept()
