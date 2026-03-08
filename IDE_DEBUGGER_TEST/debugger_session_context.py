import os
from enum import Enum

from PySide6.QtCore import QObject, Signal


class SavePolicy(Enum):
    DEBUG_BUFFER = "debug_buffer"
    REQUIRE_SAVE = "require_save"


class DebugSessionContext(QObject):
    filePathChanged = Signal(str)
    workingDirectoryChanged = Signal(str)
    argumentsChanged = Signal(tuple)
    environmentChanged = Signal(dict)
    savePolicyChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path = ""
        self._working_directory = ""
        self._arguments = ()
        self._environment = {}
        self._save_policy = SavePolicy.DEBUG_BUFFER

    def file_path(self):
        return self._file_path

    def set_file_path(self, file_path):
        normalized = file_path or ""
        if self._file_path == normalized:
            return
        self._file_path = normalized
        self.filePathChanged.emit(self._file_path)

    def working_directory(self):
        if self._working_directory:
            return self._working_directory
        if self._file_path:
            return os.path.dirname(self._file_path)
        return ""

    def set_working_directory(self, working_directory):
        normalized = working_directory or ""
        if self._working_directory == normalized:
            return
        self._working_directory = normalized
        self.workingDirectoryChanged.emit(self.working_directory())

    def arguments(self):
        return self._arguments

    def set_arguments(self, arguments):
        normalized = tuple(arguments)
        if self._arguments == normalized:
            return
        self._arguments = normalized
        self.argumentsChanged.emit(self._arguments)

    def environment(self):
        return dict(self._environment)

    def set_environment(self, environment):
        normalized = dict(environment)
        if self._environment == normalized:
            return
        self._environment = normalized
        self.environmentChanged.emit(dict(self._environment))

    def save_policy(self):
        return self._save_policy

    def set_save_policy(self, save_policy):
        if self._save_policy == save_policy:
            return
        self._save_policy = save_policy
        self.savePolicyChanged.emit(save_policy.value)
