from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QMenuBar, QMessageBox

from TPOPyside.dialogs.reusable_file_dialog import FileDialog
from pytpo.file_dialog_settings import configure_shared_file_dialog_defaults
from TPOPyside.widgets.custom_window import Window
from .desktop_integration import APP_NAME, icon_path, is_linux_desktop, should_offer_onboarding
from .desktop_integration_dialog import DesktopIntegrationDialog
from .instance_coordinator import TextEditorInstanceServer, request_editor_activation
from .settings_dialog import TextEditorSettingsDialog
from .storage_paths import migrate_legacy_text_editor_storage
from .theme_manager import TextEditorThemeManager
from .workspace import EditorWorkspace


class TextEditorWindow(Window):
    def __init__(
        self,
        initial_paths: list[Path] | None = None,
        *,
        theme_manager: TextEditorThemeManager | None = None,
    ) -> None:
        super().__init__(use_native_chrome=False)
        self.theme_manager = theme_manager or TextEditorThemeManager()
        self.workspace = EditorWorkspace(self)
        self.workspace.stateChanged.connect(self._update_window_title)
        self.set_content_widget(self.workspace)

        self._create_file_menu()
        self.resize(1280, 800)
        self._apply_app_icon()
        self.open_paths(initial_paths or [], create_if_empty=True)
        self._update_window_title()
        if should_offer_onboarding():
            QTimer.singleShot(0, self._maybe_show_desktop_onboarding)

    def _create_file_menu(self) -> None:
        menu_bar = QMenuBar(self)
        menu_bar.setNativeMenuBar(False)
        file_menu = menu_bar.addMenu("&File")

        new_action = QAction("&New", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self.workspace.new_file)
        file_menu.addAction(new_action)

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_files)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        save_action = QAction("&Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_current_file)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save &As...", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self.save_current_file_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        close_action = QAction("&Close", self)
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close_current_file)
        file_menu.addAction(close_action)

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        self.add_window_control(menu_bar)

    def _update_window_title(self) -> None:
        editor = self.workspace.current_editor()
        if editor is None:
            self.setWindowTitle(APP_NAME)
            return
        self.setWindowTitle(f"{editor.tab_title()} - {APP_NAME}")

    def _apply_app_icon(self) -> None:
        icon_file = icon_path()
        if not icon_file.is_file():
            return
        icon = QIcon(str(icon_file))
        if icon.isNull():
            return
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)

    def _maybe_show_desktop_onboarding(self) -> None:
        if not self.isVisible():
            return
        self.open_desktop_integration_dialog(onboarding=True)

    def _dialog_start_directory(self) -> str:
        editor = self.workspace.current_editor()
        if editor is not None and editor.file_path is not None:
            return str(editor.file_path.parent)
        return str(Path.home())

    def open_paths(self, paths: list[Path], *, create_if_empty: bool = False) -> None:
        opened_any = False
        for path in paths:
            try:
                self.workspace.open_path(path)
                opened_any = True
            except ValueError as exc:
                QMessageBox.warning(self, "Open Failed", f"{path}:\n{exc}")
            except OSError as exc:
                QMessageBox.critical(self, "Open Failed", f"{path}:\n{exc}")
        if create_if_empty and not opened_any and self.workspace.current_editor() is None:
            self.workspace.new_file()

    def open_files(self) -> None:
        file_names, _selected_filter, _starred = FileDialog.getOpenFileNames(
            parent=self,
            caption="Open Files",
            directory=self._dialog_start_directory(),
        )
        self.open_paths([Path(file_name) for file_name in file_names if file_name])

    def open_external_paths(self, paths: list[str]) -> None:
        self.open_paths([Path(path) for path in paths if path], create_if_empty=False)
        self.bring_to_front()

    def save_current_file(self) -> None:
        editor = self.workspace.current_editor()
        if editor is None:
            return
        if editor.file_path is None:
            self.save_current_file_as()
            return
        self.workspace.save_editor(editor, self)

    def save_current_file_as(self) -> None:
        editor = self.workspace.current_editor()
        if editor is None:
            return
        start_path = str(editor.file_path) if editor.file_path is not None else self._dialog_start_directory()
        file_name, _selected_filter, _starred = FileDialog.getSaveFileName(
            parent=self,
            caption="Save File As",
            directory=start_path,
        )
        if not file_name:
            return
        self.workspace.save_editor_as(editor, self, Path(file_name))

    def close_current_file(self) -> None:
        self.workspace.close_current_editor(self)

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def open_desktop_integration_dialog(self, checked: bool = False, *, onboarding: bool = False) -> None:
        del checked
        if not is_linux_desktop():
            QMessageBox.information(
                self,
                "Desktop Integration",
                "Desktop integration setup is currently only implemented for Linux desktops such as Pop!_OS.",
            )
            return
        dialog = DesktopIntegrationDialog(onboarding=onboarding, parent=self)
        dialog.exec()

    def open_settings_dialog(self) -> None:
        dialog = TextEditorSettingsDialog(self.theme_manager, parent=self)
        dialog.exec()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.workspace.request_close_all(self):
            event.accept()
            return
        event.ignore()


def _initial_paths_from_argv(argv: list[str]) -> list[Path]:
    return [Path(arg).expanduser() for arg in argv[1:] if str(arg).strip()]


def main(argv: list[str] | None = None) -> int:
    migrate_legacy_text_editor_storage()
    args = list(sys.argv if argv is None else argv)
    initial_paths = _initial_paths_from_argv(args)
    if request_editor_activation(initial_paths):
        return 0

    app = QApplication(args)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    configure_shared_file_dialog_defaults()
    theme_manager = TextEditorThemeManager()
    theme_result = theme_manager.apply_saved_theme()
    server = TextEditorInstanceServer()
    if not server.listen():
        QMessageBox.critical(None, APP_NAME, "Could not initialize the single-instance server.")
        return 1

    window = TextEditorWindow(initial_paths=initial_paths, theme_manager=theme_manager)
    server.openPathsRequested.connect(window.open_external_paths)
    server.activateRequested.connect(window.bring_to_front)
    app.aboutToQuit.connect(server.close)
    if theme_result.error:
        QMessageBox.warning(window, "Theme Apply Failed", theme_result.error)
    window.show()
    window.bring_to_front()
    return app.exec()
