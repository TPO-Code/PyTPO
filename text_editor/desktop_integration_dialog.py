from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.dialogs.custom_dialog import DialogWindow
from .desktop_integration import (
    APP_NAME,
    FILE_TYPE_ASSOCIATIONS,
    install_desktop_integration,
    is_installed,
    is_linux_desktop,
    mark_onboarding_seen,
    normalize_type_keys,
    selected_type_keys_from_settings,
    uninstall_desktop_integration,
)


class DesktopIntegrationPanel(QWidget):
    integrationChanged = Signal()

    def __init__(self, *, onboarding: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._onboarding = onboarding
        self._checkboxes: dict[str, QCheckBox] = {}
        self._status_label: QLabel | None = None
        self.install_button: QPushButton | None = None
        self.uninstall_button: QPushButton | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        intro = QLabel(
            (
                f"Install {APP_NAME} as a desktop editor and choose which file types it should own."
                if self._onboarding
                else f"Manage how {APP_NAME} is registered with your desktop."
            ),
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        selection_label = QLabel("File types to associate:", self)
        layout.addWidget(selection_label)

        list_host = QWidget(self)
        list_layout = QVBoxLayout(list_host)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(8)

        selected = set(normalize_type_keys(selected_type_keys_from_settings()))
        for item in FILE_TYPE_ASSOCIATIONS:
            checkbox = QCheckBox(self._item_label(item), list_host)
            checkbox.setChecked(item.key in selected)
            checkbox.setToolTip(item.description)
            list_layout.addWidget(checkbox)
            self._checkboxes[item.key] = checkbox

        list_layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(list_host)
        scroll.setMinimumHeight(220)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(self)
        self.install_button = buttons.addButton("Install", QDialogButtonBox.ButtonRole.AcceptRole)
        self.uninstall_button = buttons.addButton("Uninstall", QDialogButtonBox.ButtonRole.DestructiveRole)

        self.install_button.clicked.connect(self._install)
        self.uninstall_button.clicked.connect(self._uninstall)
        layout.addWidget(buttons)
        self.refresh_status()

    def selected_type_keys(self) -> list[str]:
        return [key for key, checkbox in self._checkboxes.items() if checkbox.isChecked()]

    @staticmethod
    def _item_label(item) -> str:
        patterns = [*item.extensions, *item.filenames]
        if not patterns:
            return item.label
        return f"{item.label} ({', '.join(patterns)})"

    def refresh_status(self) -> None:
        if not is_linux_desktop():
            if self._status_label is not None:
                self._status_label.setText("Desktop integration is currently only supported on Linux desktops.")
            if self.install_button is not None:
                self.install_button.setEnabled(False)
            if self.uninstall_button is not None:
                self.uninstall_button.setEnabled(False)
                self.uninstall_button.setVisible(False)
            return

        installed = is_installed()
        if self._status_label is not None:
            self._status_label.setText(
                "Desktop integration is currently installed."
                if installed
                else "Desktop integration is not installed yet."
            )
        if self.install_button is not None:
            self.install_button.setText("Update Integration" if installed else "Install")
        if self.uninstall_button is not None:
            self.uninstall_button.setVisible(installed)

    def _install(self) -> None:
        keys = self.selected_type_keys()
        if not keys:
            QMessageBox.warning(self, "Desktop Integration", "Select at least one file type to install.")
            return
        warnings = install_desktop_integration(keys)
        if warnings:
            QMessageBox.information(
                self,
                "Desktop Integration Installed",
                "Desktop integration was installed with warnings:\n\n" + "\n".join(warnings),
            )
        self.refresh_status()
        self.integrationChanged.emit()

    def _uninstall(self) -> None:
        result = QMessageBox.question(
            self,
            "Remove Desktop Integration",
            "Remove the launcher, MIME registration, and default editor associations for this editor?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        warnings = uninstall_desktop_integration()
        if warnings:
            QMessageBox.information(
                self,
                "Desktop Integration Removed",
                "Desktop integration was removed with warnings:\n\n" + "\n".join(warnings),
            )
        self.refresh_status()
        self.integrationChanged.emit()


class DesktopIntegrationDialog(DialogWindow):
    def __init__(self, *, onboarding: bool, parent: QWidget | None = None) -> None:
        super().__init__(use_native_chrome=False, parent=parent)
        self._onboarding = onboarding

        self.setWindowTitle("Desktop Integration")
        self.resize(560, 520)
        self._panel = DesktopIntegrationPanel(onboarding=onboarding, parent=self)
        self._panel.integrationChanged.connect(self.accept)

        host = QWidget(self)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(self._panel)

        buttons = QDialogButtonBox(host)
        self.cancel_button = buttons.addButton(
            "Not Now" if self._onboarding else "Close",
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.set_content_widget(host)

    def reject(self) -> None:
        if self._onboarding:
            mark_onboarding_seen()
        super().reject()
