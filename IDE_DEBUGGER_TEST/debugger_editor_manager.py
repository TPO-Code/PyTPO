import os

from PySide6.QtCore import QObject, Signal

from debugger_editor import DebugEditor


class DebugEditorManager(QObject):
    activeEditorChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editors_by_path = {}
        self._unnamed_editors = []
        self._active_editor = None

    def add_editor(self, editor: DebugEditor):
        if editor in self.editors():
            return
        if editor.file_path():
            self._editors_by_path[self._normalize_path(editor.file_path())] = editor
        else:
            self._unnamed_editors.append(editor)
        editor.filePathChanged.connect(lambda _path, e=editor: self._handle_editor_file_path_changed(e))
        if self._active_editor is None:
            self.set_active_editor(editor)

    def editors(self):
        return list(self._editors_by_path.values()) + list(self._unnamed_editors)

    def active_editor(self):
        return self._active_editor

    def set_active_editor(self, editor: DebugEditor | None):
        if self._active_editor is editor:
            return
        self._active_editor = editor
        self.activeEditorChanged.emit(editor)

    def editor_for_path(self, file_path):
        if not file_path:
            return None
        return self._editors_by_path.get(self._normalize_path(file_path))

    def clear_execution_lines(self):
        for editor in self.editors():
            editor.clear_execution_line()

    def set_execution_location(self, file_path, line_number):
        self.clear_execution_lines()
        editor = self.editor_for_path(file_path)
        if editor is None:
            return None
        editor.set_execution_line(line_number)
        self.set_active_editor(editor)
        return editor

    def combined_breakpoints(self):
        combined = {}
        for editor in self.editors():
            path = editor.file_path()
            if not path:
                continue
            combined[self._normalize_path(path)] = sorted(editor.breakpoints())
        return combined

    def _handle_editor_file_path_changed(self, editor: DebugEditor):
        self._unnamed_editors = [item for item in self._unnamed_editors if item is not editor]
        for path, existing in list(self._editors_by_path.items()):
            if existing is editor:
                del self._editors_by_path[path]
        if editor.file_path():
            self._editors_by_path[self._normalize_path(editor.file_path())] = editor
        else:
            self._unnamed_editors.append(editor)

    @staticmethod
    def _normalize_path(file_path):
        return os.path.normcase(os.path.abspath(file_path))
