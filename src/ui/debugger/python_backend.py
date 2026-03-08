from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment

from .backend import DebugLaunchKind, DebugLaunchRequest, DebuggerBackend, ExecutionState


def normalize_breakpoint_map(breakpoints: dict[str, list[dict]] | None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not isinstance(breakpoints, dict):
        return out
    for file_path, values in breakpoints.items():
        path = str(file_path or "").strip()
        if not path or not isinstance(values, list):
            continue
        normalized: list[dict] = []
        seen: set[int] = set()
        for raw in values:
            if not isinstance(raw, dict):
                continue
            try:
                line = int(raw.get("line") or 0)
                hit_count = max(0, int(raw.get("hit_count") or 0))
            except Exception:
                continue
            if line <= 0 or line in seen:
                continue
            seen.add(line)
            normalized.append(
                {
                    "line": line,
                    "condition": str(raw.get("condition") or "").strip(),
                    "hit_count": hit_count,
                    "log_message": str(raw.get("log_message") or "").strip(),
                }
            )
        if normalized:
            out[path] = normalized
    return out


_RUNNER_PATH = os.path.join(os.path.dirname(__file__), "python_debug_runner.py")
DEBUGGER_HARNESS_CODE = Path(_RUNNER_PATH).read_text(encoding="utf-8")


class BdbPythonDebuggerBackend(DebuggerBackend):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.user_script_path: str | None = None
        self._pending_breakpoints: dict[str, list[dict]] = {}
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._state = ExecutionState.IDLE
        self._stop_stage = 0
        self._process_error_seen = False

        self.process.readyReadStandardOutput.connect(self._handle_stdout_ready)
        self.process.readyReadStandardError.connect(self._handle_stderr_ready)
        self.process.finished.connect(self._handle_process_finished)
        self.process.errorOccurred.connect(self._handle_process_error)

    @property
    def state(self) -> ExecutionState:
        return self._state

    def start_debugging(self, launch_request: DebugLaunchRequest, breakpoints: dict[str, list[dict]]) -> None:
        self.stop_debugging(clean_only=True)

        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._pending_breakpoints = normalize_breakpoint_map(breakpoints)
        self._stop_stage = 0
        self._process_error_seen = False

        target_script_path = str(launch_request.file_path or "")
        if launch_request.launch_kind == DebugLaunchKind.SCRIPT and (launch_request.use_source_snapshot or not target_script_path):
            user_tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py", encoding="utf-8")
            user_tmp.write(launch_request.source_text)
            user_tmp.close()
            self.user_script_path = user_tmp.name
            target_script_path = self.user_script_path
        else:
            self.user_script_path = None

        interpreter = str(launch_request.interpreter or "").strip() or sys.executable
        working_directory = self._resolved_working_directory(launch_request, target_script_path)
        launch_roots = self._launch_roots_for_request(launch_request, target_script_path, working_directory)

        self._apply_state(ExecutionState.STARTING)
        self.process.setWorkingDirectory(working_directory)

        process_environment = QProcessEnvironment.systemEnvironment()
        for key, value in launch_request.environment.items():
            process_environment.insert(str(key), str(value))
        process_environment.insert("PYTPO_DEBUG_PROJECT_ROOTS", json.dumps(launch_roots))
        self.process.setProcessEnvironment(process_environment)

        launch_target = str(launch_request.module_name if launch_request.launch_kind == DebugLaunchKind.MODULE else target_script_path)
        self.process.start(
            interpreter,
            [_RUNNER_PATH, launch_request.launch_kind.value, launch_target, *launch_request.arguments],
        )

    def stop_debugging(self, clean_only: bool = False) -> None:
        if self.process.state() != QProcess.NotRunning:
            self._apply_state(ExecutionState.STOPPING)
            self.send_command("quit")
            if not self.process.waitForFinished(300):
                self.process.terminate()
                if not self.process.waitForFinished(700):
                    self.process.kill()
                    self.process.waitForFinished()

        if clean_only:
            self._cleanup_temp_files()
            self._apply_state(ExecutionState.IDLE)
            self._stop_stage = 0
            self._process_error_seen = False

    def request_stop(self) -> int:
        if self.process.state() == QProcess.NotRunning:
            return 0

        current_state = self._state
        self._apply_state(ExecutionState.STOPPING)
        next_stage, action = self._stop_plan(self._stop_stage, current_state)
        self._stop_stage = next_stage
        if action == "quit":
            if not self.send_command("quit"):
                self.process.terminate()
        elif action == "terminate":
            self.process.terminate()
        else:
            self.process.kill()
        return next_stage

    def set_breakpoints(self, breakpoints: dict[str, list[dict]]) -> None:
        self.send_command("set_breakpoints", {"breakpoints": normalize_breakpoint_map(breakpoints)})

    def send_command(self, action: str, extra: dict | None = None) -> bool:
        if self.process.state() != QProcess.Running:
            return False

        payload = {"action": action}
        if isinstance(extra, dict):
            payload.update(extra)

        self.process.write((json.dumps(payload) + "\n").encode("utf-8"))
        if action in {"continue", "step", "next"}:
            self._apply_state(ExecutionState.RUNNING)
        return True

    def _apply_state(self, state: ExecutionState) -> None:
        if self._state == state:
            return
        self._state = state
        self.stateChanged.emit(state.value)

    def _handle_stdout_ready(self) -> None:
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._stdout_buffer += data
        self._consume_stdout_lines()

    def _handle_stderr_ready(self) -> None:
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr_buffer += data
        self._consume_stderr_lines(final=False)

    def _consume_stdout_lines(self) -> None:
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            self.stdoutReceived.emit(line)

    def _consume_stderr_lines(self, *, final: bool) -> None:
        prefix = "__DBG__:"
        while "\n" in self._stderr_buffer:
            line, self._stderr_buffer = self._stderr_buffer.split("\n", 1)
            self._process_stderr_line(line, prefix=prefix)
        if final and self._stderr_buffer:
            self._process_stderr_line(self._stderr_buffer, prefix=prefix)
            self._stderr_buffer = ""

    def _process_stderr_line(self, line: str, *, prefix: str) -> None:
        if not line:
            return
        if line.startswith(prefix):
            payload = line[len(prefix):]
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                self.protocolError.emit(line)
                return
            self._process_debug_event(msg)
            return
        self.stderrReceived.emit(line)

    def _process_debug_event(self, msg: dict) -> None:
        if msg.get("type") != "event":
            return

        event = msg.get("event")
        data = msg.get("data") or {}

        if event == "started":
            self.started.emit(data)
            self.set_breakpoints(self._pending_breakpoints)
            self.send_command("continue")
            return
        if event == "breakpoints_set":
            self.breakpointsSet.emit(data)
            return
        if event == "stop":
            self._apply_state(ExecutionState.PAUSED)
            self.paused.emit(data)
            return
        if event == "watch_values":
            self.watchValuesUpdated.emit(data)
            return
        if event == "evaluation_result":
            self.evaluationResult.emit(data)
            return
        if event == "output":
            self.stdoutReceived.emit(str(data.get("text") or ""))
            return
        if event == "exception":
            self.exceptionRaised.emit(data)
            return
        if event == "fatal":
            self.fatalError.emit(data)
            return

    def _handle_process_error(self, error: QProcess.ProcessError) -> None:
        if self.process.state() != QProcess.NotRunning:
            return
        if self._process_error_seen:
            return
        self._process_error_seen = True
        self.fatalError.emit(
            {
                "message": self._process_error_message(error),
                "traceback": "",
            }
        )

    def _handle_process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if self._stdout_buffer:
            self.stdoutReceived.emit(self._stdout_buffer)
            self._stdout_buffer = ""
        self._consume_stderr_lines(final=True)

        status_text = "crashed" if exit_status == QProcess.CrashExit else "finished"
        self.processEnded.emit({"exit_code": int(exit_code), "exit_status": status_text})
        self._cleanup_temp_files()
        self._apply_state(ExecutionState.IDLE)
        self._stop_stage = 0
        self._process_error_seen = False
        self.finished.emit()

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
            return 1, "quit" if state == ExecutionState.PAUSED else "terminate"
        if stage == 1:
            return 2, "terminate"
        return 3, "kill"

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

    @classmethod
    def _launch_roots_for_request(
        cls,
        launch_request: DebugLaunchRequest,
        target_script_path: str,
        working_directory: str,
    ) -> list[str]:
        candidates = [
            working_directory,
            os.path.dirname(str(target_script_path or "").strip()),
            os.path.dirname(str(launch_request.file_path or "").strip()),
            os.getcwd(),
        ]
        return cls._normalized_existing_directories(candidates)

    @staticmethod
    def _process_error_message(error: QProcess.ProcessError) -> str:
        mapping = {
            QProcess.ProcessError.FailedToStart: "Debugger process failed to start.",
            QProcess.ProcessError.Crashed: "Debugger process crashed.",
            QProcess.ProcessError.Timedout: "Debugger process timed out.",
            QProcess.ProcessError.WriteError: "Debugger process write failed.",
            QProcess.ProcessError.ReadError: "Debugger process read failed.",
        }
        return mapping.get(error, "Debugger process failed.")


class PythonDebuggerBackend(DebuggerBackend):
    def __init__(self, parent=None, *, ide=None, preferred_backend: str = ""):
        super().__init__(parent)
        self.ide = ide
        self._backend_name = self._resolve_backend_name(ide=ide, preferred_backend=preferred_backend)
        self._impl = self._create_backend(self._backend_name)
        self._impl.setParent(self)
        self._forward_backend_signals()

    @property
    def state(self) -> ExecutionState:
        return self._impl.state

    def backend_name(self) -> str:
        return self._backend_name

    def start_debugging(self, launch_request: DebugLaunchRequest, breakpoints: dict[str, list[dict]]) -> None:
        self._impl.start_debugging(launch_request, breakpoints)

    def stop_debugging(self, clean_only: bool = False) -> None:
        self._impl.stop_debugging(clean_only=clean_only)

    def request_stop(self) -> int:
        return self._impl.request_stop()

    def set_breakpoints(self, breakpoints: dict[str, list[dict]]) -> None:
        self._impl.set_breakpoints(breakpoints)

    def send_command(self, action: str, extra: dict | None = None) -> bool:
        return self._impl.send_command(action, extra)

    @classmethod
    def available_backend_names(cls) -> list[str]:
        names = ["bdb"]
        if cls._debugpy_available():
            names.append("debugpy")
        return names

    @classmethod
    def configured_backend_names(cls) -> list[str]:
        return ["auto", "bdb", "debugpy"]

    @classmethod
    def _resolve_backend_name(cls, *, ide=None, preferred_backend: str = "") -> str:
        requested = str(preferred_backend or "").strip().lower()
        if not requested and ide is not None:
            getter = getattr(getattr(ide, "settings_manager", None), "get", None)
            if callable(getter):
                raw = getter("debugger.python_backend", scope_preference="ide", default="auto")
                requested = str(raw or "").strip().lower()
        if requested in {"", "auto"}:
            return "debugpy" if cls._debugpy_available() and cls._debugpy_ready() else "bdb"
        if requested == "debugpy":
            return "debugpy" if cls._debugpy_available() else "bdb"
        if requested in cls.available_backend_names():
            return requested
        return "bdb"

    @staticmethod
    def _create_backend(name: str) -> DebuggerBackend:
        if str(name or "").strip().lower() == "debugpy":
            from .debugpy_backend import DebugpyPythonDebuggerBackend

            return DebugpyPythonDebuggerBackend()
        if str(name or "").strip().lower() == "bdb":
            return BdbPythonDebuggerBackend()
        return BdbPythonDebuggerBackend()

    @staticmethod
    def _debugpy_available() -> bool:
        from .debugpy_backend import DebugpyPythonDebuggerBackend

        return DebugpyPythonDebuggerBackend.is_available()

    @staticmethod
    def _debugpy_ready() -> bool:
        from .debugpy_backend import DebugpyPythonDebuggerBackend

        return DebugpyPythonDebuggerBackend.implementation_ready()

    def _forward_backend_signals(self) -> None:
        self._impl.stateChanged.connect(self.stateChanged)
        self._impl.stdoutReceived.connect(self.stdoutReceived)
        self._impl.stderrReceived.connect(self.stderrReceived)
        self._impl.protocolError.connect(self.protocolError)
        self._impl.started.connect(self.started)
        self._impl.breakpointsSet.connect(self.breakpointsSet)
        self._impl.paused.connect(self.paused)
        self._impl.watchValuesUpdated.connect(self.watchValuesUpdated)
        self._impl.evaluationResult.connect(self.evaluationResult)
        self._impl.exceptionRaised.connect(self.exceptionRaised)
        self._impl.fatalError.connect(self.fatalError)
        self._impl.processEnded.connect(self.processEnded)
        self._impl.finished.connect(self.finished)
