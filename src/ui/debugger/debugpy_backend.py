from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from collections.abc import Callable

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer

from .backend import DebugLaunchKind, DebugLaunchRequest, DebuggerBackend, ExecutionState
from .python_backend import normalize_breakpoint_map


class DebugpyPythonDebuggerBackend(DebuggerBackend):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.user_script_path: str | None = None
        self._state = ExecutionState.IDLE
        self._stdout_buffer = b""
        self._stderr_buffer = ""
        self._seq = 0
        self._pending_requests: dict[int, tuple[str, Callable[[bool, dict, str], None] | None]] = {}
        self._pending_breakpoints: dict[str, list[dict]] = {}
        self._applied_breakpoint_paths: set[str] = set()
        self._watch_expressions: list[str] = []
        self._current_thread_id: int | None = None
        self._current_frame_id: int | None = None
        self._current_frames: list[dict] = []
        self._exited_info: dict | None = None
        self._launch_request: DebugLaunchRequest | None = None
        self._stop_stage = 0
        self._configuration_sent = False
        self._process_error_seen = False
        self._adapter_initialized = False
        self._launch_sent = False
        self._disconnect_sent = False
        self._shutdown_expected = False
        self._last_breakpoint_signature: tuple[tuple[str, tuple[tuple[int, str, int, str], ...]], ...] | None = None
        self._target_process_id: int | None = None
        self._target_exit_poll = QTimer(self)
        self._target_exit_poll.setInterval(250)
        self._target_exit_poll.timeout.connect(self._poll_target_process_exit)
        self._shutdown_timeout = QTimer(self)
        self._shutdown_timeout.setSingleShot(True)
        self._shutdown_timeout.setInterval(1200)
        self._shutdown_timeout.timeout.connect(self._force_shutdown_adapter)

        self.process.readyReadStandardOutput.connect(self._handle_stdout_ready)
        self.process.readyReadStandardError.connect(self._handle_stderr_ready)
        self.process.finished.connect(self._handle_process_finished)
        self.process.errorOccurred.connect(self._handle_process_error)

    @property
    def state(self) -> ExecutionState:
        return self._state

    @classmethod
    def is_available(cls) -> bool:
        return importlib.util.find_spec("debugpy") is not None

    @classmethod
    def implementation_ready(cls) -> bool:
        return True

    def start_debugging(self, launch_request: DebugLaunchRequest, breakpoints: dict[str, list[dict]]) -> None:
        self.stop_debugging(clean_only=True)
        self._reset_runtime_state()
        self._pending_breakpoints = normalize_breakpoint_map(breakpoints)
        self._launch_request = self._materialize_launch_request(launch_request)

        adapter_python = sys.executable
        self._apply_state(ExecutionState.STARTING)
        self.process.setWorkingDirectory(self._launch_request.working_directory)
        self.process.setProcessEnvironment(self._build_process_environment(self._launch_request))
        self.process.start(adapter_python, ["-m", "debugpy.adapter"])
        if not self.process.waitForStarted(3000):
            self.fatalError.emit({"message": "debugpy adapter failed to start.", "traceback": ""})
            self._cleanup_temp_files()
            self._apply_state(ExecutionState.IDLE)
            return
        self._send_initialize_request()

    def stop_debugging(self, clean_only: bool = False) -> None:
        if self.process.state() != QProcess.NotRunning:
            self._apply_state(ExecutionState.STOPPING)
            self._send_disconnect_request(terminate_debuggee=self._debuggee_is_running())
            if not self.process.waitForFinished(700):
                self.process.terminate()
                if not self.process.waitForFinished(1200):
                    self.process.kill()
                    self.process.waitForFinished()
        if clean_only:
            self._reset_runtime_state()
            self._cleanup_temp_files()
            self._apply_state(ExecutionState.IDLE)

    def request_stop(self) -> int:
        if self.process.state() == QProcess.NotRunning:
            return 0
        self._apply_state(ExecutionState.STOPPING)
        next_stage, action = self._stop_plan(self._stop_stage, self._state)
        self._stop_stage = next_stage
        if action == "terminate":
            self._send_request("terminate", {}, callback=None)
        elif action == "disconnect":
            self._send_disconnect_request(terminate_debuggee=self._debuggee_is_running())
        else:
            self.process.kill()
        return next_stage

    def set_breakpoints(self, breakpoints: dict[str, list[dict]]) -> None:
        self._pending_breakpoints = normalize_breakpoint_map(breakpoints)
        if self.process.state() != QProcess.Running:
            return
        self._apply_breakpoints(callback=self._emit_breakpoints_set)

    def send_command(self, action: str, extra: dict | None = None) -> bool:
        if self.process.state() != QProcess.Running:
            return False
        if action == "set_watches":
            expressions = [str(expr or "").strip() for expr in ((extra or {}).get("expressions") or []) if str(expr or "").strip()]
            self._watch_expressions = expressions
            self._refresh_watch_values()
            return True
        if action == "evaluate":
            expression = str((extra or {}).get("expression") or "").strip()
            self._evaluate_expression(expression, emit_signal=True)
            return True
        mapping = {
            "continue": ("continue", self._continue_arguments()),
            "next": ("next", self._thread_arguments()),
            "step": ("stepIn", self._thread_arguments()),
            "pause": ("pause", self._thread_arguments()),
            "quit": ("disconnect", {"terminateDebuggee": True}),
        }
        target = mapping.get(str(action or "").strip())
        if target is None:
            return False
        command, arguments = target
        self._send_request(command, arguments, callback=None)
        if action in {"continue", "next", "step"}:
            self._apply_state(ExecutionState.RUNNING)
        return True

    def _reset_runtime_state(self) -> None:
        self._stdout_buffer = b""
        self._stderr_buffer = ""
        self._seq = 0
        self._pending_requests.clear()
        self._applied_breakpoint_paths.clear()
        self._current_thread_id = None
        self._current_frame_id = None
        self._current_frames = []
        self._watch_expressions = []
        self._exited_info = None
        self._launch_request = None
        self._stop_stage = 0
        self._configuration_sent = False
        self._process_error_seen = False
        self._adapter_initialized = False
        self._launch_sent = False
        self._disconnect_sent = False
        self._shutdown_expected = False
        self._last_breakpoint_signature = None
        self._target_process_id = None
        self._target_exit_poll.stop()
        self._shutdown_timeout.stop()

    def _materialize_launch_request(self, launch_request: DebugLaunchRequest) -> DebugLaunchRequest:
        request = DebugLaunchRequest(
            file_path=str(launch_request.file_path or ""),
            source_text=str(launch_request.source_text or ""),
            launch_kind=launch_request.launch_kind,
            module_name=str(launch_request.module_name or ""),
            interpreter=str(launch_request.interpreter or "").strip() or sys.executable,
            working_directory=str(launch_request.working_directory or "").strip(),
            arguments=tuple(str(arg) for arg in launch_request.arguments),
            environment=dict(launch_request.environment),
            just_my_code=bool(launch_request.just_my_code),
            use_source_snapshot=bool(launch_request.use_source_snapshot),
        )
        target_script_path = request.file_path
        if request.launch_kind == DebugLaunchKind.SCRIPT and (request.use_source_snapshot or not target_script_path):
            user_tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py", encoding="utf-8")
            user_tmp.write(request.source_text)
            user_tmp.close()
            self.user_script_path = user_tmp.name
            target_script_path = self.user_script_path
            request.file_path = target_script_path
        else:
            self.user_script_path = None
        request.working_directory = self._resolved_working_directory(request, target_script_path)
        return request

    def _build_process_environment(self, launch_request: DebugLaunchRequest) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        for key, value in launch_request.environment.items():
            env.insert(str(key), str(value))
        return env

    def _send_initialize_request(self) -> None:
        args = {
            "adapterID": "pytpo",
            "clientID": "pytpo",
            "clientName": "PyTPO",
            "pathFormat": "path",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "supportsVariableType": True,
            "supportsRunInTerminalRequest": False,
            "supportsArgsCanBeInterpretedByShell": False,
        }
        self._send_request("initialize", args, self._handle_initialize_response)

    def _handle_initialize_response(self, success: bool, body: dict, message: str) -> None:
        if not success:
            self.fatalError.emit({"message": message or "Failed to initialize debugpy adapter.", "traceback": ""})
            return
        self._send_launch_request()

    def _send_launch_request(self) -> None:
        request = self._launch_request
        if request is None:
            self.fatalError.emit({"message": "Missing debug launch request.", "traceback": ""})
            return
        args = {
            "noDebug": False,
            "console": "internalConsole",
            "cwd": request.working_directory,
            "python": [request.interpreter],
            "args": list(request.arguments),
            "env": dict(request.environment),
            "justMyCode": bool(request.just_my_code),
            "redirectOutput": True,
        }
        if request.launch_kind == DebugLaunchKind.MODULE:
            args["module"] = str(request.module_name or "")
        else:
            args["program"] = str(request.file_path or "")
        self._launch_sent = True
        self._send_request("launch", args, self._handle_launch_response)
        self.started.emit(
            {
                "file": str(request.file_path or ""),
                "module": str(request.module_name or ""),
            }
        )
        self._maybe_send_configuration()

    def _handle_launch_response(self, success: bool, body: dict, message: str) -> None:
        if not success:
            self.fatalError.emit({"message": message or "debugpy launch failed.", "traceback": ""})
            return
        return

    def _send_exception_and_configuration(self) -> None:
        self._emit_breakpoints_set()
        self._send_request(
            "setExceptionBreakpoints",
            {"filters": ["uncaught"]},
            lambda _success, _body, _message: self._send_configuration_done(),
        )

    def _send_configuration_done(self) -> None:
        if self._configuration_sent:
            return
        self._configuration_sent = True
        self._send_request("configurationDone", {}, self._handle_configuration_done)

    def _handle_configuration_done(self, success: bool, body: dict, message: str) -> None:
        if not success:
            self.fatalError.emit({"message": message or "debugpy configuration failed.", "traceback": ""})
            return
        self._apply_state(ExecutionState.RUNNING)
        if self._watch_expressions:
            self._refresh_watch_values()

    def _apply_breakpoints(self, callback: Callable[[], None] | None) -> None:
        paths = sorted(set(self._applied_breakpoint_paths) | set(self._pending_breakpoints))
        if not paths:
            if callback is not None:
                callback()
            return

        def apply_next(index: int) -> None:
            if index >= len(paths):
                self._applied_breakpoint_paths = set(self._pending_breakpoints)
                if callback is not None:
                    callback()
                return
            file_path = paths[index]
            specs = self._pending_breakpoints.get(file_path, [])
            dap_breakpoints = [{"line": int(item.get("line") or 0), "condition": str(item.get("condition") or "").strip() or None} for item in specs if int(item.get("line") or 0) > 0]
            args = {
                "source": {"path": file_path},
                "lines": [bp["line"] for bp in dap_breakpoints],
                "breakpoints": dap_breakpoints,
                "sourceModified": False,
            }
            self._send_request("setBreakpoints", args, lambda _s, _b, _m, i=index + 1: apply_next(i))

        apply_next(0)

    def _handle_stdout_ready(self) -> None:
        self._stdout_buffer += bytes(self.process.readAllStandardOutput())
        self._consume_dap_messages()

    def _handle_stderr_ready(self) -> None:
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr_buffer += data
        while "\n" in self._stderr_buffer:
            line, self._stderr_buffer = self._stderr_buffer.split("\n", 1)
            if line:
                self.stderrReceived.emit(line)

    def _consume_dap_messages(self) -> None:
        while True:
            header_end = self._stdout_buffer.find(b"\r\n\r\n")
            if header_end < 0:
                return
            header_block = self._stdout_buffer[:header_end].decode("utf-8", errors="replace")
            content_length = 0
            for raw_line in header_block.split("\r\n"):
                name, _, value = raw_line.partition(":")
                if name.lower() == "content-length":
                    try:
                        content_length = int(value.strip())
                    except Exception:
                        content_length = 0
                    break
            body_start = header_end + 4
            body_end = body_start + max(0, int(content_length))
            if len(self._stdout_buffer) < body_end:
                return
            body = self._stdout_buffer[body_start:body_end]
            self._stdout_buffer = self._stdout_buffer[body_end:]
            try:
                message = json.loads(body.decode("utf-8", errors="replace"))
            except Exception as exc:
                self.protocolError.emit(f"DAP decode error: {exc}")
                continue
            self._handle_dap_message(message)

    def _handle_dap_message(self, message: dict) -> None:
        kind = str(message.get("type") or "")
        if kind == "response":
            request_seq = int(message.get("request_seq") or 0)
            pending = self._pending_requests.pop(request_seq, None)
            if pending is None:
                return
            _command, callback = pending
            if callback is not None:
                callback(bool(message.get("success")), dict(message.get("body") or {}), str(message.get("message") or ""))
            return
        if kind == "event":
            self._handle_event(str(message.get("event") or ""), dict(message.get("body") or {}))

    def _handle_event(self, event: str, body: dict) -> None:
        if event == "initialized":
            self._adapter_initialized = True
            self._maybe_send_configuration()
            return
        if event == "output":
            category = str(body.get("category") or "").strip().lower()
            text = str(body.get("output") or "")
            if not text or category == "telemetry":
                return
            if category == "stderr":
                self.stderrReceived.emit(text.rstrip("\n"))
            else:
                for line in text.splitlines() or [text]:
                    self.stdoutReceived.emit(line)
            return
        if event == "process":
            process_id = int(body.get("systemProcessId") or 0)
            if process_id > 0:
                self._target_process_id = process_id
                if not self._target_exit_poll.isActive():
                    self._target_exit_poll.start()
            return
        if event == "continued":
            self._apply_state(ExecutionState.RUNNING)
            return
        if event == "exited":
            self._exited_info = {"exit_code": int(body.get("exitCode") or 0), "exit_status": "finished"}
            self._shutdown_expected = True
            self._send_disconnect_request(terminate_debuggee=False)
            return
        if event == "terminated":
            self._shutdown_expected = True
            self._send_disconnect_request(terminate_debuggee=False)
            return
        if event == "stopped":
            self._apply_state(ExecutionState.PAUSED)
            self._current_thread_id = int(body.get("threadId") or 0) or self._current_thread_id
            self._handle_stopped_event(body)
            return

    def _maybe_send_configuration(self) -> None:
        if not self._adapter_initialized or not self._launch_sent or self._configuration_sent:
            return
        self._apply_breakpoints(callback=self._send_exception_and_configuration)

    def _handle_stopped_event(self, body: dict) -> None:
        thread_id = int(body.get("threadId") or 0) or int(self._current_thread_id or 0)
        if thread_id <= 0:
            self.fatalError.emit({"message": "debugpy stopped without a thread id.", "traceback": ""})
            return
        self._current_thread_id = thread_id
        self._send_request("stackTrace", {"threadId": thread_id}, lambda success, data, message: self._handle_stack_trace_response(success, data, message, body))

    def _handle_stack_trace_response(self, success: bool, body: dict, message: str, stopped_body: dict) -> None:
        if not success:
            self.fatalError.emit({"message": message or "Failed to fetch debug stack.", "traceback": ""})
            return
        raw_frames = list(body.get("stackFrames") or [])
        frames: list[dict] = []
        for raw in raw_frames:
            if not isinstance(raw, dict):
                continue
            source = raw.get("source") or {}
            frames.append(
                {
                    "id": int(raw.get("id") or 0),
                    "file": str((source.get("path") or "")),
                    "line": int(raw.get("line") or 0),
                    "column": int(raw.get("column") or 1),
                    "function": str(raw.get("name") or "<module>"),
                    "locals": {},
                    "globals": {},
                }
            )
        if not frames:
            self.fatalError.emit({"message": "debugpy returned no stack frames.", "traceback": ""})
            return
        self._current_frames = frames
        self._current_frame_id = int(frames[0].get("id") or 0)
        self._populate_frame_scopes(0, stopped_body)

    def _populate_frame_scopes(self, index: int, stopped_body: dict) -> None:
        if index >= len(self._current_frames):
            self._finalize_stopped_payload(stopped_body)
            return
        frame = self._current_frames[index]
        frame_id = int(frame.get("id") or 0)
        self._send_request("scopes", {"frameId": frame_id}, lambda success, body, message, i=index: self._handle_scopes_response(success, body, message, i, stopped_body))

    def _handle_scopes_response(self, success: bool, body: dict, message: str, index: int, stopped_body: dict) -> None:
        if not success:
            self._populate_frame_scopes(index + 1, stopped_body)
            return
        scopes = [item for item in (body.get("scopes") or []) if isinstance(item, dict)]
        target = {"locals": None, "globals": None}
        for scope in scopes:
            name = str(scope.get("name") or "").strip().lower()
            if name == "locals" and target["locals"] is None:
                target["locals"] = int(scope.get("variablesReference") or 0)
            elif name == "globals" and target["globals"] is None:
                target["globals"] = int(scope.get("variablesReference") or 0)
        self._load_scope_variables(index, target, "locals", stopped_body)

    def _load_scope_variables(self, index: int, refs: dict[str, int | None], scope_name: str, stopped_body: dict) -> None:
        ref = int(refs.get(scope_name) or 0)
        if ref <= 0:
            next_scope = "globals" if scope_name == "locals" else None
            if next_scope is None:
                self._populate_frame_scopes(index + 1, stopped_body)
            else:
                self._load_scope_variables(index, refs, next_scope, stopped_body)
            return
        self._send_request(
            "variables",
            {"variablesReference": ref},
            lambda success, body, message, i=index, r=refs, name=scope_name: self._handle_variables_response(success, body, message, i, r, name, stopped_body),
        )

    def _handle_variables_response(
        self,
        success: bool,
        body: dict,
        message: str,
        index: int,
        refs: dict[str, int | None],
        scope_name: str,
        stopped_body: dict,
    ) -> None:
        if success:
            values: dict[str, str] = {}
            for item in (body.get("variables") or []):
                if not isinstance(item, dict):
                    continue
                values[str(item.get("name") or "")] = str(item.get("value") or "")
            self._current_frames[index][scope_name] = values
        next_scope = "globals" if scope_name == "locals" else None
        if next_scope is None:
            self._populate_frame_scopes(index + 1, stopped_body)
        else:
            self._load_scope_variables(index, refs, next_scope, stopped_body)

    def _finalize_stopped_payload(self, stopped_body: dict) -> None:
        if self._watch_expressions:
            self._collect_watch_values(
                list(self._watch_expressions),
                [],
                lambda watches: self._emit_paused_payload(stopped_body, watches),
            )
        else:
            self._emit_paused_payload(stopped_body, [])

        reason = str(stopped_body.get("reason") or "").strip().lower()
        if reason == "exception":
            self._request_exception_info()

    def _emit_paused_payload(self, stopped_body: dict, watches: list[dict]) -> None:
        top_frame = self._current_frames[0] if self._current_frames else {}
        self.paused.emit(
            {
                "file": str(top_frame.get("file") or ""),
                "line": int(top_frame.get("line") or 0),
                "function": str(top_frame.get("function") or ""),
                "locals": dict(top_frame.get("locals") or {}),
                "globals": dict(top_frame.get("globals") or {}),
                "stack": [
                    {
                        "file": str(frame.get("file") or ""),
                        "line": int(frame.get("line") or 0),
                        "function": str(frame.get("function") or ""),
                        "locals": dict(frame.get("locals") or {}),
                        "globals": dict(frame.get("globals") or {}),
                    }
                    for frame in self._current_frames
                ],
                "watches": watches,
                "reason": str(stopped_body.get("reason") or ""),
            }
        )

    def _request_exception_info(self) -> None:
        thread_id = int(self._current_thread_id or 0)
        if thread_id <= 0:
            return
        self._send_request("exceptionInfo", {"threadId": thread_id}, self._handle_exception_info)

    def _handle_exception_info(self, success: bool, body: dict, message: str) -> None:
        if not success:
            return
        details = dict(body.get("details") or {})
        text = str(body.get("description") or details.get("message") or body.get("breakMode") or "Exception")
        exc_type = str(body.get("exceptionId") or details.get("typeName") or "Exception")
        top_frame = self._current_frames[0] if self._current_frames else {}
        self.exceptionRaised.emit(
            {
                "file": str(top_frame.get("file") or ""),
                "line": int(top_frame.get("line") or 0),
                "type": exc_type,
                "message": text,
                "traceback": "",
            }
        )

    def _refresh_watch_values(self) -> None:
        if not self._watch_expressions:
            self.watchValuesUpdated.emit({"watches": []})
            return
        self._collect_watch_values(list(self._watch_expressions), [], lambda watches: self.watchValuesUpdated.emit({"watches": watches}))

    def _collect_watch_values(
        self,
        expressions: list[str],
        results: list[dict],
        done: Callable[[list[dict]], None],
    ) -> None:
        if not expressions:
            done(results)
            return
        expr = str(expressions[0] or "").strip()
        self._evaluate_expression(
            expr,
            emit_signal=False,
            done=lambda result: self._finish_watch_evaluation(expressions[1:], results, result, done),
        )

    def _finish_watch_evaluation(
        self,
        remaining: list[str],
        results: list[dict],
        result: dict,
        done: Callable[[list[dict]], None],
    ) -> None:
        results.append(result)
        self._collect_watch_values(remaining, results, done)

    def _evaluate_expression(
        self,
        expression: str,
        *,
        emit_signal: bool,
        done: Callable[[dict], None] | None = None,
    ) -> None:
        expr = str(expression or "").strip()
        if not expr:
            result = {"expression": expr, "status": "error", "error": "Missing expression"}
            if emit_signal:
                self.evaluationResult.emit(result)
            if done is not None:
                done(result)
            return
        if int(self._current_frame_id or 0) <= 0:
            result = {"expression": expr, "status": "error", "error": "Program is not paused"}
            if emit_signal:
                self.evaluationResult.emit(result)
            if done is not None:
                done(result)
            return
        args = {"expression": expr, "frameId": int(self._current_frame_id or 0), "context": "watch" if not emit_signal else "repl"}
        self._send_request("evaluate", args, lambda success, body, message: self._handle_evaluate_response(expr, success, body, message, emit_signal, done))

    def _handle_evaluate_response(
        self,
        expression: str,
        success: bool,
        body: dict,
        message: str,
        emit_signal: bool,
        done: Callable[[dict], None] | None,
    ) -> None:
        if success:
            result = {"expression": expression, "status": "ok", "value": str(body.get("result") or "")}
        else:
            result = {"expression": expression, "status": "error", "error": message or "Evaluation failed"}
        if emit_signal:
            self.evaluationResult.emit(result)
        if done is not None:
            done(result)

    def _thread_arguments(self) -> dict:
        return {"threadId": max(1, int(self._current_thread_id or 1))}

    def _continue_arguments(self) -> dict:
        return {"threadId": max(1, int(self._current_thread_id or 1)), "singleThread": False}

    def _send_disconnect_request(self, *, terminate_debuggee: bool) -> None:
        if self.process.state() != QProcess.Running:
            return
        if self._disconnect_sent:
            return
        self._disconnect_sent = True
        self._shutdown_expected = True
        self._shutdown_timeout.start()
        self._send_request("disconnect", {"terminateDebuggee": bool(terminate_debuggee)}, callback=None)

    def _debuggee_is_running(self) -> bool:
        process_id = int(self._target_process_id or 0)
        if process_id > 0:
            return self._process_exists(process_id)
        return self._exited_info is None

    def _poll_target_process_exit(self) -> None:
        process_id = int(self._target_process_id or 0)
        if process_id <= 0:
            self._target_exit_poll.stop()
            return
        if self._exited_info is not None or self._disconnect_sent or self.process.state() != QProcess.Running:
            self._target_exit_poll.stop()
            return
        if self._process_exists(process_id):
            return
        self._target_exit_poll.stop()
        self._exited_info = {"exit_code": 0, "exit_status": "finished"}
        self._shutdown_expected = True
        self._send_disconnect_request(terminate_debuggee=False)

    def _force_shutdown_adapter(self) -> None:
        if self.process.state() == QProcess.NotRunning:
            return
        self.process.terminate()
        if not self.process.waitForFinished(250):
            self.process.kill()

    def _send_request(
        self,
        command: str,
        arguments: dict,
        callback: Callable[[bool, dict, str], None] | None,
    ) -> int:
        self._seq += 1
        seq = self._seq
        message = {
            "seq": seq,
            "type": "request",
            "command": str(command or ""),
            "arguments": dict(arguments or {}),
        }
        payload = json.dumps(message).encode("utf-8")
        packet = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
        self._pending_requests[seq] = (str(command or ""), callback)
        self.process.write(packet)
        return seq

    def _apply_state(self, state: ExecutionState) -> None:
        if self._state == state:
            return
        self._state = state
        self.stateChanged.emit(state.value)

    def _handle_process_error(self, error: QProcess.ProcessError) -> None:
        if self._shutdown_expected or self._disconnect_sent or self._exited_info is not None:
            return
        if self._process_error_seen:
            return
        self._process_error_seen = True
        self.fatalError.emit({"message": self._process_error_message(error), "traceback": ""})

    def _handle_process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._consume_dap_messages()
        if self._stderr_buffer.strip():
            self.stderrReceived.emit(self._stderr_buffer.strip())
            self._stderr_buffer = ""
        process_info = dict(self._exited_info or {})
        process_info.setdefault("exit_code", int(exit_code or 0))
        process_info.setdefault("exit_status", "crashed" if exit_status == QProcess.CrashExit else "finished")
        self.processEnded.emit(process_info)
        self._cleanup_temp_files()
        self._target_exit_poll.stop()
        self._shutdown_timeout.stop()
        self._apply_state(ExecutionState.IDLE)
        self._stop_stage = 0
        self.finished.emit()

    def _emit_breakpoints_set(self) -> None:
        signature = self._breakpoint_signature(self._pending_breakpoints)
        if signature == self._last_breakpoint_signature:
            return
        self._last_breakpoint_signature = signature
        self.breakpointsSet.emit({"files": sorted(self._pending_breakpoints)})

    def _cleanup_temp_files(self) -> None:
        if self.user_script_path and os.path.exists(self.user_script_path):
            try:
                os.unlink(self.user_script_path)
            except OSError:
                pass
        self.user_script_path = None

    @staticmethod
    def _stop_plan(current_stage: int, state: ExecutionState) -> tuple[int, str]:
        stage = max(0, int(current_stage or 0))
        if stage <= 0:
            return 1, "disconnect"
        if stage == 1:
            return 2, "terminate"
        return 3, "kill"

    @staticmethod
    def _process_exists(process_id: int) -> bool:
        try:
            os.kill(int(process_id), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _normalized_existing_directories(paths: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            text = str(raw or "").strip()
            if not text:
                continue
            resolved = os.path.normcase(os.path.abspath(os.path.expanduser(text)))
            if resolved in seen or not os.path.isdir(resolved):
                continue
            seen.add(resolved)
            out.append(resolved)
        return out

    @classmethod
    def _resolved_working_directory(cls, launch_request: DebugLaunchRequest, target_script_path: str) -> str:
        candidates = [
            str(launch_request.working_directory or "").strip(),
            os.path.dirname(str(target_script_path or "").strip()),
            os.path.dirname(str(launch_request.file_path or "").strip()),
            os.getcwd(),
        ]
        roots = cls._normalized_existing_directories(candidates)
        return roots[0] if roots else os.getcwd()

    @staticmethod
    def _process_error_message(error: QProcess.ProcessError) -> str:
        mapping = {
            QProcess.ProcessError.FailedToStart: "debugpy adapter failed to start.",
            QProcess.ProcessError.Crashed: "debugpy adapter crashed.",
            QProcess.ProcessError.Timedout: "debugpy adapter timed out.",
            QProcess.ProcessError.WriteError: "debugpy adapter write failed.",
            QProcess.ProcessError.ReadError: "debugpy adapter read failed.",
        }
        return mapping.get(error, "debugpy adapter failed.")

    @staticmethod
    def _breakpoint_signature(
        breakpoints: dict[str, list[dict]],
    ) -> tuple[tuple[str, tuple[tuple[int, str, int, str], ...]], ...]:
        out: list[tuple[str, tuple[tuple[int, str, int, str], ...]]] = []
        for file_path in sorted(breakpoints):
            rows: list[tuple[int, str, int, str]] = []
            for item in (breakpoints.get(file_path) or []):
                if not isinstance(item, dict):
                    continue
                rows.append(
                    (
                        int(item.get("line") or 0),
                        str(item.get("condition") or "").strip(),
                        int(item.get("hit_count") or 0),
                        str(item.get("log_message") or "").strip(),
                    )
                )
            out.append((str(file_path), tuple(rows)))
        return tuple(out)
