from dataclasses import dataclass, field
import os
from enum import Enum

from PySide6.QtCore import QObject, Signal


class SavePolicy(Enum):
    DEBUG_BUFFER = "debug_buffer"
    REQUIRE_SAVE = "require_save"


class LaunchTargetKind(Enum):
    ACTIVE_FILE = "active_file"
    MODULE = "module"
    NAMED_TARGET = "named_target"


@dataclass(slots=True)
class NamedLaunchTarget:
    name: str
    kind: LaunchTargetKind
    file_path: str = ""
    module_name: str = ""
    working_directory: str = ""
    arguments: tuple[str, ...] = field(default_factory=tuple)
    environment: dict[str, str] = field(default_factory=dict)


class DebugSessionContext(QObject):
    filePathChanged = Signal(str)
    workingDirectoryChanged = Signal(str)
    argumentsChanged = Signal(tuple)
    environmentChanged = Signal(dict)
    savePolicyChanged = Signal(str)
    launchTargetKindChanged = Signal(str)
    moduleNameChanged = Signal(str)
    selectedTargetChanged = Signal(str)
    namedTargetsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path = ""
        self._working_directory = ""
        self._arguments = ()
        self._environment = {}
        self._save_policy = SavePolicy.DEBUG_BUFFER
        self._launch_target_kind = LaunchTargetKind.ACTIVE_FILE
        self._module_name = ""
        self._selected_target_name = ""
        self._named_targets = {}

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

    def launch_target_kind(self):
        return self._launch_target_kind

    def set_launch_target_kind(self, launch_target_kind):
        if self._launch_target_kind == launch_target_kind:
            return
        self._launch_target_kind = launch_target_kind
        self.launchTargetKindChanged.emit(launch_target_kind.value)

    def module_name(self):
        return self._module_name

    def set_module_name(self, module_name):
        normalized = (module_name or "").strip()
        if self._module_name == normalized:
            return
        self._module_name = normalized
        self.moduleNameChanged.emit(self._module_name)

    def selected_target_name(self):
        return self._selected_target_name

    def set_selected_target_name(self, target_name):
        normalized = (target_name or "").strip()
        if self._selected_target_name == normalized:
            return
        self._selected_target_name = normalized
        self.selectedTargetChanged.emit(self._selected_target_name)

    def named_targets(self):
        return dict(self._named_targets)

    def set_named_target(self, target: NamedLaunchTarget):
        self._named_targets[target.name] = target
        self.namedTargetsChanged.emit()

    def remove_named_target(self, target_name):
        if target_name not in self._named_targets:
            return
        del self._named_targets[target_name]
        if self._selected_target_name == target_name:
            self.set_selected_target_name("")
        self.namedTargetsChanged.emit()

    def selected_named_target(self):
        return self._named_targets.get(self._selected_target_name)

    def save_policy(self):
        return self._save_policy

    def set_save_policy(self, save_policy):
        if self._save_policy == save_policy:
            return
        self._save_policy = save_policy
        self.savePolicyChanged.emit(save_policy.value)
