from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QMenuBar, QMessageBox

from TPOPyside.dialogs.reusable_file_dialog import FileDialog
from src.file_dialog_settings import configure_shared_file_dialog_defaults
from TPOPyside.widgets.custom_window import Window
from .workspace import EditorWorkspace


class TextEditorWindow(Window):
    def __init__(self) -> None:
        super().__init__(use_native_chrome=False)
        self.workspace = EditorWorkspace(self)
        self.workspace.stateChanged.connect(self._update_window_title)
        self.set_content_widget(self.workspace)

        self._create_file_menu()
        self.resize(1280, 800)
        self.workspace.new_file()
        self._update_window_title()

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
            self.setWindowTitle("Text Editor")
            return
        self.setWindowTitle(f"{editor.tab_title()} - Text Editor")

    def _dialog_start_directory(self) -> str:
        editor = self.workspace.current_editor()
        if editor is not None and editor.file_path is not None:
            return str(editor.file_path.parent)
        return str(Path.home())

    def open_files(self) -> None:
        file_names, _selected_filter, _starred = FileDialog.getOpenFileNames(
            parent=self,
            caption="Open Files",
            directory=self._dialog_start_directory(),
        )
        for file_name in file_names:
            if not file_name:
                continue
            try:
                self.workspace.open_path(Path(file_name))
            except ValueError as exc:
                QMessageBox.warning(self, "Open Failed", str(exc))
            except OSError as exc:
                QMessageBox.critical(self, "Open Failed", str(exc))

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

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.workspace.request_close_all(self):
            event.accept()
            return
        event.ignore()


def main() -> int:
    app = QApplication(sys.argv)
    configure_shared_file_dialog_defaults()
    window = TextEditorWindow()
    window.show()
    return app.exec()
