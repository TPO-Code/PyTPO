from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from TPOPyside.widgets.markdown_viewer_widget import MDHeadFlags, MarkdownViewerWidget
from src.ui.custom_dialog import DialogWindow


class AboutDialog(DialogWindow):
    def __init__(
        self,
        *,
        app_name: str,
        app_version: str,
        markdown_text: str,
        markdown_base_url: QUrl | None = None,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self._app_name = str(app_name or "").strip() or "PyTPO"
        self._app_version = str(app_version or "").strip() or "unknown"
        self.setWindowTitle(f"About {self._app_name} v{self._app_version}")
        self.resize(900, 700)

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.version_label = QLabel(f"{self._app_name} v{self._app_version}")
        self.version_label.setObjectName("AboutVersionLabel")
        self.version_label.setWordWrap(True)
        root.addWidget(self.version_label)

        self.viewer = MarkdownViewerWidget(show_toolbar=False)
        self.viewer.setHeadFlags(MDHeadFlags.none)
        self.viewer.setMarkdown(str(markdown_text or "").strip(), base_url=markdown_base_url or QUrl())
        root.addWidget(self.viewer, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setDefault(True)
        actions.addWidget(self.close_btn)
        root.addLayout(actions)

        self.close_btn.clicked.connect(self.accept)
