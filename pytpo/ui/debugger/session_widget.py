from __future__ import annotations

import html
import os
import re
import signal

from PySide6.QtCore import QSize, Qt, QUrl, QUrlQuery, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .backend import ExecutionState
from .controller import DebuggerController
from .io_terminal_widget import DebuggerIoTerminalWidget
from .python_backend import PythonDebuggerBackend
from .lldb_dap_backend import LldbDapDebuggerBackend


class DebuggerSessionWidget(QWidget):
    stateChanged = Signal(str)
    finished = Signal()
    watchExpressionsChanged = Signal(list)
    _RE_PY_TRACEBACK = re.compile(r'File "([^"]+)", line (\d+)(?:, in .*)?$')
    _RE_CXX_DIAG = re.compile(
        r"^\s*(?P<path>[^:\n][^:\n]*):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?:fatal\s+error|error)\b",
        re.IGNORECASE,
    )
    _RE_GENERIC_LOCATION = re.compile(
        r"(?:^|(?<=\s)|(?<=[(\"']))(?P<path>(?:[A-Za-z]:[\\/][^:\n]+|/[^:\n]+|(?:\./|\.\./|~/)[^:\n]+|[^:\s]+\.[A-Za-z0-9_+-]+)):(?P<line>\d+)(?::(?P<col>\d+))?"
    )
    _STEP_SKIP_LINE = 'Frame skipped from debugging during step-in.'
    _STEP_SKIP_NOTE_PREFIX = 'Note: may have been skipped because of "justMyCode" option'

    def __init__(self, ide, *, session_key: str, session_label: str, backend_id: str = "python", parent=None):
        super().__init__(parent)
        self.ide = ide
        self._session_key = str(session_key or "")
        self._backend_id = str(backend_id or "python").strip().lower() or "python"
        default_label = "Rust debug session" if self._backend_id == "rust" else "Python debug session"
        self._session_label = str(session_label or self._session_key or default_label)
        self.controller = DebuggerController(ide, self._create_backend(), self)
        self._state = ExecutionState.IDLE
        self._last_visual_state = "idle"
        self._stack_frames: list[dict] = []
        self._watch_expressions: list[str] = []
        self._debug_io_terminal: DebuggerIoTerminalWidget | None = None

        self._build_ui()
        self._restore_layout()
        self._connect_controller()

    def _create_backend(self):
        if self._backend_id == "rust":
            return LldbDapDebuggerBackend(self, ide=self.ide)
        return PythonDebuggerBackend(self, ide=self.ide)

    def session_key(self) -> str:
        return self._session_key

    def set_session_key(self, session_key: str) -> None:
        self._session_key = str(session_key or "")

    def session_label(self) -> str:
        return self._session_label

    def set_session_label(self, session_label: str) -> None:
        text = str(session_label or "").strip()
        if text:
            self._session_label = text

    def state(self) -> ExecutionState:
        return self._state

    def is_active(self) -> bool:
        return self.controller.is_active()

    def request_stop(self) -> int:
        return self.controller.request_stop()

    def stop_debugging(self) -> None:
        self.controller.stop_debugging()

    def watch_expressions(self) -> list[str]:
        return list(self._watch_expressions)

    def set_watch_expressions(self, expressions: list[str], *, persist: bool = True) -> None:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in expressions:
            expr = str(value or "").strip()
            if not expr or expr in seen:
                continue
            seen.add(expr)
            ordered.append(expr)
        self._watch_expressions = ordered
        self.watch_list.clear()
        for expression in ordered:
            self.watch_list.addItem(expression)
        self.controller.set_watch_expressions(ordered)
        if persist:
            self.watchExpressionsChanged.emit(list(ordered))

    def visual_state(self) -> str:
        if self._state in {ExecutionState.STARTING, ExecutionState.RUNNING}:
            return "running"
        if self._state == ExecutionState.PAUSED:
            return "paused"
        if self._state == ExecutionState.STOPPING:
            return "stopping"
        return self._last_visual_state

    def status_text(self) -> str:
        state = self.visual_state()
        if state == "running":
            return "Running"
        if state == "paused":
            return "Paused"
        if state == "stopping":
            return "Stopping"
        if state == "failed":
            return "Failed"
        if state == "finished":
            return "Finished"
        return "Idle"

    def minimumSizeHint(self) -> QSize:
        return QSize(320, 220 if self._debug_io_terminal is not None else 60)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.summary_label = QLabel("Idle", self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self.main_splitter = QSplitter(Qt.Horizontal, self)
        self.main_splitter.setChildrenCollapsible(True)
        self.main_splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)

        self.output_panel = QWidget(self)
        output_layout = QVBoxLayout(self.output_panel)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(4)

        self.output_view = QTextBrowser(self)
        self.output_view.setReadOnly(True)
        self.output_view.setOpenLinks(False)
        self.output_view.setOpenExternalLinks(False)
        self.output_view.anchorClicked.connect(self._on_output_link_activated)
        self.output_view.setPlaceholderText("Debugger output...")
        self.output_view.setMinimumHeight(0)
        self.output_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.output_view.document().setDefaultStyleSheet(
            """
            .dbg-line { margin: 0; white-space: pre-wrap; font-family: monospace; }
            .dbg-stdout { color: palette(text); }
            .dbg-stderr { color: #c1121f; }
            .dbg-protocol { color: #b54708; }
            .dbg-debug { color: #667085; }
            .dbg-eval { color: #175cd3; }
            .dbg-exception { color: #c1121f; font-weight: 600; }
            .dbg-fatal { color: #b42318; font-weight: 600; }
            a { color: palette(link); text-decoration: underline; }
            """
        )
        output_layout.addWidget(self.output_view, 1)

        self.io_host = QWidget(self)
        self.io_host.setVisible(False)
        self.io_host.setMinimumHeight(120)
        self.io_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.io_layout = QVBoxLayout(self.io_host)
        self.io_layout.setContentsMargins(0, 0, 0, 0)
        self.io_layout.setSpacing(0)
        output_layout.addWidget(self.io_host, 1)

        self.inspector_splitter = QSplitter(Qt.Vertical, self)
        self.inspector_splitter.setChildrenCollapsible(True)
        self.inspector_splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)

        self.stack_view = QListWidget(self)
        self.stack_view.setAlternatingRowColors(True)
        self.stack_view.setMinimumHeight(0)
        self.stack_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.stack_view.currentRowChanged.connect(self._on_stack_selection_changed)

        self.inspector_tabs = QTabWidget(self)
        self.inspector_tabs.setDocumentMode(True)
        self.inspector_tabs.setMinimumHeight(0)
        self.inspector_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)

        self.variables_view = QTreeWidget(self)
        self.variables_view.setColumnCount(2)
        self.variables_view.setHeaderLabels(["Name", "Value"])
        self.variables_view.setAlternatingRowColors(True)
        self.variables_view.setMinimumHeight(0)
        self.variables_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)

        self.watches_host = QWidget(self)
        self.watches_host.setMinimumHeight(0)
        self.watches_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        watch_layout = QVBoxLayout(self.watches_host)
        watch_layout.setContentsMargins(0, 0, 0, 0)
        watch_bar = QHBoxLayout()
        watch_bar.setContentsMargins(0, 0, 0, 0)
        self.watch_input = QLineEdit(self)
        self.watch_input.setPlaceholderText("Expression to watch")
        self.watch_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.watch_input.returnPressed.connect(self._add_watch_expression)
        self.add_watch_button = QPushButton("Add", self)
        self.add_watch_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.add_watch_button.clicked.connect(self._add_watch_expression)
        self.remove_watch_button = QPushButton("Remove", self)
        self.remove_watch_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.remove_watch_button.clicked.connect(self._remove_selected_watch)
        self.evaluate_button = QPushButton("Evaluate", self)
        self.evaluate_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.evaluate_button.clicked.connect(self._evaluate_current_expression)
        watch_bar.addWidget(self.watch_input, 1)
        watch_bar.addWidget(self.add_watch_button)
        watch_bar.addWidget(self.remove_watch_button)
        watch_bar.addWidget(self.evaluate_button)
        self.watch_list = QListWidget(self)
        self.watch_list.setMinimumHeight(0)
        self.watch_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.watch_list.currentTextChanged.connect(self._on_watch_selection_changed)
        self.watch_values = QTreeWidget(self)
        self.watch_values.setColumnCount(3)
        self.watch_values.setHeaderLabels(["Expression", "Value", "Status"])
        self.watch_values.setAlternatingRowColors(True)
        self.watch_values.setMinimumHeight(0)
        self.watch_values.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        watch_layout.addLayout(watch_bar)
        watch_layout.addWidget(self.watch_list, 1)
        watch_layout.addWidget(self.watch_values, 2)

        self.issues_view = QTreeWidget(self)
        self.issues_view.setColumnCount(2)
        self.issues_view.setHeaderLabels(["Field", "Value"])
        self.issues_view.setAlternatingRowColors(True)
        self.issues_view.setMinimumHeight(0)
        self.issues_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)

        self.inspector_tabs.addTab(self.variables_view, "Variables")
        self.inspector_tabs.addTab(self.watches_host, "Watches")
        self.inspector_tabs.addTab(self.issues_view, "Issues")

        self.inspector_splitter.addWidget(self.stack_view)
        self.inspector_splitter.addWidget(self.inspector_tabs)
        self.inspector_splitter.setStretchFactor(0, 2)
        self.inspector_splitter.setStretchFactor(1, 3)

        self.main_splitter.addWidget(self.output_panel)
        self.main_splitter.addWidget(self.inspector_splitter)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)

        self.console_host = QWidget(self)
        console_layout = QHBoxLayout(self.console_host)
        console_layout.setContentsMargins(0, 0, 0, 0)
        console_layout.setSpacing(4)
        self.console_input = QLineEdit(self)
        self.console_input.setPlaceholderText("Debugger console")
        self.console_input.returnPressed.connect(self._submit_console_input)
        self.console_submit_button = QPushButton("Send", self)
        self.console_submit_button.clicked.connect(self._submit_console_input)
        console_layout.addWidget(self.console_input, 1)
        console_layout.addWidget(self.console_submit_button)

        root.addWidget(self.summary_label)
        root.addWidget(self.main_splitter, 1)
        root.addWidget(self.console_host)
        self._refresh_console_input_affordance()

    def _connect_controller(self) -> None:
        self.controller.stateChanged.connect(self._on_state_changed)
        self.controller.stdoutReceived.connect(self._on_stdout_received)
        self.controller.stderrReceived.connect(lambda text: self._append_output(text, prefix="[stderr] ", category="stderr"))
        self.controller.protocolError.connect(lambda text: self._append_output(text, prefix="[protocol] ", category="protocol"))
        self.controller.started.connect(self._on_started)
        self.controller.breakpointsSet.connect(self._on_breakpoints_set)
        self.controller.paused.connect(self._on_paused)
        self.controller.watchValuesUpdated.connect(self._on_watch_values_updated)
        self.controller.evaluationResult.connect(self._on_evaluation_result)
        self.controller.exceptionRaised.connect(self._on_exception)
        self.controller.fatalError.connect(self._on_fatal_error)
        self.controller.processEnded.connect(self._on_process_ended)
        self.controller.finished.connect(self._on_finished)
        self.main_splitter.splitterMoved.connect(self._persist_layout)
        self.inspector_splitter.splitterMoved.connect(self._persist_layout)

    def _on_stdout_received(self, text: str) -> None:
        line, category = self._normalize_stream_output(text, category="stdout")
        self._append_output(line, category=category)

    def _layout_settings(self) -> dict:
        raw = self.ide.settings_manager.get("debugger.layout", scope_preference="ide", default={})
        return dict(raw) if isinstance(raw, dict) else {}

    def _restore_layout(self) -> None:
        cfg = self._layout_settings()
        main_sizes = cfg.get("main_splitter")
        if isinstance(main_sizes, list) and len(main_sizes) >= 2:
            try:
                self.main_splitter.setSizes([max(50, int(size)) for size in main_sizes])
            except Exception:
                pass
        inspector_sizes = cfg.get("inspector_splitter")
        if isinstance(inspector_sizes, list) and len(inspector_sizes) >= 2:
            try:
                self.inspector_splitter.setSizes([max(40, int(size)) for size in inspector_sizes])
            except Exception:
                pass

    def _persist_layout(self, *_args) -> None:
        cfg = self._layout_settings()
        cfg["main_splitter"] = [int(size) for size in self.main_splitter.sizes()]
        cfg["inspector_splitter"] = [int(size) for size in self.inspector_splitter.sizes()]
        self.ide.settings_manager.set("debugger.layout", cfg, "ide")
        try:
            self.ide.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
        except Exception:
            pass

    def _set_summary(self, text: str) -> None:
        self.summary_label.setText(str(text or "").strip() or "Idle")

    def _append_output(self, text: str, *, prefix: str = "", category: str = "stdout") -> None:
        line = f"{prefix}{text}" if prefix else str(text or "")
        if not line:
            return
        scroll_bar = self.output_view.verticalScrollBar()
        stick_to_bottom = scroll_bar.value() >= max(0, scroll_bar.maximum() - 4)
        parts = line.splitlines() or [line]
        cursor = self.output_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        for part in parts:
            if not self.output_view.document().isEmpty():
                cursor.insertBlock()
            cursor.insertHtml(self._render_output_html(part, category=category))
        self.output_view.setTextCursor(cursor)
        if stick_to_bottom:
            scroll_bar.setValue(scroll_bar.maximum())

    def _clear_issue_view(self) -> None:
        self.issues_view.clear()

    def _set_issue_data(self, title: str, rows: list[tuple[str, str]]) -> None:
        self.issues_view.clear()
        root = QTreeWidgetItem([title, ""])
        self.issues_view.addTopLevelItem(root)
        for key, value in rows:
            root.addChild(QTreeWidgetItem([str(key), str(value)]))
        root.setExpanded(True)
        self.inspector_tabs.setCurrentWidget(self.issues_view)
        self.issues_view.resizeColumnToContents(0)

    def _on_state_changed(self, state_value: str) -> None:
        try:
            self._state = ExecutionState(state_value)
        except Exception:
            self._state = ExecutionState.IDLE
        if self._state in {ExecutionState.STARTING, ExecutionState.RUNNING}:
            self._last_visual_state = "running"
        elif self._state == ExecutionState.PAUSED:
            self._last_visual_state = "paused"
        self._set_summary(self.status_text())
        self._refresh_console_input_affordance()
        self.stateChanged.emit(state_value)

    def _on_started(self, data: dict) -> None:
        self._last_visual_state = "running"
        self._stack_frames = []
        self.stack_view.clear()
        self.variables_view.clear()
        self.watch_values.clear()
        self._clear_issue_view()
        file_path = str(data.get("file") or "")
        module_name = str(data.get("module") or "").strip()
        summary = f"Running {module_name}" if module_name else "Running"
        if file_path:
            self._append_output(f"[debug] started {file_path}", category="debug")
            summary = f"Running {file_path}"
        if module_name:
            self._append_output(f"[debug] module {module_name}", category="debug")
        self._set_summary(summary)
        self.stateChanged.emit(self._state.value)

    def _on_breakpoints_set(self, data: dict) -> None:
        files = data.get("files") or []
        self._append_output(f"[debug] breakpoints set: {files}", category="debug")

    def _on_paused(self, data: dict) -> None:
        file_path = str(data.get("file") or "")
        line_number = int(data.get("line") or -1)
        function_name = str(data.get("function") or "").strip()
        label = f"{file_path}:{line_number}" if file_path else f"line {line_number}"
        if function_name:
            label = f"{label} in {function_name}"
        self._append_output(f"[debug] paused at {label}", category="debug")
        self._last_visual_state = "paused"
        self._set_summary(f"Paused at {label}")
        self._render_pause_data(data)
        self._render_watch_values(data.get("watches") or [])
        self.stateChanged.emit(self._state.value)

    def _on_watch_values_updated(self, data: dict) -> None:
        self._render_watch_values(data.get("watches") or [])

    def _on_evaluation_result(self, data: dict) -> None:
        expression = str(data.get("expression") or "")
        status = str(data.get("status") or "ok")
        value = str(data.get("value") or data.get("error") or "")
        self._append_output(f"[eval] {expression} => {value}", category="eval")
        self._set_issue_data(
            "Evaluation",
            [("Expression", expression), ("Status", status), ("Value", value)],
        )

    def _on_exception(self, data: dict) -> None:
        exc_type = str(data.get("type") or "Exception")
        message = str(data.get("message") or "")
        file_path = str(data.get("file") or "")
        line_number = str(data.get("line") or "")
        self._append_output(f"[exception] {exc_type}: {message}", category="exception")
        traceback_text = str(data.get("traceback") or "").strip()
        if traceback_text:
            for line in traceback_text.splitlines():
                self._append_output(line, category="exception")
        self._set_summary(f"Exception: {exc_type}")
        self._set_issue_data(
            "Exception",
            [("Type", exc_type), ("Message", message), ("File", file_path), ("Line", line_number), ("Traceback", traceback_text)],
        )

    def _on_fatal_error(self, data: dict) -> None:
        self._last_visual_state = "failed"
        message = str(data.get("message") or "Debugger failed.")
        traceback_text = str(data.get("traceback") or "").strip()
        self._append_output(f"[fatal] {message}", category="fatal")
        if traceback_text:
            for line in traceback_text.splitlines():
                self._append_output(line, category="fatal")
        self._set_summary(f"Failed: {message}")
        self._set_issue_data("Fatal Error", [("Message", message), ("Traceback", traceback_text)])
        self.stateChanged.emit(self._state.value)

    def _on_process_ended(self, data: dict) -> None:
        exit_code = int(data.get("exit_code") or 0)
        exit_status = str(data.get("exit_status") or "finished")
        status = f"Process {exit_status} ({exit_code})"
        self._append_output(f"[debug] {status}", category="debug")
        if exit_code != 0 or exit_status != "finished":
            self._set_issue_data(
                "Process",
                [("Exit code", str(exit_code)), ("Exit status", exit_status)],
            )
            self._set_summary(status)

    def _on_finished(self) -> None:
        self._append_output("[debug] finished", category="debug")
        if self._last_visual_state not in {"failed", "paused"}:
            self._last_visual_state = "finished"
            self._set_summary("Finished")
        self._refresh_console_input_affordance()
        self.finished.emit()
        self.stateChanged.emit(self._state.value)

    @classmethod
    def _normalize_stream_output(cls, text: str, *, category: str) -> tuple[str, str]:
        line = str(text or "")
        stripped = line.strip()
        if stripped == cls._STEP_SKIP_LINE:
            return "[debug] step-in skipped a library or framework frame.", "debug"
        if stripped.startswith(cls._STEP_SKIP_NOTE_PREFIX):
            return "[debug] Just My Code is enabled, so non-project frames are skipped during step-in. Disable it in the debugger settings or this run config to step into them.", "debug"
        return line, category

    def _on_output_link_activated(self, url: QUrl) -> None:
        if not isinstance(url, QUrl) or url.scheme() != "pytpo-debug":
            return
        query = QUrlQuery(url)
        raw_path = query.queryItemValue("path")
        line = max(1, int(query.queryItemValue("line") or 1))
        column = max(1, int(query.queryItemValue("col") or 1))
        file_path = self._resolve_output_path(raw_path)
        if not file_path or not os.path.isfile(file_path):
            return
        self.ide._on_problem_activated(file_path, line, column)

    def _resolve_output_path(self, raw_path: str) -> str:
        path_text = str(raw_path or "").strip()
        if not path_text:
            return ""
        expanded = os.path.expanduser(path_text)
        if os.path.isabs(expanded):
            return os.path.abspath(expanded)
        launch_path = str(getattr(self.controller.context, "file_path", "") or "").strip()
        if launch_path:
            candidate = os.path.abspath(os.path.join(os.path.dirname(launch_path), expanded))
            if os.path.exists(candidate):
                return candidate
        project_root = str(getattr(self.ide, "project_root", "") or "").strip()
        if project_root:
            candidate = os.path.abspath(os.path.join(project_root, expanded))
            if os.path.exists(candidate):
                return candidate
        resolver = getattr(self.ide, "_resolve_path_from_project_no_symlink_resolve", None)
        if callable(resolver):
            try:
                candidate = str(resolver(expanded) or "").strip()
            except Exception:
                candidate = ""
            if candidate:
                return os.path.abspath(os.path.expanduser(candidate))
        return os.path.abspath(expanded)

    @classmethod
    def _extract_output_reference(cls, text: str) -> tuple[int, int, str, int, int] | None:
        source = str(text or "")
        py_match = cls._RE_PY_TRACEBACK.search(source)
        if py_match:
            raw_path = str(py_match.group(1) or "").strip()
            if raw_path and not raw_path.startswith("<"):
                return py_match.start(1), py_match.end(2), raw_path, max(1, int(py_match.group(2) or 1)), 1

        cxx_match = cls._RE_CXX_DIAG.search(source)
        if cxx_match:
            raw_path = str(cxx_match.group("path") or "").strip()
            if raw_path and not raw_path.startswith("<"):
                return (
                    cxx_match.start("path"),
                    cxx_match.end("col") if cxx_match.group("col") else cxx_match.end("line"),
                    raw_path,
                    max(1, int(cxx_match.group("line") or 1)),
                    max(1, int(cxx_match.group("col") or 1)),
                )

        generic_match = cls._RE_GENERIC_LOCATION.search(source)
        if generic_match:
            raw_path = str(generic_match.group("path") or "").strip()
            if raw_path and not raw_path.startswith("<"):
                return (
                    generic_match.start("path"),
                    generic_match.end("col") if generic_match.group("col") else generic_match.end("line"),
                    raw_path,
                    max(1, int(generic_match.group("line") or 1)),
                    max(1, int(generic_match.group("col") or 1)),
                )
        return None

    @classmethod
    def _render_output_html(cls, text: str, *, category: str) -> str:
        source = str(text or "")
        ref = cls._extract_output_reference(source)
        if ref is None:
            body = html.escape(source) or "&nbsp;"
        else:
            start, end, raw_path, line, column = ref
            link = cls._build_output_href(raw_path, line, column)
            body = (
                f"{html.escape(source[:start])}"
                f"<a href=\"{html.escape(link, quote=True)}\">{html.escape(source[start:end])}</a>"
                f"{html.escape(source[end:])}"
            ) or "&nbsp;"
        return f"<div class=\"dbg-line dbg-{html.escape(category, quote=True)}\">{body}</div>"

    @staticmethod
    def _build_output_href(file_path: str, line: int, column: int) -> str:
        url = QUrl()
        url.setScheme("pytpo-debug")
        query = QUrlQuery()
        query.addQueryItem("path", str(file_path or ""))
        query.addQueryItem("line", str(max(1, int(line or 1))))
        query.addQueryItem("col", str(max(1, int(column or 1))))
        url.setQuery(query)
        return url.toString()

    def _render_pause_data(self, data: dict) -> None:
        raw_stack = data.get("stack")
        if isinstance(raw_stack, list) and raw_stack:
            self._stack_frames = [item for item in raw_stack if isinstance(item, dict)]
        else:
            self._stack_frames = [
                {
                    "file": str(data.get("file") or ""),
                    "line": int(data.get("line") or -1),
                    "function": str(data.get("function") or ""),
                    "locals": data.get("locals") or {},
                    "globals": data.get("globals") or {},
                }
            ]

        self.stack_view.clear()
        for index, frame in enumerate(self._stack_frames):
            file_path = str(frame.get("file") or "")
            line_number = int(frame.get("line") or -1)
            function_name = str(frame.get("function") or "<module>")
            short_name = file_path.rsplit("/", 1)[-1] if file_path else "<unknown>"
            item = QListWidgetItem(f"{index + 1}. {function_name}  {short_name}:{line_number}")
            item.setData(Qt.UserRole, frame)
            self.stack_view.addItem(item)

        if self.stack_view.count():
            self.stack_view.setCurrentRow(self.stack_view.count() - 1)
        else:
            self.variables_view.clear()

    def _render_watch_values(self, watches: list[dict]) -> None:
        self.watch_values.clear()
        for raw in watches:
            if not isinstance(raw, dict):
                continue
            expression = str(raw.get("expression") or "")
            status = str(raw.get("status") or "ok")
            value = str(raw.get("value") if raw.get("value") is not None else raw.get("error") or "")
            self.watch_values.addTopLevelItem(QTreeWidgetItem([expression, value, status]))
        self.watch_values.resizeColumnToContents(0)

    def _on_stack_selection_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._stack_frames):
            self.variables_view.clear()
            return
        self._render_frame_variables(self._stack_frames[row])

    def _render_frame_variables(self, frame: dict) -> None:
        self.variables_view.clear()
        self.variables_view.setHeaderLabels(["Name", "Value"])

        locals_item = QTreeWidgetItem(["Locals", ""])
        globals_item = QTreeWidgetItem(["Globals", ""])
        self.variables_view.addTopLevelItem(locals_item)
        self.variables_view.addTopLevelItem(globals_item)

        self._populate_variable_group(locals_item, frame.get("locals") or {})
        self._populate_variable_group(globals_item, frame.get("globals") or {})
        locals_item.setExpanded(True)
        globals_item.setExpanded(True)
        self.variables_view.resizeColumnToContents(0)

    def _add_watch_expression(self) -> None:
        expression = str(self.watch_input.text() or "").strip()
        if not expression:
            return
        self.watch_input.clear()
        self.set_watch_expressions([*self._watch_expressions, expression])

    def _remove_selected_watch(self) -> None:
        item = self.watch_list.currentItem()
        if item is None:
            return
        target = str(item.text() or "").strip()
        self.set_watch_expressions([expr for expr in self._watch_expressions if expr != target])

    def _on_watch_selection_changed(self, text: str) -> None:
        if text and not self.watch_input.text().strip():
            self.watch_input.setText(str(text))

    def _evaluate_current_expression(self) -> None:
        expression = str(self.watch_input.text() or "").strip()
        if not expression:
            item = self.watch_list.currentItem()
            expression = str(item.text() or "").strip() if item is not None else ""
        if not expression:
            return
        self.controller.evaluate_expression(expression)

    def _submit_console_input(self) -> None:
        if self._state == ExecutionState.PAUSED:
            expression = str(self.console_input.text() or "").strip()
            if not expression:
                return
            self.console_input.clear()
            if not self.controller.evaluate_expression(expression):
                self._append_output("[debug] Evaluation is unavailable for this frame.", category="debug")
            return
        text = str(self.console_input.text() or "")
        if not text and not self.controller.supports_stdin():
            return
        self.console_input.clear()
        if not self.controller.send_stdin(text):
            self._append_output("[debug] Program input is unavailable for this debugger session.", category="debug")

    def _refresh_console_input_affordance(self) -> None:
        if self._state == ExecutionState.PAUSED:
            self.console_input.setEnabled(True)
            self.console_submit_button.setEnabled(True)
            self.console_input.setPlaceholderText("Evaluate expression in paused frame")
            self.console_submit_button.setText("Evaluate")
            return
        stdin_enabled = self.controller.supports_stdin()
        self.console_input.setEnabled(stdin_enabled)
        self.console_submit_button.setEnabled(stdin_enabled)
        if stdin_enabled:
            self.console_input.setPlaceholderText("Send input to the running program")
            self.console_submit_button.setText("Send")
        else:
            self.console_input.setPlaceholderText("Program input is unavailable until interactive I/O is attached")
            self.console_submit_button.setText("Send")

    def start_debug_io_terminal(
        self,
        *,
        label: str,
        cwd: str,
        argv: list[str],
        env: dict[str, str | None],
        start_stopped: bool = False,
    ) -> int:
        command = [str(part) for part in (argv or []) if str(part)]
        if not command:
            return 0
        if self._debug_io_terminal is not None:
            self.io_layout.removeWidget(self._debug_io_terminal)
            self._debug_io_terminal.deleteLater()
            self._debug_io_terminal = None
        env_map = {str(key): str(value) for key, value in dict(env or {}).items() if value is not None}
        terminal = DebuggerIoTerminalWidget(
            argv=command,
            cwd=str(cwd or "").strip(),
            env=env_map,
            parent=self.io_host,
        )
        terminal.setObjectName("DebuggerIoTerminalWidget")
        terminal.setToolTip(str(label or "Debugger I/O"))
        self.io_layout.addWidget(terminal, 1)
        self._debug_io_terminal = terminal
        pid = int(terminal.process_id() or 0)
        if start_stopped and pid > 0:
            terminal.signal_process(pid, signal.SIGSTOP)
        self.io_host.setVisible(True)
        terminal.setFocus()
        self._ensure_debug_io_visible()
        self._refresh_console_input_affordance()
        return pid

    def send_debug_io_input(self, text: str) -> bool:
        if self._debug_io_terminal is None:
            return False
        self._debug_io_terminal.post(str(text))
        self._debug_io_terminal.setFocus()
        return True

    def debug_io_terminal_available(self) -> bool:
        return self._debug_io_terminal is not None

    def _ensure_debug_io_visible(self) -> None:
        dock = getattr(self.ide, "dock_debugger", None)
        if dock is None:
            return
        dock.show()
        dock.raise_()
        dock.setMinimumHeight(max(int(dock.minimumHeight() or 0), 220))
        try:
            dock.resize(dock.width(), max(dock.height(), 260))
        except Exception:
            pass
        self.updateGeometry()

    @staticmethod
    def _populate_variable_group(parent: QTreeWidgetItem, values: dict) -> None:
        if not isinstance(values, dict) or not values:
            parent.addChild(QTreeWidgetItem(["<empty>", ""]))
            return
        for key in sorted(values):
            parent.addChild(QTreeWidgetItem([str(key), str(values.get(key) or "")]))
