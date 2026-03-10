from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .backend import ExecutionState
from .breakpoint_store import DebuggerBreakpointStore
from .session_widget import DebuggerSessionWidget


class DebuggerDockWidget(QWidget):
    runStateChanged = Signal()
    breakpointsChanged = Signal()

    def __init__(self, ide, parent=None):
        super().__init__(parent)
        self.ide = ide
        self._state = ExecutionState.IDLE
        self._sessions: dict[str, DebuggerSessionWidget] = {}
        self._breakpoints = DebuggerBreakpointStore(ide, self)
        self._breakpoints.breakpointsChanged.connect(self.breakpointsChanged)

        self._build_ui()
        self._apply_state(ExecutionState.IDLE)

    def start_current_file_debugging(self) -> bool:
        editor = self.ide.current_editor()
        self.bind_editor(editor)
        file_path = str(getattr(editor, "file_path", "") or "").strip()
        session_key = file_path or "__current__"
        session_label = file_path.rsplit("/", 1)[-1] if file_path else "Current File"
        session = self._get_or_create_session(session_key, session_label, backend_id="python")
        self.session_tabs.setCurrentWidget(session)
        ok = bool(session.controller.start_current_file_debugging())
        if ok:
            self._sync_session_title(session)
        self._refresh_active_state()
        return ok

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
        key = str(session_key or file_path or "").strip()
        label = str(session_label or file_path or "Python debug session").strip()
        session = self._get_or_create_session(key, label, backend_id="python")
        self.session_tabs.setCurrentWidget(session)
        ok = bool(
            session.controller.start_script_debugging(
                file_path=file_path,
                interpreter=interpreter,
                working_directory=working_directory,
                arguments=arguments,
                environment=environment,
                just_my_code=just_my_code,
                session_label=label,
                session_key=key,
            )
        )
        if ok:
            session.set_session_key(key)
            session.set_session_label(label)
            self._sync_session_title(session)
        self._refresh_active_state()
        return ok

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
        key = str(session_key or f"module::{module_name}").strip()
        label = str(session_label or module_name or "Python module debug session").strip()
        session = self._get_or_create_session(key, label, backend_id="python")
        self.session_tabs.setCurrentWidget(session)
        ok = bool(
            session.controller.start_module_debugging(
                module_name=module_name,
                interpreter=interpreter,
                working_directory=working_directory,
                arguments=arguments,
                environment=environment,
                just_my_code=just_my_code,
                resolved_file_path=resolved_file_path,
                session_label=label,
                session_key=key,
            )
        )
        if ok:
            session.set_session_key(key)
            session.set_session_label(label)
            self._sync_session_title(session)
        self._refresh_active_state()
        return ok

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
        key = str(session_key or program_path or file_path or "").strip()
        label = str(session_label or file_path or "Native debug session").strip()
        backend_id = "rust" if str(language or "").strip().lower() == "rust" else "python"
        session = self._get_or_create_session(key, label, backend_id=backend_id)
        self.session_tabs.setCurrentWidget(session)
        ok = bool(
            session.controller.start_executable_debugging(
                file_path=file_path,
                program_path=program_path,
                working_directory=working_directory,
                arguments=arguments,
                environment=environment,
                build_command=build_command,
                target_name=target_name,
                target_kind=target_kind,
                language=language,
                session_label=label,
                session_key=key,
            )
        )
        if ok:
            session.set_session_key(key)
            session.set_session_label(label)
            self._sync_session_title(session)
        self._refresh_active_state()
        return ok

    def bind_editor(self, editor) -> None:
        self._breakpoints.bind_editor(editor)

    def all_breakpoint_specs(self) -> dict[str, list[dict]]:
        return self._breakpoints.all_breakpoint_specs()

    def watch_expressions(self) -> list[str]:
        return self._breakpoints.watch_expressions()

    def set_watch_expressions(self, expressions: list[str]) -> None:
        self._breakpoints.set_watch_expressions(expressions)
        for session in self._sessions.values():
            session.set_watch_expressions(expressions, persist=False)

    def active_session(self) -> DebuggerSessionWidget | None:
        widget = self.session_tabs.currentWidget()
        return widget if isinstance(widget, DebuggerSessionWidget) else None

    def active_controller(self):
        session = self.active_session()
        return session.controller if session is not None else None

    def is_active(self) -> bool:
        return any(session.is_active() for session in self._sessions.values())

    def running_sessions(self) -> list[dict]:
        sessions: list[dict] = []
        for key, session in self._sessions.items():
            if not session.is_active():
                continue
            sessions.append(
                {
                    "key": key,
                    "label": session.session_label(),
                    "state": session.state().value,
                }
            )
        return sessions

    def session_for_key(self, session_key: str) -> DebuggerSessionWidget | None:
        return self._sessions.get(str(session_key or "").strip())

    def request_stop_active(self) -> int:
        session = self.active_session()
        if session is None:
            return 0
        stage = int(session.request_stop() or 0)
        self._refresh_active_state()
        return stage

    def request_stop_for_key(self, session_key: str) -> int:
        session = self.session_for_key(session_key)
        if session is None:
            return 0
        stage = int(session.request_stop() or 0)
        self._refresh_active_state()
        return stage

    def minimumSizeHint(self) -> QSize:
        return QSize(360, 70)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)

        self.btn_run = QPushButton("Debug Current File")
        self.btn_run.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_run.clicked.connect(lambda: self.ide.execution_controller.debug_current_file())

        self.btn_step_over = QPushButton("Step Over")
        self.btn_step_over.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_step_over.clicked.connect(lambda: self._send_active_command("next"))

        self.btn_step_in = QPushButton("Step In")
        self.btn_step_in.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_step_in.clicked.connect(lambda: self._send_active_command("step"))

        self.btn_continue = QPushButton("Continue")
        self.btn_continue.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_continue.clicked.connect(lambda: self._send_active_command("continue"))

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_stop.clicked.connect(self.request_stop_active)

        self.status_label = QLabel("Idle")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        toolbar.addWidget(self.btn_run)
        toolbar.addWidget(self.btn_step_over)
        toolbar.addWidget(self.btn_step_in)
        toolbar.addWidget(self.btn_continue)
        toolbar.addWidget(self.btn_stop)
        toolbar.addStretch(1)
        toolbar.addWidget(self.status_label)

        self.session_tabs = QTabWidget(self)
        self.session_tabs.setDocumentMode(True)
        self.session_tabs.setMovable(True)
        self.session_tabs.setTabsClosable(True)
        self.session_tabs.setMinimumHeight(0)
        self.session_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.session_tabs.currentChanged.connect(self._on_current_tab_changed)
        self.session_tabs.tabCloseRequested.connect(self._on_tab_close_requested)

        root.addLayout(toolbar)
        root.addWidget(self.session_tabs, 1)

    def _send_active_command(self, action: str) -> None:
        controller = self.active_controller()
        if controller is not None:
            controller.send_command(action)

    def _get_or_create_session(self, session_key: str, session_label: str, *, backend_id: str = "python") -> DebuggerSessionWidget:
        key = str(session_key or "").strip()
        session = self._sessions.get(key)
        if session is not None:
            session.set_session_label(session_label)
            self._sync_session_title(session)
            return session

        session = DebuggerSessionWidget(
            self.ide,
            session_key=key,
            session_label=session_label,
            backend_id=backend_id,
            parent=self.session_tabs,
        )
        session.set_watch_expressions(self.watch_expressions(), persist=False)
        session.watchExpressionsChanged.connect(self.set_watch_expressions)
        self._sessions[key] = session
        session.stateChanged.connect(lambda _state, s=session: self._on_session_state_changed(s))
        session.finished.connect(lambda s=session: self._on_session_finished(s))
        self.session_tabs.addTab(session, session_label)
        self._sync_session_title(session)
        return session

    def _session_tab_index(self, session: DebuggerSessionWidget) -> int:
        return self.session_tabs.indexOf(session)

    def _sync_session_title(self, session: DebuggerSessionWidget) -> None:
        idx = self._session_tab_index(session)
        if idx < 0:
            return
        label = session.session_label()
        visual_state = session.visual_state()
        if visual_state == "running":
            title = f"● {label}"
        elif visual_state == "paused":
            title = f"▌ {label}"
        elif visual_state == "stopping":
            title = f"◌ {label}"
        elif visual_state == "failed":
            title = f"✖ {label}"
        else:
            title = label
        self.session_tabs.setTabText(idx, title)
        self.session_tabs.setTabToolTip(idx, f"{session.status_text()}\n{session.session_key()}")

    def _on_session_state_changed(self, session: DebuggerSessionWidget) -> None:
        self._sync_session_title(session)
        self._refresh_active_state()

    def _on_session_finished(self, session: DebuggerSessionWidget) -> None:
        self._sync_session_title(session)
        self._refresh_active_state()

    def _on_current_tab_changed(self, _index: int) -> None:
        self._refresh_active_state()

    def _on_tab_close_requested(self, index: int) -> None:
        widget = self.session_tabs.widget(index)
        if not isinstance(widget, DebuggerSessionWidget):
            return
        key = widget.session_key()
        if widget.is_active():
            widget.stop_debugging()
        self.session_tabs.removeTab(index)
        self._sessions.pop(key, None)
        widget.deleteLater()
        self._refresh_active_state()

    def _refresh_active_state(self) -> None:
        session = self.active_session()
        self._apply_state(session.state() if session is not None else ExecutionState.IDLE)
        self.runStateChanged.emit()

    def _apply_state(self, state: ExecutionState) -> None:
        self._state = state
        session = self.active_session()
        self.status_label.setText(session.status_text() if session is not None else state.value.title())
        paused = state == ExecutionState.PAUSED
        active = state in {ExecutionState.STARTING, ExecutionState.RUNNING, ExecutionState.PAUSED, ExecutionState.STOPPING}
        self.btn_run.setEnabled(True)
        self.btn_step_over.setEnabled(paused)
        self.btn_step_in.setEnabled(paused)
        self.btn_continue.setEnabled(paused)
        self.btn_stop.setEnabled(active)
