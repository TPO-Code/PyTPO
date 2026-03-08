from PySide6.QtCore import QObject, Signal

from debugger_backend import DebugLaunchRequest, DebuggerBackend
from debugger_editor import DebugEditor
from debugger_session_context import DebugSessionContext, SavePolicy


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

    def __init__(self, editor: DebugEditor, backend: DebuggerBackend, context: DebugSessionContext, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.backend = backend
        self.context = context

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

    def start_debugging(self):
        is_modified = self.editor.is_modified()
        file_path = self.editor.file_path()

        if self.context.save_policy() == SavePolicy.REQUIRE_SAVE and is_modified:
            self.fatalError.emit({
                "message": "Debugging requires a saved file",
                "traceback": "",
            })
            return False

        use_source_snapshot = True
        if file_path and not is_modified:
            use_source_snapshot = False

        self.context.set_file_path(file_path)
        launch_request = DebugLaunchRequest(
            file_path=file_path,
            source_text=self.editor.source_text(),
            working_directory=self.context.working_directory(),
            arguments=self.context.arguments(),
            environment=self.context.environment(),
            use_source_snapshot=use_source_snapshot,
        )
        self.backend.start_debugging(launch_request, self.editor.breakpoints())
        return True

    def stop_debugging(self, clean_only=False):
        self.backend.stop_debugging(clean_only=clean_only)

    def send_command(self, action, extra=None):
        return self.backend.send_command(action, extra)

    def set_breakpoints(self, lines):
        return self.backend.set_breakpoints(lines)
