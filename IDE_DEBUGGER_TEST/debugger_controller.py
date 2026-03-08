import os

from PySide6.QtCore import QObject, Signal

from debugger_backend import DebugLaunchKind, DebugLaunchRequest, DebuggerBackend
from debugger_editor import DebugEditor
from debugger_editor_manager import DebugEditorManager
from debugger_session_context import DebugSessionContext, LaunchTargetKind, SavePolicy


class DebuggerController(QObject):
    stateChanged = Signal(str)
    stdoutReceived = Signal(str)
    stderrReceived = Signal(str)
    protocolError = Signal(str)
    started = Signal(dict)
    breakpointsSet = Signal(dict)
    paused = Signal(dict)
    exceptionRaised = Signal(dict)
    fatalError = Signal(dict)
    finished = Signal()

    activeEditorChanged = Signal(object)

    def __init__(
        self,
        editor: DebugEditor,
        backend: DebuggerBackend,
        context: DebugSessionContext,
        editor_manager: DebugEditorManager | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.editor = editor
        self.backend = backend
        self.context = context
        self.editor_manager = editor_manager or DebugEditorManager(self)
        self.editor_manager.add_editor(editor)

        self.backend.stateChanged.connect(self.stateChanged)
        self.backend.stdoutReceived.connect(self.stdoutReceived)
        self.backend.stderrReceived.connect(self.stderrReceived)
        self.backend.protocolError.connect(self.protocolError)
        self.backend.started.connect(self.started)
        self.backend.breakpointsSet.connect(self.breakpointsSet)
        self.backend.paused.connect(self.paused)
        self.backend.exceptionRaised.connect(self.exceptionRaised)
        self.backend.fatalError.connect(self.fatalError)
        self.backend.finished.connect(self.finished)
        self.backend.paused.connect(self._handle_backend_paused)
        self.backend.finished.connect(self._handle_backend_finished)
        self.editor_manager.activeEditorChanged.connect(self.activeEditorChanged)

    def start_debugging(self):
        editor = self.editor_manager.active_editor() or self.editor
        is_modified = editor.is_modified()
        file_path = editor.file_path()
        launch_kind = DebugLaunchKind.SCRIPT
        module_name = ""
        working_directory = self.context.working_directory()
        arguments = self.context.arguments()
        environment = self.context.environment()
        requires_saved_file = True

        if self.context.launch_target_kind() == LaunchTargetKind.NAMED_TARGET:
            target = self.context.selected_named_target()
            if target is None:
                self.fatalError.emit({
                    "message": "No named launch target is selected",
                    "traceback": "",
                })
                return False
            if target.kind == LaunchTargetKind.MODULE:
                launch_kind = DebugLaunchKind.MODULE
                module_name = target.module_name
                file_path = target.file_path or file_path
                requires_saved_file = bool(target.file_path)
            else:
                file_path = target.file_path or file_path
            working_directory = target.working_directory or working_directory
            arguments = target.arguments or arguments
            environment = target.environment or environment
            is_modified = False if target.file_path else is_modified
        elif self.context.launch_target_kind() == LaunchTargetKind.MODULE:
            launch_kind = DebugLaunchKind.MODULE
            module_name = self.context.module_name()
            requires_saved_file = False
            if not module_name:
                self.fatalError.emit({
                    "message": "Module launch requires a module name",
                    "traceback": "",
                })
                return False

        if self.context.save_policy() == SavePolicy.REQUIRE_SAVE and requires_saved_file and (is_modified or not file_path):
            self.fatalError.emit({
                "message": "Debugging requires a saved file",
                "traceback": "",
            })
            return False

        if working_directory and not os.path.isdir(working_directory):
            self.fatalError.emit({
                "message": f"Working directory does not exist: {working_directory}",
                "traceback": "",
            })
            return False

        use_source_snapshot = launch_kind == DebugLaunchKind.SCRIPT
        if launch_kind == DebugLaunchKind.SCRIPT and file_path and not is_modified:
            use_source_snapshot = False
            if not os.path.isfile(file_path):
                self.fatalError.emit({
                    "message": f"Target file does not exist: {file_path}",
                    "traceback": "",
                })
                return False

        self.context.set_file_path(file_path)
        launch_request = DebugLaunchRequest(
            launch_kind=launch_kind,
            file_path=file_path,
            module_name=module_name,
            source_text=editor.source_text(),
            working_directory=working_directory,
            arguments=arguments,
            environment=environment,
            use_source_snapshot=use_source_snapshot,
        )
        self.backend.start_debugging(launch_request, editor.breakpoints())
        return True

    def stop_debugging(self, clean_only=False):
        self.backend.stop_debugging(clean_only=clean_only)

    def send_command(self, action, extra=None):
        return self.backend.send_command(action, extra)

    def set_breakpoints(self, lines):
        return self.backend.set_breakpoints(lines)

    def set_active_editor(self, editor: DebugEditor):
        self.editor_manager.set_active_editor(editor)

    def add_editor(self, editor: DebugEditor):
        self.editor_manager.add_editor(editor)

    def _handle_backend_paused(self, data):
        self.editor_manager.set_execution_location(data.get("file", ""), data.get("line", -1))

    def _handle_backend_finished(self):
        self.editor_manager.clear_execution_lines()
