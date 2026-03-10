from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.dialogs.custom_dialog import DialogWindow
from .desktop_integration_dialog import DesktopIntegrationPanel
from .theme_manager import TextEditorThemeManager


class TextEditorSettingsDialog(DialogWindow):
    def __init__(self, theme_manager: TextEditorThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(use_native_chrome=False, parent=parent)
        self._theme_manager = theme_manager

        self.setWindowTitle("Settings")
        self.resize(620, 560)
        self.set_content_widget(self._build_content())

    def _build_content(self) -> QWidget:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        intro = QLabel("Adjust the standalone editor appearance and desktop integration.", host)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        theme_group = QGroupBox("Appearance", host)
        theme_layout = QFormLayout(theme_group)
        self.theme_combo = QComboBox(theme_group)
        available = self._theme_manager.available_themes()
        self.theme_combo.addItems(available)
        current_theme = self._theme_manager.current_theme_name()
        index = self.theme_combo.findText(current_theme)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)
        theme_layout.addRow("Theme", self.theme_combo)
        layout.addWidget(theme_group)

        desktop_group = QGroupBox("Desktop Integration", host)
        desktop_layout = QVBoxLayout(desktop_group)
        self.desktop_panel = DesktopIntegrationPanel(onboarding=False, parent=desktop_group)
        desktop_layout.addWidget(self.desktop_panel)
        layout.addWidget(desktop_group, 1)

        buttons = QDialogButtonBox(host)
        self.apply_button = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_button = buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        self.apply_button.clicked.connect(self._apply_theme)
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        return host

    def _apply_theme(self) -> None:
        result = self._theme_manager.apply_theme(self.theme_combo.currentText(), persist=True)
        if result.error:
            QMessageBox.warning(self, "Theme Apply Failed", result.error)
            return
        index = self.theme_combo.findText(result.applied_name)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)
