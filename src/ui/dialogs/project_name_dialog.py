from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.custom_dialog import DialogWindow


class ProjectNameDialog(DialogWindow):
    def __init__(
        self,
        *,
        project_path: str,
        default_name: str,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=False, parent=parent)
        self.setWindowTitle("Project Name")
        self.resize(520, 190)

        self._project_path = str(project_path or "").strip()
        self._default_name = str(default_name or "").strip() or "My Python Project"
        self.project_name: str | None = None

        self._build_ui()
        self._refresh_continue_enabled()

    def _build_ui(self) -> None:
        host = QWidget(self)
        self.set_content_widget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        folder_name = str(Path(self._project_path).name or "").strip() or self._project_path
        prompt = QLabel(
            f"Set a project name for '{folder_name}'.\n"
            "This will be saved in .tide/project.json."
        )
        prompt.setWordWrap(True)
        root.addWidget(prompt)

        root.addWidget(QLabel("Project Name"))
        self.project_name_edit = QLineEdit(self)
        self.project_name_edit.setText(self._default_name)
        self.project_name_edit.selectAll()
        self.project_name_edit.returnPressed.connect(self._continue_clicked)
        self.project_name_edit.textChanged.connect(self._refresh_continue_enabled)
        root.addWidget(self.project_name_edit)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_btn = QPushButton("Cancel", self)
        self.continue_btn = QPushButton("Continue", self)
        self.continue_btn.setDefault(True)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.continue_btn)
        root.addLayout(actions)

        self.cancel_btn.clicked.connect(self.reject)
        self.continue_btn.clicked.connect(self._continue_clicked)

    def _refresh_continue_enabled(self) -> None:
        self.continue_btn.setEnabled(bool(str(self.project_name_edit.text() or "").strip()))

    def _continue_clicked(self) -> None:
        project_name = str(self.project_name_edit.text() or "").strip()
        if not project_name:
            self.project_name_edit.setFocus()
            return
        self.project_name = project_name
        self.accept()
