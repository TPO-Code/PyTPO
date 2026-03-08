import os

from PySide6.QtCore import QObject, Signal


class DebugEditor(QObject):
    filePathChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def file_path(self):
        raise NotImplementedError

    def display_name(self):
        path = self.file_path()
        return os.path.basename(path) if path else "Untitled"

    def source_text(self):
        raise NotImplementedError

    def breakpoints(self):
        raise NotImplementedError

    def is_modified(self):
        raise NotImplementedError

    def set_execution_line(self, line_number):
        raise NotImplementedError

    def clear_execution_line(self):
        raise NotImplementedError
