from __future__ import annotations

import os
from copy import deepcopy

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QTextCursor

from pytpo.ui.editor_workspace import EditorWidget
from pytpo.ui.widgets.code_editor import CodeEditor

from .backend import DebugLaunchKind, DebugLaunchRequest, DebuggerBackend, ExecutionState
from .editor_adapter import IdeDebugEditorAdapter
from .session_context import DebugSessionContext
from pytpo.ui.debugger_support import debugger_breakpoints_supported_for_editor, debugger_breakpoints_supported_for_path


class DebuggerController(QObject):
    stateChanged = Signal(str)
    stdoutReceived = Signal(str)
    stderrReceived = Signal(str)
    protocolError = Signal(str)
    started = Signal(dict)
    breakpointsSet = Signal(dict)
    paused = Signal(dict)
    watchValuesUpdated = Signal(dict)
    evaluationResult = Signal(dict)
    exceptionRaised = Signal(dict)
    fatalError = Signal(dict)
    processEnded = Signal(dict)
    finished = Signal()

    def __init__(self, ide, backend: DebuggerBackend, parent=None):
        super().__init__(parent)
        self.ide = ide
        self.backend = backend
        self.context = DebugSessionContext()
        self._launched_editor: IdeDebugEditorAdapter | None = None
        self._session_label = ""
        self._session_key = ""
        self._last_launch_request: DebugLaunchRequest | None = None
        self._last_session_label = ""
        self._last_session_key = ""
        self._watch_expressions: list[str] = []

        debugger_widget = getattr(self.ide, "debugger_dock_widget", None)
        breakpoints_changed = getattr(debugger_widget, "breakpointsChanged", None)
        if breakpoints_changed is not None:
            breakpoints_changed.connect(self.refresh_breakpoints)

        self.backend.stateChanged.connect(self.stateChanged)
        self.backend.stdoutReceived.connect(self.stdoutReceived)
        self.backend.stderrReceived.connect(self.stderrReceived)
        self.backend.protocolError.connect(self.protocolError)
        self.backend.started.connect(self._handle_backend_started)
        self.backend.started.connect(self.started)
        self.backend.breakpointsSet.connect(self.breakpointsSet)
        self.backend.paused.connect(self._handle_backend_paused)
        self.backend.paused.connect(self.paused)
        self.backend.watchValuesUpdated.connect(self.watchValuesUpdated)
        self.backend.evaluationResult.connect(self.evaluationResult)
        self.backend.exceptionRaised.connect(self.exceptionRaised)
        self.backend.fatalError.connect(self.fatalError)
        self.backend.processEnded.connect(self.processEnded)
        self.backend.finished.connect(self._handle_backend_finished)
        self.backend.finished.connect(self.finished)

    def start_current_file_debugging(self) -> bool:
        editor = self._current_editor()
        if editor is None:
            self.fatalError.emit({"message": "No active editor to debug.", "traceback": ""})
            return False
        if not self._is_python_file(editor):
            self.fatalError.emit({"message": "Current-file debugging currently supports Python files only.", "traceback": ""})
            return False

        if not self.ide._save_all_dirty_editors_for_run():
            return False

        file_path = self.ide._save_editor_for_run(editor)
        if not file_path:
            return False

        return self.start_script_debugging(
            file_path=file_path,
            interpreter=str(self.ide.resolve_interpreter(file_path) or "").strip(),
            working_directory=os.path.dirname(file_path),
            arguments=(),
            environment={},
            just_my_code=self._resolved_just_my_code(),
            session_label=os.path.basename(file_path) or file_path,
        )

    def start_script_debugging(
        self,
        *,
        file_path: str,
        interpreter: str = "",
        working_directory: str = "",
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
        just_my_code: bool | None = None,
        session_label: str = "",
        session_key: str = "",
    ) -> bool:
        target_path = str(file_path or "").strip()
        if not target_path:
            self.fatalError.emit({"message": "No Python file was provided for debugging.", "traceback": ""})
            return False
        if not os.path.isfile(target_path):
            self.fatalError.emit({"message": f"Python target not found: {target_path}", "traceback": ""})
            return False

        editor = self._find_open_editor_for_path(target_path)
        adapter = IdeDebugEditorAdapter(editor) if isinstance(editor, EditorWidget) else None
        source_text = self._source_text_for_target(target_path, adapter)
        launch_request = DebugLaunchRequest(
            file_path=target_path,
            source_text=source_text,
            launch_kind=DebugLaunchKind.SCRIPT,
            interpreter=str(interpreter or self.ide.resolve_interpreter(target_path) or "").strip(),
            working_directory=str(working_directory or os.path.dirname(target_path) or "").strip(),
            arguments=tuple(str(arg) for arg in arguments),
            environment=dict(environment or {}),
            just_my_code=self._resolved_just_my_code(just_my_code),
            use_source_snapshot=False,
        )
        return self._start_launch(
            launch_request,
            editor=editor,
            session_label=str(session_label or os.path.basename(target_path) or target_path),
            session_key=str(session_key or target_path),
        )

    def start_executable_debugging(
        self,
        *,
        file_path: str,
        program_path: str = "",
        working_directory: str = "",
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
        build_command: tuple[str, ...] = (),
        target_name: str = "",
        target_kind: str = "",
        language: str = "",
        session_label: str = "",
        session_key: str = "",
    ) -> bool:
        target_path = str(file_path or "").strip()
        if not target_path and not str(program_path or "").strip() and not tuple(build_command):
            self.fatalError.emit({"message": "No native debug target was provided.", "traceback": ""})
            return False

        editor = self._find_open_editor_for_path(target_path) if target_path else None
        adapter = IdeDebugEditorAdapter(editor) if isinstance(editor, EditorWidget) else None
        launch_request = DebugLaunchRequest(
            file_path=target_path,
            source_text=self._source_text_for_target(target_path, adapter),
            launch_kind=DebugLaunchKind.EXECUTABLE,
            program_path=str(program_path or "").strip(),
            working_directory=str(working_directory or os.path.dirname(target_path) or self.ide.project_root).strip(),
            arguments=tuple(str(arg) for arg in arguments),
            environment=dict(environment or {}),
            build_command=tuple(str(arg) for arg in build_command),
            target_name=str(target_name or "").strip(),
            target_kind=str(target_kind or "").strip(),
            language=str(language or "").strip(),
            just_my_code=False,
            use_source_snapshot=False,
        )
        return self._start_launch(
            launch_request,
            editor=editor,
            session_label=str(
                session_label
                or os.path.basename(target_path or program_path)
                or target_path
                or program_path
                or "Native debug session"
            ),
            session_key=str(session_key or program_path or target_path or "native"),
        )

    def start_module_debugging(
        self,
        *,
        module_name: str,
        interpreter: str = "",
        working_directory: str = "",
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
        just_my_code: bool | None = None,
        resolved_file_path: str = "",
        session_label: str = "",
        session_key: str = "",
    ) -> bool:
        target_module = str(module_name or "").strip()
        if not target_module:
            self.fatalError.emit({"message": "No Python module was provided for debugging.", "traceback": ""})
            return False

        target_path = str(resolved_file_path or "").strip()
        if target_path and not os.path.isfile(target_path):
            target_path = ""

        editor = self._find_open_editor_for_path(target_path) if target_path else None
        launch_request = DebugLaunchRequest(
            file_path=target_path,
            source_text=self._source_text_for_target(target_path, IdeDebugEditorAdapter(editor)) if isinstance(editor, EditorWidget) else "",
            launch_kind=DebugLaunchKind.MODULE,
            module_name=target_module,
            interpreter=str(interpreter or self.ide.resolve_interpreter(target_path or self.ide.project_root) or "").strip(),
            working_directory=str(working_directory or self.ide.project_root or "").strip(),
            arguments=tuple(str(arg) for arg in arguments),
            environment=dict(environment or {}),
            just_my_code=self._resolved_just_my_code(just_my_code),
            use_source_snapshot=False,
        )
        return self._start_launch(
            launch_request,
            editor=editor,
            session_label=str(session_label or target_module),
            session_key=str(session_key or f"module::{target_module}"),
        )

    def stop_debugging(self) -> None:
        self.backend.stop_debugging()

    def request_stop(self) -> int:
        return self.backend.request_stop()

    def send_command(self, action: str, extra: dict | None = None) -> bool:
        return self.backend.send_command(action, extra)

    def send_stdin(self, text: str) -> bool:
        return self.backend.send_stdin(text)

    def supports_stdin(self) -> bool:
        return self.backend.supports_stdin()

    def set_watch_expressions(self, expressions: list[str]) -> None:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in expressions:
            expr = str(value or "").strip()
            if not expr or expr in seen:
                continue
            seen.add(expr)
            ordered.append(expr)
        self._watch_expressions = ordered
        self.backend.send_command("set_watches", {"expressions": ordered})

    def watch_expressions(self) -> list[str]:
        return list(self._watch_expressions)

    def evaluate_expression(self, expression: str) -> bool:
        expr = str(expression or "").strip()
        if not expr:
            return False
        return self.backend.send_command("evaluate", {"expression": expr})

    def is_active(self) -> bool:
        return self.backend.state != ExecutionState.IDLE

    def session_label(self) -> str:
        return str(self._session_label or self._last_session_label or "Debug session")

    def session_key(self) -> str:
        return str(self._session_key or self._last_session_key or "")

    def refresh_breakpoints(self) -> None:
        if self.backend.state.value not in {"starting", "running", "paused"}:
            return
        self.backend.set_breakpoints(self._all_breakpoint_specs())

    def _handle_backend_started(self, data: dict) -> None:
        started_path = str(data.get("file") or "").strip()
        if started_path:
            self.context.file_path = started_path

        if self._launched_editor is None and started_path:
            editor = self._find_open_editor_for_path(started_path)
            if isinstance(editor, EditorWidget):
                self._launched_editor = IdeDebugEditorAdapter(editor)

        QTimer.singleShot(0, self.refresh_breakpoints)
        QTimer.singleShot(0, lambda: self.set_watch_expressions(self._watch_expressions))

    def _handle_backend_paused(self, data: dict) -> None:
        line_number = int(data.get("line") or -1)
        target_path = str(data.get("file") or "")
        self._clear_execution_lines()

        editor = self._open_or_find_editor_for_path(target_path)
        if not isinstance(editor, EditorWidget):
            return

        if isinstance(editor, CodeEditor):
            editor.set_debugger_execution_line(line_number)
        self._jump_editor_to_line(editor, line_number)
        self.ide._focus_editor(editor)

    def _handle_backend_finished(self) -> None:
        self._clear_execution_lines()
        self._launched_editor = None
        self._session_key = ""
        self._session_label = ""

    def _clear_execution_lines(self) -> None:
        for editor in self.ide.editor_workspace.all_editors():
            if isinstance(editor, CodeEditor):
                editor.clear_debugger_execution_line()

    def _all_breakpoint_specs(self) -> dict[str, list[dict]]:
        debugger_widget = getattr(self.ide, "debugger_dock_widget", None)
        raw = getattr(debugger_widget, "all_breakpoint_specs", None)
        out: dict[str, list[dict]] = dict(raw() if callable(raw) else {})
        for editor in self.ide.editor_workspace.all_editors():
            if not isinstance(editor, CodeEditor):
                continue
            file_path = str(getattr(editor, "file_path", "") or "").strip()
            if not file_path or not debugger_breakpoints_supported_for_editor(editor):
                continue
            out[self.ide._canonical_path(file_path)] = editor.debugger_breakpoint_specs()
        return {
            self.ide._canonical_path(file_path): list(specs)
            for file_path, specs in out.items()
            if debugger_breakpoints_supported_for_path(file_path)
        }

    def _open_or_find_editor_for_path(self, file_path: str) -> EditorWidget | None:
        target_path = str(file_path or "").strip()
        if not target_path:
            return None
        editor = self._find_open_editor_for_path(target_path)
        if isinstance(editor, EditorWidget):
            return editor
        try:
            self.ide.open_file(target_path)
        except Exception:
            return None
        return self._find_open_editor_for_path(target_path)

    def _jump_editor_to_line(self, editor: EditorWidget, line_number: int) -> None:
        if line_number <= 0:
            return
        block = editor.document().findBlockByNumber(max(0, int(line_number) - 1))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        editor.setTextCursor(cursor)
        editor.centerCursor()

    def _find_open_editor_for_path(self, file_path: str) -> EditorWidget | None:
        if not file_path:
            return None
        widget = self.ide.editor_workspace.find_document_by_path(file_path)
        return self.ide.editor_workspace.editor_from_document_widget(widget)

    def _current_editor(self) -> EditorWidget | None:
        editor = self.ide.current_editor()
        return editor if isinstance(editor, EditorWidget) else None

    def _start_launch(
        self,
        launch_request: DebugLaunchRequest,
        *,
        editor: EditorWidget | None,
        session_label: str,
        session_key: str,
    ) -> bool:
        adapter = IdeDebugEditorAdapter(editor) if isinstance(editor, EditorWidget) else None
        self._clear_execution_lines()

        self.context.file_path = str(launch_request.file_path or "")
        self.context.launch_kind = launch_request.launch_kind
        self.context.module_name = str(launch_request.module_name or "")
        self.context.program_path = str(launch_request.program_path or "")
        self.context.interpreter = str(launch_request.interpreter or "")
        self.context.working_directory = str(launch_request.working_directory or "")
        self.context.arguments = tuple(str(arg) for arg in launch_request.arguments)
        self.context.environment = dict(launch_request.environment)
        self._launched_editor = adapter
        self._session_key = str(session_key or launch_request.file_path or self.context.module_name)
        self._session_label = str(session_label or self.context.module_name or os.path.basename(self.context.file_path) or self.context.file_path)

        request = deepcopy(launch_request)
        request.working_directory = self.context.resolved_working_directory()
        request.arguments = self.context.arguments
        request.environment = dict(self.context.environment)
        self._last_launch_request = deepcopy(request)
        self._last_session_label = self._session_label
        self._last_session_key = self._session_key
        self.backend.start_debugging(request, self._all_breakpoint_specs())
        return True

    @staticmethod
    def _source_text_for_target(file_path: str, adapter: IdeDebugEditorAdapter | None) -> str:
        if not file_path:
            return ""
        if adapter is not None:
            return adapter.source_text()
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                return handle.read()
        except Exception:
            return ""

    @staticmethod
    def _is_python_file(editor: EditorWidget) -> bool:
        return debugger_breakpoints_supported_for_editor(editor)

    def _resolved_just_my_code(self, override: bool | None = None) -> bool:
        if isinstance(override, bool):
            return override
        raw = self.ide.settings_manager.get("debugger.just_my_code", scope_preference="project", default=True)
        if isinstance(raw, bool):
            return raw
        text = str(raw or "").strip().lower()
        if text in {"0", "false", "no", "off"}:
            return False
        if text in {"1", "true", "yes", "on"}:
            return True
        return True
