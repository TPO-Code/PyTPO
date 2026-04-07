from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QProcess

from .backend import DebugLaunchRequest, DebuggerBackend, ExecutionState
from .python_backend import normalize_breakpoint_map
from .terminal_bridge import DebugTerminalBridge

_NATIVE_DEBUG_LAUNCH_HELPER = str(Path(__file__).with_name("native_debug_launch_helper.py"))


@dataclass(slots=True)
class _CargoArtifact:
    executable: str
    target_name: str
    target_kinds: tuple[str, ...]


class LldbDapDebuggerBackend(DebuggerBackend):
    def __init__(self, parent=None, *, ide=None):
        super().__init__(parent)
        self.ide = ide
        self.process = QProcess(self)
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
        self._launch_request: DebugLaunchRequest | None = None
        self._exited_info: dict | None = None
        self._adapter_initialized = False
        self._launch_sent = False
        self._configuration_sent = False
        self._disconnect_sent = False
        self._process_error_seen = False
        self._shutdown_expected = False
        self._stop_stage = 0
        self._last_breakpoint_signature: tuple[tuple[str, tuple[tuple[int, str, int, str], ...]], ...] | None = None
        self._terminal_bridge = DebugTerminalBridge(parent) if parent is not None else None
        self._adapter_path = ""

        self.process.readyReadStandardOutput.connect(self._handle_stdout_ready)
        self.process.readyReadStandardError.connect(self._handle_stderr_ready)
        self.process.finished.connect(self._handle_process_finished)
        self.process.errorOccurred.connect(self._handle_process_error)

    @property
    def state(self) -> ExecutionState:
        return self._state

    @classmethod
    def adapter_path(cls) -> str:
        for candidate in cls._adapter_candidates():
            resolved = str(shutil.which(candidate) or "").strip()
            if resolved:
                return resolved
        return ""

    @classmethod
    def is_available(cls) -> bool:
        return bool(cls.adapter_path())

    @staticmethod
    def _adapter_candidates() -> tuple[str, ...]:
        names = ["lldb-dap", "lldb-vscode"]
        discovered: list[str] = []
        seen: set[str] = set(names)
        for raw_dir in str(os.environ.get("PATH") or "").split(os.pathsep):
            directory = str(raw_dir or "").strip()
            if not directory:
                continue
            try:
                entries = sorted(Path(directory).glob("lldb-vscode-*"), reverse=True)
            except Exception:
                continue
            for entry in entries:
                if not entry.is_file() or not os.access(entry, os.X_OK):
                    continue
                name = entry.name
                if name in seen:
                    continue
                seen.add(name)
                discovered.append(name)
        return tuple(names + discovered)

    def start_debugging(self, launch_request: DebugLaunchRequest, breakpoints: dict[str, list[dict]]) -> None:
        self.stop_debugging(clean_only=True)
        self._reset_runtime_state()
        self._pending_breakpoints = normalize_breakpoint_map(breakpoints)
        self._launch_request = self._materialize_launch_request(launch_request)

        adapter_path = self.adapter_path()
        if not adapter_path:
            self.fatalError.emit({"message": "No supported LLDB debug adapter was found in PATH.", "traceback": ""})
            self._apply_state(ExecutionState.IDLE)
            return

        self._adapter_path = adapter_path
        if not self._prepare_program_path():
            self._apply_state(ExecutionState.IDLE)
            return

        self._apply_state(ExecutionState.STARTING)
        self.process.setWorkingDirectory(self._launch_request.working_directory)
        self.process.setProcessEnvironment(self._build_process_environment(self._launch_request))
        self.process.start(adapter_path, [])
        if not self.process.waitForStarted(3000):
            self.fatalError.emit({"message": "The LLDB debug adapter failed to start.", "traceback": ""})
            self._apply_state(ExecutionState.IDLE)
            return
        self._send_initialize_request()

    def stop_debugging(self, clean_only: bool = False) -> None:
        if self.process.state() != QProcess.NotRunning:
            self._apply_state(ExecutionState.STOPPING)
            self._send_disconnect_request(terminate_debuggee=True)
            if not self.process.waitForFinished(700):
                self.process.terminate()
                if not self.process.waitForFinished(1200):
                    self.process.kill()
                    self.process.waitForFinished()
        if clean_only:
            self._reset_runtime_state()
            self._apply_state(ExecutionState.IDLE)

    def request_stop(self) -> int:
        if self.process.state() == QProcess.NotRunning:
            return 0
        self._apply_state(ExecutionState.STOPPING)
        next_stage, action = self._stop_plan(self._stop_stage)
        self._stop_stage = next_stage
        if action == "disconnect":
            self._send_disconnect_request(terminate_debuggee=True)
        elif action == "terminate":
            self._send_request("terminate", {}, callback=None)
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
            expressions = [
                str(expr or "").strip()
                for expr in ((extra or {}).get("expressions") or [])
                if str(expr or "").strip()
            ]
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

    def send_stdin(self, text: str) -> bool:
        if self._state not in {ExecutionState.STARTING, ExecutionState.RUNNING}:
            return False
        bridge = self._terminal_bridge
        if bridge is None:
            return False
        return bridge.send_input(text)

    def supports_stdin(self) -> bool:
        bridge = self._terminal_bridge
        return bool(
            self._state in {ExecutionState.STARTING, ExecutionState.RUNNING}
            and bridge is not None
            and bridge.available()
        )

    def _reset_runtime_state(self) -> None:
        self._stdout_buffer = b""
        self._stderr_buffer = ""
        self._seq = 0
        self._pending_requests.clear()
        self._applied_breakpoint_paths.clear()
        self._watch_expressions = []
        self._current_thread_id = None
        self._current_frame_id = None
        self._current_frames = []
        self._launch_request = None
        self._exited_info = None
        self._adapter_initialized = False
        self._launch_sent = False
        self._configuration_sent = False
        self._disconnect_sent = False
        self._process_error_seen = False
        self._shutdown_expected = False
        self._stop_stage = 0
        self._last_breakpoint_signature = None
        self._adapter_path = ""

    @staticmethod
    def _materialize_launch_request(launch_request: DebugLaunchRequest) -> DebugLaunchRequest:
        request = DebugLaunchRequest(
            file_path=str(launch_request.file_path or ""),
            source_text=str(launch_request.source_text or ""),
            launch_kind=launch_request.launch_kind,
            module_name=str(launch_request.module_name or ""),
            interpreter=str(launch_request.interpreter or ""),
            program_path=str(launch_request.program_path or ""),
            working_directory=str(launch_request.working_directory or "").strip(),
            arguments=tuple(str(arg) for arg in launch_request.arguments),
            environment=dict(launch_request.environment),
            build_command=tuple(str(arg) for arg in launch_request.build_command),
            target_name=str(launch_request.target_name or "").strip(),
            target_kind=str(launch_request.target_kind or "").strip(),
            language=str(launch_request.language or "").strip(),
            just_my_code=False,
            use_source_snapshot=False,
        )
        request.working_directory = LldbDapDebuggerBackend._resolved_working_directory(request)
        return request

    def _prepare_program_path(self) -> bool:
        request = self._launch_request
        if request is None:
            self.fatalError.emit({"message": "Missing native launch request.", "traceback": ""})
            return False
        program_path = str(request.program_path or "").strip()
        if program_path and os.path.isfile(program_path):
            request.program_path = program_path
            return True
        build_command = tuple(str(arg) for arg in request.build_command if str(arg).strip())
        if not build_command:
            self.fatalError.emit({"message": "No executable or build command was provided for native debugging.", "traceback": ""})
            return False
        built_program = self._build_program_from_cargo(request)
        if not built_program:
            return False
        request.program_path = built_program
        return True

    def _build_program_from_cargo(self, request: DebugLaunchRequest) -> str:
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in request.environment.items()})
        try:
            proc = subprocess.run(
                list(request.build_command),
                cwd=request.working_directory or None,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            self.fatalError.emit({"message": "cargo was not found in PATH.", "traceback": ""})
            return ""
        except Exception as exc:
            self.fatalError.emit({"message": f"Rust build failed to start: {exc}", "traceback": ""})
            return ""

        artifacts, rendered = self._parse_cargo_json_stream(proc.stdout)
        for line in rendered:
            self.stderrReceived.emit(line)
        stderr_lines = [line.rstrip() for line in proc.stderr.splitlines() if line.rstrip()]
        for line in stderr_lines:
            self.stderrReceived.emit(line)
        if proc.returncode != 0:
            self.fatalError.emit({"message": "Cargo build failed.", "traceback": proc.stderr.strip()})
            return ""
        program_path = self._select_artifact_path(
            artifacts,
            target_name=request.target_name,
            target_kind=request.target_kind,
        )
        if not program_path:
            self.fatalError.emit(
                {
                    "message": "Cargo build succeeded, but no debuggable executable could be resolved.",
                    "traceback": "",
                }
            )
            return ""
        return program_path

    @classmethod
    def _parse_cargo_json_stream(cls, text: str) -> tuple[list[_CargoArtifact], list[str]]:
        artifacts: list[_CargoArtifact] = []
        rendered: list[str] = []
        for raw_line in str(text or "").splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                rendered.append(line)
                continue
            if not isinstance(payload, dict):
                continue
            reason = str(payload.get("reason") or "").strip()
            if reason == "compiler-message":
                message = payload.get("message") or {}
                rendered_text = str(message.get("rendered") or "").strip()
                if rendered_text:
                    rendered.extend(item.rstrip() for item in rendered_text.splitlines() if item.rstrip())
                continue
            if reason != "compiler-artifact":
                continue
            executable = str(payload.get("executable") or "").strip()
            target = payload.get("target") or {}
            target_name = str(target.get("name") or "").strip()
            raw_kinds = target.get("kind") or []
            target_kinds = tuple(str(kind or "").strip() for kind in raw_kinds if str(kind or "").strip())
            if executable:
                artifacts.append(
                    _CargoArtifact(
                        executable=executable,
                        target_name=target_name,
                        target_kinds=target_kinds,
                    )
                )
        return artifacts, rendered

    @staticmethod
    def _select_artifact_path(
        artifacts: list[_CargoArtifact],
        *,
        target_name: str = "",
        target_kind: str = "",
    ) -> str:
        name_filter = str(target_name or "").strip()
        kind_filter = str(target_kind or "").strip().lower()
        candidates = list(artifacts)
        if name_filter:
            named = [item for item in candidates if item.target_name == name_filter]
            if named:
                candidates = named
        if kind_filter:
            typed = [item for item in candidates if kind_filter in {kind.lower() for kind in item.target_kinds}]
            if typed:
                candidates = typed
        if len(candidates) == 1:
            return candidates[0].executable
        if not candidates and len(artifacts) == 1:
            return artifacts[0].executable
        if len(candidates) > 1:
            exact = [item for item in candidates if item.target_name == name_filter and kind_filter in {kind.lower() for kind in item.target_kinds}]
            if len(exact) == 1:
                return exact[0].executable
        return candidates[0].executable if len(candidates) == 1 else ""

    def _build_process_environment(self, launch_request: DebugLaunchRequest):
        from PySide6.QtCore import QProcessEnvironment

        env = QProcessEnvironment.systemEnvironment()
        for key, value in launch_request.environment.items():
            env.insert(str(key), str(value))
        return env

    def _send_initialize_request(self) -> None:
        args = {
            "adapterID": "lldb",
            "clientID": "pytpo",
            "clientName": "Barley",
            "pathFormat": "path",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "supportsVariableType": True,
            "supportsRunInTerminalRequest": True,
            "supportsArgsCanBeInterpretedByShell": False,
        }
        self._send_request("initialize", args, self._handle_initialize_response)

    def _handle_initialize_response(self, success: bool, body: dict, message: str) -> None:
        if not success:
            self.fatalError.emit({"message": message or "Failed to initialize the LLDB debug adapter.", "traceback": ""})
            return
        self._send_launch_request()

    def _send_launch_request(self) -> None:
        request = self._launch_request
        if request is None:
            self.fatalError.emit({"message": "Missing native launch request.", "traceback": ""})
            return
        if self._uses_legacy_run_in_terminal_flag():
            if self._send_legacy_attach_request(request):
                self.started.emit({"file": str(request.file_path or ""), "module": ""})
            return
        env = [f"{key}={value}" for key, value in request.environment.items()]
        args = {
            "program": str(request.program_path or ""),
            "cwd": request.working_directory,
            "args": list(request.arguments),
            "env": env,
            "stopOnEntry": False,
        }
        args["console"] = "integratedTerminal"
        self._launch_sent = True
        self._send_request("launch", args, self._handle_launch_response)
        self.started.emit({"file": str(request.file_path or ""), "module": ""})

    def _send_legacy_attach_request(self, request: DebugLaunchRequest) -> bool:
        bridge = self._terminal_bridge
        if bridge is None:
            self.fatalError.emit({"message": "Interactive I/O is unavailable for this debug session.", "traceback": ""})
            return False
        argv = [str(request.program_path or ""), *[str(arg) for arg in request.arguments]]
        argv = self._legacy_attach_argv(request)
        env_map = {str(key): str(value) for key, value in request.environment.items()}
        pid = int(
            bridge.launch(
                label=self._terminal_label(),
                cwd=request.working_directory,
                argv=argv,
                env=env_map,
            )
            or 0
        )
        if pid <= 0:
            self.fatalError.emit({"message": "Failed to start the native debug target in the debugger I/O panel.", "traceback": ""})
            return False
        self.stdoutReceived.emit(f"[debug] interactive I/O attached below: {self._terminal_label()}")
        self._launch_sent = True
        self._send_request(
            "attach",
            {
                "pid": pid,
                "program": str(request.program_path or ""),
                "waitFor": False,
            },
            self._handle_launch_response,
        )
        return True

    def _legacy_attach_argv(self, request: DebugLaunchRequest) -> list[str]:
        adapter_pid = int(self.process.processId() or 0)
        return [
            str(sys.executable or "python3"),
            _NATIVE_DEBUG_LAUNCH_HELPER,
            "--ptracer",
            str(adapter_pid),
            str(request.program_path or ""),
            *[str(arg) for arg in request.arguments],
        ]

    def _handle_launch_response(self, success: bool, body: dict, message: str) -> None:
        if not success:
            self.fatalError.emit({"message": message or "The LLDB debug adapter launch failed.", "traceback": ""})
            return

    def _maybe_send_configuration(self) -> None:
        if not self._adapter_initialized or not self._launch_sent or self._configuration_sent:
            return
        self._apply_breakpoints(callback=self._send_configuration_done)

    def _send_configuration_done(self) -> None:
        if self._configuration_sent:
            return
        self._configuration_sent = True
        self._send_request("configurationDone", {}, self._handle_configuration_done)

    def _handle_configuration_done(self, success: bool, body: dict, message: str) -> None:
        if not success:
            self.fatalError.emit({"message": message or "The LLDB debug adapter configuration failed.", "traceback": ""})
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
            dap_breakpoints = []
            for item in specs:
                try:
                    line = int(item.get("line") or 0)
                    hit_count = int(item.get("hit_count") or 0)
                except Exception:
                    continue
                if line <= 0:
                    continue
                breakpoint = {"line": line}
                condition = str(item.get("condition") or "").strip()
                log_message = str(item.get("log_message") or "").strip()
                if condition:
                    breakpoint["condition"] = condition
                if hit_count > 0:
                    breakpoint["hitCondition"] = str(hit_count)
                if log_message:
                    breakpoint["logMessage"] = log_message
                dap_breakpoints.append(breakpoint)
            args = {"source": {"path": file_path}, "breakpoints": dap_breakpoints, "sourceModified": False}
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
        if kind == "request":
            self._handle_adapter_request(message)
            return
        if kind == "event":
            self._handle_event(str(message.get("event") or ""), dict(message.get("body") or {}))

    def _handle_adapter_request(self, message: dict) -> None:
        command = str(message.get("command") or "")
        request_seq = int(message.get("seq") or 0)
        arguments = dict(message.get("arguments") or {})
        if command == "runInTerminal":
            success, body, error = self._dispatch_run_in_terminal(arguments)
            self._send_response(request_seq, command, success=success, body=body, message=error)
            return
        self._send_response(request_seq, command, success=False, body={}, message=f"Unsupported adapter request: {command}")

    def _dispatch_run_in_terminal(self, arguments: dict) -> tuple[bool, dict, str]:
        bridge = self._terminal_bridge
        request = self._launch_request
        if bridge is None or request is None:
            return False, {}, "Interactive terminal support is unavailable."
        argv = [str(item) for item in (arguments.get("args") or []) if str(item)]
        cwd = str(arguments.get("cwd") or request.working_directory or "").strip()
        env_map: dict[str, str | None] = {}
        raw_env = arguments.get("env")
        if isinstance(raw_env, dict):
            env_map = {str(key): (None if value is None else str(value)) for key, value in raw_env.items()}
        label = self._terminal_label()
        if not bridge.launch(label=label, cwd=cwd, argv=argv, env=env_map):
            return False, {}, "Failed to launch the debuggee in the IDE terminal."
        self.stdoutReceived.emit(f"[debug] interactive I/O attached below: {label}")
        return True, {}, ""

    def _terminal_label(self) -> str:
        request = self._launch_request
        if request is None:
            return "Debug I/O"
        target = str(
            request.target_name
            or os.path.basename(str(request.program_path or ""))
            or os.path.basename(str(request.file_path or ""))
            or "Native"
        )
        return f"Debug I/O: {target}"

    def _uses_legacy_run_in_terminal_flag(self) -> bool:
        name = os.path.basename(str(self._adapter_path or "")).strip().lower()
        return name.startswith("lldb-vscode")

    def _handle_event(self, event: str, body: dict) -> None:
        if event == "initialized":
            self._adapter_initialized = True
            self._maybe_send_configuration()
            return
        if event == "output":
            category = str(body.get("category") or "").strip().lower()
            text = str(body.get("output") or "")
            if not text:
                return
            target_signal = self.stderrReceived if category == "stderr" else self.stdoutReceived
            for line in text.splitlines() or [text]:
                target_signal.emit(line)
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

    def _handle_stopped_event(self, body: dict) -> None:
        thread_id = int(body.get("threadId") or 0) or int(self._current_thread_id or 0)
        if thread_id <= 0:
            self.fatalError.emit({"message": "The LLDB debug adapter stopped without a thread id.", "traceback": ""})
            return
        self._current_thread_id = thread_id
        self._send_request(
            "stackTrace",
            {"threadId": thread_id},
            lambda success, data, message: self._handle_stack_trace_response(success, data, message, body),
        )

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
                    "file": str(source.get("path") or ""),
                    "line": int(raw.get("line") or 0),
                    "column": int(raw.get("column") or 1),
                    "function": str(raw.get("name") or ""),
                    "locals": {},
                    "globals": {},
                }
            )
        if not frames:
            self.fatalError.emit({"message": "The LLDB debug adapter returned no stack frames.", "traceback": ""})
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
        self._send_request(
            "scopes",
            {"frameId": frame_id},
            lambda success, body, message, i=index: self._handle_scopes_response(success, body, message, i, stopped_body),
        )

    def _handle_scopes_response(self, success: bool, body: dict, message: str, index: int, stopped_body: dict) -> None:
        if not success:
            self._populate_frame_scopes(index + 1, stopped_body)
            return
        scopes = [item for item in (body.get("scopes") or []) if isinstance(item, dict)]
        refs = {"locals": None, "globals": None}
        for scope in scopes:
            name = str(scope.get("name") or "").strip().lower()
            if "local" in name and refs["locals"] is None:
                refs["locals"] = int(scope.get("variablesReference") or 0)
            elif "global" in name and refs["globals"] is None:
                refs["globals"] = int(scope.get("variablesReference") or 0)
        self._load_scope_variables(index, refs, "locals", stopped_body)

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
        if str(stopped_body.get("reason") or "").strip().lower() == "exception":
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
        top_frame = self._current_frames[0] if self._current_frames else {}
        self.exceptionRaised.emit(
            {
                "file": str(top_frame.get("file") or ""),
                "line": int(top_frame.get("line") or 0),
                "type": str(body.get("exceptionId") or "Exception"),
                "message": str(body.get("description") or body.get("breakMode") or "Exception"),
                "traceback": "",
            }
        )

    def _refresh_watch_values(self) -> None:
        if not self._watch_expressions:
            self.watchValuesUpdated.emit({"watches": []})
            return
        self._collect_watch_values(
            list(self._watch_expressions),
            [],
            lambda watches: self.watchValuesUpdated.emit({"watches": watches}),
        )

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
        args = {"expression": expr, "frameId": int(self._current_frame_id or 0), "context": "repl" if emit_signal else "watch"}
        self._send_request(
            "evaluate",
            args,
            lambda success, body, message: self._handle_evaluate_response(expr, success, body, message, emit_signal, done),
        )

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
        if self.process.state() != QProcess.Running or self._disconnect_sent:
            return
        self._disconnect_sent = True
        self._shutdown_expected = True
        self._send_request("disconnect", {"terminateDebuggee": bool(terminate_debuggee)}, callback=None)

    def _send_request(
        self,
        command: str,
        arguments: dict,
        callback: Callable[[bool, dict, str], None] | None,
    ) -> int:
        self._seq += 1
        seq = self._seq
        message = {"seq": seq, "type": "request", "command": str(command or ""), "arguments": dict(arguments or {})}
        packet = self._encode_protocol_message(message)
        self._pending_requests[seq] = (str(command or ""), callback)
        self.process.write(packet)
        return seq

    def _send_response(
        self,
        request_seq: int,
        command: str,
        *,
        success: bool,
        body: dict,
        message: str = "",
    ) -> None:
        payload = {
            "seq": 0,
            "type": "response",
            "request_seq": int(request_seq or 0),
            "success": bool(success),
            "command": str(command or ""),
            "body": dict(body or {}),
        }
        if message:
            payload["message"] = str(message)
        self.process.write(self._encode_protocol_message(payload))

    @staticmethod
    def _encode_protocol_message(message: dict) -> bytes:
        payload = json.dumps(message).encode("utf-8")
        return f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload

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
        self._apply_state(ExecutionState.IDLE)
        self._stop_stage = 0
        self.finished.emit()

    def _emit_breakpoints_set(self) -> None:
        signature = self._breakpoint_signature(self._pending_breakpoints)
        if signature == self._last_breakpoint_signature:
            return
        self._last_breakpoint_signature = signature
        self.breakpointsSet.emit({"files": sorted(self._pending_breakpoints)})

    @staticmethod
    def _resolved_working_directory(launch_request: DebugLaunchRequest) -> str:
        candidates = [
            str(launch_request.working_directory or "").strip(),
            os.path.dirname(str(launch_request.program_path or "").strip()),
            os.path.dirname(str(launch_request.file_path or "").strip()),
            os.getcwd(),
        ]
        for raw in candidates:
            if raw and os.path.isdir(raw):
                return os.path.abspath(raw)
        return os.getcwd()

    @staticmethod
    def _stop_plan(current_stage: int) -> tuple[int, str]:
        stage = max(0, int(current_stage or 0))
        if stage <= 0:
            return 1, "disconnect"
        if stage == 1:
            return 2, "terminate"
        return 3, "kill"

    @staticmethod
    def _process_error_message(error: QProcess.ProcessError) -> str:
        mapping = {
            QProcess.ProcessError.FailedToStart: "The LLDB debug adapter failed to start.",
            QProcess.ProcessError.Crashed: "The LLDB debug adapter crashed.",
            QProcess.ProcessError.Timedout: "The LLDB debug adapter timed out.",
            QProcess.ProcessError.WriteError: "The LLDB debug adapter write failed.",
            QProcess.ProcessError.ReadError: "The LLDB debug adapter read failed.",
        }
        return mapping.get(error, "The LLDB debug adapter failed.")

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
