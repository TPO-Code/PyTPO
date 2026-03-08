from __future__ import annotations

import os

from src.ui.editor_workspace import EditorWidget


class IdeDebugEditorAdapter:
    def __init__(self, editor: EditorWidget):
        self.editor = editor

    @property
    def file_path(self) -> str:
        return str(self.editor.file_path or "")

    @property
    def canonical_file_path(self) -> str:
        path = self.file_path
        if not path:
            return ""
        try:
            return os.path.normcase(os.path.abspath(path))
        except Exception:
            return path

    def display_name(self) -> str:
        return self.editor.display_name()

    def source_text(self) -> str:
        return str(self.editor.toPlainText() or "")

    def breakpoints(self) -> set[int]:
        return set(self.editor.debugger_breakpoints())

    def is_modified(self) -> bool:
        return bool(self.editor.document().isModified())

    def set_execution_line(self, line_number: int) -> None:
        self.editor.set_debugger_execution_line(line_number)

    def clear_execution_line(self) -> None:
        self.editor.clear_debugger_execution_line()

